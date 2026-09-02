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
  7. Run summary, carried by the Routine's completion email. A run that
     wrote zero rows is a FAILED run and says so.
"""
from __future__ import annotations

import datetime
import re
import requests
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config as config_mod
from .canon import Canon, CanonError, load_canon
from .config import Config, GoogleOAuth, GoogleServiceAccount, db_get, db_insert, db_patch, load
from .docbuild import DocBuild
from .archetype import archetype
from .gates import FLOOR, names_foreign_geo, run_gates
from . import learn
from .report import report_run
from . import verdicts
from .router import classify_verdict, route_status, select_for_build
from .score import BAR, score_role
from .sheet import Sheet, SheetError, SheetRow, make_row
from .sources import (ResolvedRole, distinctive_tokens, identity_keys,
                      job_id, slugify)
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
    # Criteo and other Workday tenants: host carries the tenant, the path
    # carries the site and the posting.
    ("workday", re.compile(
        r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com/[^/]*/?([A-Za-z0-9_-]+/job/\S+)")),
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


def record_verdict_event(cfg: Config, row: dict, verdict_text: str,
                        kind: str, reason_code: str | None) -> None:
    """Append-only, because the role row gets overwritten and the learning
    loop needs the history."""
    try:
        learn.record(cfg, [dict(row, krish_verdict=verdict_text)])
    except Exception as e:
        print(f"verdict event not recorded for {row.get('job_id')}: "
              f"{e.__class__.__name__}")


def reconcile(cfg: Config, canon: Canon, sheet: Sheet) -> ReconcileLedger:
    ledger = ReconcileLedger()
    # "The sheet" is Pipeline PLUS the archive. Reading only Pipeline makes
    # every archived role look missing and direction 2 re-appends it on the
    # next run, which is how rows 111-131 happened.
    live_rows = sheet.read_pipeline(canon.sheet_headers)
    try:
        archived_rows = sheet.read_archive()
    except Exception as e:
        raise SheetError(
            f"could not read the {config_mod.ARCHIVE_TAB} tab "
            f"({e.__class__.__name__}); refusing to reconcile half the sheet, "
            f"because archived rows would be re-appended to Pipeline") from e
    sheet_rows = live_rows + archived_rows
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
        verdict_kind, reason_code, _inf = learn.classify(srow.verdict)
        # A verdict hunter wrote itself is already on the row, so there is
        # nothing to sync. But if Krish has since changed column A on one of
        # those rows, that IS his verdict and must reach the DB: without this
        # second clause reconcile would silently swallow every correction he
        # makes to a re-gated row.
        stored = (d.get("krish_verdict") or "").strip()
        his_override = (learn.is_auto(d) and stored
                        and srow.verdict.strip() != stored)
        if verdict_kind != "none" and (not stored or his_override):
            patch = {"krish_verdict": srow.verdict, "verdict_at": NOW(),
                     "verdict_source": "sheet column A"}
            if reason_code:
                patch["rejection_code"] = reason_code
            if verdict_kind == "rejection":
                patch["rejection_reason"] = srow.verdict
                patch["status"] = "dropped"
                patch["package_status"] = "blocked"
            db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]}, patch)
            record_verdict_event(cfg, d, srow.verdict, verdict_kind, reason_code)
            ledger.verdicts_synced.append(
                f"{d['job_id']}: {srow.verdict!r}"
                + (f" [{reason_code}]" if reason_code else ""))
        if (not srow.archived
                and d.get("package_status") == "built"
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


def resolve_db_row(srow, known: list[dict], paired: dict) -> tuple[dict | None, str]:
    """The DB row a sheet row is, or a reason it cannot be settled.

    match_rows deliberately leaves an ambiguous row unpaired, which is right
    for reconcile but leaves nothing to stamp. Fall back to identity: the ATS
    key first, then company tokens plus a close title. A candidate carrying
    Krish's own verdict is never chosen, and a genuine tie is refused rather
    than guessed, because stamping the wrong row writes a rejection onto a
    role he may want.
    """
    d = paired.get(srow.row_number)
    if d:
        return d, ""
    rk = ats_key(srow.jd_url)
    toks = distinctive_tokens(srow.company, srow.role)
    cands = []
    for c in known:
        if (c.get("krish_verdict") or "").strip() and not learn.is_auto(c):
            continue
        ck = ats_key(c.get("url") or c.get("job_url"))
        if rk and ck and rk == ck:
            return c, ""
        if not (toks & distinctive_tokens(c.get("company") or "",
                                          c.get("title") or "")):
            continue
        if title_jaccard(srow.role, c.get("title") or "") >= FUZZY_TITLE_MIN:
            cands.append(c)
    if len(cands) == 1:
        return cands[0], ""
    exact = [c for c in cands
             if _norm_title(c.get("title") or "") == _norm_title(srow.role)]
    if len(exact) == 1:
        return exact[0], ""
    if not cands:
        return None, "no DB row matches it"
    return None, ("several DB rows match it: "
                  + ", ".join(c["job_id"] for c in cands[:4]))


def stamp_auto_verdicts(cfg: Config, sheet: Sheet,
                        rows: list[tuple]) -> int:
    """Record hunter's own coded verdict on the DB row FIRST, then on the
    sheet. rows: (sheet_row, db_job_id_or_None, reason_label, reason_text).

    Order matters. If the sheet is written first, the next reconcile reads
    column A, cannot tell the difference, and files hunter's own re-gate
    decision as Krish's judgement. That happened to forty rows on 2026-09-02,
    and the learning loop would then have proposed blocklisting companies he
    never rejected. The DB stamp carries verdict_source so the loop can tell
    hunter's output from his.
    """
    mapping = {}
    for r, jid, code, reason in rows:
        if not jid:
            # Guessing a job_id from the sheet's own text is how two rows
            # leaked on 2026-09-02: the guess matched nothing, the DB row
            # kept no stamp, and reconcile filed hunter's verdict as Krish's.
            raise SheetError(
                f"row {r.row_number} ({r.company} / {r.role}) has no resolved "
                f"DB row; refusing to stamp a guessed job_id")
        text = f"{verdicts.DECLINE_PREFIX}{code}"
        db_patch(cfg, "hunter_seen_roles",
                 {"job_id": jid},
                 {"krish_verdict": text, "verdict_at": NOW(),
                  "verdict_source": learn.AUTO_SOURCE,
                  "rejection_code": verdicts.LABEL_TO_CODE.get(code),
                  "rejection_reason": reason[:500], "status": "dropped"})
        mapping[r.row_number] = text
    return sheet.set_verdicts(mapping)


def full_title(paired: dict, srow) -> str:
    """The posting's own title where the DB kept it, else the sheet's.

    The sheet abbreviates ("GM, UK"); the DB has "General Manager - UK", and
    the shorthand is what failed the seniority gate.
    """
    return (paired.get(srow.row_number) or {}).get("title") or srow.role


def cmd_regate(from_row: int = 41, apply: bool = False, limit: int = 0,
               archive: bool = True) -> int:
    """Re-judge rows hunter never gated, and give every one a real rationale.

    Krish, 2026-09-02: "everything from row 41 and below needs to be
    seriously scrutinized". Measured, of the 70 rows there 20 were
    quota-carry sales seats (canon 9.3 auto-reject), 13 were non-GTM
    functions, and 59 had no rationale worth the name. All of them carried
    the retired incumbent's score, and no gate had ever seen them.

    Rows Krish has already decided are never touched.
    """
    from .package.rationale import write_rationale
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    never = cfg.require_json("hunter_never_apply")
    # Column A is not the only place a verdict lives. Three roles Krish said
    # go to still read "New" on the sheet because the column was reset after
    # the incumbent synced them, and the first re-gate archived all three.
    # The DB verdict is checked too, and a verdicted row is listed, not moved.
    known = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,title,krish_verdict,url,job_url,company",
                    "limit": "5000"})
    # Pair rows to the DB the way reconcile does. Recomputing a job_id from
    # column C does not work: the sheet says "Chief of Staff to Chief
    # Strategy Officer" where the DB says cloudflare:cos-to-cso, and the
    # first re-gate archived eight roles Krish had said go to because of it.
    all_rows = sheet.read_pipeline(canon.sheet_headers)
    pairs, _, _, _ = match_rows(all_rows, list(known))
    paired = {srow.row_number: d for srow, d in pairs}
    decided = [d for d in known if (d.get("krish_verdict") or "").strip()]

    def standing_verdict(r) -> dict | None:
        """Any decided DB row this sheet row could be. Deliberately generous:
        the matcher leaves an ambiguous row unpaired, and an ambiguous row
        that might carry his yes must be held, never archived."""
        d = paired.get(r.row_number)
        if d and (d.get("krish_verdict") or "").strip():
            return d
        rk = ats_key(r.jd_url)
        toks = distinctive_tokens(r.company, r.role)
        for cand in decided:
            ck = ats_key(cand.get("url") or cand.get("job_url"))
            if rk and ck and rk == ck:
                return cand
            if not (toks & distinctive_tokens(cand.get("company") or "",
                                              cand.get("title") or "")):
                continue
            if title_jaccard(r.role, cand.get("title") or "") >= FUZZY_TITLE_MIN:
                return cand
        return None

    rows, held = [], []
    for r in all_rows:
        if r.row_number < from_row or (r.verdict or "").strip() != "New":
            continue
        d = standing_verdict(r)
        if d:
            held.append((r, d))
            continue
        rows.append(r)
    if held:
        print(f"holding {len(held)} row(s) you have already decided on, "
              f"whatever column A says. They are never archived or rescored, "
              f"but they still get a rationale so column J reads the same "
              f"everywhere:")
        for r, d in held:
            print(f"  row {r.row_number}: {r.company} / {r.role} "
                  f"[{d.get('krish_verdict')}]")
        print()
    if limit:
        rows = rows[:limit]
    print(f"re-gating {len(rows)} rows from row {from_row} down\n")

    keep, drop, unresolved = [], [], []
    for r in rows + [h[0] for h in held]:
        decided_row = r in [h[0] for h in held]
        # The sheet already knows the location and comp; without them G6 sees
        # an empty string and fails every row on geography, which would have
        # archived 70 legitimate roles.
        # The archetype test reads the title, not the posting, so it runs
        # before the fetch. Otherwise a role that is not one of his shapes
        # survives simply because its URL is a LinkedIn link nobody can
        # resolve, which is how seven of them stayed on the sheet.
        if not decided_row and not archetype(full_title(paired, r)):
            drop.append((r, 0, "function wrong",
                         f"G11: title {r.role!r} is none of his archetypes"))
            continue
        if not ats_key(r.jd_url):
            unresolved.append((r, "no ATS key on the URL, liveness unverifiable"))
            continue
        try:
            full = full_title(paired, r)
            role = _resolve_for_build({
                "url": r.jd_url, "title": full, "company": r.company,
                "source": "regate",
                "location": r.cells[12] if r.cells[12] != "Not stated" else "",
                "comp": r.cells[13] if r.cells[13] != "Not disclosed" else ""})
        except Exception as e:
            unresolved.append((r, f"fetch failed: {e.__class__.__name__}"))
            continue
        if not role.live and not decided_row:
            drop.append((r, 0, "dead posting", "the ATS no longer lists it"))
            continue
        if not role.jd_text:
            unresolved.append((r, "live but no JD text returned"))
            continue
        # Judge the posting's own title, not the sheet's shorthand. "GM, UK"
        # in column C failed the seniority gate while the real title,
        # "General Manager - UK", passes it.
        report = run_gates(role, never_apply=never)
        result = score_role(role, universe=canon.universe)
        if decided_row:
            # His verdict outranks the rubric. The row keeps the score it has
            # and only gains the rationale it was missing.
            existing = int(r.cells[8]) if str(r.cells[8]).strip().isdigit() else result.score
            why, flags = write_rationale(
                cfg, canon, company=r.company, title=full, jd=role.jd_text,
                score=existing, score_reason=result.why_it_fits,
                location=role.location, comp=role.comp)
            keep.append((r, existing, why, flags + ["your verdict, held"]))
            continue
        if result.auto_rejected:
            drop.append((r, result.score, "requirements mismatch",
                         result.rejection_reason or "canon 9.3 auto-reject"))
        elif not report.passed:
            reasons = "; ".join(f"{g.gate}: {g.reason}" for g in report.failures())
            failed = {g.gate for g in report.failures()}
            # Name the reason honestly. A role that is not one of his shapes is
            # the wrong function, not a requirements mismatch.
            code = ("geo or language" if "G6" in failed
                    else "function wrong" if "G11" in failed
                    else "requirements mismatch")
            drop.append((r, result.score, code, reasons))
        elif result.score < canon.bar:
            drop.append((r, result.score, "requirements mismatch",
                         f"scores {result.score}, below the canon {canon.bar} bar"))
        else:
            why, flags = write_rationale(
                cfg, canon, company=r.company, title=r.role, jd=role.jd_text,
                score=result.score, score_reason=result.why_it_fits,
                location=role.location, comp=role.comp)
            keep.append((r, result.score, why, flags))

    print(f"KEEP {len(keep)}, DROP {len(drop)}, UNRESOLVED {len(unresolved)}\n")
    for r, sc, why, flags in keep:
        print(f"  keep row {r.row_number:>3} score {sc:>2} {r.company[:16]:16} "
              f"{r.role[:34]:34} {'flags=' + str(flags) if flags else ''}")
        print(f"       J: {why[:150]}")
    for r, sc, code, reason in drop:
        print(f"  DROP row {r.row_number:>3} score {sc:>2} {r.company[:16]:16} "
              f"{r.role[:34]:34} [{code}] {reason[:60]}")
    for r, err in unresolved:
        print(f"  ?    row {r.row_number:>3} {r.company[:16]:16} {r.role[:34]:34} {err}")

    if not apply:
        print("\ndry run. add --apply to rewrite scores and rationales and "
              "archive the drops")
        return 0

    for r, sc, why, _ in keep:
        sheet.update_assessment(r.row_number, score=sc, why_it_fits=why)
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id(r.company, r.role)},
                 {"score": sc, "why_it_fits": why[:900]})
    if drop and not archive:
        print(f"\n--no-archive: {len(drop)} row(s) left on Pipeline for your call")
    if drop and archive:
        # write the reason into column A first so the archive carries WHY,
        # and so the learning loop sees a coded verdict like any other
        stamped, unresolved = [], []
        for r, _, code, reason in drop:
            d, why = resolve_db_row(r, list(known), paired)
            if d:
                stamped.append((r, d["job_id"], code, reason))
            else:
                unresolved.append(f"row {r.row_number} {r.company}: {why}")
        for line in unresolved:
            print(f"  NOT ARCHIVED, {line}")
        drop = [t for t in drop
                if t[0].row_number in {r.row_number for r, _, _, _ in stamped}]
        stamp_auto_verdicts(cfg, sheet, stamped)
        fresh = {r.row_number: r for r in sheet.read_pipeline(canon.sheet_headers)}
        movers = [fresh[r.row_number] for r, _, _, _ in drop if r.row_number in fresh]
        sheet.archive_rows(movers, archive_tab=config_mod.ARCHIVE_TAB,
                           archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                           headers=canon.sheet_headers)
    left = sheet.read_pipeline(canon.sheet_headers)
    print(f"\nrewrote {len(keep)} rows, archived {len(drop) if archive else 0}; "
          f"Pipeline now has {len(left)} rows")
    return 0


def cmd_restore(job_ids: list[str], apply: bool = False) -> int:
    """Put a row back on Pipeline that should never have left it.

    The 2026-09-02 re-gate archived three roles Krish had said go to, because
    it read column A (which said New) and not the DB (which said go). The
    guard is fixed; this undoes the damage, and column A comes back reading
    Yes because that is the verdict he gave.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    arch = sheet.read_archive()
    wanted = []
    for jid in job_ids:
        rows = db_get(cfg, "hunter_seen_roles",
                      {"select": "job_id,company,title,krish_verdict",
                       "job_id": f"eq.{jid}"})
        if not rows:
            print(f"no DB row for {jid}")
            return 1
        d = rows[0]
        toks = distinctive_tokens(d.get("company") or "", d.get("title") or "")
        hit = next((a for a in arch
                    if toks & distinctive_tokens(a.company, a.role)
                    and title_jaccard(a.role, d.get("title") or "") >= FUZZY_TITLE_MIN),
                   None)
        if not hit:
            print(f"{jid} is not on the {config_mod.ARCHIVE_TAB} tab")
            return 1
        wanted.append((jid, d, hit))

    for jid, d, hit in wanted:
        print(f"restore {jid}: {config_mod.ARCHIVE_TAB} row {hit.row_number} "
              f"{hit.company} / {hit.role} [{hit.verdict}] -> Pipeline as "
              f"{verdicts.BUILD} (your verdict: {d.get('krish_verdict')})")
    if not apply:
        print("\ndry run. add --apply to move them back")
        return 0

    cells = [list(hit.cells) for _, _, hit in wanted]
    for c in cells:
        c[0] = "New"          # append validation demands it; corrected below
    rng = sheet.append_rows(cells)
    start = int(rng.split("!A")[1].split(":")[0])
    sheet._post("/values:batchUpdate", {
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"Pipeline!A{start + i}", "values": [[verdicts.BUILD]]}
                 for i in range(len(cells))]})
    sheet.delete_archive_rows([hit.row_number for _, _, hit in wanted],
                              archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                              expect=[hit.company for _, _, hit in wanted])
    for i, (jid, _, _) in enumerate(wanted):
        db_patch(cfg, "hunter_seen_roles", {"job_id": jid},
                 {"status": "staging", "rejection_reason": None,
                  "rejection_code": None})
        print(f"  {jid} -> Pipeline row {start + i}")
    print(f"restored {len(wanted)} row(s)")
    return 0


