"""Orchestrator. python -m hunter.run {process,run,reconcile,migrate-columns,build,recon,drain}

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
import json
import re
from functools import lru_cache
import requests
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config as config_mod
from . import companyintel as companyintel_mod
from .canon import Canon, CanonError, load_canon
from .config import (ALL_ROWS, Config, GoogleOAuth, GoogleServiceAccount,
                     db_get, db_insert, db_patch, load)
from .docbuild import DocBuild
from .archetype import archetype
from .gates import FLOOR, names_foreign_geo, run_gates
from . import alerts, amend, comp as comp_mod, employer, invariants, layout, learn, preflight
from .report import report_run
from . import verdicts
from .router import classify_verdict, is_warm_path, route_status, select_for_build
from .score import BAR, score_role
from . import sheet as sheet_mod
from .sheet import Sheet, SheetError, SheetRow, make_row
from .sources import (ResolvedRole, company_key, distinctive_tokens, identity_keys,
                      job_id, slugify)
from .notify import send_summary

class Summary(list):
    """The run's summary, which also says each line as it happens.

    Every phase appends to a plain list and nothing was printed until the run
    ended. A sourcing run that sweeps 265 boards takes the better part of an
    hour, so for that hour there was no way to tell a working run from a hung
    one, and when one died at the last step its whole summary died with it:
    only the traceback survived, because stderr is not buffered and stdout is.

    Printing on append costs nothing and makes the Actions log a live view of
    the run, which is the only view either of us has of a scheduled one.
    """

    def append(self, line):
        print(line, flush=True)
        super().append(line)

    def extend(self, lines):
        for line in lines:
            self.append(line)


TODAY = lambda: datetime.date.today().isoformat()
NOW = lambda: datetime.datetime.utcnow().isoformat() + "Z"

# The one word hunter_seen_roles.application_state carries for an applied role,
# and the word Control Center prints straight into the Hunt lane.
#
# Two writers of this column disagreed. record_applied wrote "submitted" while
# sync_applied_state writes the sheet's own Verdict vocabulary, "Applied", and
# cmd_close_submitted treated only "submitted" as done. So the next full pass
# relabelled the row, close-submitted stopped recognising it, and record_applied
# ran again: a duplicate Submitted receipt for every applied role, every hour.
APPLIED_STATE = "Applied"
# Both spellings count as written, so rows already carrying the old one are not
# re-processed.
APPLIED_STATES = (APPLIED_STATE, "submitted")


def assert_canon_alignment(canon: Canon) -> None:
    """Canon supersedes code. If canon moved, stop and say which side to fix."""
    if list(canon.sheet_headers) != list(sheet_mod.HEADERS):
        diffs = [(sheet_mod.col_letter(i), a, b) for i, (a, b) in
                 enumerate(zip(canon.sheet_headers, sheet_mod.HEADERS)) if a != b]
        raise CanonError(f"canon 9.13 headers disagree with sheet.HEADERS at "
                         f"{diffs[:3]}; one side moved, update the losing side")
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


# Memoised because match_rows asks the same question thousands of times.
# Pairing 79 sheet rows against 6,584 database rows calls this once per
# combination: half a million URL parses of the same few thousand strings,
# and a stack dump of a live run showed the process sitting inside
# urllib.parse.unquote while sourcing had not started. Both functions are
# pure functions of a string, so the answer cannot go stale within a run.
@lru_cache(maxsize=100_000)
def norm_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if not STRIP_PARAMS.match(k.lower())])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), query, ""))


@lru_cache(maxsize=100_000)
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
    company_blocked: list[str] = field(default_factory=list)

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
                             ("G12", self.company_blocked),
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


def _squash(company: str) -> str:
    """Letters and digits only. "The Trade Desk", "thetradedesk" and
    "the-trade-desk" are one company; "Higgsfield AI" and "higgsfieldai" too.
    The incumbent minted both spellings, and reconcile re-appended the twin
    to the sheet every run."""
    return re.sub(r"[^a-z0-9]", "", (company or "").lower())


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
        sq = _squash(srow.company)
        return [d for d in remaining
                if (d.get("job_id", "").split(":")[0] == cslug
                    or _squash(d.get("company") or "") == sq)
                and _norm_title(d.get("title", "")) == _norm_title(srow.role)]

    def fuzzy_candidates(srow):
        cslug = slugify(srow.company)
        sq = _squash(srow.company)
        return [d for d in remaining
                if (d.get("job_id", "").split(":")[0] == cslug
                    or _squash(d.get("company") or "") == sq)
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


def reconcile(cfg: Config, canon: Canon, sheet: Sheet,
              company_declines: dict | None = None) -> ReconcileLedger:
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
        # sweep_date is what tells a hunter-judged row from an incumbent one
        # below; it was missing from this select, so no hunter row was ever
        # appended by reconcile (Clay Head of GTM Strategy, 2026-09-03).
        "select": "job_id,company,title,url,job_url,score,status,krish_verdict,"
                  "rejection_reason,package_status,package_cv_url,package_letter_url,"
                  "presented_at,source,location,comp,why_it_fits,sweep_date",
        "status": "neq.duplicate",
        "limit": ALL_ROWS})
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
        cfg, "hunter_seen_roles", {"select": "job_id", "limit": ALL_ROWS})}
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
            "source": (srow.cell("Source") or "sheet reconcile"),
            "status": "dropped" if verdict_kind == "rejection" else "staging",
            "why_it_fits": srow.cell("Why It Fits") if srow.cell("Why It Fits") != "Not assessed" else "",
            "location": srow.cell("Location") if srow.cell("Location") != "Not stated" else "",
            "comp": srow.cell("Comp") if srow.cell("Comp") != "Not disclosed" else "",
            "sweep_date": TODAY(), "presented_at": NOW(),
        }
        try:
            row["score"] = int(srow.cell("Score"))
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
    # A DB row that is the same posting as a sheet row under a second
    # spelling of the company is not missing from the sheet.
    sheet_idents = {(_squash(s.company), _norm_title(s.role)) for s in sheet_rows}
    to_append = []
    deleted_by_krish: list[dict] = []
    for d in db_only:
        if (_squash(d.get("company") or ""), _norm_title(d.get("title") or "")) in sheet_idents:
            ledger.skipped.append(
                f"{d['job_id']}: same posting as a sheet row under another spelling "
                f"of the company; not appended")
            continue
        url = d.get("url") or d.get("job_url")
        decided = bool(d.get("krish_verdict")) or (d.get("package_status") or "none") != "none"
        # hunter always writes sweep_date and why_it_fits; the incumbent never did
        hunter_judged = bool(d.get("sweep_date")) and bool(d.get("why_it_fits"))

        # He deleted the row. presented_at means hunter put it on the sheet;
        # the row is not on Pipeline and not in the archive, so it left by his
        # hand. Re-appending it is the system telling him his delete key does
        # not work, and it would do so every run forever. Read the deletion as
        # what it plainly is, record it as his, and stop. `restore <job_id>`
        # reverses it.
        if d.get("presented_at") and not decided:
            deleted_by_krish.append(d)
            ledger.skipped.append(
                f"{d['job_id']}: you removed this row from the sheet, so it is "
                f"recorded declined rather than put back")
            continue

        if not decided:
            hit = learn.declined_company(company_declines, d.get("company") or "")
            if hit:
                ledger.company_blocked.append(
                    f"{d['job_id']}: company declined by Krish on {hit['date']} "
                    f"({hit['code']}); not put on the sheet")
                continue
            if not hunter_judged:
                ledger.skipped.append(
                    f"{d['job_id']}: scored by the retired incumbent, never gated "
                    f"by hunter; not put on the sheet")
                continue
            if d.get("status") not in ("staging", "presented"):
                # blocked failed a gate, dropped was rejected, unresolved has
                # no JD. None of them is a shortlist row. This check was
                # missing and, once sweep_date reached the select, 38 gate
                # failures landed on the sheet in one reconcile (2026-09-03).
                ledger.skipped.append(
                    f"{d['job_id']}: status {d.get('status')}, not a staged "
                    f"role; not put on the sheet")
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

    for d in deleted_by_krish:
        db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]}, {
            "status": "dropped", "package_status": "blocked",
            "krish_verdict": f"{verdicts.DECLINE_PREFIX}removed from the sheet",
            "rejection_reason": "removed from the Pipeline tab by Krish",
            "verdict_source": "sheet deletion", "verdict_at": NOW()})

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
    rows = db_get(cfg, "hunter_seen_roles", {"select": "status", "limit": ALL_ROWS})
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
                    "limit": ALL_ROWS})
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

    declines, _notes = stored_company_declines(cfg)
    try:
        employer_index = employer.build_index(cfg, declines=declines)
        print(f"employer index: {len(employer_index.portfolio)} portfolio "
              f"entries, {len(employer_index.approved)} companies you have "
              f"approved, {len(employer_index.declined)} you have declined\n")
    except Exception as e:
        employer_index = None
        print(f"employer index unavailable ({e.__class__.__name__}); "
              f"re-gating without the company dimension\n")

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
                "location": r.cell("Location") if r.cell("Location") != "Not stated" else "",
                "comp": r.cell("Comp") if r.cell("Comp") != "Not disclosed" else ""})
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
        # The restored bar: the employer on record, and the band read out of
        # the posting body when the source left the field empty.
        role.comp = comp_mod.best(role.comp, role.jd_text)
        result = score_role(role, universe=canon.universe,
                            employer_index=employer_index)
        report = run_gates(role, never_apply=never,
                           company_declines=declines,
                           employer_index=employer_index, merit=result.merit)
        if decided_row:
            # His verdict outranks the rubric. The row keeps the score it has
            # and only gains the rationale it was missing.
            existing = int(r.cell("Score")) if str(r.cell("Score")).strip().isdigit() else result.score
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
        else:
            # Krish's ruling 2026-09-03: the shape gate filters, the score
            # orders. A role that passes every gate stays whatever it scores.
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


CLEAR_LABEL = f"{verdicts.DECLINE_PREFIX}sourced before the bar was fixed"


def recover_verdicts(cfg: Config, sheet: Sheet, canon: Canon,
                     summary: list[str] | None = None) -> int:
    """Record his declines from the sheet itself, live rows and archive both.

    A verdict only became a learning event if reconcile had managed to pair
    the sheet row with its database row. Roughly half never did, so half his
    taste was thrown away, and every company-level rule built on top of it
    was built on half the evidence. The sheet is the record; this reads it.

    Idempotent: hunter_verdict_events is unique on
    (job_id, verdict, reason_code, reason_text).
    """
    rows = list(sheet.read_pipeline(canon.sheet_headers))
    try:
        rows += list(sheet.read_archive())
    except Exception as e:
        (summary or []).append(f"archive unreadable during verdict recovery: "
                               f"{e.__class__.__name__}")
    events = learn.from_sheet_rows(rows, source="sheet column A, recovered")
    if not events:
        return 0
    before = len(learn.load_events(cfg))
    for i in range(0, len(events), 400):
        db_insert(cfg, "hunter_verdict_events", events[i:i + 400],
                  on_conflict="job_id,verdict,reason_code,reason_text",
                  ignore_duplicates=True)
    after = len(learn.load_events(cfg))
    if summary is not None:
        summary.append(f"verdict recovery: {len(events)} decline(s) read off the "
                       f"sheet, events {before} -> {after}")
    return after - before


def cmd_recover_verdicts(apply: bool = False) -> int:
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = list(sheet.read_pipeline(canon.sheet_headers)) + list(sheet.read_archive())
    events = learn.from_sheet_rows(rows, source="sheet column A, recovered")
    have = {(e.get("job_id"), e.get("reason_text")) for e in learn.load_events(cfg)}
    new = [e for e in events if (e["job_id"], e["reason_text"]) not in have]
    print(f"{len(events)} decline(s) of yours are on the sheet or in the archive")
    print(f"{len(new)} of them have no learning event on record\n")
    from collections import Counter
    for code, n in Counter(e["reason_code"] for e in new).most_common():
        print(f"  {n:>3}  {code}")
    company = [e for e in new if e["reason_code"] in learn.COMPANY_CODES]
    print(f"\n  {len(company)} are company-level declines that should drive G12:")
    for e in company[:25]:
        print(f"    {e['company'][:22]:24} {e['title'][:44]:46} {e['reason_code']}")
    if not apply:
        print("\ndry run. add --apply to record them.")
        return 0
    summary: Summary = Summary()
    gained = recover_verdicts(cfg, sheet, canon, summary)
    print("\n" + "\n".join(summary))
    declines = learn.company_declines(learn.load_events(cfg),
                                      learn.load_company_allow(cfg))
    names = sorted({d["company"] for d in declines.values()})
    print(f"\ncompanies G12 now blocks ({len(names)}):\n  " + ", ".join(names))
    return 0


def cmd_clear_unverdicted(apply: bool = False) -> int:
    """Take every row he has not ruled on off Pipeline, keeping every Yes.

    Krish 2026-09-20, after the sourcing defects were found and fixed:
    "clear all but the yes's and let Sunday refill from the fixed source".

    The reasoning behind allowing this at all. 65 of his 88 unverdicted rows
    were LinkedIn links with no readable job description, so the restored bar
    could not score them: no posting, no gates, no score. They are the output
    of the sourcing he was right to complain about, and no gate can reach
    them. Clearing is his call and he made it.

    Three properties this has to have.

    Nothing is deleted. Every row moves to the Applied tab, so the history is
    intact and `restore <job_id>` puts any of them back.

    Nothing is learned from it. Every DB row is stamped with
    learn.AUTO_SOURCE, which the learning loop excludes by construction, and
    the label carries no reason code. Clearing 59 rows must not teach hunter
    that he dislikes 59 companies. A row hunter cannot stamp is left on the
    sheet rather than archived, because an archived row with a rejection in
    column A and no DB stamp is read as HIS rejection by the next reconcile.

    Nothing he approved is touched. Counted before and after.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = sheet.read_pipeline(canon.sheet_headers)
    approved_before = [r for r in rows
                       if classify_verdict(r.verdict or "") == "go"]
    targets = [r for r in rows if classify_verdict(r.verdict or "") == "none"]
    print(f"Pipeline has {len(rows)} rows: {len(approved_before)} you approved, "
          f"{len(targets)} you have not ruled on\n")
    if not targets:
        print("nothing to clear")
        return 0

    paired = _pair_sheet_to_db(cfg, rows)
    known_ids = {r["job_id"] for r in db_get(
        cfg, "hunter_seen_roles", {"select": "job_id", "limit": ALL_ROWS})}

    stampable, mintable, stranded = [], [], []
    for r in targets:
        d = paired.get(r.row_number)
        if d and d.get("job_id"):
            stampable.append((r, d["job_id"]))
            continue
        jid = job_id(r.company, r.role)
        # A minted id that already exists belongs to some other row, and
        # stamping it would file this clearing against a different role.
        (stranded if jid in known_ids else mintable).append((r, jid))

    print(f"  {len(stampable)} row(s) have a database row to stamp")
    print(f"  {len(mintable)} row(s) need one minted")
    if stranded:
        print(f"  {len(stranded)} row(s) cannot be stamped safely and STAY on "
              f"Pipeline:")
        for r, _ in stranded:
            print(f"      row {r.row_number} {r.company} / {r.role}")
    if not apply:
        print(f"\ndry run. add --apply to archive "
              f"{len(stampable) + len(mintable)} row(s) to "
              f"{config_mod.ARCHIVE_TAB}. Nothing is deleted and restore "
              f"reverses any of it.")
        return 0

    import json as json_mod
    snapshot = [{"row": r.row_number, "job_id": jid, "company": r.company,
                 "role": r.role, "url": r.jd_url, "cells": r.cells}
                for r, jid in stampable + mintable]
    print(f"\nsnapshot of {len(snapshot)} row(s) taken before any write")

    if mintable:
        db_insert(cfg, "hunter_seen_roles", [
            {"job_id": jid, "company": r.company, "title": r.role,
             "url": r.jd_url or "", "job_url": r.jd_url or "",
             "source": r.cell("Source") or "sheet", "status": "dropped",
             "sweep_date": TODAY(), "presented_at": NOW(),
             "krish_verdict": CLEAR_LABEL, "verdict_at": NOW(),
             "verdict_source": learn.AUTO_SOURCE,
             "rejection_reason": "cleared on his instruction 2026-09-20; "
                                 "sourced before the quality bar was restored"}
            for r, jid in mintable], on_conflict="job_id", merge=True)
        print(f"minted {len(mintable)} database row(s), all marked "
              f"{learn.AUTO_SOURCE!r} so the learning loop ignores them")

    for _r, jid in stampable:
        db_patch(cfg, "hunter_seen_roles", {"job_id": jid},
                 {"krish_verdict": CLEAR_LABEL, "verdict_at": NOW(),
                  "verdict_source": learn.AUTO_SOURCE, "status": "dropped",
                  "rejection_reason": "cleared on his instruction 2026-09-20; "
                                      "sourced before the quality bar was "
                                      "restored"})
    print(f"stamped {len(stampable)} existing database row(s)")

    moving = {r.row_number for r, _ in stampable + mintable}
    sheet.set_verdicts({n: CLEAR_LABEL for n in moving})
    fresh = {r.row_number: r for r in sheet.read_pipeline(canon.sheet_headers)}
    sheet.archive_rows([fresh[n] for n in sorted(moving) if n in fresh],
                       archive_tab=config_mod.ARCHIVE_TAB,
                       archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                       headers=canon.sheet_headers)

    after = sheet.read_pipeline(canon.sheet_headers)
    approved_after = [r for r in after
                      if classify_verdict(r.verdict or "") == "go"]
    print(f"\narchived {len(moving)} row(s) to {config_mod.ARCHIVE_TAB}")
    print(f"Pipeline now has {len(after)} rows")
    print(f"approved rows: {len(approved_before)} before, "
          f"{len(approved_after)} after")
    if len(approved_after) != len(approved_before):
        raise SheetError(
            f"REFUSING TO REPORT SUCCESS: {len(approved_before)} approved rows "
            f"went in and {len(approved_after)} came out. Investigate before "
            f"anything else touches this sheet.")
    print(json_mod.dumps({"cleared": len(moving),
                          "approved_kept": len(approved_after)}))
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
                              "verdict_source", "limit": ALL_ROWS})
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
              if any((r.cell(n) or "").strip() not in ("", "Not built")
                     for n in ("CV Doc", "Cover Letter Doc", "CV PDF", "CL PDF"))]
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
                              "verdict_source", "limit": ALL_ROWS})
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

        state, hit, why = discover_posting(cfg, r.company, title, cache)
        if state == "unknown":
            unknown.append((r, why))
            continue
        if state == "absent":
            dead.append((r, why))
            continue
        live.append((r, f"{why}, relinked"))
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
        # And the database row with it. Relinking rewrote column E to the real
        # ATS posting while hunter_seen_roles kept the LinkedIn URL it was
        # sourced from, and the URL is the first thing match_rows keys on, so
        # every relinked row stopped pairing with its own database row: no
        # amendment detection, no warm path, and reconcile treating it as a
        # sheet row it had never seen.
        moved = 0
        for row_number, url in relinked.items():
            d = paired.get(row_number)
            if not d or not d.get("job_id"):
                continue
            try:
                db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]},
                         {"url": url, "job_url": url})
                moved += 1
            except Exception as e:
                print(f"  could not follow the relink in the database for "
                      f"{d['job_id']}: {e.__class__.__name__}")
        print(f"followed {moved} relink(s) into hunter_seen_roles")
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
                   {"select": "job_id,title,company,url,job_url", "limit": ALL_ROWS})
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


def cmd_prune_sheet(apply: bool = False, include_ungated: bool = False,
                    job_ids: str = "") -> int:
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
        "status": "neq.duplicate", "limit": ALL_ROWS})
    pairs, sheet_only, _, _ = match_rows(srows, db)

    def ident(s: SheetRow):
        return (slugify(s.company), _norm_title(s.role))

    untouched = lambda s: (s.verdict or "").strip() == "New"
    plan: dict[int, str] = {}
    first: dict[tuple, int] = {}

    # Rows he named, removed whatever column A says.
    #
    # The "New only" rule above exists so nothing HUNTER decides can delete a
    # row he has written on, and it stays exactly as it is. This is the other
    # case: on 2026-09-24 he asked for two rows off the sheet by name, and he
    # had already marked both of them Yes. A judgement of his is not something
    # to work around, so the only way past the guard is him naming the row.
    named = {j.strip() for j in (job_ids or "").split(",") if j.strip()}
    named_rows: set[int] = set()
    if named:
        by_id = {d.get("job_id"): srow for srow, d in pairs}
        for jid in sorted(named):
            srow = by_id.get(jid)
            if srow is None:
                # Never a silent skip: a typo here leaves a row he asked to be
                # rid of sitting on the sheet while the run reports success.
                print(f"no Pipeline row matched job id {jid!r}")
                return 1
            plan[srow.row_number] = f"removed by name ({jid})"
            named_rows.add(srow.row_number)
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
    # Only the rows he named are exempt from the write path's own column A
    # check. Everything the rules chose still has to read "New" at the moment
    # of deletion.
    removed = sheet.delete_rows(sorted(plan), named=named_rows)
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
        "limit": ALL_ROWS})
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
    allow = learn.load_company_allow(cfg)
    return {"roles": roles, "verdicted": verdicted, "recorded": recorded,
            "events": events, "findings": findings, "fixed": fixed,
            "unexplained": unexplained,
            "opens": learn.open_applications(roles),
            "allow": allow,
            "company_declines": learn.company_declines(events, allow)}


def learning_lines(out: dict) -> list[str]:
    lines = [f"learning: {out['recorded']} verdict events, "
             f"{len(out['findings'])} system miss(es), "
             f"{len(out['fixed'])} row(s) marked duplicate"]
    for e in out["unexplained"]:
        lines.append(f"  you declined a role that matches your archetypes: "
                     f"{e.get('company')} / {e.get('title')} ({e.get('reason_text')})")
    return lines


def learning_report_lines(out: dict, *, g12_hits: list[str] = ()) -> list[str]:
    """The LEARNING REPORT block of a run summary: which companies are
    closed by Krish's own verdicts, what that blocked this run, and the
    rejections the archetype gate did not predict (his to rule on)."""
    declines = out.get("company_declines") or {}
    lines = ["LEARNING REPORT"]
    dl = learn.decline_lines(declines)
    lines.append(f"  company-level declines on record: {len(dl)}"
                 + (" (" + "; ".join(dl[:12]) + (", ..." if len(dl) > 12 else "") + ")"
                    if dl else ""))
    allow = out.get("allow") or []
    lines.append(f"  allow list ({learn.ALLOW_KEY}): "
                 + (", ".join(allow) if allow else "empty"))
    hits = list(g12_hits or [])
    lines.append(f"  G12 applied this run: {len(hits)} posting(s)")
    for h in hits[:20]:
        lines.append(f"    {h}")
    for e in out.get("unexplained") or []:
        lines.append(f"  you declined a role that matches your archetypes: "
                     f"{e.get('company')} / {e.get('title')} ({e.get('reason_text')})")
    return lines


def stored_company_declines(cfg: Config) -> tuple[dict, list[str]]:
    """Company declines from the events already on record, for the phases
    that run before this run's learning step. A read failure degrades to no
    declines and a summary line, never a dead run."""
    try:
        allow = learn.load_company_allow(cfg)
        return learn.company_declines(learn.load_events(cfg), allow), []
    except Exception as e:
        return {}, [f"company declines not loaded: {e.__class__.__name__}: {e}"]


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


def newsletter_step(cfg: Config, *, apply: bool, limit: int = 0) -> dict:
    """Read every newsletter post not yet processed. One model call per new
    post, none for a post already on record, so the hourly drain can afford
    to check the feed every time it wakes."""
    from .sources import newsletter as nl
    posts = nl.fetch_feed()
    fresh = nl.new_posts(cfg, posts)
    if limit:
        fresh = fresh[-limit:]
    out = {"in_feed": len(posts), "new": len(fresh), "contacts": 0,
           "hiring": 0, "advice": 0, "dropped": [], "posts": [], "errors": []}
    model = cfg.optional("hunter_anthropic_model", "claude-opus-5")
    for post in fresh:
        signals, flags = nl.extract(cfg, post)
        if signals is None:
            out["errors"].append(f"{post['title'][:60]}: {'; '.join(flags)}")
            if apply:
                nl.record_post(cfg, post, None, flags, model)
            continue
        contacts = nl.contacts_from(signals, post)
        out["contacts"] += len(contacts)
        out["hiring"] += len(signals.get("hiring") or [])
        out["advice"] += len(signals.get("reach_out_advice") or [])
        out["dropped"].extend(flags)
        out["posts"].append({"title": post["title"], "link": post["link"],
                             "moves": [(c["full_name"], c["current_company"])
                                       for c in contacts],
                             "hiring": [h.get("company") for h in signals.get("hiring") or []],
                             "advice": signals.get("reach_out_advice") or []})
        if apply:
            if contacts:
                db_insert(cfg, "network_contacts", contacts,
                          on_conflict="contact_key", merge=True)
            nl.record_post(cfg, post, signals, flags, model)
    return out


