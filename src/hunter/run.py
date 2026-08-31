"""Orchestrator. python -m hunter.run {run,reconcile,migrate-sheet,build,recon}

Phases of a full run, in order:
  1. Load config and canon; every hard-fail guard fires before any paid call,
     and the constants encoded in gates/score are cross-checked against what
     canon currently says.
  2. Reconcile the sheet and hunter_seen_roles in BOTH directions (canon 9.13:
     "Every run must reconcile both directions and report what it reconciled").
  3. Approval watch: column A verdicts flow into krish_verdict; rejections are
     quoted verbatim into rejection_reason; go verdicts feed the router.
  4. Source, dedupe before any paid call, resolve JDs, gate G1-G7, score,
     record every evaluated role (rejects included, sweep_date always set).
  5. Stage roles at or above the bar to the sheet through the validated write
     path; a role whose presented_at is set is never re-presented absent
     material change (a query, not a judgement call).
  6. Build packages for go-verdict roles (G1 re-verified first; capped per
     run), record to DB and sheet.
  7. Telegram summary. A run that wrote zero rows is a FAILED run and says so.
"""
from __future__ import annotations

import datetime
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config as config_mod
from .canon import Canon, CanonError, load_canon
from .config import Config, GoogleOAuth, GoogleServiceAccount, db_get, db_insert, db_patch, load
from .docbuild import DocBuild
from .gates import FLOOR, run_gates
from .router import classify_verdict, route_status, select_for_build
from .score import BAR, score_role
from .sheet import Sheet, SheetRow, make_row
from .sources import ResolvedRole, job_id, slugify
from .notify import send_summary

TODAY = lambda: datetime.date.today().isoformat()
NOW = lambda: datetime.datetime.utcnow().isoformat() + "Z"


def assert_canon_alignment(canon: Canon) -> None:
    """Canon supersedes code. If canon moved, stop and say which side to fix."""
    if canon.bar != BAR:
        raise CanonError(f"canon 9.2 bar is {canon.bar} but score.py encodes {BAR}; "
                         f"update score.BAR and rerun")
    floor_match = re.search(r"\$([\d,]+) base", canon.section_text("6"))
    canon_floor = int(floor_match.group(1).replace(",", "")) if floor_match else None
    if canon_floor and canon_floor != FLOOR:
        raise CanonError(f"canon 6 floor is ${canon_floor:,} but gates.py encodes "
                         f"${FLOOR:,}; update gates.FLOOR and rerun")


# ---------- reconciliation (canon 9.13, mandatory every run) ----------

STRIP_PARAMS = re.compile(r"^(utm_|ref$|src$|gh_)")

ATS_URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)")),
    ("greenhouse", re.compile(r"gh_jid=(\d+)()")),
    ("lever", re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})")),
]


def norm_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if not STRIP_PARAMS.match(k.lower())])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), query, ""))


def ats_key(url: str | None) -> tuple | None:
    if not url:
        return None
    for ats, pat in ATS_URL_PATTERNS:
        m = pat.search(url)
        if m:
            return (ats, m.group(1), m.group(2))
    return None


TITLE_STOPWORDS = {"the", "and", "of", "to", "for", "a", "an", "in", "at"}


def title_jaccard(a: str, b: str) -> float:
    ta = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if w not in TITLE_STOPWORDS}
    tb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if w not in TITLE_STOPWORDS}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class ReconcileLedger:
    matched: list[tuple[int, str]] = field(default_factory=list)   # (sheet row, job_id)
    sheet_to_db: list[str] = field(default_factory=list)
    db_to_sheet: list[str] = field(default_factory=list)
    verdicts_synced: list[str] = field(default_factory=list)
    packages_synced: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"reconciled: {len(self.matched)} matched, "
               f"{len(self.sheet_to_db)} sheet-only inserted to DB, "
               f"{len(self.db_to_sheet)} DB-only appended to sheet, "
               f"{len(self.verdicts_synced)} verdicts synced, "
               f"{len(self.packages_synced)} package links synced"]
        for label, items in (("sheet->db", self.sheet_to_db),
                             ("db->sheet", self.db_to_sheet),
                             ("verdicts", self.verdicts_synced),
                             ("packages", self.packages_synced),
                             ("AMBIGUOUS, no action", self.ambiguous),
                             ("skipped", self.skipped)):
            for item in items:
                out.append(f"  {label}: {item}")
        return out