def cmd_decline(pairs_in: list[tuple[int, str]], apply: bool = False) -> int:
    """Write hunter's coded verdict onto named Pipeline rows, so `archive`
    can move them.

    Used when Krish approves a re-gate's drop list without wanting the whole
    re-gate re-run. Refuses the entire batch if any named row is not exactly
    'New' or carries a standing verdict, because a row he has decided on is
    not hunter's to code.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    known = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,title,krish_verdict,url,job_url,company,"
                              "verdict_source", "limit": "5000"})
    all_rows = sheet.read_pipeline(canon.sheet_headers)
    by_number = {r.row_number: r for r in all_rows}
    matched, _, _, _ = match_rows(all_rows, list(known))
    paired = {srow.row_number: d for srow, d in matched}
    decided = [d for d in known
               if (d.get("krish_verdict") or "").strip() and not learn.is_auto(d)]

    problems, plan = [], []
    for rn, label in pairs_in:
        r = by_number.get(rn)
        if r is None:
            problems.append(f"row {rn} is not on Pipeline")
            continue
        if label not in verdicts.LABEL_TO_CODE:
            problems.append(f"row {rn}: {label!r} is not a dropdown reason; "
                            f"one of {sorted(verdicts.LABEL_TO_CODE)}")
            continue
        if (r.verdict or "").strip() != "New":
            problems.append(f"row {rn} reads {r.verdict!r}, not New; not touching it")
            continue
        d, why = resolve_db_row(r, list(known), paired)
        if not d:
            problems.append(f"row {rn} ({r.company} / {r.role}): {why}")
            continue
        standing = next((c for c in decided
                         if c["job_id"] == d.get("job_id")), None)
        if standing:
            problems.append(f"row {rn} carries your verdict "
                            f"{standing['krish_verdict']!r}; not touching it")
            continue
        plan.append((r, d["job_id"], label))

    for r, jid, label in plan:
        print(f"  row {r.row_number:>3}  {r.company[:22]:24} {r.role[:40]:42} "
              f"-> {verdicts.DECLINE_PREFIX}{label}")
    if problems:
        print("\nREFUSED, nothing written:")
        for pr in problems:
            print(f"  {pr}")
        return 1
    if not apply:
        print(f"\ndry run. add --apply to write these {len(plan)} verdicts, "
              f"then run: python -m hunter.run archive --apply")
        return 0

    n = stamp_auto_verdicts(cfg, sheet, [
        (r, jid, label, f"re-gate drop approved by Krish 2026-09-02: {label}")
        for r, jid, label in plan])
    print(f"\nwrote {n} coded verdicts; they are hunter's own "
          f"(verdict_source {learn.AUTO_SOURCE!r}), so the learning loop "
          f"will not read them back as your taste")
    return 0


# Hunter took over from the incumbent on this date. Anything built before it
# came off CV v11 and letter v1, both superseded by the masters in canon 9.9.
HUNTER_TOOK_OVER = "2026-08-31"


def cmd_disconnect(apply: bool = False) -> int:
    """Unlink packages built on the superseded templates.

    Krish asked for this and did not get it. Twelve packages were built
    2026-08-11 by the retired incumbent from CV v11 and letter v1, all of them
    on roles he said go to, and every one is marked package_status='built', so
    select_for_build skips them permanently. Even after he marks a row Yes it
    would never be rebuilt on the current format.

    Disconnect means unlink. The Drive documents stay where they are: they are
    not hunter's to bin.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,package_status,package_built_at,"
                  "package_cv_url,package_letter_url",
        "package_status": "neq.none", "limit": "1000"})
    # Only a package that actually exists and predates the handover. A row
    # sitting at 'blocked' with no build date is a different problem and is
    # not this command's to touch.
    stale = [r for r in rows
             if r.get("package_status") == "built"
             and str(r.get("package_built_at") or "")[:10] < HUNTER_TOOK_OVER]
    print(f"{len(stale)} package(s) built before {HUNTER_TOOK_OVER} on the "
          f"superseded templates:")
    for r in stale:
        print(f"  {str(r.get('package_built_at'))[:10]}  {r['company'][:22]:24} "
              f"{str(r.get('title'))[:38]}")

    live = sheet.read_pipeline(canon.sheet_headers)
    linked = [r for r in live
              if any((r.cells[i] or "").strip() not in ("", "Not built")
                     for i in range(4, 8))]
    print(f"\n{len(linked)} Pipeline row(s) still show package links:")
    for r in linked:
        print(f"  row {r.row_number}: {r.company} / {r.role}")

    if not apply:
        print("\ndry run. add --apply to unlink. The Drive files are not touched.")
        return 0

    for r in stale:
        db_patch(cfg, "hunter_seen_roles", {"job_id": r["job_id"]},
                 {"package_status": "none", "package_built_at": None,
                  "package_cv_url": None, "package_letter_url": None,
                  "package_folder_url": None, "package_outreach_url": None})
    if linked:
        sheet.clear_package_links([r.row_number for r in linked])
    print(f"\nunlinked {len(stale)} package(s) and cleared {len(linked)} sheet "
          f"row(s). They rebuild on the current format once you mark them Yes.")
    return 0