def newsletter_lines(out: dict) -> list[str]:
    lines = [f"a16z newsletter: {out['new']} new post(s) of {out['in_feed']} in the "
             f"feed; {out['contacts']} people moved, {out['hiring']} companies hiring"]
    for p in out["posts"]:
        moves = ", ".join(f"{n} -> {c}" for n, c in p["moves"][:6])
        lines.append(f"  {p['title'][:70]}")
        if moves:
            lines.append(f"    moved: {moves}")
        for a in p["advice"][:3]:
            lines.append(f"    reach out: {a.get('who')}: {a.get('how')}")
    for e in out["errors"]:
        lines.append(f"  not processed, will retry: {e}")
    return lines


def cmd_newsletter(apply: bool = False, limit: int = 0) -> int:
    cfg = load()
    out = newsletter_step(cfg, apply=apply, limit=limit)
    print("\n".join(newsletter_lines(out)))
    for d in out["dropped"][:12]:
        print(f"  dropped (URL not in post): {d}")
    if not apply:
        print("\ndry run. add --apply to record the posts and the people.")
    return 0


def cmd_drain(command_id: str | None = None) -> int:
    """Run the oldest command Control Center queued, if any.

    Fires hourly from a Routine and exits in seconds when the queue is empty,
    which is most of the time. Krish's two buttons write here: `source` does a
    full sourcing pass and stops before packages, `packages` builds for rows
    reading Yes in column A that have no package yet.
    """
    cfg = load()
    # The newsletter first, every wake. Krish's ask was "every time a new post
    # is made"; posting is daily and this runs hourly, so a post is read
    # within the hour. A feed with nothing new costs one GET and no model call.
    try:
        out = newsletter_step(cfg, apply=True)
        if out["new"]:
            print("\n".join(newsletter_lines(out)))
    except Exception as e:
        print(f"newsletter check skipped: {e.__class__.__name__}: {e}")
    params = {"select": "id,command,requested_at", "state": "eq.queued",
              "order": "requested_at.asc", "limit": "1"}
    if command_id:
        # dispatched for one button press: claim that row and nothing else,
        # and exit quietly if the hourly drain already took it
        params["id"] = f"eq.{command_id}"
    queued = db_get(cfg, COMMANDS_TABLE, params)
    if not queued:
        print("nothing queued" if not command_id else f"command {command_id} is not queued")
        return 0
    job = queued[0]
    cid, command = str(job["id"]), job["command"]
    db_patch(cfg, COMMANDS_TABLE, {"id": cid},
             {"state": "running", "started_at": NOW()})
    print(f"running {command} (command {cid})")
    try:
        summary = run_command(cfg, command)
        run_url = actions_run_url()
        if run_url:
            summary = f"{summary} ({run_url})"
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


def actions_run_url() -> str:
    """The GitHub Actions run this process is, when it is one."""
    import os
    server, repo, run_id = (os.environ.get("GITHUB_SERVER_URL"),
                            os.environ.get("GITHUB_REPOSITORY"),
                            os.environ.get("GITHUB_RUN_ID"))
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def run_command(cfg: Config, command: str) -> str:
    """The work behind each button. Returns the line Control Center shows."""
    canon = load_canon(cfg)
    assert_canon_alignment(canon)
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    summary: Summary = Summary()

    if command == "source":
        declines, notes = stored_company_declines(cfg)
        summary.extend(notes)
        ledger = reconcile(cfg, canon, sheet, company_declines=declines)
        summary.extend(ledger.lines())
        try:
            out = learning_step(cfg, apply=True)
            declines = out["company_declines"]
            summary.extend(learning_lines(out))
        except Exception as e:
            summary.append(f"learning skipped: {e.__class__.__name__}")
        counts = source_and_stage(cfg, canon, sheet, summary, company_declines=declines)
        summary.extend(newsletter_movers_lines(cfg, sheet, canon.sheet_headers))
        try:
            sheet.sort_by_score()
        except Exception as e:
            summary.append(f"sort skipped: {e.__class__.__name__}: {e}")
        # Tell him the batch is there. Only cmd_run did this, so the Control
        # Center's "Find roles" button sourced roles and said nothing at all:
        # the one human step in the loop had nothing telling him it was his
        # turn, which is the whole failure the alert exists to prevent.
        try:
            fresh = sheet.read_pipeline(canon.sheet_headers)
            batches = funnel_batches(cfg, sheet, summary)
            summary.append(
                f"review email: "
                f"{alerts.send_review_ready(cfg, fresh, counts.get('staged', 0), batches=batches)}")
        except Exception as e:
            summary.append(f"review email failed: {e.__class__.__name__}: {e}")
        line = (f"{counts['discovered']} found, {counts['recorded']} recorded, "
                f"{counts['staged']} staged, ${counts['spend_usd']:.2f} spent")
    elif command == "process":
        counts = process_step(cfg, canon, sheet, summary)
        line = _process_line(counts)
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


def discover_posting(cfg: Config, company: str, title: str, cache: dict
                     ) -> tuple[str, object | None, str]:
    """Find a posting on its company's board without an ATS key.

    ("found", posting, "ats/slug"): the board lists it, exact title first,
    then a single close title. ("absent", None, why): the board exists and
    does not list it, which is verifiably dead. ("unknown", None, why): no
    board found or the board read failed, which is not an answer.
    """
    from .ats import discover as disc
    from .ats import ashby, greenhouse, lever
    boards = {"greenhouse": greenhouse.board, "ashby": ashby.board,
              "lever": lever.board}
    found = disc.discover(cfg, company, cache)
    if not found:
        return "unknown", None, "no job board found on greenhouse, ashby or lever"
    ats, slug = found
    try:
        postings = boards[ats](slug)
    except Exception as e:
        return "unknown", None, f"{ats}/{slug} board read failed: {e.__class__.__name__}"
    nt = _norm_title(title)
    hit = next((p for p in postings if _norm_title(p.title) == nt), None)
    if hit is None:
        close = [p for p in postings
                 if title_jaccard(title, p.title) >= FUZZY_TITLE_MIN]
        hit = close[0] if len(close) == 1 else None
    if hit is None:
        return "absent", None, f"not on the {ats}/{slug} board ({len(postings)} jobs)"
    return "found", hit, f"{ats}/{slug}"


PLAIN_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_jd_plain(url: str) -> tuple[bool | None, str]:
    """One plain GET on a posting page. (False, "") on 404 or 410, which is
    dead; (None, text) otherwise, where None means liveness is unknown and
    text is the page with tags stripped (a LinkedIn auth wall yields little
    and stays unknown)."""
    import html as html_mod
    try:
        r = requests.get(url, headers={"User-Agent": PLAIN_UA}, timeout=20,
                         allow_redirects=True)
    except requests.RequestException:
        return None, ""
    if r.status_code in (404, 410):
        return False, ""
    if r.status_code != 200:
        return None, ""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", r.text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return None, " ".join(text.split())


def linkedin_state(url: str) -> tuple[bool | None, str]:
    """Module-level seam so resolve_for_build's LinkedIn check is stubbable the
    same way fetch_jd_plain is, and the offline suite never opens a socket."""
    from .ats import linkedin
    return linkedin.posting_state(url)


def resolve_for_build(cfg: Config, row: dict, cache: dict, *,
                      sheet_snippet: str = "", sheet_why: str = ""
                      ) -> tuple[ResolvedRole, str | None, list[str]]:
    """(role, relink_url, flags). ATS key: the direct check. Otherwise the
    company's board, then the page itself; unknown liveness is recorded as
    unverified, never as dead. Krish's Yes is the build authority."""
    url = row.get("url") or row.get("job_url") or ""
    title = row["title"]
    company = row.get("company") or ""
    flags: list[str] = []
    if ats_key(url):
        return _resolve_for_build(row), None, flags
    state, hit, why = discover_posting(cfg, company, title, cache)
    if state == "found" and hit is not None and hit.url and ats_key(hit.url):
        role = _resolve_for_build(dict(row, url=hit.url, job_url=hit.url))
        flags.append(f"board discovery: {why}")
        return role, hit.url, flags
    if state == "absent":
        flags.append(why)
        return ResolvedRole(company=company, title=title, url=url, jd_url=url,
                            jd_text="", live=False, source=row.get("source") or "",
                            location=row.get("location") or "",
                            comp=row.get("comp") or ""), None, flags
    # LinkedIn answers 200 for a removed posting, so the generic page check
    # cannot see it. Two of the 28 approved rows on 2026-09-13 were gone and
    # both read as unverified, so packages were built for them.
    if "linkedin.com/jobs" in url:
        li_live, li_why = linkedin_state(url)
        if li_live is False:
            flags.append(li_why)
            return ResolvedRole(company=company, title=title, url=url, jd_url=url,
                                jd_text="", live=False,
                                source=row.get("source") or "",
                                location=row.get("location") or "",
                                comp=row.get("comp") or ""), None, flags
    live, text = fetch_jd_plain(url) if url.startswith("http") else (None, "")
    if live is False:
        flags.append("page returned 404 or 410")
        return ResolvedRole(company=company, title=title, url=url, jd_url=url,
                            jd_text="", live=False, source=row.get("source") or "",
                            location=row.get("location") or "",
                            comp=row.get("comp") or ""), None, flags
    jd = text if len(text) >= 200 else ""
    if not jd:
        jd = f"{title} at {company}. {sheet_snippet} {sheet_why}".strip()
        flags.append("thin JD: built from the sheet's own snippet and rationale")
    flags.append(f"liveness unverified: {why}")
    return ResolvedRole(company=company, title=title, url=url, jd_url=url,
                        jd_text=jd, live=False, source=row.get("source") or "",
                        location=row.get("location") or "",
                        comp=row.get("comp") or "", liveness="unverified"), None, flags


def package_status_for(role: ResolvedRole, row: dict) -> str:
    if getattr(role, "liveness", "checked") == "unverified":
        return sheet_mod.PKG_BUILT_UNVERIFIED
    return route_status(row)


def _sheet_row_for(sheet: Sheet, canon: Canon, row: dict) -> SheetRow | None:
    pairs, _, _, _ = match_rows(sheet.read_pipeline(canon.sheet_headers), [row])
    return pairs[0][0] if pairs else None


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


# At build time on a row Krish marked Yes, these gates are advice he has
# already weighed (the sheet shows the location, the band and the company).
# G0 (never apply), G1 (dead) and the package gates G8 to G10 still stop a
# build. Canon 9.4, amended 2026-09-07.
# Gates his Yes outranks at build time. Canon 9.4: the sheet already showed
# him the band, the location and the company, so a gate that decides what he
# is SHOWN has no business refusing to build what he asked for. Only G0, G1
# and the package gates G8 to G10 can stop a build.
#
# G13 was missing when it was added on 2026-09-20, and the first process run
# after it shipped refused to build the Citi role he had explicitly approved,
# citing the employer. That is the precise failure this set exists to
# prevent, recreated by a new gate.
BUILD_SOFT_GATES = {"G2", "G3", "G4", "G5", "G6", "G7", "G11", "G12", "G13"}


def build_one(cfg: Config, canon: Canon, sheet: Sheet, row: dict,
              summary: list[str], *, cache: dict | None = None,
              company_declines: dict | None = None,
              feedback: str = "") -> bool:
    """feedback carries Krish's own words from an amend reply, verbatim, so a
    rebuild acts on what he actually asked for rather than a paraphrase."""
    from .package.build import build_package, doc_url, read_master_facts
    from .package.tailor import load_blocks, tailor

    if cache is None:
        from .ats import discover as disc
        cache = disc.load_cache(cfg)
    srow = _sheet_row_for(sheet, canon, row)
    role, relink, rflags = resolve_for_build(
        cfg, row, cache,
        sheet_snippet=(srow.cell("JD Snippet") if srow else ""),
        sheet_why=(srow.cell("Why It Fits") if srow else ""))
    if getattr(role, "liveness", "checked") == "checked" and not role.live:
        # Verifiably gone. The row stays on Pipeline with column A untouched
        # (canon: hunter never archives a role Krish approved); Package
        # Status says why nothing was built.
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"status": "dead", "package_status": "blocked",
                  "rejection_reason": "G1: posting dead at build time"})
        if srow:
            sheet.update_package_status(srow.row_number, sheet_mod.PKG_DEAD)
        summary.append(f"DEAD {row['job_id']}: posting verifiably gone "
                       f"({'; '.join(rflags) or 'ATS check'}); Package Status says so, "
                       f"row stays for your call")
        return False
    if relink and srow:
        sheet.relink_jd_urls({srow.row_number: relink})
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"url": relink, "job_url": relink})
    never = cfg.require_json("hunter_never_apply")
    # The employer index and the role's merit, so the gate reasons written
    # onto the row say what they actually mean rather than judging the
    # company with no record of his approvals loaded.
    try:
        emp_index = employer.build_index(cfg, declines=company_declines)
    except Exception:
        emp_index = None
    merit = score_role(role, universe=canon.universe,
                       employer_index=emp_index).merit
    report = run_gates(role, never_apply=never, company_declines=company_declines,
                       employer_index=emp_index, merit=merit)
    if not report.passed:
        failed = {g.gate for g in report.failures()}
        reasons = "; ".join(f"{g.gate}: {g.reason}" for g in report.failures())
        if failed <= BUILD_SOFT_GATES:
            # Krish's Yes outranks the gates that decide what he is shown.
            # Seven of his 26 Yes rows were refused on 2026-09-07 for a
            # location or a band he had already read on the sheet.
            rflags.append(f"built on your Yes over {reasons}")
        else:
            db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                     {"package_status": "blocked", "rejection_reason": reasons})
            summary.append(f"BLOCKED {row['job_id']}: {reasons}")
            return False

    letter_blocks, cv_blocks = load_blocks(cfg)
    oauth = GoogleOAuth(cfg)
    db = DocBuild(oauth.access_token)
    facts = read_master_facts(db)
    # Evidence is what lets tailor generate the summary and the hook at all: the
    # voice gate traces every number and company name in generated prose against
    # it, and passing it empty disables generation rather than allowing ungated
    # text. The haystack is the master's own words plus Krish's recorded proof
    # points and long-form answers from the workbook, plus the JD.
    from .apply import gtmseed
    from .apply.infobank import load_bank
    from .package.voicegate import build_evidence
    bank = None
    evidence = ""
    # The mindmake engagement records and the two named programs. Without this key
    # the gate rejects any AI-native GTM claim, because it can trace none of it:
    # that is why the first real package never mentioned the work Krish is most
    # experienced in. optional(), not require(): a missing key costs that one story
    # and must not cost the application. See apply/gtmseed.py.
    # The naming law applies whether or not the workbook tabs load.
    banned = gtmseed.NAME_VARIANTS_BANNED
    gtm_evidence = cfg.optional("hunter_ai_gtm_evidence")
    if not gtm_evidence:
        rflags.append("hunter_ai_gtm_evidence missing, AI-native GTM claims "
                      "cannot be traced and will be rejected; run gtm-seed")
    try:
        bank = load_bank(lambda tab: sheet.read_tab_formulas(f"{tab}!A1:Z400"))
        evidence = build_evidence(
            facts.cv_master_text,
            "\n".join(bank.profile.values()),
            "\n".join(bank.interview.values()),
            "\n".join(e.value for e in bank.entries.values()),
            gtm_evidence,
            role.jd_text)
        banned = tuple(bank.banned_phrases) + gtmseed.NAME_VARIANTS_BANNED
    except Exception as e:
        summary.append(f"note {row['job_id']}: answer tabs unreadable "
                       f"({e.__class__.__name__}), generated prose disabled")
    tr = tailor(cfg, canon, company=role.company, title=role.title,
                jd_text=role.jd_text, master_competencies=facts.cv_competencies,
                letter_blocks=letter_blocks, highlights=facts.cv_highlights,
                evidence=evidence, banned_phrases=banned,
                feedback=feedback,
                keep_verbatim=tuple(facts.cv_summary_bold))
    result = build_package(db, tr, company=role.company, title=role.title,
                           letter_blocks=letter_blocks, cv_blocks=cv_blocks,
                           facts=facts)
    ok = (result.letter_report and result.letter_report.ok
          and result.cv_report and result.cv_report.ok)
    if ok:
        pkg_report = run_gates(role, never_apply=never, package_texts=(
            _doc_text(db, result.letter_doc_id), _doc_text(db, result.cv_doc_id)),
            company_declines=company_declines)
        # the same rule as at build entry: a Yes outranks the sourcing gates,
        # and only G0, G1 and the package gates G8 to G10 can stop it here
        hard_fails = [g for g in pkg_report.failures() if g.gate not in BUILD_SOFT_GATES]
        ok = not hard_fails
        if not ok:
            reasons = "; ".join(f"{g.gate}: {g.reason}" for g in hard_fails)
            summary.append(f"BLOCKED {row['job_id']} at package gates: {reasons}")
    if not ok:
        fails = ((result.letter_report.failures if result.letter_report else [])
                 + (result.cv_report.failures if result.cv_report else []))
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"package_status": "blocked",
                  "rejection_reason": f"package verification failed: {fails}"})
        # Said out loud, not only written to the row. This branch printed nothing at
        # all until 2026-09-15, so a build that correctly refused to ship a two-page
        # letter looked exactly like a build that did nothing.
        summary.append(f"BLOCKED {row['job_id']} at verification: "
                       + "; ".join(fails))
        for note in result.notes:
            summary.append(f"  note: {note}")
        if result.letter_doc_id:
            summary.append(f"  CL {doc_url(result.letter_doc_id)} (left for review)")
        if result.cv_doc_id:
            summary.append(f"  CV {doc_url(result.cv_doc_id)} (left for review)")
        return False

    db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, {
        "package_status": "built", "package_built_at": NOW(),
        "package_cv_url": result.cv_url, "package_letter_url": result.letter_url,
        "status": "staging" if row.get("status") in ("dead", "blocked") else row.get("status"),
    })
    status = package_status_for(role, row)
    if srow:
        sheet.update_package_cells(
            srow.row_number, cv_url=result.cv_url,
            letter_url=result.letter_url, cv_pdf_url=result.cv_pdf_url,
            letter_pdf_url=result.letter_pdf_url, package_status=status,
            built_date=TODAY())
    # What hunter wrote into the two documents, recorded at the moment of the
    # write. Any later difference is Krish editing the letter before he sends
    # it, which amend.py reads back as a correction.
    try:
        docs = DocBuild(GoogleOAuth(cfg).access_token)
        amend.save_docs(cfg, row["job_id"],
                        letter_text=doc_plain_text(docs, result.letter_doc_id or ""),
                        cv_text=doc_plain_text(docs, result.cv_doc_id or ""))
    except Exception as e:
        summary.append(f"  note: could not record the document text "
                       f"({e.__class__.__name__}), so edits to it will not be read")

    # build_package already copies tr.flags into result.notes, so adding tr.flags
    # here printed every tailoring flag twice in the run summary.
    flags = "; ".join(result.notes + rflags) or "clean"
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
    summary: Summary = Summary()
    ok = build_one(cfg, canon, sheet, rows[0], summary)
    print("\n".join(summary))
    return 0 if ok else 1