def match_rows(sheet_rows: list[SheetRow], db_rows: list[dict]
               ) -> tuple[list[tuple[SheetRow, dict]], list[SheetRow], list[dict], list[str]]:
    remaining = list(db_rows)
    pairs: list[tuple[SheetRow, dict]] = []
    ambiguous: list[str] = []
    unmatched_sheet: list[SheetRow] = []

    def db_url(d):
        return d.get("url") or d.get("job_url")

    for srow in sheet_rows:
        candidates = []
        sk = ats_key(srow.jd_url)
        if sk:
            candidates = [d for d in remaining if ats_key(db_url(d)) == sk]
        if not candidates and srow.jd_url:
            nu = norm_url(srow.jd_url)
            candidates = [d for d in remaining if norm_url(db_url(d)) == nu]
        if not candidates:
            jid = job_id(srow.company, srow.role)
            candidates = [d for d in remaining if d.get("job_id") == jid]
        if not candidates:
            cslug = slugify(srow.company)
            candidates = [d for d in remaining
                          if d.get("job_id", "").split(":")[0] == cslug
                          and title_jaccard(srow.role, d.get("title", "")) >= 0.6]
        if len(candidates) == 1:
            pairs.append((srow, candidates[0]))
            remaining.remove(candidates[0])
        elif len(candidates) > 1:
            ambiguous.append(f"sheet row {srow.row_number} {srow.company!r}/"
                             f"{srow.role!r} matched {len(candidates)} DB rows")
        else:
            unmatched_sheet.append(srow)
    return pairs, unmatched_sheet, remaining, ambiguous