def cmd_verify(apply: bool = False) -> int:
    """Is every role on the sheet still live?

    Three answers, never two. A row whose board hunter cannot find is
    UNVERIFIABLE, which is not the same as live and must never be reported as
    such: that conflation is how a role Krish said go to sat on the sheet
    after the posting had gone.

    Where the board is discovered, column D is rewritten with the real ATS
    URL so the row is checkable from then on without discovery.
    """
    from .ats import discover as disc
    from .ats import ashby, greenhouse, lever, workday
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = sheet.read_pipeline(canon.sheet_headers)
    known = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,title,company,url,job_url,krish_verdict,"
                              "verdict_source", "limit": "5000"})
    pairs, _, _, _ = match_rows(rows, list(known))
    paired = {srow.row_number: d for srow, d in pairs}
    cache = disc.load_cache(cfg)
    boards = {"greenhouse": greenhouse.board, "ashby": ashby.board,
              "lever": lever.board}
    fetchers = {"greenhouse": greenhouse.fetch_posting,
                "lever": lever.fetch_posting, "ashby": ashby.fetch_posting,
                "workday": workday.fetch_posting}

    live, dead, unknown, relinked = [], [], [], {}
    for r in rows:
        title = full_title(paired, r)
        key = ats_key(r.jd_url)
        if key:
            ats, slug, pid = key
            try:
                is_live, _, _ = fetch_with_retry(fetchers[ats], slug, pid)
            except Exception as e:
                unknown.append((r, f"{ats} fetch failed: {e.__class__.__name__}"))
                continue
            (live if is_live else dead).append((r, f"{ats}/{slug}"))
            continue

        found = disc.discover(cfg, r.company, cache)
        if not found:
            unknown.append((r, "no job board found on greenhouse, ashby or lever"))
            continue
        ats, slug = found
        try:
            postings = boards[ats](slug)
        except Exception as e:
            unknown.append((r, f"{ats}/{slug} board read failed: {e.__class__.__name__}"))
            continue
        nt = _norm_title(title)
        hit = next((p for p in postings if _norm_title(p.title) == nt), None)
        if hit is None:
            close = [p for p in postings
                     if title_jaccard(title, p.title) >= FUZZY_TITLE_MIN]
            hit = close[0] if len(close) == 1 else None
        if hit is None:
            dead.append((r, f"not on the {ats}/{slug} board ({len(postings)} jobs)"))
            continue
        live.append((r, f"{ats}/{slug}, relinked"))
        if hit.url:
            relinked[r.row_number] = hit.url

    print(f"LIVE {len(live)}, DEAD {len(dead)}, UNVERIFIABLE {len(unknown)}\n")
    for r, why in live:
        print(f"  live  row {r.row_number:>3} {r.company[:22]:24} "
              f"{r.role[:36]:38} {why}")
    for r, why in dead:
        d = paired.get(r.row_number) or {}
        his = (d.get("krish_verdict") or "").strip()
        held = his and not learn.is_auto(d)
        print(f"  DEAD  row {r.row_number:>3} {r.company[:22]:24} "
              f"{r.role[:36]:38} {why}"
              + (f"  [your verdict {his!r}, held]" if held else ""))
    for r, why in unknown:
        print(f"  ?     row {r.row_number:>3} {r.company[:22]:24} "
              f"{r.role[:36]:38} {why}")

    if not apply:
        print("\ndry run. add --apply to relink the rows that were found and "
              "archive the dead ones you have not verdicted.")
        return 0

    if relinked:
        sheet.relink_jd_urls(relinked)
        print(f"\nrelinked {len(relinked)} row(s) to their real ATS posting")
    disc.save_cache(cfg, cache)

    movers = []
    for r, why in dead:
        d = paired.get(r.row_number) or {}
        if (d.get("krish_verdict") or "").strip() and not learn.is_auto(d):
            continue  # his call, not hunter's
        if d.get("job_id"):
            movers.append((r, d["job_id"], "dead posting", why))
    if movers:
        stamp_auto_verdicts(cfg, sheet, movers)
        fresh = {x.row_number: x for x in sheet.read_pipeline(canon.sheet_headers)}
        sheet.archive_rows([fresh[r.row_number] for r, _, _, _ in movers
                            if r.row_number in fresh],
                           archive_tab=config_mod.ARCHIVE_TAB,
                           archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                           headers=canon.sheet_headers)
    left = sheet.read_pipeline(canon.sheet_headers)
    print(f"archived {len(movers)} dead row(s); Pipeline now has {len(left)} rows")
    return 0