def seen_identity_keys(cfg: Config) -> set:
    """Every identity under which a role is already known: job_id, ATS key,
    normalized URL, and (company-slug, normalized title). Duplicate-marked
    rows count too; a role once seen stays seen."""
    keys: set = set()
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,url,job_url,company,title,status", "limit": ALL_ROWS})
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
                  "presented_at,status,url,job_url",
        "status": "neq.duplicate", "limit": ALL_ROWS})
    groups: dict = {}
    exact: set = set()
    for r in rows:
        # one ATS posting is one posting whatever the incumbent called it;
        # without a key, the squashed company plus the title decides
        key = ats_key(r.get("url") or r.get("job_url"))
        if key:
            exact.add(key)
        groups.setdefault(
            key or (_squash(r.get("company") or ""), _norm_title(r.get("title") or "")),
            []).append(r)
    marked, held = 0, 0
    for ident, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(group) < 2 or not ident[0] or not ident[1]:
            continue

        def rank(r):
            return (bool(r.get("krish_verdict")),
                    (r.get("package_status") or "none") != "none",
                    bool(r.get("presented_at")),
                    bool(HASH_SUFFIX.search(r.get("job_id") or "")))

        ranked = sorted(group, key=rank, reverse=True)
        keeper, losers = ranked[0], ranked[1:]
        # Standing protects a row whose identity was INFERRED from a company and
        # a title, because that match can be wrong and discarding a role Krish
        # judged would be worse than a duplicate. It protects nothing when both
        # rows carry the same ATS posting URL: that is not an inference, it is
        # the same application twice, and holding it is how one Harvey posting
        # sent two approval emails.
        protected = [] if ident in exact else [
            r for r in losers
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


def newsletter_movers_lines(cfg: Config, sheet: Sheet, headers: list[str]) -> list[str]:
    """People the a16z newsletter says joined a company on Krish's sheet in
    the last seven days. The signal is worth having even before a tracked
    role exists, so it goes in the run summary as well as onto a bridge."""
    try:
        rows = db_get(cfg, "network_contacts", {
            "select": "full_name,current_company,current_title,strength_evidence",
            "source": "eq.a16z newsletter", "order": "updated_at.desc", "limit": "200"})
        live = sheet.read_pipeline(headers)
        on_sheet = {}
        for r in live:
            for tok in distinctive_tokens(r.company, r.role):
                on_sheet[tok] = r.company
        lines = []
        for c in rows:
            ev = c.get("strength_evidence") or {}
            days = _days_since_any(ev.get("newsletter_date") or "")
            if days is None or days > 7:
                continue
            toks = distinctive_tokens(c.get("current_company") or "")
            hit = next((on_sheet[t] for t in toks if t in on_sheet), None)
            if hit:
                lines.append(f"  {c['full_name']} joined {hit}"
                             + (f" as {c['current_title']}" if c.get("current_title") else "")
                             + f" ({ev.get('newsletter_post', '')})")
        return (["people moving into companies you track this week:"] + lines) if lines else []
    except Exception:
        return []


def _days_since_any(published: str):
    from .people.bridges import _days_since
    return _days_since(published)


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


def targets_careers_urls(cfg: Config, sheet: Sheet) -> dict[str, str]:
    """slug -> careers page, from his Target Companies tab.

    Never fatal. A tab he renames should cost hunter a better board
    discovery, not the whole sourcing run.
    """
    try:
        from . import targets
        return targets.careers_urls(sheet)
    except Exception:
        return {}


def live_universe(cfg: Config, canon: Canon, sheet: Sheet,
                  summary: list[str]) -> list[str]:
    """The companies to sweep, taken from the tab he edits.

    Canon 9.1 and the Target Companies tab held two copies of the same list
    and had already drifted by three names. The tab is the one he opens, so
    the tab wins, and the difference is proposed as a canon amendment rather
    than written into canon from code.
    """
    try:
        from . import targets
        named = targets.universe(sheet)
    except Exception as e:
        summary.append(f"Target Companies tab unreadable ({e.__class__.__name__}), "
                       f"falling back to canon 9.1")
        return list(canon.universe)
    if not named:
        summary.append("Target Companies tab is empty, falling back to canon 9.1")
        return list(canon.universe)
    tab_keys = {slugify(n): n for n in named}
    canon_keys = {slugify(c): c for c in canon.universe}
    only_tab = sorted(tab_keys[k] for k in tab_keys.keys() - canon_keys.keys())
    only_canon = sorted(canon_keys[k] for k in canon_keys.keys() - tab_keys.keys())
    if only_tab or only_canon:
        summary.append(
            f"Target Companies tab and canon 9.1 disagree: "
            f"{len(only_tab)} only on the tab ({', '.join(only_tab[:4])}), "
            f"{len(only_canon)} only in canon ({', '.join(only_canon[:4])}). "
            f"The tab is what hunter swept.")
        propose_canon_universe(cfg, named, only_tab, only_canon)
    return named


def propose_canon_universe(cfg: Config, named: list[str], only_tab: list[str],
                           only_canon: list[str]) -> None:
    """Canon is never edited from code. The drift is filed as a proposal."""
    try:
        db_insert(cfg, "workflow_proposals", [{
            "agent_id": "hunter",
            "title": "canon 9.1 has drifted from the Target Companies tab",
            "body": ("The tab is the list Krish edits and the one hunter now "
                     "sweeps. Canon 9.1 should be rewritten to match it.\n\n"
                     "Only on the tab: " + (", ".join(only_tab) or "none") +
                     "\nOnly in canon: " + (", ".join(only_canon) or "none") +
                     "\n\nFull list (" + str(len(named)) + "): " +
                     ", ".join(named)),
            "status": "open",
        }], on_conflict="agent_id,title", merge=True)
    except Exception:
        # A proposal that cannot be filed must not take the sourcing run with
        # it. The summary line above already says the two disagree.
        pass


def source_and_stage(cfg: Config, canon: Canon, sheet: Sheet,
                     summary: list[str], company_declines: dict | None = None) -> dict:
    from .ats import ashby, greenhouse, lever
    from .gates import SENIOR_TITLE
    from .package.rationale import write_rationale
    from .sources import ats_for
    from .sources.apify_linkedin import SpendTracker, sweep_linkedin

    counts = {"discovered": 0, "senior": 0, "fresh": 0, "resolved": 0,
              "recorded": 0, "staged": 0, "unresolved": 0, "spend_usd": 0.0}
    postings = []
    from .ats import discover as disc
    board_cache = disc.load_cache(cfg)
    careers = targets_careers_urls(cfg, sheet)
    universe = live_universe(cfg, canon, sheet, summary)
    if cfg.optional("hunter_company_discovery", "1") != "0":
        try:
            from .sources import portfolio as _pf
            summary.extend(_pf.refresh_if_stale(cfg))
        except Exception as e:
            summary.append(f"portfolio refresh skipped: {e.__class__.__name__}")
        try:
            found = discover_companies(cfg, sheet, summary)
        except Exception as e:
            summary.append(f"company discovery failed: {e.__class__.__name__}: {e}")
            found = []
        known = {slugify(c) for c in universe}
        universe = universe + [f for f in found if slugify(f) not in known]
    swept, gaps, learned = [], [], 0
    for company in universe:
        mapping = ats_for(company)
        if not mapping:
            # Until 2026-09-20 this line read "gaps.append(company); continue",
            # and ats_for is a hardcoded 20 entry dict against a universe of
            # 52. Thirty-two companies Krish had personally named were
            # collected into a list, counted in the summary as
            # "discovery-only coverage", and never swept once. The machinery
            # to resolve them already existed and was used for LinkedIn
            # postings and a16z companies; it was simply never pointed here.
            found = disc.discover(cfg, company, board_cache,
                                  careers_url=careers.get(slugify(company), ""))
            if not found:
                gaps.append(company)
                continue
            mapping = found
            learned += 1
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
    swept_companies = [s.rsplit(":", 1)[0] for s in swept]
    if learned:
        disc.save_cache(cfg, board_cache)
    summary.append(
        f"target company boards swept: {len(swept)} ({learned} resolved this "
        f"run); still unreadable: {len(gaps)}"
        + (f" ({', '.join(sorted(gaps)[:8])})" if gaps else ""))

    # The a16z portfolio, as Krish asked on 2026-09-03. The board is a strong
    # company list and a strong ATS finder and a poor job list (25 relevance
    # sorted postings per query, no paging), so it works in two ways here:
    # its family-query postings go through the ordinary gates, and every ATS
    # board it reveals is remembered and swept in full from then on.
    try:
        from .ats import discover as disc
        from .sources import a16z
        cos = a16z.fetch_companies()
        kept = [c for c in cos if not a16z.excluded(c)]
        if kept:
            db_insert(cfg, "hunter_a16z_companies", [{
                "slug": c["slug"], "name": c["name"], "domain": c.get("domain"),
                "markets": c.get("markets") or [], "stage": c.get("stage"),
                "band": c.get("band"), "job_count": c.get("job_count"),
                "last_seen": NOW()} for c in kept],
                on_conflict="slug", merge=True)
        cache = disc.load_cache(cfg)

        # Find the boards of the portfolio itself, not only of the 25
        # relevance sorted postings each family query returns. Without this
        # the a16z leg contributed 4 of the 233 roles that have ever reached
        # his sheet, and a LinkedIn keyword sweep contributed 149.
        probed = learned_boards = 0
        budget = int(cfg.optional("hunter_max_board_probes_per_run", "120"))
        for c in a16z.board_probe_plan(kept, cache, cap=budget):
            probed += 1
            hit = None
            for slug in disc.slug_candidates(c["name"]):
                hit = disc.probe(slug)
                if hit:
                    cache[c["slug"]] = {"ats": hit[0], "slug": slug}
                    learned_boards += 1
                    break
            if not hit:
                # Remember the miss so the next run spends its budget on
                # companies it has not tried, not on the same 17 every week.
                cache[c["slug"]] = {"missed_at": NOW()}
        if probed:
            disc.save_cache(cfg, cache)
            # `name` is NOT NULL with no default, and a PostgREST upsert is an
            # INSERT ON CONFLICT: the not-null check fires before the conflict
            # is resolved, so omitting it answers 400 even for a row that
            # already exists. The first version of this omitted it, and the
            # only reason it was noticed is that the write was read back.
            try:
                db_insert(cfg, "hunter_a16z_companies",
                          [{"slug": c["slug"], "name": c["name"],
                            "ats": (cache.get(c["slug"]) or {}).get("ats"),
                            "ats_slug": (cache.get(c["slug"]) or {}).get("slug")}
                           for c in kept if c["slug"] in cache],
                          on_conflict="slug", merge=True)
                stored = sum(1 for c in kept
                             if (cache.get(c["slug"]) or {}).get("ats"))
                summary.append(f"a16z boards recorded on the portfolio table: "
                               f"{stored}")
            except Exception as e:
                summary.append(f"a16z board write failed, cache still holds them: "
                               f"{e.__class__.__name__}: {e}")
        summary.append(f"a16z boards: probed {probed} portfolio companies, "
                       f"found {learned_boards} readable board(s)")

        board_posts, failed = a16z.family_sweep()
        learned = 0
        for bp in board_posts:
            key = ats_key(bp.url)
            if key:
                bp.ats, bp.ats_slug, bp.ats_posting_id = key
                ck = slugify(bp.company)
                if ck and not cache.get(ck):
                    cache[ck] = {"ats": key[0], "slug": key[1]}
                    learned += 1
        disc.save_cache(cfg, cache)
        postings.extend(board_posts)
        summary.append(f"a16z board: {len(cos)} portfolio companies ({len(cos) - len(kept)} "
                       f"excluded by canon 5), {len(board_posts)} family postings, "
                       f"{learned} new ATS boards learned"
                       + (f"; {len(failed)} queries failed" if failed else ""))
    except Exception as e:
        summary.append(f"a16z board skipped: {e.__class__.__name__}: {e}")

    # Boards learned from the a16z board or from LinkedIn discovery, swept in
    # full like the canon universe. This is where the exhaustive listing
    # comes from.
    try:
        cache = disc.load_cache(cfg)
        fns = {"greenhouse": greenhouse.board, "ashby": ashby.board, "lever": lever.board}
        swept_slugs = {slugify(c) for c in swept_companies}
        extra = 0
        for ck, hit in cache.items():
            # This used to skip every slug in canon.universe, on the
            # assumption the first leg had already swept it. The first leg
            # could only sweep the 20 companies in ats_for, so a target
            # company whose board hunter had successfully LEARNED was
            # excluded here and absent there: swept by nobody, permanently.
            # Descript, Gamma and Luma AI were all sitting in this cache.
            # The exclusion is now what was actually swept, not what was
            # hoped to be.
            if disc.is_miss(hit) or ck in swept_slugs or hit["ats"] not in fns:
                continue
            try:
                board = fns[hit["ats"]](hit["slug"])
                postings.extend(board)
                extra += 1
            except Exception:
                continue
        summary.append(f"learned boards swept in full: {extra}")
    except Exception as e:
        summary.append(f"learned board sweep skipped: {e.__class__.__name__}")

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
    staged = stage_postings(cfg, canon, sheet, postings, summary,
                            company_declines=company_declines)
    staged["spend_usd"] = counts["spend_usd"]
    return staged


def posting_text(p) -> str:
    """The description a sweep already fetched, when it is one worth
    reading. The Apify LinkedIn actor returns descriptionText on every item."""
    raw = getattr(p, "raw", None) or {}
    text = raw.get("descriptionText") or raw.get("description") or ""
    text = " ".join(str(text).split())
    return text if len(text) >= 200 else ""


def discover_companies(cfg: Config, sheet: Sheet, summary: list[str], *,
                       apply: bool = True) -> list[str]:
    """Score companies he has never named, and propose the best of them.

    Returns the names that cleared the sweep floor, so this run can sweep
    their boards as well as his own list. Nothing here writes to his own
    columns and nothing is contacted: a good company becomes a row he can
    adopt by typing a tier.
    """
    from . import company as comp_score
    from . import prospect, targets
    from .ats import discover as disc

    try:
        named = targets.read(sheet)
    except Exception as e:
        summary.append(f"company discovery skipped, tab unreadable: "
                       f"{e.__class__.__name__}")
        return []
    exclude = {company_key(t_.name) for t_ in named}
    # A company he has already ruled on is not a discovery.
    try:
        for r in db_get(cfg, "hunter_verdict_events",
                        {"select": "company", "limit": ALL_ROWS}):
            if r.get("company"):
                exclude.add(company_key(r["company"]))
    except Exception:
        pass
    boards = disc.load_cache(cfg)
    cands = prospect.candidates(cfg, exclude=exclude, boards=boards)
    budget = int(cfg.optional("hunter_max_new_companies_per_run", "60"))
    batch = cands[:budget]
    all_scores = company_scores(cfg, [c.name for c in batch], sheet, summary)
    # company_scores returns everything hunter has ever scored, because the
    # cache is shared with staging. A proposal must come from THIS batch, or
    # the tab fills up with the companies he already named.
    wanted = {c.key for c in batch}
    scores = {k: s for k, s in all_scores.items() if k in wanted}
    rows = prospect.proposals(scores, batch,
                              floor=comp_score.DISCOVERY_FLOOR,
                              limit=targets.MAX_PROPOSED)
    summary.append(prospect.summary_line(cands, len(batch), len(rows)))
    if rows:
        try:
            for line in targets.write_proposals(sheet, rows, apply=apply):
                summary.append(line)
        except Exception as e:
            summary.append(f"proposals not written: {e.__class__.__name__}: {e}")
    # Anything at or above the sweep floor is worth sweeping now, adopted or
    # not. Waiting for him to type a tier would mean the discovery only ever
    # pays off next week.
    return [s.name for s in scores.values()
            if not s.status and s.total >= comp_score.SWEEP_FLOOR]


def funnel_batches_measure(cfg: Config, sheet: Sheet | None = None) -> list:
    """The batches, measured but not written. What `stats` prints."""
    out: list = []
    funnel_batches(cfg, sheet, None, save=False, into=out)
    return out


def funnel_batches(cfg: Config, sheet: Sheet | None = None,
                   summary: list[str] | None = None, *, save: bool = True,
                   into: list | None = None) -> list:
    """Accept rate per batch, measured and written down.

    Never fatal. If this cannot be computed the run still runs; it just
    stops being able to tell him the funnel is drifting, which is exactly
    the state hunter was in for the six weeks the rate fell from 77 percent
    to single figures.
    """
    try:
        from . import batchstats, companyintel, targets
        # Hunter's own computed tier first, because it covers every company
        # it has scored rather than only the 53 he named. His stated tier
        # wins where he has given one: the split is meant to tell him which
        # kind of company is worth his review time, and his own label is the
        # one he will recognise.
        tiers: dict = {}
        try:
            for k, row in companyintel.load(cfg).items():
                if row.get("tier"):
                    tiers[k] = int(row["tier"])
        except Exception:
            pass
        if sheet is not None:
            try:
                for k, v in targets.stated_tiers(sheet).items():
                    tiers[company_key(k) or k] = v
            except Exception:
                pass
        batches = batchstats.measure(cfg, tiers)
        if save:
            batchstats.save(cfg, batches)
        if into is not None:
            into.extend(batches)
        if summary is not None:
            summary.extend(batchstats.lines(batches))
        return batches
    except Exception as e:
        if summary is not None:
            summary.append(f"accept rate not measured: {e.__class__.__name__}")
        return []


def company_outcomes(cfg: Config) -> dict[str, dict]:
    """key -> {seen, yes, no} from what actually happened at each company.

    This is the half of the Target Companies tab that makes it a record
    rather than a wish list: next to hunter's score sits how many roles it
    has put in front of him from that company and how he ruled on them.
    """
    out: dict[str, dict] = {}

    def row(key: str) -> dict:
        return out.setdefault(key, {"seen": 0, "yes": 0, "no": 0})

    try:
        for r in db_get(cfg, "hunter_seen_roles",
                        {"select": "company,presented_at", "limit": ALL_ROWS}):
            if r.get("presented_at") and r.get("company"):
                row(company_key(r["company"]))["seen"] += 1
    except Exception:
        return out
    try:
        for e in db_get(cfg, "hunter_verdict_events",
                        {"select": "company,verdict,source", "limit": ALL_ROWS}):
            src = (e.get("source") or "").lower()
            if not e.get("company") or ("krish" not in src and "column a" not in src):
                continue
            r = row(company_key(e["company"]))
            if (e.get("verdict") or "").lower() in ("go", "yes", "approved"):
                r["yes"] += 1
            else:
                r["no"] += 1
    except Exception:
        pass
    return out


def known_company_keys(cfg: Config) -> set[str]:
    """Companies that have already reached his sheet at least once.

    The discovery quota is for businesses he has never been shown. A company
    hunter staged last week is not a discovery however well it scores.
    """
    try:
        rows = db_get(cfg, "hunter_seen_roles",
                      {"select": "company", "status": "eq.staging",
                       "limit": ALL_ROWS})
    except Exception:
        return set()
    return {company_key(r["company"] or "") for r in rows if r.get("company")}


def company_scores(cfg: Config, names: list[str], sheet: Sheet | None = None,
                   summary: list[str] | None = None, *,
                   budget: int | None = None,
                   postings: dict[str, tuple[str, str]] | None = None) -> dict:
    """slug -> CompanyScore for every company in this batch.

    Cached in hunter_company_intel and re-gathered only for companies hunter
    has never scored, so a weekly run pays for the new names and nothing
    else. A company that cannot be scored is absent from the result, which
    the caller must read as unknown rather than as bad.

    budget overrides the per-run cap. Staging passes one big enough to cover
    every company it is about to judge, because a shared cap meant discovery
    spent it first: on 2026-09-20 the run staged PayPal, Google, CreatorIQ,
    vivenu and openrouter, all of which score zero and would have been
    blocked, simply because nothing had got round to scoring them.

    postings is {company key: (job description, url)}, the posting hunter is
    already holding for that company. Measured 2026-09-24: half the companies
    in a batch were marked needs evidence, most of them with nothing gathered
    at all, which means G14 cannot refuse them however poor they are. The
    reason was narrow. from_posting existed and read a company's description
    out of its own job advert, and it was reachable only when a company
    already had a known ATS board. A company the keyword sweep dragged in has
    no board, no resolvable domain and therefore no evidence, while its job
    description sat in hand the whole time. This is not a new source and it
    costs no request; it is a citation hunter already had and was throwing
    away.
    """
    from . import company as comp_score
    from . import companyintel as intel
    from .ats import discover as disc
    from .ats import ashby, greenhouse, lever

    out: dict = {}
    if not names:
        return out
    keys = {}
    for n in names:
        k = company_key(n) or slugify(n)
        keys.setdefault(k, n)
    try:
        known = intel.load(cfg)
    except Exception:
        known = {}
    # Never scored, or scored long enough ago to be worth asking again. A
    # company hunter could not read a fortnight ago may simply have had a
    # page down, and without a retry that becomes a permanent exclusion.
    fresh = [k for k in keys
             if k not in known or intel.is_stale(known[k])]
    if budget is None:
        budget = int(cfg.optional("hunter_max_company_evidence_per_run", "60"))
    careers = targets_careers_urls(cfg, sheet) if sheet is not None else {}
    a16z_rows = {}
    try:
        a16z_rows = {company_key(r["name"]) or r["slug"]: r
                     for r in db_get(cfg, "hunter_a16z_companies",
                                     {"select": "slug,name,domain,markets,stage,band,job_count",
                                      "limit": ALL_ROWS})}
    except Exception:
        pass
    cache = disc.load_cache(cfg)
    fns = {"greenhouse": greenhouse.board, "ashby": ashby.board, "lever": lever.board}
    # A firm's own portfolio board answers, in one request for the whole
    # portfolio, the component the scorer can least often establish: who is
    # behind the company. It also carries a description, a stage and a
    # location set, which is four of the five components for a company whose
    # own site refuses hunter.
    try:
        from .sources import portfolio
        portfolio_rows = portfolio.load(cfg)
    except Exception:
        portfolio, portfolio_rows = None, {}

    def gather(k: str):
        """Evidence for one company. Every call is a network read of a
        public page, so they run side by side: serially, a budget of 60 was
        three minutes of a Sunday morning spent waiting on DNS."""
        name = keys[k]
        got: dict = {}
        a16 = a16z_rows.get(k)
        # The a16z index contributes membership, stage and band. Its
        # "markets" line ("a16z lists its markets as AI, Enterprise") is NOT
        # a description of the business and was winning over the company's
        # own site, because it was merged first and everything after it used
        # setdefault. Held back to the end, where it is a last resort.
        a16z_markets = None
        if a16:
            a16_facts = intel.from_a16z(a16)
            a16z_markets = a16_facts.pop("what_it_does", None)
            got.update(a16_facts)
        pf = portfolio_rows.get(k)
        if pf and portfolio is not None:
            for a, fact in portfolio.row_to_facts(pf).items():
                got.setdefault(a, fact)
        domain = (a16 or {}).get("domain") or (pf or {}).get("domain") or ""
        # The ROOT of his careers link, not the careers page itself. A
        # careers page says "Careers at TollBit" and nothing about the
        # business, so reading it left 25 of his 52 named companies with no
        # description and therefore no score at all.
        careers_url = careers.get(slugify(name)) or ""
        url = ""
        if careers_url:
            parts = careers_url.split("/")
            url = "/".join(parts[:3]) if len(parts) >= 3 else careers_url
        if not url and domain:
            url = domain if domain.startswith("http") else "https://" + domain
        guessed = False
        if not url:
            url, _ = intel.resolve_domain(name)
            guessed = bool(url)
        if url:
            site, _ = intel.site_facts(url)
            if guessed and site:
                site = intel.mark_guessed(site)
            for a, fact in site.items():
                got.setdefault(a, fact)
        board = cache.get(slugify(name)) or None
        if board and board.get("ats") in fns:
            try:
                posts = fns[board["ats"]](board["slug"])
                burl = f"{board['ats']}:{board['slug']}"
                for a, fact in intel.from_board([x.title for x in posts], burl).items():
                    got.setdefault(a, fact)
                locs = sorted({(x.location or "") for x in posts if x.location})
                if locs and "locations" not in got:
                    got["locations"] = comp_score.Fact(
                        "hires in " + "; ".join(locs[:12])[:300], burl)
                if "what_it_does" not in got:
                    for x in posts[:6]:
                        found = intel.from_posting(
                            x.raw.get("descriptionPlain") or "", x.url or burl, name)
                        if found:
                            got.update(found)
                            break
            except Exception:
                pass
        # The company's own posting, last, because its homepage and its board
        # are better evidence of what the business is than one advert. Still
        # cited to the posting URL, so nothing here is uncited.
        if "what_it_does" not in got and postings:
            jd, jurl = postings.get(k, ("", ""))
            if jd and jurl:
                got.update(intel.from_posting(jd, jurl, name))
        if "what_it_does" not in got and a16z_markets is not None:
            got["what_it_does"] = a16z_markets
        a16z_member = bool(got.pop("a16z_portfolio", False))
        facts = comp_score.Facts(slug=k, name=name, a16z_portfolio=a16z_member,
                                 **got)
        return comp_score.score_company(facts)

    scored_now = []
    batch = fresh[:budget]
    if batch:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        workers = int(cfg.optional("hunter_company_evidence_workers", "8"))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(gather, k) for k in batch]
            for fu in as_completed(futures):
                try:
                    scored_now.append(fu.result())
                except Exception as e:
                    if summary is not None:
                        summary.append(
                            f"company evidence failed: {e.__class__.__name__}")
    if scored_now:
        try:
            intel.save(cfg, scored_now)
        except Exception as e:
            if summary is not None:
                summary.append(f"company intel not saved: {e.__class__.__name__}")
    for s in scored_now:
        out[s.slug] = s
    for k, row in known.items():
        if k in out:
            continue
        out[k] = intel.restore(row)
    if summary is not None:
        retried = sum(1 for k in batch if k in known)
        if retried:
            summary.append(f"{retried} company(ies) re-scored because what "
                           f"hunter knew about them had gone stale")
    if summary is not None and scored_now:
        summary.append(f"scored {len(scored_now)} company(ies) this run; "
                       f"{max(0, len(fresh) - len(batch))} left for next run")
    return out