def reconcile(cfg: Config, canon: Canon, sheet: Sheet) -> ReconcileLedger:
    ledger = ReconcileLedger()
    sheet_rows = sheet.read_pipeline(canon.sheet_headers)
    db_rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,status,krish_verdict,"
                  "rejection_reason,package_status,package_cv_url,package_letter_url,"
                  "presented_at,source,location,comp,why_it_fits",
        "limit": "2000"})
    pairs, sheet_only, db_only, ambiguous = match_rows(sheet_rows, db_rows)
    ledger.ambiguous.extend(ambiguous)
    ledger.matched = [(s.row_number, d["job_id"]) for s, d in pairs]

    # direction 1: sheet-only rows insert into hunter_seen_roles
    inserts = []
    for srow in sheet_only:
        verdict_kind = classify_verdict(srow.verdict)
        row = {
            "job_id": job_id(srow.company, srow.role), "company": srow.company,
            "title": srow.role, "url": srow.jd_url or "", "job_url": srow.jd_url or "",
            "source": (srow.cells[15] or "sheet reconcile"),
            "status": "dropped" if verdict_kind == "rejection" else "staging",
            "why_it_fits": srow.cells[9] if srow.cells[9] != "Not assessed" else "",
            "location": srow.cells[12] if srow.cells[12] != "Not stated" else "",
            "comp": srow.cells[13] if srow.cells[13] != "Not disclosed" else "",
            "sweep_date": TODAY(), "presented_at": NOW(),
        }
        try:
            row["score"] = int(srow.cells[8])
        except (ValueError, TypeError):
            pass
        if verdict_kind == "rejection":
            row["krish_verdict"] = srow.verdict
            row["rejection_reason"] = srow.verdict  # verbatim, per canon 9.13
            row["verdict_source"] = "sheet column A"
            row["verdict_at"] = NOW()
        elif verdict_kind in ("go", "applied"):
            row["krish_verdict"] = srow.verdict
            row["verdict_source"] = "sheet column A"
            row["verdict_at"] = NOW()
        inserts.append(row)
        ledger.sheet_to_db.append(f"{row['job_id']} (row {srow.row_number}, "
                                  f"verdict {srow.verdict!r})")
    if inserts:
        db_insert(cfg, "hunter_seen_roles", inserts, on_conflict="job_id",
                  ignore_duplicates=True)

    # direction 2: DB rows the sheet lacks, but only ones with standing
    to_append = []
    for d in db_only:
        has_standing = (d.get("status") in ("staging", "presented")
                        or (d.get("package_status") or "none") != "none"
                        or d.get("krish_verdict"))
        if not has_standing:
            continue
        url = d.get("url") or d.get("job_url")
        if not url or not str(url).startswith("http"):
            ledger.skipped.append(f"{d['job_id']}: no resolvable URL, cannot "
                                  f"build column D")
            continue
        if not d.get("score"):
            ledger.skipped.append(f"{d['job_id']}: no score; needs review before "
                                  f"a sheet row exists")
            continue
        to_append.append(d)
    if to_append:
        rows = [make_row(company=d.get("company") or "Unknown", role=d["title"],
                         jd_url=d.get("url") or d.get("job_url"),
                         score=int(d["score"]),
                         why_it_fits=d.get("why_it_fits") or "",
                         location=d.get("location") or "",
                         comp=d.get("comp") or "",
                         source=d.get("source") or "hunter",
                         jd_snippet="Reconciled from hunter_seen_roles.")
                for d in to_append]
        rng = sheet.append_rows(rows)
        start = int(rng.split("!A")[1].split(":")[0])
        for offset, d in enumerate(to_append):
            ledger.db_to_sheet.append(f"{d['job_id']} -> sheet row {start + offset}")
            if (d.get("package_status") == "built" and d.get("package_cv_url")
                    and d.get("package_letter_url")):
                ledger.skipped.append(
                    f"{d['job_id']}: built package links known but PDFs not "
                    f"recorded in DB; links left as Not built for a manual pass")

    # matched rows: field-level sync, one direction per field owner
    for srow, d in pairs:
        verdict_kind = classify_verdict(srow.verdict)
        if verdict_kind != "none" and not d.get("krish_verdict"):
            patch = {"krish_verdict": srow.verdict, "verdict_at": NOW(),
                     "verdict_source": "sheet column A"}
            if verdict_kind == "rejection":
                patch["rejection_reason"] = srow.verdict
                patch["status"] = "dropped"
                patch["package_status"] = "blocked"
            db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]}, patch)
            ledger.verdicts_synced.append(f"{d['job_id']}: {srow.verdict!r}")
        if (d.get("package_status") == "built"
                and srow.package_urls.get("cv") is None
                and d.get("package_cv_url") and d.get("package_letter_url")):
            ledger.packages_synced.append(
                f"{d['job_id']}: sheet row {srow.row_number} lacks package links; "
                f"cv={d['package_cv_url']}")
    return ledger


# ---------- commands ----------

def build_context():
    cfg = load()
    canon = load_canon(cfg)
    assert_canon_alignment(canon)
    return cfg, canon


def cmd_recon() -> int:
    cfg, canon = build_context()
    rows = db_get(cfg, "hunter_seen_roles", {"select": "status", "limit": "2000"})
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"] or "none"] = by_status.get(r["status"] or "none", 0) + 1
    print(f"canon v{canon.version}: bar {canon.bar}, {len(canon.universe)} universe "
          f"companies, gates {sorted(canon.gates)}")
    print(f"hunter_seen_roles: {len(rows)} rows by status {by_status}")
    return 0


def cmd_migrate_sheet() -> int:
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    print(sheet.migrate_formatting())
    return 0


def cmd_reconcile() -> int:
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    ledger = reconcile(cfg, canon, sheet)
    print("\n".join(ledger.lines()))
    return 0


def fetch_with_retry(fetch, slug: str, pid: str, attempts: int = 2):
    """One transient network failure (timeout, reset) never decides liveness
    or aborts a run; the second consecutive one propagates to the caller."""
    import requests as requests_mod
    for i in range(attempts):
        try:
            return fetch(slug, pid)
        except requests_mod.RequestException:
            if i + 1 == attempts:
                raise
            time.sleep(2)


