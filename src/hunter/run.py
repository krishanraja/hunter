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
from .gates import FLOOR, names_foreign_geo, run_gates
from .report import report_run
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


HASH_SUFFIX = re.compile(r"-[0-9a-f]{6}$")


# 0.6 lets "Director of Sales, Enterprise" capture the "- New York" variant
# (3/5 tokens); 0.65 keeps "Chief of Staff" pairing with "Chief of Staff to
# the CEO" (2/3) while regional variants stay distinct.
FUZZY_TITLE_MIN = 0.65


def _norm_title(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def match_rows(sheet_rows: list[SheetRow], db_rows: list[dict]
               ) -> tuple[list[tuple[SheetRow, dict]], list[SheetRow], list[dict],
                          list[tuple[SheetRow, int]]]:
    remaining = list(db_rows)
    pairs: list[tuple[SheetRow, dict]] = []
    ambiguous: list[tuple[SheetRow, int]] = []
    ambiguous_db: list[dict] = []
    unmatched_sheet: list[SheetRow] = []

    def db_url(d):
        return d.get("url") or d.get("job_url")

    def tie_break(cands: list[dict], role: str) -> list[dict]:
        # Rows the incumbent recorded with a shared board URL collide on the
        # URL passes; the title decides when it can do so unambiguously.
        if len(cands) <= 1:
            return cands
        close = [d for d in cands
                 if title_jaccard(role, d.get("title", "")) >= FUZZY_TITLE_MIN]
        if len(close) == 1:
            return close
        pool = close or cands
        exact = [d for d in pool if _norm_title(d.get("title", "")) == _norm_title(role)]
        if len(exact) == 1:
            return exact
        return pool

    def strict_candidates(srow):
        sk = ats_key(srow.jd_url)
        if sk:
            c = [d for d in remaining if ats_key(db_url(d)) == sk]
            if c:
                return c
        if srow.jd_url:
            nu = norm_url(srow.jd_url)
            # a shared careers-page URL is weak identity, so this pass also
            # demands a plausible title; the job_id passes below still run
            c = [d for d in remaining
                 if norm_url(db_url(d)) == nu
                 and (title_jaccard(srow.role, d.get("title", "")) >= FUZZY_TITLE_MIN
                      or _norm_title(d.get("title", "")) == _norm_title(srow.role))]
            if c:
                return c
        # exact job_id, or the incumbent's variant that differs by exactly
        # one trailing 6-hex hash segment; never a longer slug
        # (…-enterprise must not swallow …-enterprise-new-york-b94aed)
        jid = job_id(srow.company, srow.role)
        c = [d for d in remaining
             if d.get("job_id") == jid
             or HASH_SUFFIX.sub("", d.get("job_id", "")) == jid]
        if c:
            return c
        cslug = slugify(srow.company)
        return [d for d in remaining
                if d.get("job_id", "").split(":")[0] == cslug
                and _norm_title(d.get("title", "")) == _norm_title(srow.role)]

    def fuzzy_candidates(srow):
        cslug = slugify(srow.company)
        return [d for d in remaining
                if d.get("job_id", "").split(":")[0] == cslug
                and title_jaccard(srow.role, d.get("title", "")) >= FUZZY_TITLE_MIN]

    # Two phases: every row's strong-identity matches (URL, job_id, exact
    # title) resolve before any fuzzy pairing. A fuzzy match must never
    # steal a DB row whose true partner appears later on the sheet (the
    # 2026-08-31 ElevenLabs chief-of-staff mis-sync: row 20's fuzzy match
    # took the France row that belonged, by exact URL, to row 84).
    deferred: list[SheetRow] = []
    for phase in ("strict", "fuzzy"):
        rows_this_phase = sheet_rows if phase == "strict" else deferred
        for srow in rows_this_phase:
            candidates = (strict_candidates(srow) if phase == "strict"
                          else fuzzy_candidates(srow))
            candidates = tie_break(candidates, srow.role)
            if len(candidates) == 1:
                pairs.append((srow, candidates[0]))
                remaining.remove(candidates[0])
            elif len(candidates) > 1:
                ambiguous.append((srow, len(candidates)))
                ambiguous_db.extend(candidates)
            elif phase == "strict":
                deferred.append(srow)
            else:
                unmatched_sheet.append(srow)
    # A DB row tangled in an ambiguity is NOT missing from the sheet; letting
    # it through db_only re-appends it every run (the 2026-08-31 duplicate
    # rows 111-131 incident). It stays withheld until a human untangles it.
    amb_ids = {id(d) for d in ambiguous_db}
    db_only = [d for d in remaining if id(d) not in amb_ids]
    return pairs, unmatched_sheet, db_only, ambiguous


def reconcile(cfg: Config, canon: Canon, sheet: Sheet) -> ReconcileLedger:
    ledger = ReconcileLedger()
    sheet_rows = sheet.read_pipeline(canon.sheet_headers)
    db_rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,status,krish_verdict,"
                  "rejection_reason,package_status,package_cv_url,package_letter_url,"
                  "presented_at,source,location,comp,why_it_fits",
        "status": "neq.duplicate",
        "limit": "2000"})
    pairs, sheet_only, db_only, ambiguous = match_rows(sheet_rows, db_rows)
    ledger.matched = [(s.row_number, d["job_id"]) for s, d in pairs]

    # A sheet row that duplicates another sheet row must never mint a new DB
    # job_id or count as a fresh ambiguity; it is reported for Krish to
    # delete, and nothing else happens to it. Identity is company + title,
    # the same rule the sourcing dedupe and dedupe-db use: two postings
    # sharing both are one application target even when their URLs differ.
    def sheet_ident(s: SheetRow):
        return (slugify(s.company), _norm_title(s.role))

    seen_idents = {sheet_ident(s): s.row_number for s, _ in pairs}
    for srow, n_cands in ambiguous:
        dup_of = seen_idents.get(sheet_ident(srow))
        if dup_of and dup_of != srow.row_number:
            ledger.skipped.append(
                f"sheet row {srow.row_number} duplicates row {dup_of} "
                f"({srow.company} / {srow.role}); no action, safe to delete")
        else:
            seen_idents.setdefault(sheet_ident(srow), srow.row_number)
            ledger.ambiguous.append(
                f"sheet row {srow.row_number} {srow.company!r}/{srow.role!r} "
                f"matched {n_cands} DB rows")

    # direction 1: sheet-only rows insert into hunter_seen_roles
    # (the id guard includes duplicate-marked rows: their job_ids are taken)
    db_ids = {r["job_id"] for r in db_get(
        cfg, "hunter_seen_roles", {"select": "job_id", "limit": "5000"})}
    inserts = []
    for srow in sheet_only:
        dup_of = seen_idents.get(sheet_ident(srow))
        if dup_of:
            ledger.skipped.append(
                f"sheet row {srow.row_number} duplicates row {dup_of} "
                f"({srow.company} / {srow.role}); not inserted, safe to delete")
            continue
        seen_idents[sheet_ident(srow)] = srow.row_number
        if job_id(srow.company, srow.role) in db_ids:
            ledger.skipped.append(
                f"sheet row {srow.row_number} ({srow.company} / {srow.role}): "
                f"job_id already in the DB but paired elsewhere; no action")
            continue
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

    # direction 2: DB rows the sheet lacks, but only ones with standing.
    #
    # This direction writes onto Krish's sheet, so the bar is evidence, not a
    # status string. The retired incumbent left ~57 rows sitting at
    # status='staging' carrying its own scores, and trusting those put 12 non
    # UK, non US roles in front of him at score 8: ElevenLabs GM Brazil,
    # Denmark, Poland, Saudi Arabia and the rest. Hunter's own scorer rates
    # that Brazil role 2 and its G6 gate fails it outright on geography
    # (2026-09-01 audit). A score this system did not produce is not evidence.
    to_append = []
    for d in db_only:
        url = d.get("url") or d.get("job_url")
        decided = bool(d.get("krish_verdict")) or (d.get("package_status") or "none") != "none"
        # hunter always writes sweep_date and why_it_fits; the incumbent never did
        hunter_judged = bool(d.get("sweep_date")) and bool(d.get("why_it_fits"))
        if not decided:
            if not hunter_judged:
                ledger.skipped.append(
                    f"{d['job_id']}: scored by the retired incumbent, never gated "
                    f"by hunter; not put on the sheet")
                continue
            if int(d.get("score") or 0) < canon.bar:
                ledger.skipped.append(
                    f"{d['job_id']}: score {d.get('score')} is below the canon "
                    f"{canon.bar} bar; not put on the sheet")
                continue
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