def stage_postings(cfg: Config, canon: Canon, sheet: Sheet,
                   postings: list, summary: list[str],
                   company_declines: dict | None = None) -> dict:
    """Dedupe, resolve, gate, score, write a rationale, stage.

    Split out of source_and_stage so postings that arrive some other way go
    through exactly this path. On 2026-09-02 a paid Apify run's 2924 results
    had to be recovered after a timeout, and recovering them down a parallel
    code path would have meant roles reaching the sheet without the gates.
    """
    from .ats import ashby, greenhouse, lever
    from .ats import discover as disc
    from .company import (DISCOVERY_FLOOR, NEEDS_EVIDENCE as COMPANY_UNKNOWN,
                          SWEEP_FLOOR as COMPANY_FLOOR)
    from .gates import EXCEPTIONAL_MERIT as EXCEPTIONAL, SENIOR_TITLE
    from .package.rationale import write_rationale_and_snippet

    counts = {"discovered": 0, "senior": 0, "fresh": 0, "resolved": 0,
              "recorded": 0, "staged": 0, "unresolved": 0, "spend_usd": 0.0,
              "boards_found": 0, "g12_blocked": [], "g14_blocked": [],
              "from_description": 0, "comp_from_text": 0}
    counts["discovered"] = len(postings)
    # Who the employers are, read once rather than per posting.
    try:
        employer_index = employer.build_index(cfg, declines=company_declines)
    except Exception as e:
        summary.append(f"employer index unavailable, scoring without it: "
                       f"{e.__class__.__name__}")
        employer_index = None
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
    # The company question is asked once per company, after dedupe, so a run
    # pays for the companies that actually have a live candidate rather than
    # for every name the sweep touched.
    # Every company with a live candidate is scored, whatever discovery
    # spent earlier in the run. This is the gate that decides what he looks
    # at, and it cannot be the thing that runs out of budget.
    live_companies = sorted({p.company for p in fresh if p.company})
    # The description the sweep already carried, one per company, so a company
    # with no board and no resolvable domain can still be read out of its own
    # advert. Longest wins: a two line advert says less about the business than
    # a full one, and this is the only shot each company gets.
    company_postings: dict[str, tuple[str, str]] = {}
    for p in fresh:
        if not p.company:
            continue
        text = (p.raw.get("descriptionPlain") or p.raw.get("description")
                or p.raw.get("jd_text") or "")
        if not isinstance(text, str) or len(text) < 120 or not p.url:
            continue
        k = company_key(p.company) or slugify(p.company)
        if len(text) > len(company_postings.get(k, ("", ""))[0]):
            company_postings[k] = (text, p.url)
    company_view = company_scores(cfg, live_companies, sheet, summary,
                                  budget=max(len(live_companies), 1),
                                  postings=company_postings)
    if company_postings:
        summary.append(
            f"{len(company_postings)} company description(s) available from "
            f"the postings already in hand")

    never = cfg.require_json("hunter_never_apply")
    opens = learn.open_applications(db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,krish_verdict,verdict_at", "limit": ALL_ROWS}))
    inserts, staged_rows = [], []
    fetchers = {"greenhouse": greenhouse.fetch_posting,
                "ashby": ashby.fetch_posting, "lever": lever.fetch_posting}
    for p in fresh:
        # G12 before any fetch or paid probe: a company Krish declined is
        # recorded as blocked with the dated reason and never staged.
        # No employer named, nothing to judge, and a row he cannot act on is
        # worse than no row. "Confidential" reached his sheet on 2026-09-24.
        if companyintel_mod.is_placeholder(p.company):
            counts["placeholder"] = counts.get("placeholder", 0) + 1
            continue
        hit = learn.declined_company(company_declines, p.company)
        if hit:
            reason = f"G12: company declined by Krish on {hit['date']} ({hit['code']})"
            inserts.append({"job_id": job_id(p.company, p.title), "title": p.title,
                            "company": p.company, "url": p.url, "job_url": p.url,
                            "status": "blocked", "rejection_reason": reason,
                            "source": p.source, "sweep_date": TODAY(),
                            "why_it_fits": "", "location": p.location or "",
                            "comp": p.comp_text or ""})
            counts["g12_blocked"].append(f"{p.company} / {p.title}: {reason}")
            continue
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
        liveness = "checked"
        if fetch:
            try:
                live, jd, jd_url = fetch_with_retry(fetch, p.ats_slug, p.ats_posting_id)
            except Exception as e:
                summary.append(f"resolve failed, recorded unresolved: "
                               f"{p.company}/{p.title!r}: {e.__class__.__name__}")
                fetch = None
        if not fetch and posting_text(p):
            # The LinkedIn sweep already carries the full description. Until
            # 2026-09-07 hunter threw it away and recorded the posting as
            # unresolved: 1,190 of them in one morning, 220 matching an
            # archetype, most in New York. The description is enough to gate
            # and score; liveness stays unverified, the sheet says so in
            # JD URL Verified, and the build step checks again.
            live, jd, jd_url = False, posting_text(p), p.url
            liveness = "unverified"
            counts["from_description"] += 1
            fetch = True
        if not fetch:
            counts["unresolved"] += 1
            inserts.append({"job_id": job_id(p.company, p.title), "title": p.title,
                            "company": p.company, "url": p.url, "job_url": p.url,
                            "status": "unresolved", "source": p.source,
                            "sweep_date": TODAY(), "why_it_fits": "",
                            "location": p.location or "", "comp": p.comp_text or ""})
            continue
        counts["resolved"] += 1
        # Pay, read from the body when the source left the field empty. Only
        # Ashby ever filled it, so 92 percent of roles reached him with no
        # band at all and the $200,000 floor could not fire on any of them.
        band = comp_mod.best(p.comp_text, jd)
        if band and not (p.comp_text or "").strip():
            counts["comp_from_text"] = counts.get("comp_from_text", 0) + 1
        role = ResolvedRole(company=p.company, title=p.title, url=jd_url,
                            jd_url=jd_url, jd_text=jd, live=live,
                            source=p.source, location=p.location or "",
                            comp=band, liveness=liveness)
        # Score BEFORE gating. G12 and G13 need the role's merit, which is
        # its score with the employer left out, to decide whether a company
        # he declined is overridden by an exceptional role.
        result = score_role(role, universe=canon.universe,
                            employer_index=employer_index)
        report = run_gates(role, never_apply=never, company_declines=company_declines,
                           employer_index=employer_index, merit=result.merit)
        cscore = company_view.get(company_key(role.company) or slugify(role.company))
        status, reason = "scanned", None
        if result.auto_rejected:
            status, reason = "dropped", result.rejection_reason
        elif not report.passed:
            status = "blocked"
            reason = "; ".join(f"{g.gate}: {g.reason}" for g in report.failures())
        elif (cscore is not None and cscore.status != COMPANY_UNKNOWN
              and cscore.total < COMPANY_FLOOR
              and (result.merit < EXCEPTIONAL or disqualified(cscore))):
            # The company question, asked before the seat. A company hunter
            # has NOT been able to read is not blocked here, only ranked
            # lower: refusing on an absent observation is the thing this
            # repo forbids, and "never block on no evidence" is already
            # written into CLAUDE.md for roles.
            status = "blocked"
            reason = (f"G14: company scores {cscore.total} of 10, below the "
                      f"{COMPANY_FLOOR:g} floor ({cscore.why(2) or 'no evidence'})"
                      + ("; an exceptional role does not open a disqualified "
                         "company" if disqualified(cscore) else ""))
            counts["g14_blocked"].append(role.company)
        else:
            # The score no longer blocks (canon 9.2 as amended 2026-09-03):
            # 16 of the 17 roles Krish said yes to scored below the old bar.
            # G11 decides what is his shape; the score orders the sheet.
            status = "staging"
        row = {"job_id": role.job_id, "title": role.title, "company": role.company,
               "url": role.url, "job_url": role.jd_url, "score": result.score,
               "status": status, "auto_rejected": result.auto_rejected,
               "rejection_reason": reason, "source": role.source,
               "location": role.location, "comp": role.comp,
               "sweep_date": TODAY(), "why_it_fits": result.why_it_fits,
               # The text this score came from. Without it a role cannot be
               # replayed through the scorer, so the taste test can only
               # measure the parts of the bar that do not need the posting.
               #
               # 6,000 characters, not 20,000. The scorer's signals are all in
               # the first page or two of a posting, and the rest is benefits
               # boilerplate. At 20,000 a sourcing run's single insert became
               # tens of megabytes of JSON and the database answered 500,
               # throwing away half an hour of sweeping at the last step.
               "jd_text": (role.jd_text or "")[:6000],
               "last_verified_at": NOW() if live else None}
        inserts.append(row)
        if status == "staging":
            # The same rationale generator the re-gate uses, so a row staged
            # today reads exactly like a row re-judged last week. Krish asked
            # for one standard; this is where it is applied.
            why, snippet, rflags = write_rationale_and_snippet(
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
            staged_rows.append((role, result, snippet, why))

    if inserts:
        db_insert(cfg, "hunter_seen_roles", inserts, on_conflict="job_id",
                  ignore_duplicates=True)
    counts["recorded"] = len(inserts)
    disc.save_cache(cfg, cache)
    if counts["boards_found"]:
        summary.append(f"board discovery resolved {counts['boards_found']} "
                       f"LinkedIn posting(s) to their real ATS")
    if counts["g12_blocked"]:
        summary.append(f"G12 blocked {len(counts['g12_blocked'])} posting(s) at "
                       f"companies Krish has declined")
    if counts["from_description"]:
        summary.append(f"{counts['from_description']} posting(s) gated from the "
                       f"description the sweep carried, liveness unverified")

    if counts["g14_blocked"]:
        summary.append(
            f"G14 blocked {len(counts['g14_blocked'])} posting(s) at companies "
            f"scoring below {COMPANY_FLOOR:g}: "
            + ", ".join(sorted(set(counts["g14_blocked"]))[:6]))

    if staged_rows:
        # Ranked on the company AND the seat, not the seat alone. Under the
        # old key a role at a company he named tied with a role at an
        # advertising holding company, and at the staging cap the tie was
        # broken by whichever leg happened to run first.
        def rank(entry):
            role, result = entry[0], entry[1]
            cs = company_view.get(company_key(role.company) or slugify(role.company))
            company_part = cs.total if cs and cs.status != COMPANY_UNKNOWN else 5.0
            return -((result.score or 0) + company_part)

        staged_rows.sort(key=rank)
        # Diversity before volume. Ranked first so the best of each leg and
        # each company survives the trim, then trimmed, then capped.
        cap = int(cfg.optional("hunter_max_staged_per_run", "40"))
        staged_rows = cap_by_leg_and_company(
            staged_rows, summary, cap=cap,
            per_company=int(cfg.optional("hunter_max_per_company_per_run", "3")),
            leg_share=float(cfg.optional("hunter_max_leg_share_per_run", "0.35")))
        # And only the top of it reaches the sheet. There was no cap until
        # 2026-09-20, which is how the tab reached 176 rows: a sheet he
        # cannot judge in one sitting is a sheet he does not judge. The
        # roles below the line keep their database row and their score, so
        # nothing is lost and raising the cap surfaces them next run.
        if cap and len(staged_rows) > cap:
            held = len(staged_rows) - cap
            cut = staged_rows[cap][1].score
            # Some of the cap is reserved for companies he has never seen.
            # His instruction on 2026-09-20 was not to lock the funnel to
            # the list he had already written down: "There are so many
            # companies I am not thinking about that has good backing,
            # potential, mission, leadership and opportunity". Ranking alone
            # would spend every slot on the companies hunter already knows
            # most about, which is the same list forever.
            quota = int(cfg.optional("hunter_discovery_quota_per_run", "5"))
            seen_before = known_company_keys(cfg)
            def is_new(entry):
                k = company_key(entry[0].company) or slugify(entry[0].company)
                cs = company_view.get(k)
                return (k not in seen_before and cs is not None
                        and cs.status != COMPANY_UNKNOWN
                        and cs.total >= DISCOVERY_FLOOR)

            head = staged_rows[:cap]
            tail = staged_rows[cap:]
            newcomers = [e for e in tail if is_new(e)][:quota]
            if newcomers:
                head = head[:cap - len(newcomers)] + newcomers
                summary.append(
                    f"{len(newcomers)} slot(s) went to companies hunter has "
                    f"never staged before, scoring {DISCOVERY_FLOOR:g} or more: "
                    + ", ".join(sorted({e[0].company for e in newcomers})))
            staged_rows = head
            summary.append(
                f"staging capped at {cap}: {held} more role(s) passed every "
                f"gate and are held at score {cut} or below. They keep their "
                f"database row; raise hunter_max_staged_per_run to see them.")
        new_rows = [make_row(company=role.company, role=role.title,
                             jd_url=role.jd_url, score=result.score,
                             why_it_fits=why,
                             location=role.location, comp=role.comp,
                             source=role.source, jd_snippet=snippet,
                             jd_verified=(role.liveness != "unverified"))
                    for role, result, snippet, why in staged_rows]
        sheet.append_rows(new_rows)
        for role, _, _, _ in staged_rows:
            db_patch(cfg, "hunter_seen_roles", {"job_id": role.job_id},
                     {"presented_at": NOW()})
        counts["staged"] = len(staged_rows)

    return counts


def write_warm_paths(cfg: Config, sheet: Sheet, canon: Canon,
                     rows: list[dict]) -> int:
    """Warm Path and Path Evidence for the given DB rows, matched to their
    sheet rows the way reconcile matches them."""
    from .people import bridges as bridges_mod
    if not rows:
        return 0
    cells = bridges_mod.warm_path_cells(cfg, [r["job_id"] for r in rows])
    live = sheet.read_pipeline(canon.sheet_headers)
    pairs, _, _, _ = match_rows(live, list(rows))
    mapping = {}
    for srow, d in pairs:
        warm, evidence = cells.get(d["job_id"], (sheet_mod.WARM_NONE, sheet_mod.EVIDENCE_NONE))
        mapping[srow.row_number] = (warm, evidence)
    return sheet.update_warm_paths(mapping)


def sync_applied_state(cfg: Config, sheet: Sheet, canon: Canon) -> dict:
    """Mirror application state onto hunter_seen_roles. Never authored here.

    Krish 2026-09-15: the Hunt lane on his People tab shows the roles he said Yes to
    and the person who can get him in, and could not say which ones he had applied
    to, because nothing recorded it.

    Two sources, and the first attempt read the wrong one. The sheet's own
    "Application Status" column reads "Not applied" on all 29 rows of his Applied
    tab: it was backfilled with the canon 9.13 default and never updated. What he
    actually maintains is COLUMN A, where the verdict reads "Applied", "Already
    applied" or a decline in his own words, and verdicts.parse already classifies it.

    Both tabs are read, because a row moves from Pipeline to Applied once decided,
    and the applied ones are almost all on Applied. hunter_application_approvals wins
    where they disagree, because a recorded submission is a fact and a cell is a note.
    Nothing is ever written back to his sheet.
    """
    from .verdicts import parse as parse_verdict
    out = {"from_sheet": 0, "from_ledger": 0, "written": 0}
    state: dict[str, tuple[str, str]] = {}

    db_rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,status,application_state,applied_at",
        "limit": ALL_ROWS})
    # config.ARCHIVE_TAB is literally "Applied", and read_archive reads it: a role
    # moves there once decided, which is where almost every applied row actually is.
    grid = list(sheet.read_pipeline(canon.sheet_headers))
    try:
        grid += list(sheet.read_archive())
    except Exception:
        pass
    pairs, _us, _ud, _amb = match_rows(grid, db_rows)
    for srow, row in pairs:
        verdict, _reason = parse_verdict(srow.cell("Verdict") or "")
        if verdict != "applied":
            continue
        when = (srow.cell("Applied Date") or "").strip()
        state[row["job_id"]] = ((srow.cell("Verdict") or "Applied").strip(),
                                "" if when.lower() in ("", "n/a") else when)
        out["from_sheet"] += 1

    for a in db_get(cfg, "hunter_application_approvals", {
            "select": "job_id,state,submitted_at", "state": "eq.submitted",
            "limit": "1000"}):
        state[a["job_id"]] = ("Applied", a.get("submitted_at") or "")
        out["from_ledger"] += 1

    by_id = {r["job_id"]: r for r in db_rows}
    for job_id, (label, when) in state.items():
        row = by_id.get(job_id) or {}
        if (row.get("application_state") or "") == label:
            continue
        patch = {"application_state": label}
        if when:
            patch["applied_at"] = when
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id}, patch)
        out["written"] += 1
    return out


def yes_db_rows(cfg: Config, sheet: Sheet, canon: Canon) -> list[dict]:
    """The DB rows behind every Yes on Pipeline, whatever their package
    state. The warm path pass covers all of them, built or not."""
    yes_rows = [r for r in sheet.read_pipeline(canon.sheet_headers)
                if classify_verdict(r.verdict or "") == "go"]
    if not yes_rows:
        return []
    known = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,status,krish_verdict,"
                  "warm_path_person,warm_path_tier,package_status",
        "limit": ALL_ROWS})
    pairs, _, _, _ = match_rows(yes_rows, list(known))
    return [d for _, d in pairs]


def _pair_sheet_to_db(cfg: Config, rows: list[SheetRow]) -> dict[int, dict]:
    """row number -> the hunter_seen_roles row behind it."""
    if not rows:
        return {}
    known = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,company,title,url,job_url,score,status,"
                              "krish_verdict,package_status", "limit": ALL_ROWS})
    pairs, _, _, _ = match_rows(rows, list(known))
    return {srow.row_number: d for srow, d in pairs}


def amendment_step(cfg: Config, canon: Canon, sheet: Sheet,
                   summary: list[str]) -> int:
    """Read what Krish changed since hunter last wrote, before hunter writes
    again. Documents are compared too, because the cover letter he edits in
    Docs before sending is the sharpest correction he ever gives.
    """
    rows = sheet.read_pipeline(canon.sheet_headers)
    paired = _pair_sheet_to_db(cfg, rows)
    state = amend.load_state(cfg)
    if not state:
        summary.append("amendments: nothing recorded yet, this run sets the "
                       "baseline")
        return 0
    found = amend.detect(rows, paired, state)

    try:
        docs = DocBuild(GoogleOAuth(cfg).access_token)

        def read_doc(url: str) -> str:
            return doc_plain_text(docs, url)

        found += amend.detect_docs(cfg, read_doc, rows, paired, state)
    except Exception as e:
        summary.append(f"document amendments skipped: {e.__class__.__name__}: {e}")

    n = amend.record(cfg, found)
    if n:
        summary.append(f"amendments read from your edits: {n}")
        for a in found[:10]:
            summary.append(f"  {a['company']} {a['field']}: "
                           f"{(a['before_text'] or '')[:60]!r} became "
                           f"{(a['after_text'] or '')[:60]!r}")
        for p in amend.proposals(found):
            summary.append(f"  PATTERN: {p['question']}")
    else:
        summary.append("amendments: nothing changed since the last run")
    return n


def _doc_id_from(url: str) -> str:
    m = re.search(r"/document/d/([A-Za-z0-9_-]{20,})", url or "")
    return m.group(1) if m else ""


def doc_plain_text(docs: DocBuild, url_or_id: str) -> str:
    """A document's text, or "" when it cannot be read. Never raises: an
    unreadable document must not present as a rewritten one."""
    doc_id = _doc_id_from(url_or_id) or (url_or_id if "/" not in (url_or_id or "")
                                         else "")
    if not doc_id:
        return ""
    try:
        return "\n".join(p["text"] for p in DocBuild.paragraphs(docs.get(doc_id)))
    except Exception:
        return ""


def snapshot_step(cfg: Config, canon: Canon, sheet: Sheet) -> int:
    """Record what the sheet says now, so the next run can tell his edits from
    hunter's own. Runs last, after every write this pass made."""
    rows = sheet.read_pipeline(canon.sheet_headers)
    paired = _pair_sheet_to_db(cfg, rows)
    return amend.sync_after(cfg, rows, paired)


def archive_decided(sheet: Sheet, canon: Canon) -> tuple[int, list[str]]:
    """Move every Applied and Declined row to the Applied tab. Yes and New
    stay: Yes is work in flight, New is a decision not yet made."""
    rows = sheet.read_pipeline(canon.sheet_headers)
    movers, lines = [], []
    for r in rows:
        kind, code = verdicts.parse(r.verdict)
        if kind in ("applied", "rejection"):
            movers.append(r)
            lines.append(f"{r.company} / {r.role} [{r.verdict}]")
    if not movers:
        return 0, lines
    moved = sheet.archive_rows(movers, archive_tab=config_mod.ARCHIVE_TAB,
                               archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                               headers=canon.sheet_headers)
    return moved, lines


def process_step(cfg: Config, canon: Canon, sheet: Sheet, summary: list[str], *,
                 max_packages: int = 0, retry_dead: bool = False) -> dict:
    """Everything that follows from Krish's column A, in order: reconcile,
    learn, build every Yes, find the person for every Yes, archive the
    decided, sort. Each phase reports; a phase that fails does not stop the
    later ones, because a tidy sheet is worth having even when a build is
    not."""
    from .people import bridges as bridges_mod
    from .people import enrich as enrich_mod
    from .ats import discover as disc

    counts: dict = {"built": 0, "dead": 0, "unverified": 0, "blocked": 0,
                    "warm_paths": 0, "cold_targets": 0, "archived": 0,
                    "sorted": 0, "g12_blocked": 0, "amendments": 0}

    # His edits are read BEFORE anything of hunter's is written, because the
    # first build of the pass would overwrite the very cells that carry them.
    try:
        counts["amendments"] = amendment_step(cfg, canon, sheet, summary)
    except Exception as e:
        summary.append(f"amendment pass skipped: {e.__class__.__name__}: {e}")

    declines, notes = stored_company_declines(cfg)
    summary.extend(notes)

    ledger = reconcile(cfg, canon, sheet, company_declines=declines)
    summary.extend(ledger.lines())
    counts["reconciled"] = len(ledger.matched)
    counts["g12_blocked"] = len(ledger.company_blocked)

    try:
        applied = sync_applied_state(cfg, sheet, canon)
        counts["applied_synced"] = applied["written"]
        summary.append(f"applied state: {applied['from_sheet']} from your sheet, "
                       f"{applied['from_ledger']} from the approval ledger, "
                       f"{applied['written']} row(s) updated")
    except Exception as e:
        summary.append(f"applied state sync skipped: {e.__class__.__name__}: {e}")

    out = {}
    try:
        out = learning_step(cfg, apply=True)
        declines = out["company_declines"]
        summary.extend(learning_lines(out))
    except Exception as e:
        summary.append(f"learning loop skipped: {e.__class__.__name__}: {e}")

    # packages for every Yes without one
    cache = disc.load_cache(cfg)
    try:
        todo = select_for_build(cfg, sheet, canon.sheet_headers,
                                cap=max_packages, retry_dead=retry_dead)
    except Exception as e:
        todo = []
        summary.append(f"package selection failed: {e.__class__.__name__}: {e}")
    summary.append(f"packages to build: {len(todo)}")
    for row in todo:
        try:
            before = len(summary)
            if build_one(cfg, canon, sheet, row, summary, cache=cache,
                         company_declines=declines):
                counts["built"] += 1
                if any("liveness unverified" in line for line in summary[before:]):
                    counts["unverified"] += 1
            elif any(line.startswith(f"DEAD {row['job_id']}") for line in summary[before:]):
                counts["dead"] += 1
            else:
                counts["blocked"] += 1
        except Exception as e:
            counts["blocked"] += 1
            summary.append(f"BUILD FAILED {row['job_id']}: {e.__class__.__name__}: {e}")
            try:
                db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                         {"package_status": "blocked",
                          "rejection_reason": f"build error: {e.__class__.__name__}"})
            except Exception:
                pass
    try:
        disc.save_cache(cfg, cache)
    except Exception:
        pass

    # the person for every Yes
    try:
        yes_rows = yes_db_rows(cfg, sheet, canon)
        targets = {slugify(r.get("company") or "") for r in yes_rows}
        if cfg.optional("hunter_apify_enrichment_token"):
            try:
                summary.append(f"enrich: {enrich_mod.enrich(cfg, targets)}")
            except Exception as e:
                summary.append(f"enrich failed, continuing: {e.__class__.__name__}: {e}")
        summary.append(f"warm paths: {bridges_mod.build_bridges(cfg, sheet)}")
        # The Hunt lane shows people to contact about a live role. A bridge
        # into a role he declined, applied to, or that no longer exists is a
        # person he has no reason to write to, so it goes.
        try:
            purged = bridges_mod.purge_orphan_bridges(cfg)
            counts["bridges_purged"] = purged["deleted"]
            summary.append(f"bridge purge: {purged}")
        except Exception as e:
            summary.append(f"bridge purge skipped: {e.__class__.__name__}: {e}")
        cleared = bridges_mod.clear_junk_warm_paths(cfg)
        if cleared:
            summary.append(f"cleared {cleared} placeholder warm path(s)")
        try:
            cold = bridges_mod.cold_targets(cfg, yes_rows)
            counts["cold_targets"] = cold.get("found", 0)
            summary.append(f"cold targets: {cold}")
        except Exception as e:
            summary.append(f"cold targets skipped: {e.__class__.__name__}: {e}")
        counts["warm_paths"] = write_warm_paths(cfg, sheet, canon, yes_rows)
        summary.append(f"warm path cells written: {counts['warm_paths']}")
    except Exception as e:
        summary.append(f"warm path pass failed: {e.__class__.__name__}: {e}")

    # The invariants run BEFORE the archive, because the repair that stamps a
    # duplicate row is what makes it a decided row the archive can then move.
    try:
        inv = invariants.enforce(sheet, canon.sheet_headers, apply=True,
                                 batches=funnel_batches(cfg, sheet, summary))
        counts["repairs"] = len(inv["repaired"])
        counts["still_broken"] = len(inv["still_broken"])
        summary.extend(inv["lines"])
    except Exception as e:
        summary.append(f"invariant pass failed: {e.__class__.__name__}: {e}")

    # decided rows leave, the rest sort
    try:
        moved, lines = archive_decided(sheet, canon)
        counts["archived"] = moved
        summary.append(f"archived {moved} decided row(s) to {config_mod.ARCHIVE_TAB}")
        for line in lines[:40]:
            summary.append(f"  {line}")
    except Exception as e:
        summary.append(f"archive failed: {e.__class__.__name__}: {e}")
    try:
        srt = sheet.sort_by_score()
        counts["sorted"] = srt["rows"]
        summary.append(f"Pipeline sorted: {srt['rows']} rows, "
                       f"{srt['blank_rows_removed']} blank row(s) removed")
    except Exception as e:
        summary.append(f"sort failed: {e.__class__.__name__}: {e}")

    # The shape he judges in, re-asserted every run so a dragged column or a
    # newly appended block cannot put the scroll back.
    try:
        lay = layout.apply_layout(sheet, sheet_id=config_mod.PIPELINE_SHEET_ID)
        summary.append(f"layout: {lay['visible']} columns visible, "
                       f"{lay['hidden']} hidden, {lay['width_after']}px wide"
                       + (" (unchanged)" if lay.get("noop") else ""))
    except Exception as e:
        summary.append(f"layout pass failed: {e.__class__.__name__}: {e}")

    # From here on, any difference between the sheet and this record is his.
    try:
        counts["snapshotted"] = snapshot_step(cfg, canon, sheet)
    except Exception as e:
        summary.append(f"snapshot skipped: {e.__class__.__name__}: {e}")

    if out:
        summary.extend(learning_report_lines(out, g12_hits=ledger.company_blocked))
    return counts


def _process_line(counts: dict) -> str:
    return (f"{counts.get('built', 0)} built ({counts.get('unverified', 0)} unverified), "
            f"{counts.get('dead', 0)} dead, {counts.get('blocked', 0)} blocked, "
            f"{counts.get('warm_paths', 0)} warm paths, "
            f"{counts.get('archived', 0)} archived")