def _resolve_for_build(row: dict) -> ResolvedRole:
    from .ats import ashby, greenhouse, lever
    url = row.get("url") or row.get("job_url") or ""
    key = ats_key(url)
    live, jd, jd_url = False, "", url
    if key:
        ats, slug, pid = key
        fetch = {"greenhouse": greenhouse.fetch_posting,
                 "lever": lever.fetch_posting,
                 "ashby": ashby.fetch_posting}[ats]
        live, jd, jd_url = fetch_with_retry(fetch, slug, pid)
    return ResolvedRole(company=row.get("company") or "", title=row["title"],
                        url=url, jd_url=jd_url, jd_text=jd, live=live,
                        source=row.get("source") or "", location=row.get("location") or "",
                        comp=row.get("comp") or "")


def _doc_text(db: DocBuild, doc_id: str) -> str:
    return "".join(p["text"] for p in db.paragraphs(db.get(doc_id)))


def build_one(cfg: Config, canon: Canon, sheet: Sheet, row: dict,
              summary: list[str]) -> bool:
    from .package.build import build_package, read_master_facts
    from .package.tailor import load_blocks, tailor

    role = _resolve_for_build(row)
    if not role.live:
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"status": "dead", "package_status": "blocked",
                  "rejection_reason": "G1: posting dead at build time"})
        summary.append(f"BLOCKED {row['job_id']}: died between approval and build")
        return False
    never = cfg.require_json("hunter_never_apply")
    report = run_gates(role, never_apply=never)
    if not report.passed:
        reasons = "; ".join(f"{g.gate}: {g.reason}" for g in report.failures())
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"package_status": "blocked", "rejection_reason": reasons})
        summary.append(f"BLOCKED {row['job_id']}: {reasons}")
        return False

    letter_blocks, cv_blocks = load_blocks(cfg)
    oauth = GoogleOAuth(cfg)
    db = DocBuild(oauth.access_token)
    facts = read_master_facts(db)
    tr = tailor(cfg, canon, company=role.company, title=role.title,
                jd_text=role.jd_text, master_competencies=facts.cv_competencies,
                letter_blocks=letter_blocks)
    result = build_package(db, tr, company=role.company, title=role.title,
                           letter_blocks=letter_blocks, cv_blocks=cv_blocks,
                           facts=facts)
    ok = (result.letter_report and result.letter_report.ok
          and result.cv_report and result.cv_report.ok)
    if ok:
        pkg_report = run_gates(role, never_apply=never, package_texts=(
            _doc_text(db, result.letter_doc_id), _doc_text(db, result.cv_doc_id)))
        ok = pkg_report.passed
        if not ok:
            reasons = "; ".join(f"{g.gate}: {g.reason}" for g in pkg_report.failures())
            summary.append(f"BLOCKED {row['job_id']} at package gates: {reasons}")
    if not ok:
        fails = ((result.letter_report.failures if result.letter_report else [])
                 + (result.cv_report.failures if result.cv_report else []))
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"package_status": "blocked",
                  "rejection_reason": f"package verification failed: {fails}"})
        return False

    db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, {
        "package_status": "built", "package_built_at": NOW(),
        "package_cv_url": result.cv_url, "package_letter_url": result.letter_url,
    })
    status = route_status(row)
    sheet_rows = sheet.read_pipeline(canon.sheet_headers)
    pairs, _, _, _ = match_rows(sheet_rows, [row])
    if pairs:
        sheet.update_package_cells(
            pairs[0][0].row_number, cv_url=result.cv_url,
            letter_url=result.letter_url, cv_pdf_url=result.cv_pdf_url,
            letter_pdf_url=result.letter_pdf_url, package_status=status,
            built_date=TODAY())
    flags = "; ".join(tr.flags + result.notes) or "clean"
    summary.append(f"BUILT {row['job_id']} block={tr.block_key} "
                   f"words={result.letter_report.body_word_count} flags={flags}")
    summary.append(f"  CV {result.cv_url}")
    summary.append(f"  CL {result.letter_url}")
    return True