def cmd_archive(apply: bool = False) -> int:
    """Move decided rows off Pipeline onto the Applied tab.

    Krish's ruling 2026-09-02: he only ever sets Applied or Declined, so
    Pipeline should hold what still needs a decision and nothing else. Rows
    still marked New or Yes stay: Yes is work in flight, not a decision made.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = sheet.read_pipeline(canon.sheet_headers)
    movers = []
    for r in rows:
        kind, code = verdicts.parse(r.verdict)
        if kind in ("applied", "rejection"):
            movers.append((r, kind, code))
    print(f"{len(rows)} rows on Pipeline; {len(movers)} decided and ready to archive; "
          f"{len(rows) - len(movers)} stay")
    for r, kind, code in movers:
        print(f"  row {r.row_number:>3}  {r.company[:20]:20} {r.role[:38]:38} "
              f"{kind}{' [' + code + ']' if code else ''}")
    if not apply:
        print("\ndry run. add --apply to move these rows")
        return 0
    moved = sheet.archive_rows([r for r, _, _ in movers],
                               archive_tab=config_mod.ARCHIVE_TAB,
                               archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                               headers=canon.sheet_headers)
    left = sheet.read_pipeline(canon.sheet_headers)
    print(f"\narchived {moved} rows; Pipeline now has {len(left)} rows awaiting you")
    return 0


def cmd_set_dropdown() -> int:
    """Publish the column A vocabulary that carries verdict plus reason."""
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    values = verdicts.dropdown_values()
    rows = sheet.set_verdict_dropdown(values)
    print(f"column A dropdown set to {len(values)} values over {rows} rows:")
    for v in values:
        print(f"  {v}")
    return 0


def cmd_prune_orphans(apply: bool = False) -> int:
    """Delete Pipeline rows that no DB row explains.

    A staged row always has a hunter_seen_roles row behind it. One without is
    debris from a run that half-failed, and it cannot be assessed, verified or
    archived because there is nothing to assess: every other command here
    refuses to touch it, correctly. Only rows still reading exactly "New" are
    ever removed, so nothing Krish has written on is at risk.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    live = sheet.read_pipeline(canon.sheet_headers)
    known = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,title,company,url,job_url", "limit": "5000"})
    _, unmatched, _, _ = match_rows(live, list(known))
    orphans = [r for r in unmatched if (r.verdict or "").strip() == "New"]
    held = [r for r in unmatched if (r.verdict or "").strip() != "New"]
    print(f"{len(orphans)} orphan row(s) to delete:")
    for r in orphans:
        print(f"  row {r.row_number:>3} {r.company[:24]:26} {r.role[:44]}")
    for r in held:
        print(f"  HELD row {r.row_number}: {r.company} reads {r.verdict!r}, not New")
    if not apply:
        print("\ndry run. add --apply to delete them.")
        return 0
    if orphans:
        sheet.delete_rows([r.row_number for r in orphans], expect_verdict="New")
    left = sheet.read_pipeline(canon.sheet_headers)
    print(f"\ndeleted {len(orphans)}; Pipeline now has {len(left)} rows")
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