def cmd_process(max_packages: int = 0, retry_dead: bool = False) -> int:
    summary: Summary = Summary([f"hunter process {TODAY()}"])
    failed = False
    started_at = datetime.datetime.now(datetime.timezone.utc)
    counts: dict = {}
    run_error: str | None = None
    try:
        cfg, canon = build_context()
        may, lines = preflight.gate(preflight.run(cfg))
        summary.extend(lines)
        if not may:
            raise RuntimeError("preflight failed; no credential work was attempted")
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        counts = process_step(cfg, canon, sheet, summary,
                              max_packages=max_packages, retry_dead=retry_dead)
        counts["recorded"] = counts.get("reconciled", 0)
        summary.append(_process_line(counts))
    except Exception as e:
        summary.append(f"PROCESS ABORTED: {e.__class__.__name__}: {e}")
        run_error = f"{e.__class__.__name__}: {e}"
        failed = True
    finally:
        try:
            cfg2 = load()
            report_run(cfg2, started_at=started_at, ok=not failed, counts=counts,
                       spend_usd=0.0, summary_line=_process_line(counts) if not failed
                       else "process failed", error=run_error)
        except Exception as e:
            print(f"run reporting failed: {e}")
        try:
            cfg2 = load()
            send_summary(cfg2, "\n".join(summary))
        except Exception as e:
            print(f"notify failed: {e}")
            print("\n".join(summary))
    return 1 if failed else 0


def cmd_migrate_columns(apply: bool = False) -> int:
    """Pipeline first, then the Applied tab, both to the 30-column layout."""
    cfg = load()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    r1 = sheet.migrate_columns(tab=sheet_mod.TAB, sheet_id=config_mod.PIPELINE_SHEET_ID,
                               apply=apply)
    print(r1)
    if apply and not (r1.get("verified") or r1.get("noop")):
        print("Pipeline migration did not verify; the Applied tab is untouched")
        return 1
    r2 = sheet.migrate_columns(tab=config_mod.ARCHIVE_TAB,
                               sheet_id=config_mod.ARCHIVE_SHEET_ID,
                               trailing=sheet_mod.ARCHIVE_TRAILING, apply=apply)
    print(r2)
    if not apply:
        print("\ndry run. add --apply to move the columns on both tabs")
        return 0
    canon = load_canon(cfg)
    live = sheet.read_pipeline(canon.sheet_headers)
    arch = sheet.read_archive()
    print(f"\nverified: Pipeline reads {len(live)} rows on the new layout, "
          f"{config_mod.ARCHIVE_TAB} reads {len(arch)}")
    return 0


def cmd_run() -> int:
    summary: Summary = Summary([f"hunter run {TODAY()}"])
    failed = False
    started_at = datetime.datetime.now(datetime.timezone.utc)
    counts: dict = {}
    run_error: str | None = None
    try:
        cfg, canon = build_context()
        may, lines = preflight.gate(preflight.run(cfg, for_sourcing=True))
        summary.extend(lines)
        if not may:
            raise RuntimeError("preflight failed; no paid call was attempted")
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)

        # Krish's verdicts first: reconcile, learn, build every Yes, find
        # the person, archive the decided, sort. Then source into a tidy
        # sheet, with this run's company declines already in force.
        pcounts = process_step(cfg, canon, sheet, summary)
        declines, _ = stored_company_declines(cfg)

        counts = source_and_stage(cfg, canon, sheet, summary, company_declines=declines)
        summary.extend(newsletter_movers_lines(cfg, sheet, canon.sheet_headers))
        for k in ("reconciled", "dead", "unverified", "archived", "warm_paths",
                  "cold_targets", "g12_blocked"):
            counts[k] = pcounts.get(k, 0)
        try:
            srt = sheet.sort_by_score()
            summary.append(f"Pipeline sorted after staging: {srt['rows']} rows")
        except Exception as e:
            summary.append(f"sort after staging failed: {e.__class__.__name__}: {e}")
        summary.append(
            f"sourced: {counts['discovered']} discovered, {counts['senior']} senior, "
            f"{counts['fresh']} fresh, {counts['recorded']} recorded, "
            f"{counts['staged']} staged to the sheet, "
            f"{counts.get('from_description', 0)} gated from the sweep's own description, "
            f"{counts['unresolved']} unresolved (never reach the sheet), "
            f"apify spend ${counts['spend_usd']:.2f}")
        if counts["recorded"] == 0 and not ledger.sheet_to_db:
            summary.append("FAILED: a run that reads roles and writes no rows has "
                           "failed even with a good summary")
            failed = True

        summary.append(f"packages built this run: {pcounts.get('built', 0)}")
        counts["built"] = pcounts.get("built", 0)

        # The one human step in the loop, and the only thing that makes it a
        # loop: tell him there is something to review. Nothing said this until
        # 2026-09-19, so roles sat unjudged because nobody knew they existed.
        try:
            fresh = sheet.read_pipeline(canon.sheet_headers)
            notes = []
            if pcounts.get("still_broken"):
                notes.append(f"{pcounts['still_broken']} sheet problem(s) hunter "
                             f"could not repair on its own; see the run log.")
            if pcounts.get("amendments"):
                notes.append(f"{pcounts['amendments']} edit(s) of yours were read "
                             f"as corrections and fed back into the scorer.")
            out = alerts.send_review_ready(cfg, fresh, counts.get("staged", 0),
                                           batches=funnel_batches(cfg, sheet, summary),
                                           notes=notes)
            summary.append(f"review email: {out}")
        except Exception as e:
            summary.append(f"review email failed: {e.__class__.__name__}: {e}")
        try:
            summary.append(f"watchdog: {alerts.send_trouble(cfg)}")
        except Exception as e:
            summary.append(f"watchdog failed: {e.__class__.__name__}: {e}")
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


def cmd_bank_check() -> int:
    """Read only. What can the answer tabs already answer, and what still blocks?

    Krish's ruling 2026-09-13: the answers live in the sheet, in the three tabs
    that already exist, not in system_config and not in a new tab. This command
    reports their real state, including the superseded master doc IDs that canon
    9.9 says must not linger anywhere as if they were current.
    """
    from .apply.infobank import INFO_TAB, INTERVIEW_TAB, PROFILE_TAB, load_bank
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)

    def read_tab(tab: str) -> list[list]:
        return sheet.read_tab_formulas(f"{tab}!A1:Z400")

    bank = load_bank(read_tab)
    print(f"answer bank: {len(bank.entries)} rows in {INFO_TAB!r}, "
          f"{len(bank.profile)} facts in {PROFILE_TAB!r}, "
          f"{len(bank.interview)} answers in {INTERVIEW_TAB!r}")
    print(f"voice rules: {len(bank.banned_phrases)} banned phrases, "
          f"{len(bank.positioning_rules)} positioning rules, "
          f"{len(bank.why_company_slots)} why-this-company slots")

    blocking = bank.blocking
    print(f"\nstill empty and declared as needed: {len(blocking)}")
    for e in sorted(blocking, key=lambda x: (x.section, x.field_name)):
        note = f"  ({e.notes})" if e.notes else ""
        print(f"  {e.section} {e.field_name}{note}")

    dashes = bank.em_dash_cells
    print(f"\ncells whose stored value carries an em dash: {len(dashes)}")
    for e in sorted(dashes, key=lambda x: x.field_name):
        print(f"  {e.field_name}")
        print(f"    stored:    {e.raw_value[:90]}")
        print(f"    used as:   {e.value[:90]}")
    if dashes:
        print("  hunter substitutes on read so an application is never blocked; "
              "fix the cells to stop the substitution.")

    sens = bank.sensitive
    print(f"\nnever auto filled, Krish's to give: {len(sens)}")
    for e in sorted(sens, key=lambda x: x.field_name):
        print(f"  {e.field_name}: {e.value or '(empty)'}")

    # Canon 9.9: a superseded artifact ID must not linger anywhere as current.
    current = {config_mod.CV_MASTER_ID, config_mod.LETTER_MASTER_ID}
    stale = []
    for key, entry in bank.entries.items():
        for doc_id in re.findall(r"[-\w]{25,}", entry.value):
            if len(doc_id) >= 25 and doc_id not in current and (
                    "doc" in entry.field_name.lower()
                    or "cv" in entry.field_name.lower()
                    or "letter" in entry.field_name.lower()
                    or "template" in entry.field_name.lower()):
                stale.append((entry.field_name, doc_id))
    print(f"\nmaster doc pointers that are not canon 9.9 current: {len(stale)}")
    for name, doc_id in stale:
        print(f"  {name}: {doc_id}")
    if stale:
        print(f"  canon 9.9 current: CV {config_mod.CV_MASTER_ID}, "
              f"letter {config_mod.LETTER_MASTER_ID}")
    return 0


def cmd_audit_forms(apply: bool = False, limit: int = 0) -> int:
    """Read the real application form for every Yes row and derive the six
    form-audit columns. Dry run by default; --apply writes the six cells.

    Never touches column A, Application Status or Applied Date. Nothing here
    contacts a company: it reads the same public form contract a candidate sees
    before typing anything.
    """
    from .apply import audit as audit_mod
    from .apply.fetch import account_required, form_for
    from .apply.infobank import load_bank
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)

    def read_tab(tab: str) -> list[list]:
        return sheet.read_tab_formulas(f"{tab}!A1:Z400")

    bank = load_bank(read_tab)
    rows = sheet.read_pipeline(canon.sheet_headers)
    todo = [r for r in rows if verdicts.parse(r.verdict)[0] == "go"]
    if limit:
        todo = todo[:limit]
    print(f"auditing {len(todo)} Yes rows against {len(bank.entries)} stored answers"
          f"{'' if apply else ' (dry run, pass --apply to write)'}\n")

    buckets: dict[str, list[str]] = {}
    wrote = 0
    for r in todo:
        company = r.company or r.cell("Business") or ""
        url = r.jd_url or ""
        try:
            spec = form_for(url, ats_key)
        except Exception as e:
            print(f"  {company[:24]:<26} form read failed: {e.__class__.__name__}: {e}")
            continue
        result = audit_mod.audit(
            spec, bank, account_required=account_required(spec),
            role_location=r.cell("Location"))
        buckets.setdefault(result.autonomy_score, []).append(company)
        print(f"  {company[:24]:<26} {result.application_format:<15} "
              f"{result.form_complexity:<8} {result.autonomy_score:<18} "
              f"{result.additional_questions}")
        for item in result.unresolved:
            print(f"      unresolved: {item}")
        for item in result.flagged:
            print(f"      flagged:    {item}")
        if apply:
            sheet.update_form_audit(r.row_number, result.cells())
            wrote += 1

    print("\nby autonomy:")
    for score in audit_mod.AUTONOMIES:
        names = buckets.get(score) or []
        if names:
            print(f"  {score:<18} {len(names):>2}  {', '.join(sorted(names))}")
    if apply:
        print(f"\nwrote the six form-audit columns on {wrote} rows")
    return 0


def _answer_bank(sheet: Sheet):
    from .apply.infobank import load_bank
    return load_bank(lambda tab: sheet.read_tab_formulas(f"{tab}!A1:Z400"))


def cmd_bank_seed(apply: bool = False) -> int:
    """Write the answers Krish gave in conversation into the Application Info
    Bank, and correct the two superseded master doc pointers canon 9.9 forbids.

    Dry run by default. Every value carries the ruling that produced it, row
    numbers are resolved by matching the field name rather than hard coded, and
    every cell is read back and asserted after writing.
    """
    from .apply import bankseed
    from .apply.infobank import INFO_TAB
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = sheet.read_tab_formulas(f"'{INFO_TAB}'!A1:D200")
    changes, problems = bankseed.plan_changes(rows)

    print(f"{INFO_TAB}: {len(rows)} rows read, {len(changes)} cell(s) to write"
          f"{'' if apply else ' (dry run, pass --apply to write)'}\n")
    for c in sorted(changes, key=lambda x: (x.row, x.column)):
        tag = "NEW ROW" if c.is_new else "       "
        print(f"  {tag} {c.column}{c.row:<4} {c.field_name[:46]}")
        if c.before:
            print(f"          was:  {c.before[:88]}")
        print(f"          now:  {c.after[:88]}")
        if c.ruling:
            print(f"          why:  {c.ruling}")
    if problems:
        print("\nnot written:")
        for p in problems:
            print("  " + p)
    if apply:
        n = bankseed.apply_changes(sheet, changes)
        print(f"\nwrote and verified {n} cell(s)")
        print("re-run: python -m hunter.run simulate")
    return 0


def build_fill_plan(cfg: Config, sheet: Sheet, row: dict, *,
                    bank=None, summary: str = "", hook: str = "",
                    draft_essays: bool = True):
    """(FillPlan, Audit) for one approved role. No network writes, no mail.

    Shared by approvals and, later, submit, so the plan the email shows and the
    plan the browser fills are produced by the same code. A second implementation
    is how the plan hash stops meaning anything.
    """
    from .apply import audit as audit_mod
    from .apply import fetch, fill
    from .ats import discover as disc
    role, _relink, _flags = resolve_for_build(cfg, row, disc.load_cache(cfg))
    bank = bank if bank is not None else _answer_bank(sheet)
    spec = fetch.form_for(role.jd_url, ats_key)
    au = audit_mod.audit(spec, bank, role_location=role.location)
    plan = fill.build_payload(spec, bank, company=role.company, role=role.title,
                              jd_url=role.jd_url, role_location=role.location,
                              summary=summary, hook=hook,
                              attachment_style=au.attachment_style)
    # The open questions. FillPlan has carried an `essays` slot since it was
    # written and nothing ever filled it, so "What makes you excited about the
    # ElevenLabs mission?" came back blank on every form since the first
    # application. Drafted from the same evidence the cover letter is traced
    # against and put through the same voice gate, then flagged, because these
    # are the sentences most likely to need his judgement.
    if draft_essays:
        from .apply.resolve import NEEDS_ESSAY
        questions = [f.label for f in plan.fields
                     if f.unresolved
                     and (f.reason or "").startswith(NEEDS_ESSAY)]
        if questions:
            notes: list[str] = []
            drafted = _draft_essays(cfg, sheet, role, questions, notes)
            for line in notes:
                plan.notes.append(line)
            if drafted:
                plan = fill.build_payload(
                    spec, bank, company=role.company, role=role.title,
                    jd_url=role.jd_url, role_location=role.location,
                    summary=summary, hook=hook, essays=drafted,
                    attachment_style=au.attachment_style)
                plan.notes.extend(notes)
    return plan, au, role


def _draft_essays(cfg: Config, sheet: Sheet, role, questions: list[str],
                  notes: list[str]) -> dict[str, str]:
    """Answers to the open questions, or nothing and a reason for each."""
    from .apply import essays as essays_mod, gtmseed
    from .apply.infobank import load_bank
    from .docbuild import DocBuild
    from .package.build import read_master_facts
    from .package.voicegate import build_evidence
    try:
        db = DocBuild(GoogleOAuth(cfg).access_token())
        facts = read_master_facts(db)
        bank = load_bank(lambda tab: sheet.read_tab_formulas(f"{tab}!A1:Z400"))
        evidence = build_evidence(
            facts.cv_master_text,
            "\n".join(bank.profile.values()),
            "\n".join(bank.interview.values()),
            "\n".join(e.value for e in bank.entries.values()),
            cfg.optional("hunter_ai_gtm_evidence") or "",
            role.jd_text or "")
        banned = tuple(bank.banned_phrases) + gtmseed.NAME_VARIANTS_BANNED
    except Exception as e:
        notes.append(f"NOT drafted: evidence unreadable "
                     f"({e.__class__.__name__}: {str(e)[:120]})")
        return {}
    return essays_mod.draft_all(cfg, questions, company=role.company,
                                role=role.title, jd_text=role.jd_text or "",
                                evidence=evidence, banned_phrases=banned,
                                notes=notes)


def hunt_url_for(cfg: Config, job_id: str) -> str:
    """A link that OPENS Control Center on this role.

    Never a link that submits. Mail scanners and link preview bots issue GET
    requests to every URL in an email, so a one-click send URL can be pressed by a
    robot before Krish has read the message. The app is authenticated and the send
    button lives there.
    """
    base = (cfg.optional("control_center_url") or "").rstrip("/")
    if not base:
        return ""
    from urllib.parse import quote
    return f"{base}/#/people?lane=bridges&job={quote(job_id)}"


def posting_key(row: dict) -> str:
    """One posting, however many role rows point at it.

    The employer's form is what is being applied to, so its URL is the identity
    that matters. job_id is not: the scheme changed between two sweeps and one
    Harvey posting ended up under `harvey:head-gtm-strategy-ops-amer` and
    `harvey:head-of-gtm-strategy-operations-amer`, which are two ids for one
    application.
    """
    url = (row.get("job_url") or row.get("url") or "").strip().lower()
    return url.split("#")[0].split("?")[0].rstrip("/")


def live_postings(cfg: Config) -> dict[str, tuple[str, str]]:
    """posting_key -> (token, state) for every approval still in play."""
    from .apply import approval
    states = (approval.AWAITING, approval.APPROVED, approval.SUBMITTED)
    live = db_get(cfg, approval.TABLE,
                  {"select": "token,state,job_id",
                   "state": f"in.({','.join(states)})", "limit": "500"})
    if not live:
        return {}
    ids = sorted({r["job_id"] for r in live if r.get("job_id")})
    rows = db_get(cfg, "hunter_seen_roles",
                  {"select": "job_id,job_url,url",
                   "job_id": f"in.({','.join(ids)})", "limit": "500"})
    by_id = {r["job_id"]: posting_key(r) for r in rows}
    out: dict[str, tuple[str, str]] = {}
    for r in live:
        key = by_id.get(r.get("job_id") or "")
        if key:
            out.setdefault(key, (r["token"], r["state"]))
    return out


# Forms hunter cannot enumerate without a signed in session, plus the
# catch-all for a URL no adapter matches. An unreadable form of these kinds
# says nothing about whether the posting is still open, so a role carrying one
# is never retired as dead. apply/fetch.py returns exactly these ats values.
CANNOT_ENUMERATE = frozenset({"linkedin", "google", "unknown"})


def cmd_approvals(apply: bool = False, job_id: str = "", prefill: bool = True) -> int:
    """Build and, with --apply, send one approval email per built package.

    Dry run by default: it prints the whole application as the email will show it,
    and mints no token, so nothing can be approved by accident. A token is recorded
    only when the mail is actually sent, because a token with no email behind it is
    a live approval Krish never saw.
    """
    from . import notify
    from .apply import approval, merge
    from .apply import submit as submit_mod
    from .docbuild import DocBuild
    from .package.build import doc_url, slugify

    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    params = {"select": "*", "package_status": "eq.built",
              # A row the dedupe already marked is not a second application, it
              # is the same one twice. It was marked and then ignored here, so
              # two Harvey emails went out for one posting.
              "status": "neq.duplicate",
              "order": "package_built_at.desc", "limit": "50"}
    if job_id:
        params["job_id"] = f"eq.{job_id}"
    rows = db_get(cfg, "hunter_seen_roles", params)
    if not rows:
        print("no built packages to send" + (f" for {job_id}" if job_id else ""))
        return 1

    # The sheet decides, not package_status. This query reads a database flag
    # and never re-read column A, so a role whose row has LEFT the Pipeline
    # still queued an approval email. On 2026-09-24 Krish had two rows removed
    # by name, Confidential and Strativ Group; both kept krish_verdict "Yes"
    # and package_status "built", and the next approvals run would have mailed
    # him an application to press for each of the two roles he had just had
    # deleted. select_for_build already treats the sheet as the authority
    # (router.py: "column A as it reads now, never a DB field") and this step
    # is the one place downstream of it that did not.
    #
    # A sheet hunter cannot read refuses the whole batch rather than mailing
    # it: the failure mode this prevents is sending, and sending cannot be
    # undone.
    srows = sheet.read_pipeline(canon.sheet_headers)
    paired, _, _, _ = match_rows(srows, list(rows))
    on_sheet = {d.get("job_id") for _, d in paired if d.get("job_id")}
    gone = [r for r in rows if r.get("job_id") not in on_sheet]
    if gone:
        for r in gone:
            print(f"skip {r['job_id']}: no longer on the Pipeline sheet "
                  f"({r.get('company')} / {r.get('title')})")
        rows = [r for r in rows if r.get("job_id") in on_sheet]
    if not rows:
        print("every built package belongs to a row that has left the sheet")
        return 1

    to = notify.mailbox(cfg)
    bank = _answer_bank(sheet)
    oauth = GoogleOAuth(cfg)
    db = DocBuild(oauth.access_token())
    sent = 0
    # One live approval per POSTING, not per row. The job_id scheme changed
    # between two sweeps, so one Harvey posting arrived under two ids; each got
    # its own token and Krish got the same application twice. The employer's form
    # is the thing being applied to, so its URL is what has to be unique.
    taken = live_postings(cfg)
    failed: list[str] = []
    for row in rows:
        key = posting_key(row)
        if key and key in taken:
            print(f"skip {row['job_id']}: {taken[key][1]} token already "
                  f"{taken[key][0]} for this same posting")
            continue
        # One posting must never take the batch with it. On 2026-09-20 a
        # single Ashby form carrying an EducationHistory block raised on row
        # 14 of 38 and the other 24 applications, already built and waiting,
        # were never sent. Whatever one form does, the rest still go, and
        # every failure is named at the end rather than ending the run.
        try:
            plan, au, role = build_fill_plan(cfg, sheet, row, bank=bank)
        except Exception as e:
            failed.append(f"{row['job_id']}: {e.__class__.__name__}: "
                          f"{str(e)[:160]}")
            print(f"  SKIPPED {row['job_id']}: {e.__class__.__name__}: {e}")
            continue
        token = approval.new_token(row["job_id"])
        if key:
            taken[key] = (token, "awaiting")
        attachments: list[tuple[str, bytes]] = []
        attachments_by_kind: dict[str, bytes] = {}
        attachment_names: dict[str, str] = {}
        merged_name = ""
        cv_id = (row.get("package_cv_url") or "").split("/d/")[-1].split("/")[0]
        letter_id = (row.get("package_letter_url") or "").split("/d/")[-1].split("/")[0]
        from .apply.audit import ATT_CV
        if cv_id and letter_id:
            cv_pdf, letter_pdf = db.export_pdf(cv_id), db.export_pdf(letter_id)
            if au.attachment_style == ATT_CV:
                # One upload slot on the form, so one file: canon's merge order is
                # the letter first, then the CV.
                merged_name = merge.merged_name(role.company)
                merged = merge.merge_pdfs(letter_pdf, cv_pdf)
                attachments.append((merged_name, merged))
                attachments_by_kind["file_resume"] = merged
                # The employer reads this filename off the form.
                attachment_names["file_resume"] = merged_name
            else:
                attachments_by_kind["file_resume"] = cv_pdf
                attachments_by_kind["file_cover"] = letter_pdf
                attachment_names["file_resume"] = "KrishRaja_CV.pdf"
                attachment_names["file_cover"] = "KrishRaja_CoverLetter.pdf"
        # Fill the real form and photograph it BEFORE asking. Approving the picture
        # is approving what gets submitted, because plan_hash covers the exact field
        # values, so asking twice bought nothing and cost him two more emails and up
        # to two more hours. Nothing is submitted here: submit() presses only with
        # confirm=True, which this call does not pass.
        shot_name, filled, missed = "", 0, ()
        form_notes: list[str] = []
        if prefill:
            pv = submit_mod.preview(plan, attachments=attachments_by_kind,
                                    names=attachment_names)
            filled, missed = len(pv["filled"]), tuple(pv["missed"])
            # What a control offered when nothing matched. Ashby's Location has no
            # "Brooklyn, New York", so without this the email says the field is
            # missed and he has to open the form himself to find out why.
            form_notes = list(pv.get("notes") or [])
            if pv["png"]:
                shot_name = f"form_{slugify(role.company)}.png"
                attachments.append((shot_name, pv["png"]))
            if pv["blocker"] or pv["error"]:
                print(f"  form not filled in advance: "
                      f"{pv['blocker'] or pv['error']}")

        # What the extension will put into the form. Built from the same plan and
        # the same documents the picture above was taken from, so what he sees in
        # the email and what lands in the form are one thing.
        from .apply import payload as payload_mod
        open_key = payload_mod.new_key()
        try:
            pay = payload_mod.build(plan, attachments=attachments_by_kind,
                                    names=attachment_names, bank=bank)
        except Exception as e:
            pay, open_key = None, ""
            print(f"  no fill payload for this one: {e}")
        open_url = (f"{pay['url']}#hunter={token}.{open_key}" if pay else "")

        email = approval.render(
            company=role.company, role=role.title, jd_url=role.jd_url,
            autonomy=au.autonomy_score, token=token, to=to,
            form_shot=shot_name, form_filled=filled, form_missed=missed,
            hunt_url=hunt_url_for(cfg, row["job_id"]),
            open_url=open_url,
            lines=plan.field_lines(), essays=plan.essays,
            summary=plan.summary, hook=plan.hook,
            cv_url=row.get("package_cv_url") or "",
            letter_url=row.get("package_letter_url") or "",
            cv_pdf_url=row.get("package_cv_pdf_url") or "",
            letter_pdf_url=row.get("package_letter_pdf_url") or "",
            merged_attachment=merged_name,
            # Not au.unresolved or au.flagged: render() already derives both
            # from `lines` and prints them above. Passing them again put every
            # flagged field in the email twice, which was invisible only while
            # the text part was dropping notes.
            notes=plan.notes + form_notes)

        print(f"\n{'=' * 72}\n{email.subject}\n{'=' * 72}")
        print(email.text)
        print(f"  fields: {len(plan.fields)}, blocking: {len(plan.blocking)}, "
              f"flagged: {len(plan.flagged)}, attachments: "
              f"{merged_name or 'two links'}")
        if not apply:
            print("  (dry run, no token minted. pass --apply to send)")
            continue
        if not plan.readable or not plan.fields:
            # A posting the board no longer serves is gone, not pending. Leaving
            # it as a built package means it comes back on every run and Krish
            # gets an approval email for a role nobody can apply to. Retire it
            # where it lives: the role row, and his sheet.
            #
            # But ONLY when the evidence is death. This branch used to retire
            # every unreadable form, and "unreadable" covers two different
            # facts. BOI (Board of Innovation) is a role he approved whose only
            # URL is a LinkedIn job view: fetch returns unreadable because
            # LinkedIn needs an authenticated session, not because the posting
            # has gone. Retiring it would have written "Declined - dead
            # posting" on his sheet about a job that is very likely still open,
            # which is the manufactured claim this whole repository exists to
            # avoid. account_required already drew the distinction and this
            # branch ignored it.
            why = plan.notes[0] if plan.notes else "the form could not be read"
            if plan.ats in CANNOT_ENUMERATE:
                print(f"  REFUSING to send: {why}")
                print(f"    the row stays: this is hunter unable to read the "
                      f"form, not evidence the posting has gone. Apply by hand "
                      f"at {plan.jd_url or row.get('url') or 'the posting'}")
                failed.append(f"{row['job_id']}: {plan.ats} form cannot be "
                              f"filled automatically, apply by hand")
                continue
            print(f"  REFUSING to send: {why}")
            if apply:
                retire_dead_posting(cfg, canon, sheet, row["job_id"],
                                    company=role.company, role=role.title, why=why)
            continue
        if not plan.ready:
            print(f"  REFUSING to send: {len(plan.blocking)} required field(s) "
                  f"have no answer: "
                  + ", ".join(f.label for f in plan.blocking))
            continue
        out = notify.send_email(cfg, email.subject, email.html, to=to,
                               text=email.text,
                               attachments=attachments or None)
        approval.record_sent(cfg, token=token, job_id=row["job_id"],
                             company=role.company, role=role.title,
                             fill_plan=plan.as_dict(),
                             message_id=out.get("id", ""))
        try:
            db_patch(cfg, approval.TABLE, {"token": token},
                     {"fill_payload": pay, "open_key": open_key})
        except Exception as e:
            print(f"  could not store the fill payload: {e}")
        sent += 1
        print(f"  sent to {to}, token {token}, "
              f"plan_hash {approval.plan_hash(plan.as_dict())}")
    if apply:
        print(f"\n{sent} approval email(s) sent")
    if failed:
        print(f"\n{len(failed)} posting(s) could not be prepared, and the rest "
              f"were sent anyway:")
        for line in failed:
            print(f"  {line}")
    # A failure that stopped some applications reaching him is a failing run,
    # but only after every one that could be sent has been.
    return 1 if failed else 0