def cmd_build(target_job_id: str) -> int:
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = db_get(cfg, "hunter_seen_roles",
                  {"select": "*", "job_id": f"eq.{target_job_id}", "limit": "1"})
    if not rows:
        print(f"job_id not found: {target_job_id}")
        return 1
    summary: list[str] = []
    ok = build_one(cfg, canon, sheet, rows[0], summary)
    print("\n".join(summary))
    return 0 if ok else 1


def source_and_stage(cfg: Config, canon: Canon, sheet: Sheet,
                     summary: list[str]) -> dict:
    from .ats import ashby, greenhouse, lever
    from .gates import SENIOR_TITLE
    from .sources import ats_for
    from .sources.apify_linkedin import SpendTracker, sweep_linkedin

    counts = {"discovered": 0, "senior": 0, "fresh": 0, "resolved": 0,
              "recorded": 0, "staged": 0, "unresolved": 0, "spend_usd": 0.0}
    postings = []
    swept, gaps = [], []
    for company in canon.universe:
        mapping = ats_for(company)
        if not mapping:
            gaps.append(company)
            continue
        ats, slug = mapping
        try:
            fn = {"greenhouse": greenhouse.board, "ashby": ashby.board,
                  "lever": lever.board}[ats]
            board = fn(slug)
            for p in board:
                p.company = company
            postings.extend(board)
            swept.append(f"{company}:{len(board)}")
        except Exception as e:
            summary.append(f"board {company}/{slug} failed: {e.__class__.__name__}")
    summary.append(f"ATS boards swept: {len(swept)}; unmapped companies "
                   f"(discovery-only coverage): {len(gaps)}")

    spend = SpendTracker(cap_usd=float(cfg.optional("hunter_apify_max_usd_per_run", "5.00")))
    li_urls = cfg.optional("hunter_linkedin_search_urls")
    if li_urls:
        try:
            import json as json_mod
            postings.extend(sweep_linkedin(
                cfg, json_mod.loads(li_urls), spend=spend,
                max_charge_usd=float(cfg.optional("hunter_apify_max_usd_per_call", "2.00"))))
        except Exception as e:
            summary.append(f"apify linkedin sweep failed: {e.__class__.__name__}: {e}")
    counts["spend_usd"] = round(spend.spent, 2)

    counts["discovered"] = len(postings)
    senior = [p for p in postings if p.title and SENIOR_TITLE.search(p.title)]
    counts["senior"] = len(senior)

    ids = [job_id(p.company, p.title) for p in senior]
    seen: set[str] = set()
    for i in range(0, len(ids), 80):
        chunk = ",".join(f'"{x}"' for x in ids[i:i + 80])
        seen |= {r["job_id"] for r in db_get(
            cfg, "hunter_seen_roles",
            {"select": "job_id", "job_id": f"in.({chunk})"})}
    fresh = [p for p, jid in zip(senior, ids) if jid not in seen]
    counts["fresh"] = len(fresh)

    never = cfg.require_json("hunter_never_apply")
    inserts, staged_rows = [], []
    fetchers = {"greenhouse": greenhouse.fetch_posting,
                "ashby": ashby.fetch_posting, "lever": lever.fetch_posting}
    for p in fresh:
        fetch = fetchers.get(p.ats or "")
        if fetch:
            try:
                live, jd, jd_url = fetch_with_retry(fetch, p.ats_slug, p.ats_posting_id)
            except Exception as e:
                summary.append(f"resolve failed, recorded unresolved: "
                               f"{p.company}/{p.title!r}: {e.__class__.__name__}")
                fetch = None
        if not fetch:
            counts["unresolved"] += 1
            inserts.append({"job_id": job_id(p.company, p.title), "title": p.title,
                            "company": p.company, "url": p.url, "job_url": p.url,
                            "status": "unresolved", "source": p.source,
                            "sweep_date": TODAY(), "why_it_fits": "",
                            "location": p.location or "", "comp": p.comp_text or ""})
            continue
        counts["resolved"] += 1
        role = ResolvedRole(company=p.company, title=p.title, url=jd_url,
                            jd_url=jd_url, jd_text=jd, live=live,
                            source=p.source, location=p.location or "",
                            comp=p.comp_text or "")
        report = run_gates(role, never_apply=never)
        result = score_role(role)
        status, reason = "scanned", None
        if result.auto_rejected:
            status, reason = "dropped", result.rejection_reason
        elif not report.passed:
            status = "blocked"
            reason = "; ".join(f"{g.gate}: {g.reason}" for g in report.failures())
        elif result.score >= canon.bar:
            status = "staging"
        else:
            status, reason = "dropped", f"score {result.score} below bar {canon.bar}"
        row = {"job_id": role.job_id, "title": role.title, "company": role.company,
               "url": role.url, "job_url": role.jd_url, "score": result.score,
               "status": status, "auto_rejected": result.auto_rejected,
               "rejection_reason": reason, "source": role.source,
               "location": role.location, "comp": role.comp,
               "sweep_date": TODAY(), "why_it_fits": result.why_it_fits,
               "last_verified_at": NOW() if live else None}
        inserts.append(row)
        if status == "staging":
            staged_rows.append((role, result, jd[:300]))

    if inserts:
        db_insert(cfg, "hunter_seen_roles", inserts, on_conflict="job_id",
                  ignore_duplicates=True)
    counts["recorded"] = len(inserts)

    if staged_rows:
        new_rows = [make_row(company=role.company, role=role.title,
                             jd_url=role.jd_url, score=result.score,
                             why_it_fits=result.why_it_fits,
                             location=role.location, comp=role.comp,
                             source=role.source, jd_snippet=snippet)
                    for role, result, snippet in staged_rows]
        sheet.append_rows(new_rows)
        for role, _, _ in staged_rows:
            db_patch(cfg, "hunter_seen_roles", {"job_id": role.job_id},
                     {"presented_at": NOW()})
        counts["staged"] = len(staged_rows)
    return counts