def learning_step(cfg: Config, *, apply: bool) -> dict:
    """Krish's verdicts, read back for the one thing they can honestly say:
    where hunter got it wrong.

    This used to also cluster his rejections into candidate suppression rules.
    It no longer does. The archetype gate decides what he is shown, so a
    rejection is either a bug of hunter's (a dead posting it should have
    caught, a duplicate it should have collapsed) or a question about the
    archetype definition, which is his to answer and not hunter's to infer.
    """
    roles = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,krish_verdict,verdict_source,verdict_at,"
                  "rejection_code,status,last_verified_at,presented_at,url,"
                  "job_url,location,why_it_fits",
        "limit": "5000"})
    # His verdicts only. A row carrying hunter's own coded verdict is its
    # output, not his judgement.
    verdicted = [r for r in roles
                 if (r.get("krish_verdict") or "").strip() and not learn.is_auto(r)]
    recorded = learn.record(cfg, verdicted) if apply else len(verdicted)

    if apply:
        events = learn.load_events(cfg)
    else:
        events = []
        for r in verdicted:
            kind, code, _ = learn.classify(r["krish_verdict"])
            events.append({"job_id": r["job_id"], "company": r.get("company"),
                           "title": r.get("title"), "verdict": kind,
                           "reason_code": code, "reason_text": r["krish_verdict"]})

    findings = learn.system_findings(events, roles)
    fixed = []
    for f in findings:
        twin = f.get("twin")
        if not twin:
            continue
        if apply:
            db_patch(cfg, "hunter_seen_roles", {"job_id": f["job_id"]},
                     {"status": "duplicate", "rejection_code": f["code"],
                      "rejection_reason": f"duplicate of {twin} ({f['quote']})"})
        fixed.append(f["job_id"])

    # A rejection no gate caught is a question about the archetypes, not a
    # rule. Named for him, never acted on.
    unexplained = [e for e in events
                   if e.get("verdict") == "rejection"
                   and not verdicts.is_system_code(e.get("reason_code"))
                   and archetype(e.get("title") or "")]
    return {"roles": roles, "verdicted": verdicted, "recorded": recorded,
            "events": events, "findings": findings, "fixed": fixed,
            "unexplained": unexplained,
            "opens": learn.open_applications(roles)}