def cmd_approvals_drain(apply: bool = False, send: bool = False) -> int:
    """Read Krish's replies and act on each one. Dry run by default.

    Three outcomes, and every reply gets exactly one:
      approve  the token moves to approved, and with --send the application is
               submitted in the same pass. He approved a picture of the completed
               form, so the approval covers the send; asking a second time cost two
               more emails and up to two more hours and bought nothing. The gate
               inside submit still runs and still refuses on a changed plan hash
      amend    the old token is superseded so a stale APPROVE cannot land later,
               the package is rebuilt with his words passed through verbatim, and
               a fresh approval email goes out with a new token
      skip     already processed, hunter's own outbound, or no row behind the token

    An amend that fails to rebuild leaves the old token superseded and says so
    loudly, rather than quietly leaving him with a live token for a package that no
    longer matches.
    """
    from .apply import approval, inbox
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    try:
        replies = inbox.fetch_replies(cfg)
    except inbox.InboxError as e:
        print(str(e))
        return 1
    print(f"{len(replies)} message(s) carrying a token"
          f"{'' if apply else ' (dry run, pass --apply to act)'}\n")
    acted = 0
    for r in replies:
        row = approval.get_row(cfg, r["token"]) or {}
        action, detail = inbox.classify(r, row)
        print(f"{action.upper():<8} {r['token']}")
        if detail:
            print(f"         {detail[:300]}")
        if action == "skip":
            continue
        if action == "reject":
            if apply:
                approval.mark_processed(cfg, r["token"], r["message_id"],
                                        row.get("processed_message_ids"))
            continue
        if not apply:
            continue

        if action == "approve":
            approval.set_state(cfg, r["token"], approval.APPROVED,
                               decided_at=NOW())
            approval.mark_processed(cfg, r["token"], r["message_id"],
                                    row.get("processed_message_ids"))
            acted += 1
            if not send:
                print(f"         approved, not sent. To send: "
                      f"python -m hunter.run submit --token {r['token']} --confirm")
                continue
            # He approved a picture of the completed form, so the approval covers
            # the send: asking again bought nothing and cost two more emails. The
            # gate still runs inside submit and still refuses on a changed plan hash.
            rc = cmd_submit(r["token"], confirm=True)
            print(f"         {'submitted' if rc == 0 else 'not submitted, see above'}")
            continue

        # amend. The old token dies first: if the rebuild fails, the worst case is
        # no live token, never a live token for the wrong package.
        approval.supersede(cfg, r["token"])
        approval.mark_processed(cfg, r["token"], r["message_id"],
                                row.get("processed_message_ids"))
        db_patch(cfg, approval.TABLE, {"token": r["token"]}, {"feedback": detail})
        job_id = row.get("job_id") or ""
        rows = db_get(cfg, "hunter_seen_roles",
                      {"select": "*", "job_id": f"eq.{job_id}", "limit": "1"})
        if not rows:
            print(f"         SUPERSEDED but cannot rebuild: no role row for {job_id}")
            continue
        summary: Summary = Summary()
        ok = build_one(cfg, canon, sheet, rows[0], summary, feedback=detail)
        for line in summary:
            print("         " + line)
        if not ok:
            print("         SUPERSEDED and the rebuild failed. No live token; "
                  "fix the build and re-run approvals.")
            continue
        rc = cmd_approvals(apply=True, job_id=job_id)
        acted += 1
        if rc:
            print("         rebuilt, but the new approval email did not send")
    if apply:
        print(f"\n{acted} reply(ies) acted on")
    report_stalled_approvals(cfg, apply=apply)
    return 0


def report_stalled_approvals(cfg: Config, *, apply: bool = False) -> list[str]:
    """Name every approval the machine should have moved on and did not.

    The class of failure behind Krish's "no feedback at all": he replied APPROVE,
    nothing read it, and nothing anywhere noticed that nothing had happened. A
    queue with no alarm on it is a queue that stops quietly.

    Rows still AWAITING are not a fault, since they are waiting on him. Rows
    APPROVED or AMENDING are hunter's own work in flight, and a drain that runs
    every hour should clear them within minutes of the decision.
    """
    from .apply import approval as ap
    stuck: list[str] = []
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=2)).isoformat()
    for state in (ap.APPROVED, ap.AMENDING):
        for r in db_get(cfg, ap.TABLE,
                        {"select": "token,company,role,state,decided_at,sent_at",
                         "state": f"eq.{state}",
                         "order": "sent_at.asc", "limit": "50"}):
            when = (r.get("decided_at") or r.get("sent_at") or "")
            if when and when > cutoff:
                continue
            stuck.append(f"{r['state']} since {when[:19] or 'unknown'}: "
                         f"{r.get('company')} {r.get('role')} [{r['token']}]")
    if not stuck:
        return stuck
    print("\nSTALLED, the machine should have moved these on:")
    for line in stuck:
        print(f"  {line}")
    if apply:
        try:
            from . import notify
            html = ("<div style='display:none'>[hunter-outbound]</div>"
                    "<h2 style='font-size:19px;margin:0 0 10px'>Stalled approvals</h2>"
                    "<p style='color:#555'>Decided, and not acted on within the "
                    "hour. Something in the chain is not running.</p><ul>"
                    + "".join(f"<li>{l}</li>" for l in stuck) + "</ul>")
            notify.send_email(cfg, f"Stalled: {len(stuck)} approval(s)", html,
                              to=notify.mailbox(cfg),
                              text="\n".join(["[hunter-outbound]",
                                              "Stalled approvals"] + stuck))
        except Exception as e:
            print(f"  stall notice email FAILED: {e}")
    return stuck


class _SheetNotWritten(Exception):
    """The sheet write did not land, so nothing may record the role as done.

    Not an error anyone handles: it exists so the ledger patch is skipped by the
    same control flow that reports the failure, rather than by a second condition
    that could drift away from the first one.
    """


def already_archived(sheet, company: str, role: str) -> int:
    """The row number on the Applied tab for this company and role, or 0.

    A role that has already been moved to Applied is finished, and looking for
    it on Pipeline finds nothing. Without this, the ledger row stayed open and
    retried against a row that no longer exists, for ever, which is what
    happened to Mutiny on 2026-09-24.
    """
    want = ((company or "").strip().lower(), (role or "").strip().lower())
    if not any(want):
        return 0
    try:
        grid = sheet.read_tab_values(
            f"{config_mod.ARCHIVE_TAB}!A1:{sheet_mod.ARCHIVE_LAST_COL}2000")
    except Exception:
        return 0
    b, r = sheet_mod.COLS["Business"], sheet_mod.COLS["Role"]
    for i, cells in enumerate(grid or [], start=1):
        if len(cells) <= max(b, r):
            continue
        if ((cells[b] or "").strip().lower(),
                (cells[r] or "").strip().lower()) == want:
            return i
    return 0


def record_applied(cfg: Config, canon, sheet: Sheet, job_id: str, *,
                   company: str, role: str, screenshot: str = "",
                   confirmation: str = "", after_png: bytes = b"",
                   archive: bool = True, receipt: bool = True,
                   summary: list[str] | None = None) -> bool:
    """Everything that has to be true once an application is actually sent.

    Four writes and a message, none of which existed. A submission reached the
    employer, the approval ledger recorded it, and nothing else moved: the
    Pipeline row went on reading "Not applied" with no date, the role row in
    Supabase kept a null application_state, the row never left the Pipeline tab,
    and Krish was told nothing at all. He asked for confirmation and there was
    none to give, because the run printed into a GitHub Actions log and stopped.

    Deliberately best effort per step and loud about each failure: a sent
    application must never be un-sent by a bookkeeping error, and a step that
    fails silently here puts us straight back where we started.
    """
    note = summary if summary is not None else []
    today = datetime.date.today().isoformat()

    # The sheet carries no job_id, so the row is found by company and role. It
    # must match exactly one: Harvey alone has six roles on these tabs and
    # stamping the wrong one "Applied" is worse than stamping none.
    rows = sheet.read_pipeline(canon.sheet_headers)
    want = ((company or "").strip().lower(), (role or "").strip().lower())
    hits = [r for r in rows
            if ((r.company or "").strip().lower(), (r.role or "").strip().lower()) == want]
    target = hits[0] if len(hits) == 1 else None
    # Whether the sheet actually took it. Nothing below may latch on a write that
    # did not happen: the ledger patch used to run either way, and
    # cmd_close_submitted skips any row already carrying an applied state, so one
    # failed match meant the Pipeline row read "Not applied" for ever on an
    # application that was sent. That is the exact condition this function exists
    # to remove, so a failure has to leave the work retriable.
    wrote_sheet = False
    if target is None and not hits:
        # Already on the Applied tab is not a failure, it is the finished state.
        # The archive pass moves every decided row, so a role written earlier in
        # the same batch, or under a second job id for the same posting, has no
        # Pipeline row left to find. Treating that as a miss left the ledger open
        # and retrying against a row that will never come back.
        arch = already_archived(sheet, company, role)
        if arch:
            note.append(f"already on the {config_mod.ARCHIVE_TAB} tab at row "
                        f"{arch}; nothing left to write")
            wrote_sheet = True
    if target is None and not wrote_sheet:
        note.append(f"{len(hits)} Pipeline rows match {company} / {role}; "
                    f"sheet NOT updated and the ledger left open so the next run "
                    f"retries. Fix the company or role text, or do the row by hand")
    elif target is not None:
        rn = target.row_number
        failures = 0
        for what, fn in (
                ("Application Status and Applied Date",
                 lambda: sheet.mark_applied(rn, when=today)),
                ("column A verdict",
                 lambda: sheet.set_verdicts({rn: verdicts.APPLIED}))):
            try:
                fn()
                note.append(f"sheet row {rn}: wrote {what}")
            except Exception as e:
                failures += 1
                note.append(f"sheet row {rn}: {what} FAILED: {e}")
        wrote_sheet = failures == 0

    if not wrote_sheet:
        # Say it once more, at the end, where he reads it.
        note.append("NOT marked done in Supabase, so close-submitted will try "
                    "this role again on the next hourly run")
    try:
        if not wrote_sheet:
            raise _SheetNotWritten()
        # "Applied", not "submitted". Two writers of this one column disagreed:
        # this one wrote "submitted" and sync_applied_state writes "Applied" from
        # the sheet's own Verdict vocabulary. cmd_close_submitted skips a row only
        # when it reads "submitted", so the next full pass relabelled it,
        # close-submitted stopped recognising it as done, and record_applied ran
        # again: a duplicate Submitted receipt for every applied role, every hour,
        # for ever. Control Center also prints this column straight into the Hunt
        # lane (DesktopBridges), where "Applied" is the word and "submitted" was a
        # lowercase oddity beside it.
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id},
                 {"application_state": APPLIED_STATE, "applied_at": NOW()})
        note.append(f"hunter_seen_roles: application_state {APPLIED_STATE}")
    except _SheetNotWritten:
        pass
    except Exception as e:
        note.append(f"hunter_seen_roles patch FAILED: {e}")

    # Off Pipeline and onto Applied, which is cmd_archive's job and was only ever
    # run by hand against a column A value nothing set.
    #
    # A caller writing several roles at once passes archive=False and runs the
    # pass once itself. Archiving between roles moved rows out from under the
    # roles still to be written, and each of those then matched nothing.
    if archive:
        try:
            cmd_archive(apply=True)
            note.append("ran the archive pass: decided rows moved to the Applied tab")
        except Exception as e:
            note.append(f"archive to the Applied tab FAILED: {e}")

    # One role, one receipt. A caller recording several passes receipt=False and
    # sends a single digest instead: eight roles marked in one go put eight
    # separate emails in his inbox, and his answer was "you dont need to send
    # tons and tons of emails for every single application complete".
    if receipt:
        try:
            send_applied_receipt(cfg, company=company, role=role, when=today,
                                 screenshot=screenshot, notes=note,
                                 confirmation=confirmation, after_png=after_png)
            note.append("receipt emailed")
        except Exception as e:
            note.append(f"receipt email FAILED: {e}")
    for line in note:
        print(f"  {line}")
    return wrote_sheet


def send_applied_receipt(cfg: Config, *, company: str, role: str, when: str,
                         screenshot: str = "", notes: list[str] | None = None,
                         confirmation: str = "", after_png: bytes = b"") -> None:
    """Tell Krish it went, and be honest about how well that is known.

    His question, and the right one: "usually when I apply I get an email from
    the company saying thanks for applying". The button having been pressed
    without raising is not the same as the employer having the application, and
    the first version of this receipt asserted the second while only knowing the
    first. So the receipt now quotes what the form itself said, attaches a
    picture of the page AFTER the press, and says plainly when there was no
    acknowledgement to quote.
    """
    from . import notify
    from .apply.approval import _esc
    lines = "".join(f"<li>{_esc(n)}</li>" for n in (notes or []))
    shot = (f"<p><a href=\"{_esc(screenshot)}\">the form as it was submitted</a></p>"
            if screenshot else "")
    if confirmation:
        # Escaped, because this is no longer hunter's own words. The browser
        # extension sends what the employer's page said, so the bytes come from
        # a page hunter does not control, and this receipt is an email Krish
        # trusts as hunter's own. Unescaped, a page could put a link or a second
        # Submit button inside it.
        proof = (f"<p style='border-left:3px solid #1a7f37;background:#f2fbf4;"
                 f"padding:10px 12px;margin:0 0 14px'>The form acknowledged it: "
                 f"<strong>{_esc(confirmation)}</strong></p>")
    else:
        proof = ("<p style='border-left:3px solid #c47f00;background:#fffbf0;"
                 "padding:10px 12px;margin:0 0 14px'><strong>Pressed, but the "
                 "form showed no confirmation.</strong> The attached picture is "
                 "the page straight after the click. Nothing has been retried: a "
                 "second submission is worse than an unconfirmed one.</p>")
    html = (f"<div style=\"font:15px/1.55 -apple-system,BlinkMacSystemFont,"
            f"'Segoe UI',system-ui,sans-serif;color:#111;max-width:680px\">"
            f"<div style='display:none'>[hunter-outbound]</div>"
            f"<h2 style='margin:0 0 2px;font-size:19px'>Submitted</h2>"
            f"<div style='color:#555;margin-bottom:18px'>{role} at {company}"
            f" &middot; {when}</div>{proof}{shot}"
            f"<p style='color:#555'>The sheet and the ledger were updated:</p>"
            f"<ul style='color:#555'>{lines}</ul></div>")
    text = "\n".join(
        ["[hunter-outbound]", f"Submitted: {role} at {company} on {when}",
         (f"The form acknowledged it: {confirmation}" if confirmation else
          "Pressed, but the form showed no confirmation. See the attached "
          "picture of the page straight after the click. Nothing retried.")]
        + ([f"Form: {screenshot}"] if screenshot else [])
        + [f"  {n}" for n in (notes or [])])
    attachments = ([(f"after_submit_{slugify(company)}.png", after_png)]
                   if after_png else None)
    subject = (f"Submitted: {company} {role}" if confirmation
               else f"Submitted (UNCONFIRMED): {company} {role}")
    notify.send_email(cfg, subject, html, to=notify.mailbox(cfg), text=text,
                      attachments=attachments)


def send_applied_digest(cfg: Config, rows: list[tuple[str, str, str]],
                        *, when: str) -> None:
    """One email for a batch, instead of one per role.

    Krish, 2026-09-24, looking at five copies of the same receipt: "you dont
    need to send tons and tons of emails for every single application
    complete". The duplicates were a defect and are fixed at the source; the
    volume was not a defect, it was the design, and the design was wrong.

    Keeps the distinction the single receipt exists for. A role whose form
    acknowledged it is quoted; a role recorded on his own say so says that, in
    those words, and is never dressed up as the form speaking.
    """
    from . import notify
    from .apply.approval import _esc
    items = []
    for company, role, confirmation in rows:
        said = (f"<span style='color:#1a7f37'>{_esc(confirmation)}</span>"
                if confirmation else
                "<span style='color:#c47f00'>no confirmation recorded</span>")
        items.append(f"<li><strong>{_esc(company)}</strong> {_esc(role)}"
                     f"<br><span style='font-size:13px'>{said}</span></li>")
    html = (f"<div style=\"font:15px/1.55 -apple-system,BlinkMacSystemFont,"
            f"'Segoe UI',system-ui,sans-serif;color:#111;max-width:680px\">"
            f"<div style='display:none'>[hunter-outbound]</div>"
            f"<h2 style='margin:0 0 2px;font-size:19px'>"
            f"{len(rows)} application(s) recorded</h2>"
            f"<div style='color:#555;margin-bottom:18px'>{when}</div>"
            f"<ul style='color:#333'>{''.join(items)}</ul>"
            f"<p style='color:#555'>Each one is marked Applied on the sheet "
            f"with today's date and has moved to the Applied tab.</p></div>")
    text = "\n".join(
        ["[hunter-outbound]", f"{len(rows)} application(s) recorded on {when}", ""]
        + [f"  {c} {r} ({conf or 'no confirmation recorded'})"
           for c, r, conf in rows])
    notify.send_email(cfg, f"Submitted: {len(rows)} application(s)", html,
                      to=notify.mailbox(cfg), text=text)


def disqualified(cscore) -> bool:
    """Does this company carry the scorer's own disqualifier penalty.

    EXCEPTIONAL_MERIT exists for Krish's ruling on Citi, 2026-09-20: "I'd
    reject that company unless the role was ideal, which that one was". That
    reasoning needs the company to BE the employer. A recruitment agency is
    not: the seat belongs to somebody else, the agency's score says nothing
    about the workplace, and an ideal role advertised by an agency is an ideal
    role somewhere unknown.

    Strativ Group, 2026-09-24, scored 0.0 with "staffing (Recruitment Agency)
    (-5)" and staged anyway on merit.

    Read off the scorer's own components rather than matching the word
    "staffing", so this follows company.py instead of drifting from it.
    """
    from .company import DISQUALIFIER_PENALTY
    for c in getattr(cscore, "components", None) or []:
        if getattr(c, "name", "") == "disqualifier" and getattr(c, "evidenced", False):
            return c.points <= DISQUALIFIER_PENALTY
    return False


# The same supply leg has reached the sheet under four different labels:
# "apify_linkedin", "Apify LinkedIn", "Apify LinkedIn (2026-09-10 sweep)" and
# "linkedin". Fragmented like that it looked like four small sources instead of
# one carrying 142 of the 231 verdicts Krish has ever given, and any cap keyed
# on the raw label would be bypassed by the next spelling.
def source_leg(source: str) -> str:
    """The supply leg a source label belongs to."""
    s = (source or "").strip().lower()
    if not s:
        return "unknown"
    if "apify" in s or s.startswith("linkedin"):
        return "linkedin keyword sweep"
    if s.startswith("market scan"):
        return "market scan"
    if "a16z" in s:
        return "a16z portfolio"
    if s.startswith("direct ats sweep"):
        return "legacy ats sweep"
    if s.startswith("newsletter"):
        return "newsletter"
    for ats in ("greenhouse", "ashby", "lever", "workday", "smartrecruiters"):
        if s.startswith(ats):
            return "company board"
    return s


def cap_by_leg_and_company(staged_rows: list, summary: list[str], *,
                           cap: int, per_company: int,
                           leg_share: float) -> list:
    """Trim a ranked batch so no one leg and no one company can own it.

    Measured 2026-09-24 against his own verdicts: the LinkedIn keyword sweep
    carried 62 percent of everything he has ever ruled on and ran at 12
    percent, while the company boards ran at 67 percent on six verdicts each.
    He was not seeing the good roles because the good legs were starved.

    Sierra is the same failure one level down: one board put fourteen roles on
    the sheet and he declined all fourteen.

    Neither is a quality judgement and neither deletes anything. The rows
    trimmed here keep their database row and their score, exactly as the
    overall cap already leaves them, so a leg that is genuinely producing gets
    its slots back next run rather than being written off.
    """
    if not staged_rows:
        return staged_rows
    leg_cap = max(1, int(cap * leg_share)) if leg_share > 0 else 0
    kept: list = []
    per_leg_count: dict[str, int] = {}
    per_company_count: dict[str, int] = {}
    held_leg: dict[str, int] = {}
    held_company: dict[str, int] = {}
    for entry in staged_rows:
        role = entry[0]
        leg = source_leg(getattr(role, "source", ""))
        ckey = company_key(role.company) or slugify(role.company)
        if per_company and per_company_count.get(ckey, 0) >= per_company:
            held_company[role.company] = held_company.get(role.company, 0) + 1
            continue
        if leg_cap and per_leg_count.get(leg, 0) >= leg_cap:
            held_leg[leg] = held_leg.get(leg, 0) + 1
            continue
        kept.append(entry)
        per_leg_count[leg] = per_leg_count.get(leg, 0) + 1
        per_company_count[ckey] = per_company_count.get(ckey, 0) + 1
    if held_company:
        summary.append(
            f"held {sum(held_company.values())} role(s) over the {per_company} "
            f"per company limit: "
            + ", ".join(f"{c} ({n})" for c, n in
                        sorted(held_company.items(), key=lambda kv: -kv[1])[:5]))
    if held_leg:
        summary.append(
            f"held {sum(held_leg.values())} role(s) over the "
            f"{round(100 * leg_share)}% per supply leg limit ({leg_cap} slots): "
            + ", ".join(f"{l} ({n})" for l, n in
                        sorted(held_leg.items(), key=lambda kv: -kv[1])))
    return kept