def cmd_run() -> int:
    summary: list[str] = [f"hunter run {TODAY()}"]
    failed = False
    try:
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)

        ledger = reconcile(cfg, canon, sheet)
        summary.extend(ledger.lines())

        counts = source_and_stage(cfg, canon, sheet, summary)
        summary.append(
            f"sourced: {counts['discovered']} discovered, {counts['senior']} senior, "
            f"{counts['fresh']} fresh, {counts['recorded']} recorded, "
            f"{counts['staged']} staged to the sheet, "
            f"{counts['unresolved']} unresolved (never reach the sheet), "
            f"apify spend ${counts['spend_usd']:.2f}")
        if counts["recorded"] == 0 and not ledger.sheet_to_db:
            summary.append("FAILED: a run that reads roles and writes no rows has "
                           "failed even with a good summary")
            failed = True

        built = 0
        for row in select_for_build(cfg):
            try:
                if build_one(cfg, canon, sheet, row, summary):
                    built += 1
            except Exception as e:
                # one package's failure never costs the rest of the batch
                summary.append(f"BUILD FAILED {row['job_id']}: "
                               f"{e.__class__.__name__}: {e}")
                try:
                    db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                             {"package_status": "blocked",
                              "rejection_reason": f"build error: {e.__class__.__name__}"})
                except Exception:
                    pass
        summary.append(f"packages built this run: {built}")
    except Exception as e:
        summary.append(f"RUN ABORTED: {e.__class__.__name__}: {e}")
        failed = True
    finally:
        try:
            cfg2 = load()
            send_summary(cfg2, "\n".join(summary))
        except Exception as e:
            print(f"notify failed: {e}")
            print("\n".join(summary))
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "recon"
    if cmd == "run":
        return cmd_run()
    if cmd == "reconcile":
        return cmd_reconcile()
    if cmd == "migrate-sheet":
        return cmd_migrate_sheet()
    if cmd == "build":
        if len(argv) < 3 or argv[1] != "--job-id":
            print("usage: python -m hunter.run build --job-id <job_id>")
            return 2
        return cmd_build(argv[2])
    if cmd == "recon":
        return cmd_recon()
    print(f"unknown command {cmd!r}; commands: run, reconcile, migrate-sheet, "
          f"build --job-id X, recon")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