def learning_lines(out: dict) -> list[str]:
    lines = [f"learning: {out['recorded']} verdict events, "
             f"{len(out['findings'])} system miss(es), "
             f"{len(out['fixed'])} row(s) marked duplicate"]
    for e in out["unexplained"]:
        lines.append(f"  you declined a role that matches your archetypes: "
                     f"{e.get('company')} / {e.get('title')} ({e.get('reason_text')})")
    return lines


def cmd_learn(apply: bool = False) -> int:
    """Read Krish's verdicts back for what hunter got wrong.

    System codes are hunter's bugs and are fixed here. Nothing infers a
    standing rule about his taste: the archetype gate does that job, and a
    rejection it did not predict is reported for him to rule on.
    """
    cfg = load()
    out = learning_step(cfg, apply=apply)
    print(f"{len(out['verdicted'])} verdicts on record; {out['recorded']} events "
          f"{'written' if apply else 'would be written'}")

    print("\nSYSTEM codes (hunter's misses, fixed without asking):")
    if not out["findings"]:
        print("  none")
    for f in out["findings"]:
        print(f"  {f['job_id']}\n    {f['code']} [{f['gate']}]: {f['evidence']}"
              f"\n    fix: {f['fix']}\n    his words: {f['quote']!r}")
    if out["findings"]:
        print(f"  {len(out['fixed'])} row(s) "
              f"{'marked' if apply else 'would be marked'} duplicate")

    if out["opens"]:
        print(f"\nopen applications at {len(out['opens'])} companies; a new role "
              f"at one of them is staged with a note, never suppressed")

    print("\nFOR YOU (a rejection the archetype gate did not predict):")
    if not out["unexplained"]:
        print("  none. Every role you declined was one the gate now blocks.")
    for e in out["unexplained"]:
        print(f"  {e.get('company')} / {e.get('title')}\n"
              f"    matches {archetype(e.get('title') or '')}, "
              f"you said: {e.get('reason_text')!r}")
    if not apply:
        print("\ndry run. add --apply to write events and mark duplicates.")
    return 0


COMMANDS_TABLE = "hunter_commands"