def recent_sourced_rows(cfg: Config, days: int = 14) -> list[dict]:
    """The roles hunter sourced recently, with where they were and why they
    died. Read by the geography report, which needs the rows a run PAID FOR
    rather than the rows that survived: the point of it is what was thrown
    away after the money was spent.
    """
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    return list(db_get(cfg, "hunter_seen_roles",
                       {"select": "source,location,rejection_reason,sweep_date",
                        "sweep_date": f"gte.{since}", "limit": ALL_ROWS}))


def score_coverage_lines(cfg: Config, batches: int = 4) -> list[str]:
    """How much of the recent funnel the company gate can actually see.

    G14 blocks a company hunter has READ and found wanting, and ranks one it
    could not read. That is deliberate and right. It also means the gate is a
    no-op wherever coverage is thin, and nothing reported coverage, so "the
    scorer is on" and "the scorer sees 12 percent of the funnel" looked the
    same from outside.

    Measured per batch, because company scoring only entered staging on
    2026-09-20: an older batch reading zero says the gate did not exist yet,
    not that it is broken, and mixing the two eras hides both.
    """
    from . import companyintel as intel
    from . import company as comp_score
    roles = db_get(cfg, "hunter_seen_roles",
                   {"select": "company,presented_at", "limit": ALL_ROWS})
    try:
        known = intel.load(cfg)
    except Exception as e:
        return [f"company score coverage unavailable: {e.__class__.__name__}"]
    per: dict[str, dict] = {}
    for r in roles:
        day = (r.get("presented_at") or "")[:10]
        if not day:
            continue
        b = per.setdefault(day, {"companies": set(), "scored": set(),
                                 "actionable": set(), "totals": []})
        name = (r.get("company") or "").strip()
        if not name:
            continue
        k = company_key(name) or slugify(name)
        b["companies"].add(k)
        row = known.get(k) or {}
        if row.get("total") is None:
            continue
        b["scored"].add(k)
        # Having a number is not the same as the gate being able to use it.
        # G14 blocks only a company hunter has READ; one marked needs
        # evidence is ranked lower and still reaches him, which is the
        # correct policy and also means the gate is a no-op for it. Counting
        # only "has a score" hid that distinction completely.
        if (row.get("status") or "") != comp_score.NEEDS_EVIDENCE:
            b["actionable"].add(k)
        try:
            b["totals"].append(float(row["total"]))
        except (TypeError, ValueError):
            pass
    if not per:
        return []
    days = sorted(per)[-batches:]
    out = [f"company score coverage, last {len(days)} batch(es) "
           f"(gate floor {comp_score.SWEEP_FLOOR}):",
           f"  {'batch':12} {'companies':>9} {'scored':>7} {'gate can act':>13} "
           f"{'mean':>6}"]
    for d in days:
        b = per[d]
        n, sc, act = len(b["companies"]), len(b["scored"]), len(b["actionable"])
        cover = f"{sc}/{n}" if n else "-"
        can = f"{act}/{n} ({round(100 * act / n)}%)" if n else "-"
        mean = f"{sum(b['totals']) / len(b['totals']):.1f}" if b["totals"] else "-"
        out.append(f"  {d:12} {n:9} {cover:>7} {can:>13} {mean:>6}")
    out.append("  a batch before 2026-09-20 predates the gate; zero there is "
               "history, not a fault")
    out.append("  scored but not actionable means needs evidence: ranked lower, "
               "never blocked, by design")
    return out


def unreadable_lines(cfg: Config, limit: int = 15) -> list[str]:
    """The companies the gate cannot act on, and what hunter failed to learn.

    Half the funnel's companies are marked needs evidence, which means G14
    cannot refuse them however poor they are. Knowing that is not enough to
    fix it: "could not read it" covers a missing domain, a site that refused
    the fetch, and a homepage that never says what the business does, and
    those are three different repairs. This prints which.
    """
    from . import companyintel as intel
    from . import company as comp_score
    roles = db_get(cfg, "hunter_seen_roles",
                   {"select": "company,presented_at", "limit": ALL_ROWS})
    days = sorted({(r.get("presented_at") or "")[:10] for r in roles
                   if r.get("presented_at")})
    if not days:
        return []
    latest = days[-1]
    want: dict[str, str] = {}
    for r in roles:
        if (r.get("presented_at") or "")[:10] != latest:
            continue
        name = (r.get("company") or "").strip()
        if name:
            want[company_key(name) or slugify(name)] = name
    try:
        known = intel.load(cfg)
    except Exception as e:
        return [f"unreadable companies unavailable: {e.__class__.__name__}"]
    out = [f"companies the gate cannot act on, batch {latest}:"]
    n = 0
    for k, name in sorted(want.items(), key=lambda kv: kv[1].lower()):
        row = known.get(k)
        if row is None:
            out.append(f"  {name[:26]:<26} never scored")
            n += 1
            continue
        if (row.get("status") or "") != comp_score.NEEDS_EVIDENCE:
            continue
        got = []
        try:
            for c in json.loads(row.get("components") or "[]"):
                if c.get("evidenced"):
                    got.append(c.get("name") or "?")
        except Exception:
            pass
        out.append(f"  {name[:26]:<26} evidenced {row.get('evidenced') or 0}"
                   f" ({', '.join(got) or 'nothing'})"
                   f"  {(row.get('why') or '')[:60]}")
        n += 1
        if n >= limit:
            break
    if n == 0:
        out.append("  none; the gate can act on every company in this batch")
    out.append("  category is the prerequisite: without it the score is absent, "
               "not low, and absent may never refuse")
    return out


def staged_company_lines(cfg: Config, limit: int = 25) -> list[str]:
    """Which companies actually fill his sheet, and what he said about them.

    The accept rate says the funnel is drifting. This says who is doing the
    drifting, by name, which is the difference between "sourcing is bad" and a
    change somebody can make.
    """
    roles = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,company,presented_at,source", "limit": ALL_ROWS})
    try:
        events = db_get(cfg, "hunter_verdict_events",
                        {"select": "job_id,verdict,source,recorded_at",
                         "limit": ALL_ROWS})
    except Exception:
        events = []
    ruled: dict[str, str] = {}
    for e in sorted(events, key=lambda x: x.get("recorded_at") or ""):
        src = (e.get("source") or "").lower()
        if ("krish" in src or "column a" in src) and e.get("job_id"):
            ruled[e["job_id"]] = e.get("verdict") or ""
    seen: dict[str, dict] = {}
    for r in roles:
        if not (r.get("presented_at") or ""):
            continue
        name = (r.get("company") or "unknown").strip() or "unknown"
        c = seen.setdefault(name, {"staged": 0, "yes": 0, "no": 0,
                                   "sources": set()})
        c["staged"] += 1
        c["sources"].add((r.get("source") or "unknown").strip() or "unknown")
        v = (ruled.get(r.get("job_id") or "") or "").strip().lower()
        if v in ("yes", "go", "approved"):
            c["yes"] += 1
        elif v and not v.startswith("new"):
            c["no"] += 1
    if not seen:
        return []
    rows = sorted(seen.items(), key=lambda kv: (-kv[1]["staged"], kv[0]))[:limit]
    out = [f"companies that have filled the sheet, top {len(rows)}:",
           f"  {'company':<26} {'staged':>6} {'yes':>4} {'no':>4}  source"]
    for name, c in rows:
        out.append(f"  {name[:26]:<26} {c['staged']:>6} {c['yes']:>4} "
                   f"{c['no']:>4}  {','.join(sorted(c['sources']))[:40]}")
    return out


def cmd_submit(token: str, confirm: bool = False) -> int:
    """Fill one approved application. Press submit only with --confirm.

    Without --confirm it fills the real form on the real site and stops, which is
    how the first live one gets checked: read the screenshot, confirm every field
    and that the button is untouched, then run it again with --confirm.
    """
    from .apply import approval, merge, submit as submit_mod
    from .apply.audit import ATT_CV
    from .docbuild import DocBuild

    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    row = approval.get_row(cfg, token)
    if not row:
        print(f"no approval row for token {token!r}")
        return 1
    rows = db_get(cfg, "hunter_seen_roles",
                  {"select": "*", "job_id": f"eq.{row['job_id']}", "limit": "1"})
    if not rows:
        print(f"no role row for {row['job_id']}")
        return 1

    plan, au, role = build_fill_plan(cfg, sheet, rows[0])
    db = DocBuild(GoogleOAuth(cfg).access_token())
    cv_id = (rows[0].get("package_cv_url") or "").split("/d/")[-1].split("/")[0]
    letter_id = (rows[0].get("package_letter_url") or "").split("/d/")[-1].split("/")[0]
    attachments: dict[str, bytes] = {}
    if cv_id and letter_id:
        cv_pdf, letter_pdf = db.export_pdf(cv_id), db.export_pdf(letter_id)
        if au.attachment_style == ATT_CV:
            # One slot, one file, letter first: the same rule the approval email
            # told him it would follow.
            attachments["file_resume"] = merge.merge_pdfs(letter_pdf, cv_pdf)
        else:
            attachments["file_resume"] = cv_pdf
            attachments["file_cover"] = letter_pdf

    print(f"{role.company} {role.title}\n  {plan.ats} {plan.slug}/{plan.posting_id}"
          f"\n  {len(plan.fields)} fields, {len(plan.blocking)} blocking, "
          f"attachments: {', '.join(attachments) or 'none'}")
    try:
        out = submit_mod.submit(cfg, token, plan, attachments=attachments,
                                confirm=confirm)
    except submit_mod.SubmitBlocked as e:
        print(f"\nREFUSED: {e}")
        return 1
    print(f"\nstate: {out['state']}"
          + (f"  ({out['reason']})" if out.get("reason") else ""))
    if out.get("filled"):
        print(f"  filled: {', '.join(out['filled'][:8])}")
    if out.get("missed"):
        print(f"  NOT filled: {', '.join(out['missed'][:8])}")
    if out["state"] == "filled":
        print(f"\nNothing was sent. Re-run with --confirm to press submit:"
              f"\n  python -m hunter.run submit --token {token} --confirm")
    if out["state"] == approval.SUBMITTED:
        record_applied(cfg, canon, sheet, row["job_id"],
                       company=role.company, role=role.title,
                       screenshot=out.get("screenshot") or "",
                       confirmation=out.get("confirmation") or "",
                       after_png=out.get("after_png") or b"")
    elif confirm:
        # A send that was attempted and did not land is exactly as silent as a
        # send that landed used to be. The reason reached a GitHub Actions log and
        # stopped, and Krish went on believing an approved application was in.
        try:
            send_send_failed(cfg, company=role.company, role=role.title,
                             token=token, state=out["state"],
                             reason=out.get("reason") or "",
                             missed=out.get("missed") or [],
                             notes=out.get("notes") or [])
            print("  told him it did not send")
        except Exception as e:
            print(f"  failure notice email FAILED: {e}")
    return 0 if out["state"] in ("filled", approval.SUBMITTED) else 1


def send_send_failed(cfg: Config, *, company: str, role: str, token: str,
                     state: str, reason: str, missed: list[str],
                     notes: list[str]) -> None:
    """Say plainly that an approved application did NOT go, and why."""
    from . import notify
    bits = "".join(f"<li>{n}</li>" for n in (list(missed) + list(notes)))
    html = (f"<div style=\"font:15px/1.55 -apple-system,BlinkMacSystemFont,"
            f"'Segoe UI',system-ui,sans-serif;color:#111;max-width:680px\">"
            f"<div style='display:none'>[hunter-outbound]</div>"
            f"<h2 style='margin:0 0 2px;font-size:19px'>NOT sent</h2>"
            f"<div style='color:#555;margin-bottom:18px'>{role} at {company}</div>"
            f"<p><strong>{state}</strong>: {reason}</p>"
            + (f"<ul style='color:#555'>{bits}</ul>" if bits else "")
            + f"<p style='color:#888;font-size:13px'>The approval is still on "
              f"record. Nothing was submitted.</p></div>")
    text = "\n".join(["[hunter-outbound]", f"NOT sent: {role} at {company}",
                       f"{state}: {reason}"] + [f"  {n}" for n in
                                                (list(missed) + list(notes))])
    notify.send_email(cfg, f"NOT sent: {company} {role}", html,
                      to=notify.mailbox(cfg), text=text)


def cmd_apply_local(token: str = "", cdp_url: str = "", profile_dir: str = "",
                    port: int = 0) -> int:
    """Open the next approved application, filled, in Krish's own browser.

    His answer to the thing that actually blocks this: "have the approve button
    take me to a filled-out form, ready to press submit myself, with the uploads
    attached". A link in an email cannot do that, because a page may not put a
    file into a file input, which is the one part of an application that matters
    most. A browser being driven can, and this one is his.

    It also removes the reason the first submission never arrived. Harvey's form
    scores its visitor with invisible reCAPTCHA v3, and a headless Chromium in a
    datacentre fails that score, so the press was refused with nothing shown.
    Attaching to a normally started Chrome reads navigator.webdriver false, not
    because anything is masked but because it genuinely was not started by
    automation, and the person pressing the button really is a person.

    Nothing here can submit: open_for_human never calls press_submit and holds no
    reference to it. The last click is his.
    """
    from .apply import approval, merge, submit as submit_mod
    from .apply.audit import ATT_CV
    from .docbuild import DocBuild

    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    if token:
        row = approval.get_row(cfg, token)
        rows = [row] if row else []
    else:
        rows = db_get(cfg, approval.TABLE,
                      {"select": "*", "state": f"eq.{approval.APPROVED}",
                       "order": "decided_at.desc", "limit": "1"})
    if not rows or not rows[0]:
        print("nothing approved and waiting. Reply APPROVE to an application "
              "email first, or pass --token")
        return 1
    row = rows[0]
    if row["state"] != approval.APPROVED:
        print(f"token is {row['state']!r}, not {approval.APPROVED!r}")
        return 1

    roles = db_get(cfg, "hunter_seen_roles",
                   {"select": "*", "job_id": f"eq.{row['job_id']}", "limit": "1"})
    if not roles:
        print(f"no role row for {row['job_id']}")
        return 1
    plan, au, role = build_fill_plan(cfg, sheet, roles[0])
    db = DocBuild(GoogleOAuth(cfg).access_token())
    cv_id = (roles[0].get("package_cv_url") or "").split("/d/")[-1].split("/")[0]
    letter_id = (roles[0].get("package_letter_url") or "").split("/d/")[-1].split("/")[0]
    attachments: dict[str, bytes] = {}
    names: dict[str, str] = {}
    if cv_id and letter_id:
        cv_pdf, letter_pdf = db.export_pdf(cv_id), db.export_pdf(letter_id)
        if au.attachment_style == ATT_CV:
            attachments["file_resume"] = merge.merge_pdfs(letter_pdf, cv_pdf)
            names["file_resume"] = merge.merged_name(role.company)
        else:
            attachments["file_resume"], attachments["file_cover"] = cv_pdf, letter_pdf
            names["file_resume"] = "KrishRaja_CV.pdf"
            names["file_cover"] = "KrishRaja_CoverLetter.pdf"

    print(f"{role.company} {role.title}\n  opening the form in your browser")
    try:
        out = submit_mod.open_for_human(
            plan, attachments=attachments, names=names, cdp_url=cdp_url,
            profile_dir=profile_dir,
            **({"port": port} if port else {}))
    except submit_mod.SubmitBlocked as e:
        # A missing browser is the one setup mistake this will actually hit, and
        # a stack trace is not an instruction.
        print(f"  {e}")
        return 1
    if out["error"] or out["blocker"]:
        print(f"  could not open it: {out['blocker'] or out['error']}")
        return 1
    print(f"  filled {len(out['filled'])} field(s)")
    if out["missed"]:
        print(f"  NOT filled, do these yourself: {', '.join(out['missed'])}")
    for n in out["notes"]:
        print(f"  {n}")
    if out["files"]:
        print(f"  attached: {', '.join(p.split('/')[-1] for p in out['files'])}")
    print(f"\n  {out['url']}\n"
          f"  Read it and press Submit. The employer's receipt closes the row by\n"
          f"  itself, usually within minutes. Nothing else to do.\n"
          f"  (If it never arrives: python -m hunter.run applied --token {row['token']})")
    return 0


SAID_SO = "Krish said he submitted this himself"


def cmd_applied_list() -> int:
    """Every application still waiting on a submission, with the id to name it by.

    Marking a role applied means naming exactly one, and the only place the ids
    were visible was a run log nobody keeps. Reading the wrong one off an old log
    stamps Applied on a role he has not sent.
    """
    from .apply import approval
    cfg = load()
    rows = db_get(cfg, approval.TABLE,
                  {"select": "token,job_id,company,role,state,sent_at",
                   "state": f"in.({approval.AWAITING},{approval.APPROVED})",
                   "order": "sent_at.desc", "limit": "200"})
    if not rows:
        print("nothing is waiting on a submission")
        return 0
    print(f"{len(rows)} application(s) waiting on a submission:\n")
    for r in rows:
        print(f"  {r['job_id']}")
        print(f"    {r.get('company')} {(r.get('role') or '')[:60]}  "
              f"[{r['state']}, sent {(r.get('sent_at') or '')[:10]}]")
    print("\nto record the ones you sent yourself:")
    print("  applied --job-id id1,id2,id3")
    return 0


def cmd_applied(token: str = "", job_ids: str = "") -> int:
    """Record an application Krish pressed himself.

    The other half of apply-local. Hunter cannot see his click, so he says so
    once and everything that would have happened after an automated submit
    happens now: the ledger, the sheet, the role row, the move to the Applied tab.

    His word is the evidence and the receipt says so in those words. It never
    borrows the phrasing used when a form actually acknowledged something,
    because "the form said" and "he told me" are different claims and the whole
    point of that field is to keep them apart.

    Takes several ids because he applies in batches, and a batch of one dispatch
    per role is a batch he will not do.
    """
    from .apply import approval
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)

    wanted = [j.strip() for j in (job_ids or "").split(",") if j.strip()]
    rows: list[dict] = []
    if token:
        row = approval.get_row(cfg, token)
        if not row:
            print(f"no approval row for token {token!r}")
            return 1
        rows.append(row)
    for jid in wanted:
        # ONE row per job, the newest one still in play. A job id collects a
        # ledger row every time an approval is sent, and the 21 resent on
        # 2026-09-24 gave most of them several. Taking all of them recorded the
        # same application five times and put five identical Submitted receipts
        # in his inbox, which is what he saw. It also reopened tokens that had
        # been cancelled on purpose.
        found = db_get(cfg, approval.TABLE,
                       {"select": "token,job_id,company,role,state",
                        "job_id": f"eq.{jid}",
                        "state": f"in.({approval.AWAITING},{approval.APPROVED})",
                        "order": "sent_at.desc", "limit": "1"})
        if not found:
            # Never a silent skip: an id that names nothing is a typo, and a
            # typo that passes quietly leaves a role he sent reading Not applied.
            print(f"no approval row awaiting submission for job id {jid!r}")
            return 1
        rows.extend(found)

    if not rows:
        print("usage: applied --list | applied --job-id id1,id2 | applied --token X")
        return 2

    recorded: list[tuple[str, str, str]] = []
    for row in rows:
        if row["state"] == approval.SUBMITTED:
            print(f"already recorded as submitted: {row.get('company')} "
                  f"{row.get('role')}")
            continue
        approval.set_state(cfg, row["token"], approval.SUBMITTED,
                           submitted_at=NOW(), failure_reason=SAID_SO)
        # One archive pass and one email at the end, not one of each per role.
        # Archiving between roles takes rows out from under the roles still to
        # be written, and a receipt per role filled his inbox.
        wrote = record_applied(cfg, canon, sheet, row["job_id"],
                               company=row.get("company") or "",
                               role=row.get("role") or "",
                               confirmation=SAID_SO,
                               archive=False, receipt=False)
        print(f"recorded: {row.get('company')} {row.get('role')}")
        if wrote:
            recorded.append((row.get("company") or "", row.get("role") or "",
                             SAID_SO))
    if recorded:
        cmd_archive(apply=True)
        try:
            send_applied_digest(cfg, recorded,
                                when=datetime.date.today().isoformat())
        except Exception as e:
            print(f"digest email FAILED: {e}")
    return 0


def cmd_watch(once: bool = False, every: int = 60, port: int = 0,
              profile_dir: str = "") -> int:
    """Sit on Krish's PC and open each approved application as it is approved.

    His actual ask: press Approve in the email and have a filled form appear,
    ready to submit. Nothing else in this system can deliver that, because a web
    page may not put a file into a file input and no link ever will. Something
    has to be running on the machine with the browser on it. This is that thing,
    and it is the only part of hunter that lives on his computer.

    So the loop he sees is: reply APPROVE, wait a minute, press Submit. No
    command, no terminal. It also runs the confirmation pass, so the receipt from
    the employer closes the row without him doing anything at all.
    """
    from .apply import submit as submit_mod
    seen: set[str] = set()
    port = port or submit_mod.DEBUG_PORT
    print(f"watching for approved applications, every {every}s. Ctrl-C to stop.")
    while True:
        try:
            opened = _open_approved(seen, port=port, profile_dir=profile_dir)
            if opened:
                print(f"  {opened} form(s) opened. Read each one and press Submit.")
            cmd_confirmations(apply=True)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0
        except Exception as e:
            # A watcher that dies on one bad poll is a watcher he finds dead a
            # week later, which is the failure this whole session has been about.
            print(f"  poll failed, carrying on: {e.__class__.__name__}: {e}")
        if once:
            return 0
        try:
            time.sleep(every)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0


def _open_approved(seen: set[str], *, port: int, profile_dir: str) -> int:
    """Open every approved application not opened already this session."""
    from .apply import approval
    cfg, _canon = build_context()
    rows = db_get(cfg, approval.TABLE,
                  {"select": "token", "state": f"eq.{approval.APPROVED}",
                   "order": "decided_at.asc", "limit": "10"})
    opened = 0
    for r in rows:
        if r["token"] in seen:
            continue
        seen.add(r["token"])
        if cmd_apply_local(token=r["token"], port=port,
                           profile_dir=profile_dir) == 0:
            opened += 1
        # One at a time. Ten approvals answered in one sitting would otherwise
        # arrive as ten tabs at once, which is not a review, and the next one is
        # a minute away anyway.
        if opened:
            break
    return opened


def retire_dead_posting(cfg: Config, canon, sheet: Sheet, job_id: str, *,
                        company: str, role: str, why: str) -> None:
    """Take a posting the board no longer serves off the board here too.

    Krish: "slingshot says the job is not found, which means the listing has been
    taken down, so I should not be sent an email in this case, the role should be
    purged". Skipping it quietly is not enough, because a skipped row is still a
    built package and comes back on the next run and the one after that.
    """
    note: list[str] = []
    try:
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id},
                 {"package_status": "dead", "status": "dead",
                  "rejection_reason": why[:300]})
        note.append("role row marked dead")
    except Exception as e:
        note.append(f"could not mark the role row dead: {e}")

    rows = sheet.read_pipeline(canon.sheet_headers)
    want = ((company or "").strip().lower(), (role or "").strip().lower())
    hits = [r for r in rows
            if ((r.company or "").strip().lower(), (r.role or "").strip().lower()) == want]
    if len(hits) != 1:
        note.append(f"{len(hits)} Pipeline rows match; sheet left alone")
    else:
        rn = hits[0].row_number
        try:
            sheet.update_package_status(rn, sheet_mod.PKG_DEAD)
            sheet.set_verdicts(
                {rn: verdicts.DECLINE_PREFIX + verdicts.ALL_CODES["dead_posting"]})
            note.append(f"sheet row {rn} retired")
        except Exception as e:
            note.append(f"sheet row {rn} not retired: {e}")
    for line in note:
        print(f"    {line}")