def cmd_prune_sheet(apply: bool = False, include_ungated: bool = False) -> int:
    """Remove rows this system should never have written.

    Only rows where column A still reads exactly "New", so nothing Krish has
    touched is ever at risk, and only two unambiguous classes by default:
      1. duplicates of an earlier row (same company and title),
      2. rows sitting outside canon 9.4 geography, which is how ElevenLabs GM
         Brazil, Denmark, Poland and Saudi Arabia reached the sheet at score 8.

    Rows the retired incumbent scored but hunter never gated are REPORTED, not
    deleted: some are plausible New York and US-remote roles, and throwing away
    possibly good work is worse than leaving it for a re-gate. --incumbent
    removes them too, if that is what you want.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    srows = sheet.read_pipeline(canon.sheet_headers)
    db = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,status,krish_verdict,"
                  "rejection_reason,package_status,package_cv_url,package_letter_url,"
                  "presented_at,source,location,comp,why_it_fits,sweep_date",
        "status": "neq.duplicate", "limit": "5000"})
    pairs, sheet_only, _, _ = match_rows(srows, db)

    def ident(s: SheetRow):
        return (slugify(s.company), _norm_title(s.role))

    untouched = lambda s: (s.verdict or "").strip() == "New"
    plan: dict[int, str] = {}
    first: dict[tuple, int] = {}
    for s in srows:
        k = ident(s)
        if k in first:
            if untouched(s):
                plan[s.row_number] = f"duplicate of row {first[k]}"
        else:
            first[k] = s.row_number
    ungated = []
    for s, d in pairs:
        if s.row_number in plan or not untouched(s):
            continue
        loc = (d.get("location") or "")
        decided = bool(d.get("krish_verdict")) or (d.get("package_status") or "none") != "none"
        judged = bool(d.get("sweep_date")) and bool(d.get("why_it_fits"))
        if names_foreign_geo(loc):
            plan[s.row_number] = f"outside canon geography: {loc[:34]}"
        elif not decided and not judged:
            ungated.append((s, d))
    if include_ungated:
        for s, d in ungated:
            plan[s.row_number] = (f"incumbent score {d.get('score')}, never gated by "
                                  f"hunter ({(d.get('location') or 'no location')[:26]})")

    kept = [s.row_number for s in srows if not untouched(s)]
    print(f"{len(srows)} data rows; {len(plan)} to remove; "
          f"{len(kept)} rows you have written on are untouchable")
    for n in sorted(plan):
        row = next(r for r in srows if r.row_number == n)
        print(f"  row {n:>3}  {row.company[:18]:18} {row.role[:38]:38} {plan[n]}")
    if ungated and not include_ungated:
        print(f"\n{len(ungated)} more rows carry an incumbent score hunter never "
              f"gated. Left in place; --incumbent removes them too:")
        for s2, d2 in sorted(ungated, key=lambda x: x[0].row_number)[:8]:
            print(f"  row {s2.row_number:>3}  {s2.company[:18]:18} {s2.role[:34]:34} "
                  f"score {d2.get('score')}")
        if len(ungated) > 8:
            print(f"  ... {len(ungated) - 8} more")
    if not apply:
        print("\ndry run. add --apply to delete these rows")
        return 0
    removed = sheet.delete_rows(sorted(plan))
    print(f"\ndeleted {removed} rows; sheet now has "
          f"{len(sheet.read_pipeline(canon.sheet_headers))} data rows")
    return 0


def cmd_bridges(ingest_dir: str | None = None) -> int:
    """The bridge layer: optional export ingest, lazy enrichment, bridge
    build, top-five report. Drafts only; nothing is ever sent to anyone."""
    from .people import bridges as bridges_mod
    from .people import enrich as enrich_mod
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    if ingest_dir:
        from .people import ingest as ingest_mod
        stats = ingest_mod.ingest(cfg, ingest_dir)
        print(f"ingest: {stats}")
    targets = {slugify(c) for c in canon.universe}
    for r in bridges_mod.target_roles(cfg):
        targets.add(slugify(r["company"]))
    if cfg.optional("hunter_apify_enrichment_token"):
        try:
            print(f"enrich: {enrich_mod.enrich(cfg, targets)}")
        except Exception as e:
            # bridges still build from what is already known
            print(f"enrich failed, continuing without fresh history: "
                  f"{e.__class__.__name__}: {e}")
    else:
        print("enrich skipped: hunter_apify_enrichment_token absent")
    print(f"bridges: {bridges_mod.build_bridges(cfg, sheet)}")
    for i, b in enumerate(bridges_mod.top_bridges(cfg), start=1):
        print(f"#{i} [{b['path_tier']}] score {b['bridge_score']} "
              f"{b['job_id']}\n    {b['path_evidence']}\n    draft: {b['draft_ask']}")
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


def seen_identity_keys(cfg: Config) -> set:
    """Every identity under which a role is already known: job_id, ATS key,
    normalized URL, and (company-slug, normalized title). Duplicate-marked
    rows count too; a role once seen stays seen."""
    keys: set = set()
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,url,job_url,company,title", "limit": "5000"})
    for r in rows:
        keys.add(r["job_id"])
        keys.add((slugify(r.get("company") or ""), _norm_title(r.get("title") or "")))
        u = r.get("url") or r.get("job_url") or ""
        if u.startswith("http"):
            keys.add(norm_url(u))
            ak = ats_key(u)
            if ak:
                keys.add(ak)
    return keys


def cmd_dedupe_db() -> int:
    """Mark DB rows that duplicate another row's identity (same company +
    normalized title) status=duplicate so reconcile and the router ignore
    them. The keeper is chosen by standing: a verdict, then a package, then
    presented_at, then the incumbent's hash-suffixed job_id. A row with a
    verdict or a package is never marked; groups where standing ties are
    reported and left alone."""
    cfg = load()
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,krish_verdict,package_status,"
                  "presented_at,status",
        "status": "neq.duplicate", "limit": "5000"})
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(
            (slugify(r.get("company") or ""), _norm_title(r.get("title") or "")),
            []).append(r)
    marked, held = 0, 0
    for ident, group in sorted(groups.items()):
        if len(group) < 2 or not ident[0] or not ident[1]:
            continue

        def rank(r):
            return (bool(r.get("krish_verdict")),
                    (r.get("package_status") or "none") != "none",
                    bool(r.get("presented_at")),
                    bool(HASH_SUFFIX.search(r.get("job_id") or "")))

        ranked = sorted(group, key=rank, reverse=True)
        keeper, losers = ranked[0], ranked[1:]
        protected = [r for r in losers
                     if r.get("krish_verdict")
                     or (r.get("package_status") or "none") != "none"]
        if protected:
            held += 1
            print(f"HELD {ident[0]}/{ident[1]}: more than one row has standing; "
                  f"kept nothing, review {[r['job_id'] for r in group]}")
            continue
        for r in losers:
            db_patch(cfg, "hunter_seen_roles", {"job_id": r["job_id"]},
                     {"status": "duplicate",
                      "rejection_reason": f"duplicate of {keeper['job_id']}"})
            marked += 1
            print(f"duplicate: {r['job_id']} -> keeper {keeper['job_id']}")
    print(f"dedupe-db: {marked} rows marked duplicate, {held} groups held for review")
    return 0


def linkedin_search_urls(cfg: Config, sheet: Sheet) -> list[str]:
    """The nine sourcing searches live in the Role Targeting tab (brief:
    read each run so Krish can edit them without a deploy); the
    hunter_linkedin_search_urls config key is the fallback."""
    urls: list[str] = []
    try:
        for row in sheet.read_tab_values("Role Targeting!A1:Z400"):
            for cell in row:
                if isinstance(cell, str) and "linkedin.com/jobs/search" in cell:
                    urls.append(cell.strip())
    except Exception:
        pass
    if not urls:
        raw = cfg.optional("hunter_linkedin_search_urls")
        if raw:
            import json as json_mod
            urls = json_mod.loads(raw)
    return urls


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
    urls = linkedin_search_urls(cfg, sheet)
    if urls:
        try:
            postings.extend(sweep_linkedin(
                cfg, urls, spend=spend,
                max_charge_usd=float(cfg.optional("hunter_apify_max_usd_per_call", "2.00"))))
            summary.append(f"apify linkedin: {len(urls)} search urls swept")
        except Exception as e:
            summary.append(f"apify linkedin sweep failed: {e.__class__.__name__}: {e}")
    else:
        summary.append("apify linkedin sweep skipped: no search URLs in the "
                       "Role Targeting tab or hunter_linkedin_search_urls")
    counts["spend_usd"] = round(spend.spent, 2)

    counts["discovered"] = len(postings)
    senior = [p for p in postings if p.title and SENIOR_TITLE.search(p.title)]
    counts["senior"] = len(senior)

    # Dedupe BEFORE any paid or per-posting call, on identity rather than
    # job_id alone: the incumbent's job_ids carry 6-hex hash suffixes, so a
    # re-discovered posting would otherwise re-record under a bare job_id
    # forever (the 2026-08-31 DB near-duplicates). A second posting with the
    # same company and title is the same application target for Krish even
    # when the ATS ids differ.
    seen_keys = seen_identity_keys(cfg)
    fresh = []
    for p in senior:
        keys = [job_id(p.company, p.title),
                (slugify(p.company), _norm_title(p.title))]
        if p.url:
            keys.append(norm_url(p.url))
            ak = ats_key(p.url)
            if ak:
                keys.append(ak)
        if any(k in seen_keys for k in keys):
            continue
        for k in keys:
            seen_keys.add(k)
        fresh.append(p)
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
    started_at = datetime.datetime.now(datetime.timezone.utc)
    counts: dict = {}
    run_error: str | None = None
    try:
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)

        ledger = reconcile(cfg, canon, sheet)
        summary.extend(ledger.lines())

        counts = source_and_stage(cfg, canon, sheet, summary)
        counts["reconciled"] = len(ledger.matched)
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
        counts["built"] = built
    except Exception as e:
        summary.append(f"RUN ABORTED: {e.__class__.__name__}: {e}")
        run_error = f"{e.__class__.__name__}: {e}"
        failed = True
    finally:
        try:
            cfg2 = load()
            # Control Center reads workflow_runs and silent_failures; without
            # this the run is invisible there, and invisible work reads as
            # work that never happened.
            report_run(cfg2, started_at=started_at, ok=not failed, counts=counts,
                       spend_usd=float(counts.get("spend_usd") or 0),
                       summary_line=_status_line(counts, failed),
                       error=run_error)
        except Exception as e:
            print(f"run reporting failed: {e}")
        try:
            cfg2 = load()
            send_summary(cfg2, "\n".join(summary))
        except Exception as e:
            print(f"notify failed: {e}")
            print("\n".join(summary))
    return 1 if failed else 0


def _status_line(counts: dict, failed: bool) -> str:
    if failed:
        return "run failed"
    return (f"{counts.get('recorded', 0)} roles recorded, "
            f"{counts.get('staged', 0)} staged, "
            f"{counts.get('built', 0)} packages built")


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
    if cmd == "dedupe-db":
        return cmd_dedupe_db()
    if cmd == "prune-sheet":
        return cmd_prune_sheet(apply="--apply" in argv,
                               include_ungated="--incumbent" in argv)
    if cmd == "bridges":
        ingest_dir = None
        if len(argv) >= 3 and argv[1] == "--ingest":
            ingest_dir = argv[2]
        return cmd_bridges(ingest_dir)
    print(f"unknown command {cmd!r}; commands: run, reconcile, migrate-sheet, "
          f"build --job-id X, recon, dedupe-db, bridges [--ingest DIR], prune-sheet [--apply]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