def cmd_drain() -> int:
    """Run the oldest command Control Center queued, if any.

    Fires hourly from a Routine and exits in seconds when the queue is empty,
    which is most of the time. Krish's two buttons write here: `source` does a
    full sourcing pass and stops before packages, `packages` builds for rows
    reading Yes in column A that have no package yet.
    """
    cfg = load()
    queued = db_get(cfg, COMMANDS_TABLE,
                    {"select": "id,command,requested_at", "state": "eq.queued",
                     "order": "requested_at.asc", "limit": "1"})
    if not queued:
        print("nothing queued")
        return 0
    job = queued[0]
    cid, command = str(job["id"]), job["command"]
    db_patch(cfg, COMMANDS_TABLE, {"id": cid},
             {"state": "running", "started_at": NOW()})
    print(f"running {command} (command {cid})")
    try:
        summary = run_command(cfg, command)
        db_patch(cfg, COMMANDS_TABLE, {"id": cid},
                 {"state": "done", "finished_at": NOW(),
                  "result": summary[:2000]})
        print(summary)
        return 0
    except Exception as e:
        # A command that dies must not sit at 'running' forever, or the
        # button reports work in flight that stopped hours ago.
        db_patch(cfg, COMMANDS_TABLE, {"id": cid},
                 {"state": "failed", "finished_at": NOW(),
                  "error": f"{e.__class__.__name__}: {e}"[:1000]})
        print(f"FAILED {command}: {e.__class__.__name__}: {e}")
        return 1