def cmd_retire(job_ids: str, apply: bool = False) -> int:
    """Retire postings Krish names, because the board no longer serves them.

    cmd_decline refuses any row not reading exactly "New", so nothing hunter
    decides can overwrite a judgement of his. That rule stays. This is the
    other case, the same shape as prune-sheet --job-id: on 2026-09-24 he
    marked Databricks and Ladders "Yes", verify found both postings gone, and
    he said "drop them". A role he approved whose form no longer exists cannot
    be applied to, and there was no path to retire it that did not mean him
    editing the dropdown by hand.

    The posting being dead is CHECKED here rather than assumed. A live posting
    is refused, whatever was typed, because "he named it" is authority to
    retire a dead role and not a licence to delete a live one.
    """
    from .ats import ashby, greenhouse, lever, workday
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    named = [j.strip() for j in (job_ids or "").split(",") if j.strip()]
    if not named:
        print("nothing named; pass --job-id a,b")
        return 2
    rows = db_get(cfg, "hunter_seen_roles",
                  {"select": "job_id,company,title,url,job_url,status,"
                             "package_status,krish_verdict", "limit": ALL_ROWS})
    by_id = {r.get("job_id"): r for r in rows}
    fetchers = {"greenhouse": greenhouse.fetch_posting,
                "lever": lever.fetch_posting, "ashby": ashby.fetch_posting,
                "workday": workday.fetch_posting}
    plan: list[tuple[dict, str]] = []
    for jid in named:
        row = by_id.get(jid)
        if row is None:
            # Never a silent skip: a typo leaves a role he asked to be rid of
            # sitting there while the run reports success.
            print(f"no role matched job id {jid!r}")
            return 1
        key = ats_key(row.get("url") or row.get("job_url") or "")
        if key:
            ats, slug, pid = key
            try:
                live, _, _ = fetch_with_retry(fetchers[ats], slug, pid)
            except Exception as e:
                # Unreadable is not dead, and must never be reported as such.
                print(f"REFUSING {jid}: could not reach {ats} to check whether "
                      f"the posting is live ({e.__class__.__name__})")
                return 1
            if live:
                print(f"REFUSING {jid}: the posting is still live on "
                      f"{ats}/{slug}. Retiring it would be a lie on the sheet.")
                return 1
            why = f"posting gone from {ats}/{slug}"
        elif (row.get("status") or "") == "dead":
            # No ATS link to re-check, but hunter already recorded it dead.
            why = "posting recorded dead and carries no ATS link to re-check"
        else:
            print(f"REFUSING {jid}: no ATS link and hunter has not recorded it "
                  f"dead, so there is no evidence the posting has gone")
            return 1
        plan.append((row, why))
    print(f"{len(plan)} posting(s) to retire:")
    for row, why in plan:
        print(f"  {row['company'][:24]:26} {row['title'][:40]:42} {why}")
    if not apply:
        print("\ndry run. add --apply to retire these")
        return 0
    for row, why in plan:
        print(f"  {row['job_id']}")
        retire_dead_posting(cfg, canon, sheet, row["job_id"],
                            company=row.get("company") or "",
                            role=row.get("title") or "", why=why)
    return 0


def cmd_close_submitted(apply: bool = False) -> int:
    """Do the sheet work for applications Krish pressed Submit on himself.

    His question: "can you confirm that when I click the submit button, the
    extension can read that I successfully submitted, and make the appropriate
    changes in the google sheet to move the role out of pipeline and into
    applied?" It could not. The extension tells Control Center now, which marks
    the row, and this is the half that touches the sheet, because the sheet needs
    Google credentials that have no business being in a browser.

    Idempotent by construction: a row whose role already reads submitted is
    skipped, so running this hourly costs nothing and running it twice is safe.
    """
    from .apply import approval
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = db_get(cfg, approval.TABLE,
                  {"select": "token,job_id,company,role,submitted_at,failure_reason",
                   "state": f"eq.{approval.SUBMITTED}",
                   "order": "submitted_at.desc", "limit": "100"})
    if not rows:
        print("nothing newly submitted")
        return 0
    ids = sorted({r["job_id"] for r in rows if r.get("job_id")})
    state = {r["job_id"]: r for r in db_get(
        cfg, "hunter_seen_roles",
        {"select": "job_id,application_state,job_url,url",
         "job_id": f"in.({','.join(ids)})", "limit": "200"})}
    open_ones = [r for r in rows
                 if (state.get(r["job_id"], {}).get("application_state") or "")
                 not in APPLIED_STATES]

    # One posting, however many ledger rows point at it. Mutiny reached this
    # function under three job ids on 2026-09-24; the first wrote its Pipeline
    # row, the archive took the row away, and the other two matched nothing and
    # were left open to retry against a row that no longer existed. The
    # employer's form is the thing applied to, so it is written once and its
    # siblings are closed with it.
    first: dict[str, dict] = {}
    siblings: dict[str, list[dict]] = {}
    todo: list[dict] = []
    for r in open_ones:
        key = posting_key(state.get(r["job_id"], {}))
        if not key:
            todo.append(r)
            continue
        if key in first:
            siblings.setdefault(key, []).append(r)
            continue
        first[key] = r
        todo.append(r)
    dupes = sum(len(v) for v in siblings.values())

    written: list[tuple[str, str, str]] = []
    print(f"{len(rows)} submitted, {len(open_ones)} not yet written to the sheet"
          f"{f', {dupes} of them a second id for a posting already in the list' if dupes else ''}"
          f"{'' if apply else ' (dry run, pass --apply)'}")
    for r in todo:
        print(f"\n  {r['company']} {r['role'][:50]}")
        print(f"    {r.get('failure_reason') or 'no confirmation recorded'}")
        for s in siblings.get(posting_key(state.get(r["job_id"], {})), []):
            print(f"    same posting as {s['job_id']}, which closes with it")
        if not apply:
            continue
        # Passed through as it is, empty included. send_applied_receipt branches
        # on it: something means "the form acknowledged it: <quote>", nothing
        # means "Pressed, but the form showed no confirmation" and an UNCONFIRMED
        # subject line. Substituting the word "submitted" for an empty value made
        # every receipt claim an acknowledgement, which is the distinction
        # submit.py keeps on purpose and the reason this receipt exists.
        wrote = record_applied(
            cfg, canon, sheet, r["job_id"],
            company=r.get("company") or "", role=r.get("role") or "",
            confirmation=(r.get("failure_reason") or "").strip(),
            archive=False, receipt=len(todo) == 1)
        if wrote and len(todo) > 1:
            written.append((r.get("company") or "", r.get("role") or "",
                            (r.get("failure_reason") or "").strip()))
        if not wrote:
            # The row was not written, so its siblings are not finished either.
            continue
        for s in siblings.get(posting_key(state.get(r["job_id"], {})), []):
            try:
                db_patch(cfg, "hunter_seen_roles", {"job_id": s["job_id"]},
                         {"application_state": APPLIED_STATE, "applied_at": NOW()})
                print(f"    closed {s['job_id']}: same posting, already written")
            except Exception as e:
                print(f"    could not close {s['job_id']}: {e}")
    # Once, at the end. Archiving between roles took rows out from under the
    # roles still to be written.
    if apply and todo:
        try:
            cmd_archive(apply=True)
        except Exception as e:
            print(f"archive to the {config_mod.ARCHIVE_TAB} tab FAILED: {e}")
    if written:
        try:
            send_applied_digest(cfg, written,
                                when=datetime.date.today().isoformat())
        except Exception as e:
            print(f"digest email FAILED: {e}")
    return 0


def cmd_confirmations(apply: bool = False) -> int:
    """Close the loop from the employer's own receipt. Dry run by default.

    Krish presses Submit himself, so hunter cannot see the click. Asking him to
    run one more command afterwards is a step he will forget, and then the sheet
    lies in the other direction. Every ATS sends "thanks for applying" within
    minutes, and that email is better evidence than anything hunter could observe
    from its own side, because it comes from the employer rather than from the
    browser that pressed the button.

    Never decides that an application happened. It only recognises a receipt for
    one already on record as approved or pressed, and matches it by company.
    """
    from .apply import approval, confirmations
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    open_rows = []
    for state in (approval.APPROVED, approval.SUBMITTED):
        open_rows += db_get(cfg, approval.TABLE,
                            {"select": "token,company,role,state,job_id,submitted_at",
                             "state": f"eq.{state}", "limit": "100"})
    # A SUBMITTED row with a confirmation already recorded is finished.
    open_rows = [r for r in open_rows
                 if r["state"] == approval.APPROVED or not r.get("submitted_at")
                 or r["state"] == approval.SUBMITTED]
    if not open_rows:
        print("no applications waiting on a receipt")
        return 0
    who = confirmations.mailbox(cfg)
    if who.get("error"):
        print(f"mailbox: {who['error']}")
    else:
        print(f"reading {who['address'] or 'an unnamed mailbox'} "
              f"({who['total']} messages in it)")
    try:
        messages = confirmations.fetch(cfg)
    except confirmations.ConfirmError as e:
        print(str(e))
        return 1
    pairs = confirmations.match(messages, open_rows)
    print(f"{len(messages)} candidate message(s), {len(open_rows)} open "
          f"application(s), {len(pairs)} matched"
          f"{'' if apply else ' (dry run, pass --apply to record)'}")
    closed = 0
    for msg, row in pairs:
        already = row["state"] == approval.SUBMITTED and row.get("submitted_at")
        print(f"\n  {row['company']} {row['role']}")
        print(f"    receipt: {msg['subject'][:80]!r}")
        print(f"    from:    {msg['sender'][:60]}")
        if already:
            print("    already recorded as submitted; nothing to do")
            continue
        if not apply:
            continue
        approval.set_state(cfg, row["token"], approval.SUBMITTED,
                           submitted_at=NOW(),
                           failure_reason=f"confirmed by the employer: "
                                          f"{msg['subject'][:200]}")
        record_applied(cfg, canon, sheet, row["job_id"],
                       company=row.get("company") or "",
                       role=row.get("role") or "",
                       confirmation=f"{msg['sender']}: {msg['subject']}"[:200])
        closed += 1
    if apply:
        print(f"\n{closed} application(s) closed from an employer receipt")
    return 0


def cmd_gtm_seed(apply: bool = False) -> int:
    """Write the AI-native GTM evidence key. Dry run by default.

    Krish 2026-09-15: the first package said nothing about the AI-native GTM work
    he is most experienced in. It could not: the voice gate traces every generated
    number and name back to the evidence haystack, and none of that work was in
    it. This command puts it there, printed in full first, because hunter does not
    write its own evidence unreviewed.
    """
    from .apply import gtmseed
    cfg, _canon = build_context()

    # The blocks come first. tailor.load_blocks raises when a key in BLOCK_KEYS has
    # no approved block, and ai_native_gtm is in BLOCK_KEYS, so until these two
    # rows exist every build fails.
    print("approved blocks for the sixth family, ai_native_gtm:\n")
    for cfg_key, block_key, text, present in gtmseed.block_plan(cfg):
        state = "already present, will be REPLACED" if present else "new"
        print(f"  {cfg_key}[{block_key}] ({state}, {len(text)} chars)")
        print(f"    {text}\n")

    key, value, exists = gtmseed.plan(cfg)
    print(f"system_config.{key}: {len(value)} chars, "
          f"{'UPDATE existing key' if exists else 'INSERT new key'}"
          f"{'' if apply else ' (dry run, pass --apply to write)'}\n")
    print(value)
    if apply:
        n_blocks = gtmseed.write_blocks(cfg)
        print(f"\nwrote and read back {n_blocks} block map(s)")
        n = gtmseed.write(cfg)
        print(f"wrote and read back {n} chars of evidence")
        print("re-run: python -m hunter.run build <job_id>")
    return 0


def cmd_simulate(send: bool = False) -> int:
    """A dummy rehearsal of the whole application loop. Touches no company, no
    Drive document and no Pipeline row; the posting is a literal, not a fetch.

    Krish's instruction 2026-09-14: rehearse before the first real dry run.
    """
    from . import notify
    from .apply import simulate
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    bank = _answer_bank(sheet)
    to = notify.mailbox(cfg)
    result = simulate.run(bank, to=to)

    print(f"simulation against {len(bank.entries)} stored answers, "
          f"recipient {to}\n")
    for name, ok, detail in result.checks:
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {name}" + (f"  [{detail}]" if detail else ""))
    print(f"\n{sum(1 for _, ok, _ in result.checks if ok)} of "
          f"{len(result.checks)} checks passed")
    if result.failures:
        print("\nfailures:")
        for name, _, detail in result.failures:
            print(f"  {name}: {detail}")

    if send:
        if not result.ok:
            print("\nrefusing to send the simulated email while checks fail")
            return 1
        out = notify.send_email(cfg, result.email.subject, result.email.html,
                                to=to, text=result.email.text)
        print(f"\nsimulated approval email sent: {out}")
    else:
        print("\n(dry run. pass --send to mail the simulated approval email "
              "to yourself)")
    return 0 if result.ok else 1


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "recon"
    if cmd == "run":
        return cmd_run()
    if cmd == "process":
        mx = int(argv[argv.index("--max") + 1]) if "--max" in argv else 0
        return cmd_process(max_packages=mx, retry_dead="--retry-dead" in argv)
    if cmd == "migrate-columns":
        return cmd_migrate_columns(apply="--apply" in argv)
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
    # source and packages have been on the workflow's dispatch menu since it was
    # written and run.main handled neither, so choosing either exited 2 with a usage
    # line. They live in run_command, which is the hunter_commands path Control
    # Center presses; this routes the menu to the same work.
    if cmd in ("source", "packages"):
        cfg = load()
        print(run_command(cfg, cmd))
        return 0
    if cmd == "drain":
        cid = argv[argv.index("--id") + 1] if "--id" in argv else None
        return cmd_drain(cid)
    if cmd == "newsletter":
        lim = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
        return cmd_newsletter(apply="--apply" in argv, limit=lim)
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
    if cmd == "retire":
        jids = ""
        if "--job-id" in argv:
            i = argv.index("--job-id")
            if i + 1 < len(argv):
                jids = argv[i + 1]
        if not jids:
            print("usage: python -m hunter.run retire --job-id a,b [--apply]")
            return 2
        return cmd_retire(jids, apply="--apply" in argv)
    if cmd == "bank-seed":
        return cmd_bank_seed(apply="--apply" in argv)
    if cmd == "gtm-seed":
        return cmd_gtm_seed(apply="--apply" in argv)
    if cmd == "submit":
        tok = ""
        if "--token" in argv:
            i = argv.index("--token")
            if i + 1 < len(argv):
                tok = argv[i + 1]
        if not tok:
            print("usage: python -m hunter.run submit --token X [--confirm]")
            return 2
        return cmd_submit(tok, confirm="--confirm" in argv)
    def _flag(name: str, default: str = "") -> str:
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv):
                return argv[i + 1]
        return default

    if cmd == "apply-local":
        return cmd_apply_local(token=_flag("--token"),
                               cdp_url=_flag("--cdp"),
                               profile_dir=_flag("--profile"))
    if cmd == "watch":
        return cmd_watch(once="--once" in argv,
                         every=int(_flag("--every", "60")),
                         port=int(_flag("--port", "0")),
                         profile_dir=_flag("--profile"))
    if cmd == "close-submitted":
        return cmd_close_submitted(apply="--apply" in argv)
    if cmd == "confirmations":
        return cmd_confirmations(apply="--apply" in argv)
    if cmd == "applied":
        if "--list" in argv:
            return cmd_applied_list()
        tok, jids = _flag("--token"), _flag("--job-id")
        if not tok and not jids:
            print("usage: python -m hunter.run applied --list")
            print("       python -m hunter.run applied --job-id id1,id2")
            print("       python -m hunter.run applied --token X")
            return 2
        return cmd_applied(token=tok, job_ids=jids)
    if cmd == "approvals-drain":
        return cmd_approvals_drain(apply="--apply" in argv,
                                   send="--send" in argv)
    if cmd == "approvals":
        jid = ""
        if "--job-id" in argv:
            i = argv.index("--job-id")
            if i + 1 < len(argv):
                jid = argv[i + 1]
        return cmd_approvals(apply="--apply" in argv, job_id=jid)
    if cmd == "simulate":
        return cmd_simulate(send="--send" in argv)
    if cmd == "bank-check":
        return cmd_bank_check()
    if cmd == "audit-forms":
        lim = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
        return cmd_audit_forms(apply="--apply" in argv, limit=lim)
    if cmd == "verify":
        return cmd_verify(apply="--apply" in argv)
    if cmd == "disconnect":
        return cmd_disconnect(apply="--apply" in argv)
    if cmd == "recover-verdicts":
        return cmd_recover_verdicts(apply="--apply" in argv)
    if cmd == "clear-unverdicted":
        return cmd_clear_unverdicted(apply="--apply" in argv)
    if cmd == "archive":
        return cmd_archive(apply="--apply" in argv)
    if cmd == "set-dropdown":
        return cmd_set_dropdown()
    if cmd == "prune-orphans":
        return cmd_prune_orphans(apply="--apply" in argv)
    if cmd == "prune-sheet":
        return cmd_prune_sheet(apply="--apply" in argv,
                               include_ungated="--incumbent" in argv,
                               job_ids=_flag("--job-id"))
    if cmd == "layout":
        cfg = load()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        out = layout.apply_layout(sheet, sheet_id=config_mod.PIPELINE_SHEET_ID)
        print(out)
        for line in layout.describe():
            print(f"  {line}")
        return 0
    if cmd == "portfolios":
        # Refresh the VC portfolio boards. Read only against the boards; the
        # only write is hunter's own cache of what they said.
        from .sources import portfolio
        cfg = load()
        n, notes = portfolio.refresh(cfg)
        for line in notes:
            print(line)
        print(f"stored {n} portfolio companies with their facts")
        return 0
    if cmd == "discover":
        # Companies he has never named, scored and proposed. Read only
        # unless --apply, and nothing is ever contacted.
        cfg = load()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        summary: list[str] = []
        found = discover_companies(cfg, sheet, summary, apply="--apply" in argv)
        for line in summary:
            print(line)
        if found:
            print(f"\n{len(found)} company(ies) cleared the sweep floor and "
                  f"would be swept this run:")
            for name in sorted(found):
                print(f"  {name}")
        if "--apply" not in argv:
            print("\ndry run. add --apply to write the proposals onto the tab.")
        return 0
    if cmd == "stats":
        # The funnel's own report card, on demand. Nothing here writes to the
        # sheet or sends anything; it reads what he decided and divides.
        from . import batchstats, geoscope
        cfg = load()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        batches = funnel_batches_measure(cfg, sheet)
        print(f"{'batch':12} {'staged':>7} {'ruled':>6} {'yes':>4} {'rate':>6}")
        for b in batches:
            rate = f"{round(100 * b.rate)}%" if b.rate is not None else "-"
            print(f"{b.key:12} {b.staged:7} {b.verdicted:6} {b.accepted:4} {rate:>6}")
        print()
        for line in batchstats.lines(batches):
            print(line)
        print()
        for line in batchstats.leg_lines(batches, source_leg):
            print(line)
        print()
        for line in batchstats.source_lines(batches):
            print(line)
        print()
        for line in score_coverage_lines(cfg):
            print(line)
        print()
        for line in unreadable_lines(cfg):
            print(line)
        print()
        for line in staged_company_lines(cfg, limit=25):
            print(line)
        # Where the sourcing is pointed, and what that costs. G6 deleted 42
        # percent of the LinkedIn leg and 52 percent of the boards in run
        # 126, and nothing anywhere showed which search was asking for it.
        print()
        for line in geoscope.search_lines(linkedin_search_urls(cfg, sheet)):
            print(line)
        print()
        for line in geoscope.deleted_lines(recent_sourced_rows(cfg),
                                           leg_of=source_leg):
            print(line)
        if "--apply" in argv:
            batchstats.save(cfg, batches)
            print(f"\nwrote {len(batches)} batch(es) to {batchstats.TABLE}")
        return 0
    if cmd == "companies":
        # Score the companies on his Target Companies tab and show the
        # working, so a number he disagrees with can be argued with.
        from . import company as comp_score
        from . import targets
        cfg = load()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        named = targets.read(sheet)
        summary: list[str] = []
        scores = company_scores(cfg, [t.name for t in named], sheet, summary)
        for line in summary:
            print(line)
        from .ats import discover as disc
        outcomes = company_outcomes(cfg)
        boards = disc.load_cache(cfg)
        print(f"\n{'score':>5} {'tier':>4} {'his':>3}  company")
        rows = {}
        for t_ in named:
            key = company_key(t_.name) or slugify(t_.name)
            s = scores.get(key)
            if not s:
                continue
            tier = s.tier if s.tier else "-"
            print(f"{s.total:5.1f} {str(tier):>4} {str(t_.tier_number or '-'):>3}  "
                  f"{t_.name[:24]:25} {(s.status or s.why(2))[:80]}")
            hit = boards.get(slugify(t_.name)) or {}
            board = f"{hit['ats']}:{hit['slug']}" if hit else ""
            if not board:
                # A company in the seed map never goes through discovery, so
                # its board is not in the cache. Reading blank there made it
                # look unreachable when it is the one hunter has always had.
                from .sources import ats_for
                mapped = ats_for(t_.name)
                if mapped:
                    board = f"{mapped[0]}:{mapped[1]}"
            o = outcomes.get(key) or {}
            rows[t_.row] = [s.total, s.tier or "", board, targets.today(),
                            o.get("seen") or "", o.get("yes") or "",
                            o.get("no") or "", (s.status or s.why(2))[:400]]
        if "--apply" in argv and rows:
            for line in targets.write_hunter_columns(sheet, rows):
                print(line)
        elif rows:
            print(f"\ndry run. add --apply to write {len(rows)} score(s) onto "
                  f"the {targets.TAB} tab.")
        return 0
    if cmd == "invariants":
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        batches = []
        if "--rate" in argv:
            from . import batchstats
            batches = batchstats.measure(cfg)
        out = invariants.enforce(sheet, canon.sheet_headers,
                                 apply="--apply" in argv, batches=batches)
        print("\n".join(out["lines"]))
        if out["repaired"]:
            print("\nrepaired:")
            for note in out["repaired"]:
                print(f"  {note}")
        if not out["ok"]:
            print(f"\nstill broken: {', '.join(out['still_broken'])}")
        if "--apply" not in argv:
            print("\ndry run. add --apply to repair what has a safe repair.")
        return 0 if out["ok"] else 1
    if cmd == "amendments":
        cfg = load()
        for line in amend.report_lines(cfg, limit=25):
            print(line)
        return 0
    if cmd == "preflight":
        cfg = load()
        ok, lines = preflight.gate(preflight.run(cfg, for_sourcing=True))
        print("\n".join(lines))
        return 0 if ok else 1
    if cmd == "watchdog":
        cfg = load()
        problems = alerts.trouble_checks(cfg)
        for p in problems:
            print(f"  {p['kind']}: {p['detail']}")
        if not problems:
            print("  nothing wrong; the loop is turning")
            return 0
        if "--send" in argv:
            print(alerts.send_trouble(cfg, force="--force" in argv))
        else:
            print("\ndry run. add --send to mail these.")
        return 0
    if cmd == "review-email":
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        rows = sheet.read_pipeline(canon.sheet_headers)
        state = alerts.batch_state(rows)
        subject, _html, text = alerts.review_email(rows, state["to_review"],
                                                   state=state)
        print(f"subject: {subject}\n\n{text}")
        if "--send" in argv:
            print("\n" + str(alerts.send_review_ready(
                cfg, rows, state["to_review"], force=True)))
        else:
            print("\ndry run. add --send to mail it.")
        return 0
    if cmd == "doctor":
        # Does what is DEPLOYED agree with what is built. pytest cannot ask that:
        # it checks this repository against itself, and the three drifts that cost
        # a whole day (main behind the branch, the workflow on main missing a
        # command, the control-center endpoint never pushed) all passed it.
        from . import doctor
        import inspect
        cfg = load()
        return doctor.report(doctor.run(cfg, inspect.getsource(main),
                                        offline="--offline" in argv))
    if cmd == "bridges":
        ingest_dir = None
        if len(argv) >= 3 and argv[1] == "--ingest":
            ingest_dir = argv[2]
        return cmd_bridges(ingest_dir)
    print(f"unknown command {cmd!r}; commands: process [--max N] [--retry-dead], "
          f"run, reconcile, migrate-columns [--apply], migrate-sheet, "
          f"build --job-id X, recon, dedupe-db, learn [--apply], drain [--id X], verify, "
          f"bank-check, audit-forms [--apply] [--limit N], simulate [--send], bank-seed [--apply], "
          "gtm-seed [--apply], approvals [--apply] [--job-id X], "
          "approvals-drain [--apply] [--send], submit --token X [--confirm], "
          "newsletter [--apply] [--limit N], "
          "bridges [--ingest DIR], prune-sheet [--apply], regate, archive, "
          "layout, invariants [--apply], amendments, preflight, "
          "clear-unverdicted [--apply], "
          "watchdog [--send], review-email [--send], doctor [--offline]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