def run_command(cfg: Config, command: str) -> str:
    """The work behind each button. Returns the line Control Center shows."""
    canon = load_canon(cfg)
    assert_canon_alignment(canon)
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    summary: list[str] = []

    if command == "source":
        ledger = reconcile(cfg, canon, sheet)
        summary.extend(ledger.lines())
        try:
            summary.extend(learning_lines(learning_step(cfg, apply=True)))
        except Exception as e:
            summary.append(f"learning skipped: {e.__class__.__name__}")
        counts = source_and_stage(cfg, canon, sheet, summary)
        line = (f"{counts['discovered']} found, {counts['recorded']} recorded, "
                f"{counts['staged']} staged, ${counts['spend_usd']:.2f} spent")
    elif command == "packages":
        rows = select_for_build(cfg, sheet, canon.sheet_headers)
        built = 0
        for row in rows:
            try:
                if build_one(cfg, canon, sheet, row, summary):
                    built += 1
            except Exception as e:
                summary.append(f"BUILD FAILED {row['job_id']}: "
                               f"{e.__class__.__name__}: {e}")
        line = f"{built} of {len(rows)} package(s) built"
    else:
        raise ValueError(f"unknown command {command!r}")

    summary.append(line)
    try:
        send_summary(cfg, "\n".join(summary))
    except Exception:
        pass
    return line


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
        from .ats import workday
        fetch = {"greenhouse": greenhouse.fetch_posting,
                 "lever": lever.fetch_posting,
                 "ashby": ashby.fetch_posting,
                 "workday": workday.fetch_posting}[ats]
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
        "select": "job_id,url,job_url,company,title,status", "limit": "5000"})
    for r in rows:
        # A row hunter could not resolve was never actually assessed. Counting
        # it as seen means it can never be reconsidered once resolution
        # improves, and 1451 LinkedIn postings were sitting in exactly that
        # state when board discovery arrived.
        if r.get("status") == "unresolved":
            continue
        keys.add(r["job_id"])
        keys.update(identity_keys(r.get("company") or "", r.get("title") or ""))
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
        from .sources import company_key
        groups.setdefault(
            (company_key(r.get("company") or "", r.get("title") or ""),
             _norm_title(r.get("title") or "")),
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


def open_application_note(opens: dict, company: str) -> str:
    """A second role at a company where an application is already open is
    often exactly what Krish wants, so it is never suppressed. It is a line
    in the rationale so he is not surprised by it, which is what "already
    applied above" cost him three times."""
    for tok in distinctive_tokens(company):
        hit = opens.get(tok)
        if hit:
            return (f"NOTE: you already have an application open at "
                    f"{hit['company']} ({hit['title']}).")
    return ""


def source_and_stage(cfg: Config, canon: Canon, sheet: Sheet,
                     summary: list[str]) -> dict:
    from .ats import ashby, greenhouse, lever
    from .gates import SENIOR_TITLE
    from .package.rationale import write_rationale
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
        # One connection reset used to kill the whole paid sourcing leg: the
        # ATS fetches had fetch_with_retry and this did not, so a transient
        # network blip cost the entire LinkedIn sweep for the run.
        cap = float(cfg.optional("hunter_apify_max_usd_per_call", "2.00"))
        for attempt in (1, 2):
            try:
                postings.extend(sweep_linkedin(cfg, urls, spend=spend,
                                               max_charge_usd=cap))
                summary.append(f"apify linkedin: {len(urls)} search urls swept")
                break
            except Exception as e:
                transient = isinstance(e, (requests.ConnectionError, requests.Timeout))
                if transient and attempt == 1:
                    time.sleep(5)
                    continue
                summary.append(
                    f"apify linkedin sweep failed: {e.__class__.__name__}: {e}")
                break
    else:
        summary.append("apify linkedin sweep skipped: no search URLs in the "
                       "Role Targeting tab or hunter_linkedin_search_urls")
    counts["spend_usd"] = round(spend.spent, 2)
    staged = stage_postings(cfg, canon, sheet, postings, summary)
    staged["spend_usd"] = counts["spend_usd"]
    return staged


def stage_postings(cfg: Config, canon: Canon, sheet: Sheet,
                   postings: list, summary: list[str]) -> dict:
    """Dedupe, resolve, gate, score, write a rationale, stage.

    Split out of source_and_stage so postings that arrive some other way go
    through exactly this path. On 2026-09-02 a paid Apify run's 2924 results
    had to be recovered after a timeout, and recovering them down a parallel
    code path would have meant roles reaching the sheet without the gates.
    """
    from .ats import ashby, greenhouse, lever
    from .ats import discover as disc
    from .gates import SENIOR_TITLE
    from .package.rationale import write_rationale

    counts = {"discovered": 0, "senior": 0, "fresh": 0, "resolved": 0,
              "recorded": 0, "staged": 0, "unresolved": 0, "spend_usd": 0.0,
              "boards_found": 0}
    counts["discovered"] = len(postings)
    cache = disc.load_cache(cfg)
    boards = {"greenhouse": greenhouse.board, "ashby": ashby.board,
              "lever": lever.board}
    probe_budget = int(cfg.optional("hunter_max_board_discoveries_per_run", "60"))
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
        keys = [job_id(p.company, p.title)] + identity_keys(p.company, p.title)
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
    opens = learn.open_applications(db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,krish_verdict,verdict_at", "limit": "5000"}))
    inserts, staged_rows = [], []
    fetchers = {"greenhouse": greenhouse.fetch_posting,
                "ashby": ashby.fetch_posting, "lever": lever.fetch_posting}
    for p in fresh:
        # A LinkedIn posting carries no ATS link, so hunter could never read
        # its JD and recorded it unresolved: 1451 of them on 2026-09-02, the
        # entire paid sweep, none of which reached the sheet. Find the
        # company's own board and the posting on it. Only for roles that
        # match an archetype, and only within a probe budget, because
        # probing every company found on LinkedIn is neither cheap nor useful.
        if not p.ats and probe_budget > 0 and archetype(p.title):
            found = disc.discover(cfg, p.company, cache)
            if found is None:
                probe_budget -= 1
            else:
                ats, slug = found
                # Exact title only. A fuzzy match across two sources pairs
                # a LinkedIn posting with a different job on the same board:
                # on 2026-09-02 "Managing Director, Enterprise Accounts,
                # Financial Services AI" was linked to "Managing Director
                # Strategic Banking Accounts", so the sheet showed one title
                # and the rationale was written from the other posting's JD.
                # A missed match costs one role; a wrong one costs trust.
                try:
                    hit = next((b for b in boards[ats](slug)
                                if _norm_title(b.title) == _norm_title(p.title)),
                               None)
                except Exception:
                    hit = None
                if hit is not None:
                    counts["boards_found"] += 1
                    p.ats, p.ats_slug = hit.ats, hit.ats_slug
                    p.ats_posting_id, p.url = hit.ats_posting_id, hit.url or p.url

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
        result = score_role(role, universe=canon.universe)
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
            # The same rationale generator the re-gate uses, so a row staged
            # today reads exactly like a row re-judged last week. Krish asked
            # for one standard; this is where it is applied.
            why, rflags = write_rationale(
                cfg, canon, company=role.company, title=role.title,
                jd=role.jd_text, score=result.score,
                score_reason=result.why_it_fits,
                location=role.location, comp=role.comp)
            note = open_application_note(opens, role.company)
            if note:
                why = f"{why} {note}"[:900]
                rflags = rflags + ["open application at this company"]
            row["why_it_fits"] = why
            if rflags:
                summary.append(f"rationale flags {role.job_id}: {', '.join(rflags)}")
            staged_rows.append((role, result, jd[:300], why))

    if inserts:
        db_insert(cfg, "hunter_seen_roles", inserts, on_conflict="job_id",
                  ignore_duplicates=True)
    counts["recorded"] = len(inserts)
    disc.save_cache(cfg, cache)
    if counts["boards_found"]:
        summary.append(f"board discovery resolved {counts['boards_found']} "
                       f"LinkedIn posting(s) to their real ATS")

    if staged_rows:
        new_rows = [make_row(company=role.company, role=role.title,
                             jd_url=role.jd_url, score=result.score,
                             why_it_fits=why,
                             location=role.location, comp=role.comp,
                             source=role.source, jd_snippet=snippet)
                    for role, result, snippet, why in staged_rows]
        sheet.append_rows(new_rows)
        for role, _, _, _ in staged_rows:
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

        # Learn before sourcing: a rule Krish approved since the last run
        # takes effect on this one, and his verdicts are on record before
        # anything overwrites the rows they came from.
        try:
            summary.extend(learning_lines(learning_step(cfg, apply=True)))
        except Exception as e:
            summary.append(f"learning loop skipped: {e.__class__.__name__}: {e}")

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
        for row in select_for_build(cfg, sheet, canon.sheet_headers):
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
    if cmd == "regate":
        frm = int(argv[argv.index("--from") + 1]) if "--from" in argv else 41
        lim = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
        return cmd_regate(from_row=frm, apply="--apply" in argv, limit=lim,
                          archive="--no-archive" not in argv)
    if cmd == "drain":
        return cmd_drain()
    if cmd == "learn":
        return cmd_learn(apply="--apply" in argv)
    if cmd == "restore":
        ids = [a for a in argv[1:] if not a.startswith("--")]
        if not ids:
            print("usage: python -m hunter.run restore <job_id> [...] [--apply]")
            return 2
        return cmd_restore(ids, apply="--apply" in argv)
    if cmd == "decline":
        pairs_in = []
        for a in argv[1:]:
            if a.startswith("--"):
                continue
            if "=" not in a:
                print(f"bad argument {a!r}; use <row>=<reason label>")
                return 2
            rn, label = a.split("=", 1)
            pairs_in.append((int(rn), label.strip()))
        if not pairs_in:
            print("usage: python -m hunter.run decline <row>=<reason> ... [--apply]")
            return 2
        return cmd_decline(pairs_in, apply="--apply" in argv)
    if cmd == "verify":
        return cmd_verify(apply="--apply" in argv)
    if cmd == "disconnect":
        return cmd_disconnect(apply="--apply" in argv)
    if cmd == "archive":
        return cmd_archive(apply="--apply" in argv)
    if cmd == "set-dropdown":
        return cmd_set_dropdown()
    if cmd == "prune-orphans":
        return cmd_prune_orphans(apply="--apply" in argv)
    if cmd == "prune-sheet":
        return cmd_prune_sheet(apply="--apply" in argv,
                               include_ungated="--incumbent" in argv)
    if cmd == "bridges":
        ingest_dir = None
        if len(argv) >= 3 and argv[1] == "--ingest":
            ingest_dir = argv[2]
        return cmd_bridges(ingest_dir)
    print(f"unknown command {cmd!r}; commands: run, reconcile, migrate-sheet, "
          f"build --job-id X, recon, dedupe-db, learn [--apply], drain, verify, "
          "bridges [--ingest DIR], prune-sheet [--apply], regate, archive")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
