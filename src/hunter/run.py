"""Orchestrator. python -m hunter.run {process,run,reconcile,build,recon,drain}

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
import html as html_mod
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
                     db_get, db_insert, db_patch, db_patch_where, load)
from .docbuild import DocBuild
from .archetype import archetype
from .gates import FLOOR, names_foreign_geo, run_gates
from . import alerts, amend, comp as comp_mod, employer, invariants, judge, layout, learn, preflight, schedule
from .report import report_run
from . import verdicts
from . import router
from . import leave as leave_mod
from .package import style as style_mod
from .router import classify_verdict, is_warm_path, route_status, select_for_build
from .score import BAR, score_role
from . import sheet as sheet_mod
from .sheet import Sheet, SheetError, SheetRow, make_row
from .sources import (ResolvedRole, company_key, country_of, distinctive_tokens,
                      identity_keys, placed_identity_keys,
                      job_id, slugify)
from .notify import send_summary
from .llm import redact as llm_redact

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
        # Every line can reach an email. An Apify error used to carry the URL,
        # token and all, so the redaction happens here, once, for every line.
        from .sources.apify_linkedin import redact as redact_apify
        line = llm_redact(redact_apify(str(line)))
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
# A posting whose sheet row he had REMOVED by name, as opposed to one hunter
# dropped on a gate. reconcile must not put it back.
#
# 2026-09-24: two rows were removed by name at 17:57 and confirmed gone at
# 18:12. The 19:15 process run put both back. reconcile treats a database row
# with no matching sheet row as a row missing from the sheet and appends it,
# and its "he deleted this, respect it" branch is gated on `not decided`:
#
#     decided = bool(krish_verdict) or package_status != "none"
#     if presented_at and not decided: ...skip
#
# Both rows carried his verdict "Yes" and a built package, so decided was
# True, the protection did not apply, and they came back. Keeping the verdict
# while removing the row, which is what he asked for, is exactly the case that
# branch does not cover. Nothing recorded that the removal was deliberate, so
# this does.
REMOVED_STATUS = "removed"
# A role his score sent to the Applied tab (leave.py). Defined there; named
# here because reconcile and the discovery quota read it.
SET_ASIDE_STATUS = leave_mod.SET_ASIDE
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
    """(ats, board slug, posting id): the posting's identity, for telling two
    links to one posting apart from two postings.

    A gh_jid link is a company's own careers page carrying a Greenhouse
    posting id and no board, so its key is ("greenhouse", <posting id>, "")
    and names no board at all. That shape is an identity and never something
    to fetch: board_key is the one to ask before reading a board."""
    if not url:
        return None
    for ats, pat in ATS_URL_PATTERNS:
        m = pat.search(url)
        if m:
            return (ats, m.group(1), m.group(2))
    return None


def board_key(url: str | None) -> tuple | None:
    """ats_key, only when it names a board AND a posting on it, so it can be
    fetched. None for a gh_jid link, whose key holds the posting id where the
    slug should be and nothing after it.

    Fetching that key asked boards-api.greenhouse.io/v1/boards/<posting id>
    /jobs/, which answers 404, and a 404 from a board is read as the posting
    being dead. Measured 2026-10-14 on two postings of his: a careers page
    link with a gh_jid and a careers subdomain with one. Both were live on
    their real boards (200). The first was already marked "Posting dead,
    cannot build" on his sheet, and the second would have been the first
    time his score asked for its package."""
    key = ats_key(url)
    return key if key and key[1] and key[2] else None


_GH_JID = re.compile(r"[?&]gh_jid=(\d+)")


def gh_jid(url: str | None) -> str:
    """The Greenhouse posting id in a link that names no board, or ''."""
    if not url or board_key(url):
        return ""
    m = _GH_JID.search(url)
    return m.group(1) if m else ""


def board_url(posting) -> str:
    """A link to a board posting that board_key can read. A Greenhouse board
    lists a company that hosts its own careers page under that page's link,
    gh_jid and all, so relinking a row to what the board says would hand
    the next check the same unfetchable key it started with."""
    url = getattr(posting, "url", "") or ""
    if board_key(url):
        return url
    ats, slug = getattr(posting, "ats", ""), getattr(posting, "ats_slug", "")
    pid = getattr(posting, "ats_posting_id", "") or ""
    if not (slug and pid):
        return ""
    return {"greenhouse": f"https://job-boards.greenhouse.io/{slug}/jobs/{pid}",
            "ashby": f"https://jobs.ashbyhq.com/{slug}/{pid}",
            "lever": f"https://jobs.lever.co/{slug}/{pid}"}.get(ats, "")


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
        # duplicate is the same application twice; removed is a row he had
        # taken off the sheet by name and must not be appended back.
        "status": f"not.in.(duplicate,{REMOVED_STATUS})",
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
            "why_it_fits": (srow.cell(sheet_mod.AI_WHY)
                            if srow.cell(sheet_mod.AI_WHY) != "Not assessed" else ""),
            "location": srow.cell("Location") if srow.cell("Location") != "Not stated" else "",
            "comp": srow.cell("Comp") if srow.cell("Comp") != "Not disclosed" else "",
            "sweep_date": TODAY(), "presented_at": NOW(),
        }
        try:
            row["score"] = int(srow.cell(sheet_mod.AI_SCORE))
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
        # Set aside by his score (leave_step): the row is on the Applied tab,
        # and a role whose row this pass could not pair is not one he deleted.
        # Never appended back, never recorded as his decline.
        if d.get("status") == SET_ASIDE_STATUS:
            ledger.skipped.append(
                f"{d['job_id']}: set aside by your score; not put back on the sheet")
            continue
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
        # A verdict that moved on (Yes, then Applied; Yes, then Declined) is a
        # new ruling. Only the first was ever recorded, so a role he applied to
        # stayed "Yes" in the database and the learning loop never heard it.
        moved_on = bool(stored) and learn.classify(stored)[0] != verdict_kind
        if verdict_kind != "none" and (not stored or his_override or moved_on):
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


# How a row his score asked a package for is shown when it is held: never
# with the score beside the role, because these lines can reach a public log.
ASKS_FOR_PACKAGE = "your score asks for a package"


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
        if router.build_trigger(r):
            # A score of 7 or more is his approval for the rule that hunter
            # never archives what he approved (canon v10), and a score build
            # never writes krish_verdict, so the database check below cannot
            # see it. Shown without the score: this output can be a log.
            held.append((r, {"krish_verdict": ASKS_FOR_PACKAGE}))
            continue
        d = standing_verdict(r)
        if d:
            held.append((r, d))
            continue
        rows.append(r)
    if held:
        print(f"holding {len(held)} row(s) you have already decided on, "
              f"whatever column A says. They are never archived or rescored, "
              f"but they still get a rationale so column "
              f"{sheet_mod.letter(sheet_mod.AI_WHY)} reads the same everywhere:")
        for r, d in held:
            print(f"  row {r.row_number}: {r.company} / {r.role} "
                  f"[{d.get('krish_verdict')}]")
        print()
    if limit:
        rows = rows[:limit]
    print(f"re-gating {len(rows)} rows from row {from_row} down\n")

    keep, drop, unresolved = [], [], []
    # By row number, fixed before the loop. Re-reading `held` on every pass
    # let a local of the same name rebind it to one row's score, after which
    # every later held row read as undecided and went down the drop path.
    held_rows = frozenset(r.row_number for r, _ in held)
    for r in rows + [h[0] for h in held]:
        decided_row = r.row_number in held_rows
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
        if not board_key(r.jd_url):
            unresolved.append((r, "no ATS board on the URL, liveness unverifiable"))
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
            shown = str(r.cell(sheet_mod.AI_SCORE)).strip()
            existing = int(shown) if shown.isdigit() else result.score
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
    # His score and reaction come back with his row: they are his words, not
    # hunter writing into his columns.
    rng = sheet.append_rows(cells, carry_his=True)
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


def dead_but_his(r: SheetRow, d: dict) -> bool:
    """A dead row hunter must not stamp or archive: his own verdict in the
    database, or a row that asks for a package now, a Yes in column A or a
    Krish Score of 7 or more (router.build_trigger). Canon 9.13 on a dead
    posting: the row stays, column A untouched, Krish decides. A score build
    never writes krish_verdict, so the database alone could not see that
    the first score passes would mark six of his 7-or-more rows dead."""
    if (d.get("krish_verdict") or "").strip() and not learn.is_auto(d):
        return True
    return router.build_trigger(r) is not None


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
        key = board_key(r.jd_url)
        if key:
            ats, slug, pid = key
            try:
                is_live, _, _ = fetch_with_retry(fetchers[ats], slug, pid)
            except Exception as e:
                unknown.append((r, f"{ats} fetch failed: {e.__class__.__name__}"))
                continue
            (live if is_live else dead).append((r, f"{ats}/{slug}"))
            continue

        # A gh_jid link names no board: its own page and posting id find it.
        jid = gh_jid(r.jd_url)
        state, hit, why = (discover_posting(cfg, r.company, title, cache,
                                            careers_url=r.jd_url or "", posting_id=jid)
                           if jid else discover_posting(cfg, r.company, title, cache))
        if state == "unknown":
            unknown.append((r, why))
            continue
        if state == "absent":
            dead.append((r, why))
            continue
        link = board_url(hit)
        live.append((r, f"{why}, relinked" if link else why))
        if link:
            relinked[r.row_number] = link

    print(f"LIVE {len(live)}, DEAD {len(dead)}, UNVERIFIABLE {len(unknown)}\n")
    for r, why in live:
        print(f"  live  row {r.row_number:>3} {r.company[:22]:24} "
              f"{r.role[:36]:38} {why}")
    for r, why in dead:
        d = paired.get(r.row_number) or {}
        his = (d.get("krish_verdict") or "").strip()
        held = his and not learn.is_auto(d)
        asks = not held and dead_but_his(r, d)
        print(f"  DEAD  row {r.row_number:>3} {r.company[:22]:24} "
              f"{r.role[:36]:38} {why}"
              + (f"  [your verdict {his!r}, held]" if held else "")
              + (f"  [{ASKS_FOR_PACKAGE}, held]" if asks else ""))
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
        if dead_but_his(r, d):
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
    """Move off Pipeline onto the Applied tab what leaves it: the one
    predicate (leave.should_leave) through the one pass (leave_step).

    Krish's ruling 2026-09-02: Pipeline holds what still needs a decision,
    so his Applied and Declined rows go. His rulings 2026-10-13, once canon
    9.13 carries them: the rows he scored 1 to 5, gave only a reaction,
    scored past or called Duplicate go too, and his score wins over an older
    Yes. close-submitted and confirmations call this after writing Applied,
    and it moves what settle would, no more and no less. The decided rows are
    named, as they always were; a row his score moves is counted, never
    named, because this prints to public logs.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rows = sheet.read_pipeline(canon.sheet_headers)
    decided = [r for r in rows if leave_mod.should_leave(r, None)]
    print(f"{len(rows)} rows on Pipeline; {len(decided)} decided by column A")
    for r in decided:
        kind, code = verdicts.parse(r.verdict)
        print(f"  row {r.row_number:>3}  {r.company[:20]:20} {r.role[:38]:38} "
              f"{kind}{' [' + code + ']' if code else ''}")
    summary: list[str] = []
    leave_step(cfg, canon, sheet, summary, apply=apply)
    print("\n".join(summary))
    if not apply:
        print("\ndry run. add --apply to move these rows")
        return 0
    left = sheet.read_pipeline(canon.sheet_headers)
    print(f"\nPipeline now has {len(left)} rows awaiting you")
    return 0


def cmd_leave(apply: bool = False, out: str = "") -> int:
    """What leaves Pipeline and what comes back, by his record and column A.

    A dry run by default, and the list it gives is the one put to him before
    the first apply: it is computed whether or not canon 9.13 carries the
    rule yet, and says which. Printed as counts only, because the run log is
    public; --out PATH writes the rows themselves (company, title, column A,
    his score, why, the approvals that would be cancelled) to a local file,
    which never goes anywhere hunter publishes. --apply moves them, and only
    once canon carries the rule; until then it moves column A's rows alone.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    summary: list[str] = []
    leave_step(cfg, canon, sheet, summary, apply=apply, out_path=out or None)
    print("\n".join(summary))
    if out:
        print(f"the rows themselves are in {out}")
    if not apply:
        print("dry run. add --apply to move them")
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


def cmd_prune_sheet(apply: bool = False, include_ungated: bool = False,
                    job_ids: str = "") -> int:
    """Remove rows this system should never have written.

    Only rows where column A still reads exactly "New" and Krish Score and
    Krish Reaction are both blank, so nothing Krish has touched is ever at
    risk, and only two unambiguous classes by default:
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
        return sheet_identity(s.company, s.role)

    # Untouched means column A New AND his score and reaction blank. Column A
    # alone planned his 9 on Notion as a "duplicate" of the twin he had
    # marked Duplicate, and his 10 on Reddit with it, because both read New.
    def untouched(s: SheetRow) -> bool:
        return ((s.verdict or "").strip() == "New"
                and not any(s.cell(n).strip() for n in sheet_mod.HIS_COLS))
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
    # Record that the rows he NAMED were removed on purpose, or reconcile
    # appends them again on the next run. His verdict is left exactly as it
    # is: he chose to keep it, and this says nothing about the role, only
    # that its row is off the sheet by his instruction.
    for jid in sorted(named):
        try:
            db_patch(cfg, "hunter_seen_roles", {"job_id": jid},
                     {"status": REMOVED_STATUS})
            print(f"  {jid}: recorded as removed, reconcile will not re-add it")
        except Exception as e:
            print(f"  {jid}: could NOT be recorded as removed "
                  f"({e.__class__.__name__}); reconcile will put the row back")
            return 1
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

    findings, fixed = duplicate_path(cfg, events, roles, apply=apply)

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


def duplicate_path(cfg: Config, events: list[dict], roles: list[dict], *,
                   apply: bool) -> tuple[list[dict], list[str]]:
    """What each system-code event says hunter missed, and the recorded twin
    marked duplicate where there is one. The events may be his column A
    verdicts or, from ratings.sync, roles he marked Duplicate in his reaction
    column; those are in memory only and this path never records an event."""
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
    return findings, fixed


RATINGS_FAILED = alerts.RATINGS_FAILED
# The hunter_alerts prefix the sync's failure marks live under.
RATINGS_MARK = "ratings:"


def ratings_step(cfg: Config, canon: Canon, sheet: Sheet, summary: list[str], *,
                 apply: bool, roles: list[dict] | None = None) -> dict:
    """K and M into hunter_krish_ratings, after column A has been dealt with.

    Never fatal: a failure is a summary line, and the rest of the run still
    happens. It reaches workflow_runs once, when the same failure comes
    twice in a row (alerts.record_step_failure), never once an hour: settle
    runs this hourly, and a row per hour would have been a "runs are failing"
    email a day and, after twelve hours, a watchdog claiming hunter had no
    successful run at all. The error is named by class and status only, because a
    database error can quote the row it refused and the row holds his words.

    With `roles` (the learning step's, from process), the sync re-pairs
    whatever the hash says and a role he marked Duplicate goes through the
    duplicate path. Settle passes none: it reports, and the hourly settle
    reaching an edit first no longer leaves process with nothing to check."""
    from . import ratings
    started = datetime.datetime.now(datetime.timezone.utc)
    try:
        out = ratings.sync(cfg, sheet, apply=apply, canon_headers=canon.sheet_headers,
                           force=roles is not None)
    except Exception as e:
        why = ratings.safe_error(e)
        summary.append(f"{RATINGS_FAILED}: {why}")
        if apply:
            try:
                summary.append(alerts.record_step_failure(
                    cfg, RATINGS_MARK, why, kind=RATINGS_FAILED, started_at=started))
            except Exception as e2:
                summary.append(f"  and the failure was not recorded: {e2.__class__.__name__}")
        return {}
    summary.extend(ratings.lines(out))
    if apply:
        try:
            alerts.forget_step_failure(cfg, RATINGS_MARK)
        except Exception as e:
            summary.append(f"Krish Score: an earlier failure's mark not cleared "
                           f"({e.__class__.__name__})")
    events = out.get("duplicate_events") or []
    if events and roles is None:
        summary.append("Krish Score duplicates: not checked here; the next process "
                       "run checks them against the recorded roles")
    elif events:
        try:
            findings, fixed = duplicate_path(cfg, events, roles, apply=apply)
            summary.append(f"Krish Score duplicates: {len(findings)} checked, "
                           f"{len(fixed)} marked duplicate of a recorded twin")
        except Exception as e:
            summary.append(f"Krish Score duplicates not checked: {e.__class__.__name__}")
    return out


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
                from .people.bridges import upsert_contacts
                upsert_contacts(cfg, contacts)
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
    if not command_id:
        # A full run that is owed runs here, whichever trigger woke this drain.
        # See schedule.py for why the slot is decided from the record.
        owed = scheduled_full_run(cfg)
        if owed is not None:
            return owed
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
    if command == "run":
        # The full run does its own reporting and emails; the command row only
        # needs to say how it ended.
        rc = cmd_run()
        db_patch(cfg, COMMANDS_TABLE, {"id": cid},
                 {"state": "done" if rc == 0 else "failed", "finished_at": NOW(),
                  "result": ("full run finished" if rc == 0 else
                             "full run failed; the run summary email says why")})
        return rc
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


def scheduled_full_run(cfg: Config) -> int | None:
    """Run the latest slot's full run if it is owed. None means nothing was
    owed (the reason is printed); an int is the full run's exit code."""
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=8)).isoformat()
    try:
        commands = db_get(cfg, COMMANDS_TABLE, {
            "select": "id,state,requested_at,started_at", "command": "eq.run",
            "requested_at": f"gte.{since}", "order": "requested_at.asc"})
        runs = db_get(cfg, "workflow_runs", {
            "select": "run_at,status,metadata", "agent_id": "eq.hunter",
            "workflow_id": "eq.hunter-run", "run_at": f"gte.{since}"})
    except Exception as e:
        # Unable to read the record is not permission to pay for a run.
        print(f"schedule: could not read the run record ({e.__class__.__name__}); "
              f"not starting a full run")
        return None
    # Only a run that swept counts as the batch. A Process press writes a
    # workflow_runs row too, without "discovered".
    full = [r for r in runs if isinstance(r.get("metadata"), dict)
            and "discovered" in r["metadata"]]
    decision = schedule.decide(now, commands, full)
    print(f"schedule: {decision.why}")
    if not decision.due:
        return None
    tag = f"schedule {decision.slot.isoformat()}"
    db_insert(cfg, COMMANDS_TABLE, [{"command": "run", "state": "running",
                                     "requested_by": tag, "started_at": NOW()}])
    claimed = db_get(cfg, COMMANDS_TABLE, {
        "select": "id", "command": "eq.run", "state": "eq.running",
        "requested_by": f"eq.{tag}", "order": "requested_at.desc", "limit": "1"})
    rc = cmd_run()
    if claimed:
        db_patch(cfg, COMMANDS_TABLE, {"id": str(claimed[0]["id"])},
                 {"state": "done" if rc == 0 else "failed", "finished_at": NOW(),
                  "result": ("full run finished" if rc == 0 else
                             "full run failed; the run summary email says why")})
    return rc


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
        rows = select_for_build(cfg, sheet, canon.sheet_headers,
                                score_wins=leave_mod.in_canon(canon))
        built = 0
        for row in rows:
            try:
                if build_one(cfg, canon, sheet, row, summary):
                    built += 1
            except Exception as e:
                # The class only, and the record decided from his row as it
                # reads now, as process does (build_error_outcome).
                summary.append(f"BUILD FAILED {row['job_id']}: {e.__class__.__name__}")
                outcome, lines = build_error_outcome(cfg, row["job_id"], e, sheet=sheet,
                                                     canon=canon, row=row)
                summary.extend(lines)
                if outcome == BUILD_KEPT:
                    built += 1
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


def discover_posting(cfg: Config, company: str, title: str, cache: dict, *,
                     careers_url: str = "", posting_id: str = ""
                     ) -> tuple[str, object | None, str]:
    """Find a posting on its company's board without an ATS key.

    ("found", posting, "ats/slug"): the board lists it, exact title first,
    then a single close title. ("absent", None, why): the board exists and
    does not list it, which is verifiably dead. ("unknown", None, why): no
    board found or the board read failed, which is not an answer.

    posting_id is a gh_jid's posting id, and with it in hand the posting id
    is the only match: it is the posting itself, and a retitled posting
    keeps its id. No title is ever matched for it. The same title under
    another id is another posting, often the same seat in another country
    (Sierra's London and Seoul RVP), and relinking to it built a package for
    a role he never scored and called a closed one live. An id the board
    does not list is "absent" only when the link's own careers page names
    that board; a board from the cache or a guessed slug may be the wrong
    one, so the answer is "unknown". careers_url is the page the link came
    from, which names its own board more reliably than a slug guessed from
    the name.
    """
    from .ats import discover as disc
    from .ats import ashby, greenhouse, lever
    boards = {"greenhouse": greenhouse.board, "ashby": ashby.board,
              "lever": lever.board}
    # With no page in hand, the same three-argument call every other caller
    # of discover makes.
    found = (disc.discover(cfg, company, cache, careers_url=careers_url) if careers_url
             else disc.discover(cfg, company, cache))
    if not found:
        return "unknown", None, "no job board found on greenhouse, ashby or lever"
    ats, slug = found
    try:
        postings = boards[ats](slug)
    except Exception as e:
        return "unknown", None, f"{ats}/{slug} board read failed: {e.__class__.__name__}"
    if posting_id:
        if ats != "greenhouse":
            return "unknown", None, (f"the link names Greenhouse posting {posting_id} and "
                                     f"the board found is {ats}/{slug}")
        hit = next((p for p in postings
                    if str(getattr(p, "ats_posting_id", "") or "") == posting_id), None)
        if hit is not None:
            return "found", hit, f"{ats}/{slug}, posting {posting_id}"
        try:
            own = disc.from_careers_page(careers_url) if careers_url else None
        except Exception:
            own = None
        if own == (ats, slug):
            return "absent", None, (f"posting {posting_id} not on the {ats}/{slug} board "
                                    f"its own careers page names ({len(postings)} jobs)")
        return "unknown", None, (f"posting {posting_id} not on {ats}/{slug}, a board its "
                                 f"own page does not name")
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
    if board_key(url):
        return _resolve_for_build(row), None, flags
    # A gh_jid link has an ATS key and no board in it. It goes the way of a
    # link with no key at all, its posting id and its own careers page in
    # hand: found by its id, dead only when the board its own page names no
    # longer lists that id, and otherwise unverified, never a dead posting
    # read off a board that does not exist and never another posting of the
    # same title.
    jid = gh_jid(url)
    state, hit, why = (discover_posting(cfg, company, title, cache,
                                        careers_url=url, posting_id=jid) if jid
                       else discover_posting(cfg, company, title, cache))
    link = board_url(hit) if state == "found" and hit is not None else ""
    if link:
        role = _resolve_for_build(dict(row, url=link, job_url=link))
        flags.append(f"board discovery: {why}")
        return role, link, flags
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


def same_link(a: str | None, b: str | None) -> bool:
    """Two links that name one posting: the same ATS posting, or the same
    page once tracking parameters are gone. Two empty links are not one."""
    if not a or not b:
        return False
    ka, kb = ats_key(a), ats_key(b)
    if ka and kb:
        return ka == kb
    return norm_url(a) == norm_url(b)


def _sheet_row_for(sheet: Sheet, canon: Canon, row: dict) -> SheetRow | None:
    return own_sheet_row(sheet.read_pipeline(canon.sheet_headers), row)


def own_sheet_row(pipeline: list[SheetRow], row: dict) -> SheetRow | None:
    """The Pipeline row that is this record's posting, never a twin.

    The row select_for_build chose first (sheet_link, then sheet_row while
    it still carries the same company and title), then the record's own
    link. This was match_rows(pipeline, [row]) alone, which walks Pipeline
    from the top with one record to place, so the first row with the same
    company and title took it through the job_id or exact-title pass even
    when its link was another posting. Two postings of one title in two
    countries are two rows, and his live list on 2026-10-14 had a pair of
    them scored 10 and 9: one was skipped every hour as "changed", and with
    equal scores both packages were written onto the upper row.

    match_rows is the last resort, for a record whose link no row carries,
    and its answer is refused when that row's link is another ATS posting.
    """
    chosen = row.get("sheet_link") or ""
    if chosen:
        hit = [r for r in pipeline if same_link(r.jd_url, chosen)]
        exact = [r for r in hit if r.row_number == row.get("sheet_row")]
        if exact or hit:
            return (exact or hit)[0]
    if row.get("sheet_row"):
        ident = sheet_identity(row.get("company") or "", row.get("title") or "")
        at = next((r for r in pipeline if r.row_number == row["sheet_row"]), None)
        if at is not None and sheet_identity(at.company, at.role) == ident:
            return at
    mine = [u for u in (row.get("url"), row.get("job_url")) if u]
    hit = [r for r in pipeline if any(same_link(r.jd_url, u) for u in mine)]
    if hit:
        return hit[0]
    pairs, _, _, _ = match_rows(pipeline, [row])
    if not pairs:
        return None
    s = pairs[0][0]
    theirs, ours = ats_key(s.jd_url), {ats_key(u) for u in mine} - {None}
    if theirs and ours and theirs not in ours:
        return None
    return s


def _resolve_for_build(row: dict) -> ResolvedRole:
    from .ats import ashby, greenhouse, lever
    url = row.get("url") or row.get("job_url") or ""
    key = board_key(url)
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


def record_trigger(cfg: Config, job_id: str, trigger: str) -> list[str]:
    """Read back what build_one wrote as the package's trigger, and put a
    score trigger on his rating of the role as well. Lines only when
    something did not land: a score-built package whose trigger is missing
    has no Prepare in Control Center, and that has to be said."""
    out = []
    try:
        back = db_get(cfg, "hunter_seen_roles", {"select": "package_trigger",
                                                 "job_id": f"eq.{job_id}", "limit": "1"})
        if not back or (back[0].get("package_trigger") or "") != trigger:
            out.append(f"  note {job_id}: package built, but its trigger did not read "
                       f"back, so Control Center may not offer Prepare for it")
    except Exception as e:
        out.append(f"  note {job_id}: package trigger not read back ({e.__class__.__name__})")
    score = router.built_on_score(trigger)
    if score is None:
        return out
    from . import ratings
    from .config import db_patch_where
    try:
        n = db_patch_where(cfg, ratings.TABLE, {"job_id": f"eq.{job_id}",
                                                "kind": f"eq.{ratings.SCORED}",
                                                "score": f"eq.{score}"},
                           {"package_trigger": trigger})
        if not n:
            out.append(f"  note {job_id}: no recorded rating of this score to mark "
                       f"as the one that built it")
    except Exception as e:
        out.append(f"  note {job_id}: rating not marked ({ratings.safe_error(e)})")
    return out


def record_his_view(cfg: Config, job_id: str, view, tr) -> list[str]:
    """Write which of his reasons the package just built carries, and read it
    back: his view's fingerprint when the tailor call had one and its prose
    came through the gate, null when it did not, so a package written from
    the approved blocks alone is never counted as carrying his reasons.
    Written on its own, after the built record, because the column arrives
    with a migration (cc6) and a write that names a missing column must not
    take the package's own record down with it. Lines only when something
    did not land, and never his words: the fingerprint is a hash."""
    from .package import his_view as his_view_mod
    wrote = getattr(tr, "summary", "") or getattr(tr, "hook", "")
    value = (view.fingerprint if wrote else "") or None
    field = his_view_mod.FIELD
    try:
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id}, {field: value})
        back = db_get(cfg, "hunter_seen_roles", {"select": field,
                                                 "job_id": f"eq.{job_id}", "limit": "1"})
    except Exception as e:
        if value is None:
            # Nothing of his to record, and a missing record already reads as
            # none: not worth a line in every Yes build's summary until cc6.
            return []
        return [f"  note {job_id}: which of his reasons this package carries was not "
                f"recorded ({e.__class__.__name__}); rebuild-packages cannot tell it apart"]
    if not back or (back[0].get(field) or None) != value:
        return [f"  note {job_id}: which of his reasons this package carries did not "
                f"read back"]
    return []


# The reason a package carries when a rebuild of it did not land. Until
# 2026-10-15 the package built before stayed "built" and every send path
# would still send it; a rebuild is asked for because that package is no
# longer what he wants sent, so it is blocked with this reason instead
# (package/style.py), and rebuild-packages may still choose it again.
REBUILD_REFUSED = "rebuild did not land: "


def rebuild_did_not_land(cfg: Config, row: dict, why: str) -> list[str]:
    """Take a package whose rebuild did not land out of every send path:
    blocked, with REBUILD_REFUSED and why, read back. Lines only when the
    write did not read back, by class: the summary is public."""
    patch = {"package_status": "blocked", "rejection_reason": REBUILD_REFUSED + why}
    try:
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, patch)
        back = db_get(cfg, "hunter_seen_roles", {"select": "package_status",
                                                 "job_id": f"eq.{row['job_id']}", "limit": "1"})
    except Exception as e:
        return [f"  note {row['job_id']}: not recorded as blocked "
                f"({e.__class__.__name__}); the send paths still refuse it for its style"]
    if not back or back[0].get("package_status") != "blocked":
        return [f"  note {row['job_id']}: written as blocked, and the read-back did not "
                f"find it; the send paths still refuse it for its style"]
    return []


def record_held(cfg: Config, row: dict, view) -> list[str]:
    """A rebuild the gate held or verification failed, recorded against what
    it was tried on (his_view.held_mark) and read back, so the next run does
    not pay to fail the same way. Lines only when it did not land."""
    from .package import his_view as his_view_mod
    field = his_view_mod.FIELD
    mark = his_view_mod.held_mark(view.fingerprint, row.get("package_built_at"),
                                  row.get("sheet_link") or row.get("url"))
    try:
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, {field: mark})
        back = db_get(cfg, "hunter_seen_roles", {"select": field,
                                                 "job_id": f"eq.{row['job_id']}", "limit": "1"})
    except Exception as e:
        return [f"  note {row['job_id']}: this held rebuild was not recorded "
                f"({e.__class__.__name__}); the next run tries it again"]
    if not back or back[0].get(field) != mark:
        return [f"  note {row['job_id']}: this held rebuild did not read back; the next "
                f"run tries it again"]
    return []


def baseline_for_linked(cfg: Config, sheet: Sheet, row_number: int, job_id: str,
                        result) -> list[str]:
    """After a package write to his row that raised: the text of each new
    document his row now links, saved as what hunter wrote (amend.save_docs).
    A document the row still links from before keeps the baseline it had."""
    try:
        cv, letter = (list(sheet.package_links(row_number)) + ["", ""])[:2]
        new_letter = bool(result.letter_url) and letter == result.letter_url
        new_cv = bool(result.cv_url) and cv == result.cv_url
        if not (new_letter or new_cv):
            return []
        docs = DocBuild(GoogleOAuth(cfg).access_token)
        amend.save_docs(cfg, job_id,
                        letter_text=(doc_plain_text(docs, result.letter_doc_id or "")
                                     if new_letter else ""),
                        cv_text=(doc_plain_text(docs, result.cv_doc_id or "")
                                 if new_cv else ""))
    except Exception as e:
        return [f"  note {job_id}: the row write failed and which documents it links "
                f"could not be checked ({e.__class__.__name__}); an edit to them may "
                f"be misread"]
    return []


def build_one(cfg: Config, canon: Canon, sheet: Sheet, row: dict,
              summary: list[str], *, cache: dict | None = None,
              company_declines: dict | None = None,
              feedback: str = "", rebuild: bool = False) -> bool:
    """feedback carries Krish's own words from an amend reply, verbatim, so a
    rebuild acts on what he actually asked for rather than a paraphrase.

    Every build reads his view of the role (package.his_view: K and M off
    his row as it reads now, and hunter's case in L) and the tailor call
    carries it, so a package for a role he wants is written from his reasons.

    rebuild is rebuild-packages replacing a package he has not acted on with
    one written from those reasons. The package already built is worth more
    than a worse one, so a rebuild whose prose did not come through (no
    model, the gate refused it) builds nothing, and one that fails
    verification takes the earlier package out of every send path
    (rebuild_did_not_land): he asked for a different one."""
    from .package.build import build_package, doc_url, read_master_facts
    from .package.tailor import load_blocks, tailor

    if style_mod.builds_paused():
        # Before anything is read, fetched or paid for: no composer here can
        # write his house style, so whatever this built could never be sent
        # (package/style.py). Nothing is written to the record, so the role
        # is chosen again once a build can carry the style. Counted by every
        # caller as skipped, never as a refusal.
        summary.append(f"SKIPPED {row['job_id']}: {style_mod.PAUSED}; nothing "
                       f"fetched or built")
        return False
    if cache is None:
        from .ats import discover as disc
        cache = disc.load_cache(cfg)
    srow = _sheet_row_for(sheet, canon, row)
    # Why this build is happening, read again off the row as it is now and
    # before anything is fetched or paid for. A score he lowered, or a
    # Declined he typed, between the selection and this row's turn used to
    # be built anyway: nothing re-read the row but for its number. Only a
    # row that came here for a reason is held to it; build --job-id and an
    # amend of a package built before the trigger was recorded carry none.
    was = row.get("build_trigger") or row.get("package_trigger") or ""
    relies = (bool(row.get("build_trigger")) or was == router.YES_TRIGGER
              or was.startswith(router.SCORE_TRIGGER) or was.startswith(router.WITHDRAWN))
    trigger = router.build_trigger(srow) if srow else None
    if relies and trigger is None:
        summary.append(f"SKIPPED {row['job_id']}: the row no longer reads Yes in "
                       f"column A or 7 or more in {sheet_mod.KRISH_SCORE}"
                       + ("" if srow else ", or has left Pipeline")
                       + "; nothing fetched or built")
        return False
    if row.get("build_trigger") and trigger != row["build_trigger"]:
        # Still wanted, for a different reason or a different score than it
        # was chosen on. The next pass chooses it again on what it reads then,
        # so a refused build is recorded against the score that asked for it.
        summary.append(f"SKIPPED {row['job_id']}: the row changed since it was "
                       f"chosen; the next pass builds it on what it reads then")
        return False
    # His score wins over an older Yes (canon 9.13 from v11). The selections
    # already refuse a Yes he has scored 1 to 5 or answered with only a
    # reaction, but a run takes minutes a role and he scores while it runs:
    # this is the last read before paying, and a package written from his no
    # would be mailed to him the day he scores the role up again. Said
    # without the role: the line is public, and the reason is his score.
    if (trigger == router.YES_TRIGGER and leave_mod.in_canon(canon)
            and leave_mod.yes_overridden(srow)):
        summary.append("SKIPPED: a Yes his score has since taken back; nothing "
                       "fetched or built")
        return False
    if rebuild and (srow is None or router.wants(srow) is None):
        # A rebuild is for a role he still wants an application for, read
        # off his row now: a Yes he has since scored 6, or called a
        # Duplicate, keeps the package it has rather than a paid new one.
        summary.append("SKIPPED: a rebuild for a row that no longer asks for an "
                       "application; nothing fetched or built")
        return False
    attempt = row.get("attempt") if trigger and trigger != router.YES_TRIGGER else None
    # His house style (letters.py): what the build waits on is read off the
    # record before the posting is fetched, so a role held for his pick of
    # header, for a line of his, or for the writer costs nothing an hour and
    # takes no place under any cap (SKIPPED is never counted against one).
    house_plan = house_view = None
    house_text = ""
    if style_mod.house_builds():
        from . import letters as letters_mod
        from .package import his_view as his_view_mod
        house_view = his_view_mod.his_view(cfg, row["job_id"], srow, record=row)
        house_text = his_view_mod.section(house_view, company=row.get("company") or "")
        house_plan = letters_mod.plan(cfg, canon, row["job_id"], house_text)
        if house_plan.reason:
            letters_mod.hold(cfg, row["job_id"], house_plan.reason, base_key=house_plan.base_key)
            summary.append(f"SKIPPED {row['job_id']}: {house_plan.reason}; nothing fetched "
                           f"or built")
            return False

    def refused(patch: dict, *, final: bool = True) -> dict:
        # A score build that fails is recorded with what it saw, so the hourly
        # pass does not pay to fail the same way again (router.attempt_of).
        if attempt:
            patch["package_trigger"] = router.not_built(trigger, attempt, final=final)
        return patch

    role, relink, rflags = resolve_for_build(
        cfg, row, cache,
        sheet_snippet=(srow.cell("JD Snippet") if srow else ""),
        sheet_why=(srow.cell(sheet_mod.AI_WHY) if srow else ""))
    if getattr(role, "liveness", "checked") == "checked" and not role.live:
        # Verifiably gone. The row stays on Pipeline with column A untouched
        # (canon: hunter never archives a role Krish approved); Package
        # Status says why nothing was built.
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 refused({"status": "dead", "package_status": "blocked",
                          "rejection_reason": "G1: posting dead at build time"}))
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
        if attempt and row.get("attempt_masters"):
            # What the next pass will see is the relinked row and record. A
            # refusal fingerprinted on the old links would not match it, and
            # the same refusal would be paid for a second time.
            attempt = router.attempt_fingerprint(row.get("krish_score"), relink, relink,
                                                 row["attempt_masters"])
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
            # location or a band he had already read on the sheet. So does
            # his score of 7 or more (canon 9.4, v10): the row showed him the
            # same band and the same location when he scored it.
            # "your score", never the number: this line is printed by build
            # and by the amend rebuild in approvals-drain, beside the role,
            # in a public log (CLAUDE.md 3f). The record keeps the number.
            why = ("your score" if trigger and trigger != router.YES_TRIGGER
                   else "your Yes")
            rflags.append(f"built on {why} over {reasons}")
        else:
            db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                     refused({"package_status": "blocked", "rejection_reason": reasons}))
            summary.append(f"BLOCKED {row['job_id']}: {reasons}")
            return False

    if house_plan is not None:
        return house_build(cfg, canon, sheet, row, role, srow, summary, plan=house_plan,
                           view=house_view, view_text=house_text, trigger=trigger,
                           refused=refused, attempt=attempt, rebuild=rebuild, rflags=rflags,
                           never=never, company_declines=company_declines)
    letter_blocks, cv_blocks = load_blocks(cfg)
    oauth = GoogleOAuth(cfg)
    db = DocBuild(oauth.access_token)
    facts = read_master_facts(db)
    # Evidence is what lets tailor generate the summary and the hook at all: the
    # voice gate traces every number and company name in generated prose against
    # it, and passing it empty disables generation rather than allowing ungated
    # text. The haystack is the master's own words plus Krish's recorded proof
    # points and long-form answers from the workbook, plus the JD.
    from .apply.infobank import load_bank
    from .package.voicegate import build_evidence, package_banned
    bank = None
    evidence = ""
    # The mindmake engagement records and the two named programs. Without this key
    # the gate rejects any AI-native GTM claim, because it can trace none of it:
    # that is why the first real package never mentioned the work Krish is most
    # experienced in. optional(), not require(): a missing key costs that one story
    # and must not cost the application. See apply/gtmseed.py.
    # The naming law and the arrogance list apply whether or not the workbook
    # tabs load (voicegate.package_banned).
    banned = package_banned()
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
        banned = package_banned(bank.banned_phrases)
    except Exception as e:
        summary.append(f"note {row['job_id']}: answer tabs unreadable "
                       f"({e.__class__.__name__}), generated prose disabled")
    # What he said about this role, off his row as it reads now: his score,
    # his reaction, hunter's case. Read here rather than by the callers, so
    # the hourly score builds, process's Yes builds, an amend and a rebuild
    # all carry it. Never printed: the summary lines are public.
    from .package import his_view as his_view_mod
    view = his_view_mod.his_view(cfg, row["job_id"], srow, record=row)
    if rebuild and view.empty:
        summary.append(f"HELD {row['job_id']}: the row no longer carries a score or "
                       f"a reaction, so there is nothing to rebuild it from; the "
                       f"package built before is not sent")
        summary.extend(rebuild_did_not_land(cfg, row, "no score or reaction on the row "
                                                      "to write it from"))
        return False
    tr = tailor(cfg, canon, company=role.company, title=role.title,
                jd_text=role.jd_text, master_competencies=facts.cv_competencies,
                letter_blocks=letter_blocks, highlights=facts.cv_highlights,
                evidence=evidence, banned_phrases=banned,
                feedback=feedback,
                keep_verbatim=tuple(facts.cv_summary_bold),
                his_view=view)
    if rebuild and not (getattr(tr, "summary", "") or getattr(tr, "hook", "")):
        # The approved blocks alone are no rebuild: the package already there
        # may carry generated prose, and this one would carry none of his
        # reasons either. The tailor call is paid; no document is made.
        summary.append(f"HELD {row['job_id']}: the prose written from his reasons did "
                       f"not come through ({'; '.join(tr.flags)[:240] or 'no reason given'}), "
                       f"so no document was made and the package built before is not sent")
        summary.extend(rebuild_did_not_land(cfg, row, "the prose written from his "
                                                      "reasons did not come through"))
        from .package.tailor import GATE_FELL_BACK
        if GATE_FELL_BACK in (getattr(tr, "flags", None) or []):
            summary.extend(record_held(cfg, row, view))
        return False
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
        # One more try on the same inputs, then it waits: the letter is a
        # model's output and a second call can pass where the first did not,
        # but every try is paid for.
        final = bool(attempt) and router.retry_of(row.get("package_trigger")) == attempt
        if rebuild:
            # The package built before is still on the row, and it is the one
            # he asked to have replaced: blocked, so no path sends it, and
            # recorded as tried, so the next run does not pay to fail again.
            summary.append(f"REBUILD FAILED {row['job_id']} at verification: "
                           + "; ".join(fails) + "; the package built before is not sent")
            summary.extend(rebuild_did_not_land(
                cfg, row, f"package verification failed: {fails}"))
            summary.extend(record_held(cfg, row, view))
        else:
            db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                     refused({"package_status": "blocked",
                              "rejection_reason": f"package verification failed: {fails}"},
                             final=final))
            # Said out loud, not only written to the row. This branch printed
            # nothing at all until 2026-09-15, so a build that correctly refused
            # to ship a two-page letter looked exactly like a build that did
            # nothing.
            summary.append(f"BLOCKED {row['job_id']} at verification: "
                           + "; ".join(fails))
        for note in result.notes:
            summary.append(f"  note: {note}")
        if result.letter_doc_id:
            summary.append(f"  CL {doc_url(result.letter_doc_id)} (left for review)")
        if result.cv_doc_id:
            summary.append(f"  CV {doc_url(result.cv_doc_id)} (left for review)")
        return False

    built = {
        "package_status": "built", "package_built_at": NOW(),
        "package_cv_url": result.cv_url, "package_letter_url": result.letter_url,
        "status": "staging" if row.get("status") in ("dead", "blocked") else row.get("status"),
    }
    if trigger:
        # What asked for it, as it read on the row a moment ago. Never column
        # A and never krish_verdict: a score of his is not a Yes of his.
        built["package_trigger"] = trigger
    db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, built)
    if trigger:
        summary.extend(record_trigger(cfg, row["job_id"], trigger))
    status = package_status_for(role, row)
    if srow:
        try:
            sheet.update_package_cells(
                srow.row_number, cv_url=result.cv_url,
                letter_url=result.letter_url, cv_pdf_url=result.cv_pdf_url,
                letter_pdf_url=result.letter_pdf_url, package_status=status,
                built_date=TODAY())
        except Exception:
            # The write may have landed in part, or whole with its read-back
            # failing. What amend reads his edits against has to be the text
            # of the documents his row links now, whichever they are: left
            # on the old letter beside a row that links the new one, the next
            # pass files hunter's own rewrite as his edit, and rebuild-packages
            # never tries this role again.
            summary.extend(baseline_for_linked(cfg, sheet, srow.row_number,
                                               row["job_id"], result))
            raise
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
    # Which of his reasons it carries, last: only once his row reads back with
    # the new links, so a rebuild whose row write failed is chosen again.
    summary.extend(record_his_view(cfg, row["job_id"], view, tr))

    # build_package already copies tr.flags into result.notes, so adding tr.flags
    # here printed every tailoring flag twice in the run summary.
    flags = "; ".join(result.notes + rflags) or "clean"
    summary.append(f"BUILT {row['job_id']} block={tr.block_key} "
                   f"words={result.letter_report.body_word_count} flags={flags}")
    summary.append(f"  CV {result.cv_url}")
    summary.append(f"  CL {result.letter_url}")
    return True


def house_evidence(cfg: Config, sheet: Sheet, db: DocBuild, jd_text: str) -> dict:
    """What the writer is given and held to: his CV (the house master), his
    Profile, Interview Answers and Info Bank, his AI GTM record and the
    posting; the model letter he rewrote; the phrases he has banned. The
    evidence is shown to the writer in its own case and lowercased only for
    the gate."""
    from .apply.infobank import load_bank
    from .package.voicegate import build_evidence, package_banned
    cv_text = _doc_text(db, config_mod.CV_MASTER_ID if style_mod.COMPOSER_LIVE
                        else config_mod.HOUSE_CV_MASTER_ID)
    model = _doc_text(db, config_mod.LETTER_MODEL_ID)
    bank = load_bank(lambda tab: sheet.read_tab_formulas(f"{tab}!A1:Z400"))
    parts = [cv_text, "\n".join(bank.profile.values()), "\n".join(bank.interview.values()),
             "\n".join(e.value for e in bank.entries.values()),
             cfg.optional("hunter_ai_gtm_evidence") or ""]
    text = "\n\n".join(x for x in parts if x)
    return {"text": text, "haystack": build_evidence(text, jd_text, model),
            "banned": package_banned(bank.banned_phrases), "model": model}


def house_build(cfg: Config, canon: Canon, sheet: Sheet, row: dict, role, srow,
                summary: list[str], *, plan, view, view_text: str, trigger, refused, attempt,
                rebuild: bool, rflags: list[str], never, company_declines,
                db: DocBuild | None = None, page_count=None, bases=None,
                evidence: dict | None = None) -> bool:
    """A role's package in his house style (letters.py), once its posting
    is fetched and gated: the letter is asked of the writer, or built from
    the writer's checked answer, read back, and recorded. Codes and counts in
    every summary line."""
    import types
    from . import letters as letters_mod
    from .package import compose, contact, house, masters as masters_mod
    from .package import his_view as his_view_mod
    from .package.build import _unique_title, doc_url, pdf_url, slugify
    job = row["job_id"]
    db = db or DocBuild(GoogleOAuth(cfg).access_token)
    if page_count is None:
        from .apply.merge import page_count
    if plan.draft is None:
        ev = evidence or house_evidence(cfg, sheet, db, role.jd_text)
        letters_mod.queue(cfg, plan, company=role.company, title=role.title,
                          location=getattr(role, "location", "") or row.get("location") or "",
                          jd_text=role.jd_text, judge_answers=plan.judge_answers,
                          view_text=view_text, model_letter=ev["model"],
                          evidence_text=ev["text"], evidence=ev["haystack"], banned=ev["banned"])
        letters_mod.hold(cfg, job, letters_mod.WAITING, base_key=plan.base_key)
        summary.append(f"SKIPPED {job}: {letters_mod.WAITING}; the letter is asked of the "
                       f"routine, never the API")
        return False
    letter, problems = letters_mod.checked(plan)
    if letter is not None and his_view_mod.quotes_note(" ".join(letter.spans()), view,
                                                       plan.draft.get("evidence") or ""):
        problems = ["the letter quotes his note"]
    if problems:
        letters_mod.mark(cfg, plan.draft["id"], "failed", problems)
        letters_mod.hold(cfg, job, "the writer's letter did not pass its check; asked again",
                         base_key=plan.base_key)
        summary.append(f"BLOCKED {job}: the writer's letter failed its check "
                       f"({len(problems)} problem(s)); it is asked again")
        return False
    bases = bases if bases is not None else contact.load_bases(cfg)
    base = bases.get(plan.base_key)
    if base is None:
        letters_mod.hold(cfg, job, "the header for this role is not readable",
                         base_key=plan.base_key)
        summary.append(f"BLOCKED {job}: the {plan.base_key.upper()} header is not readable")
        return False
    date_text = datetime.date.today().strftime("%B %d, %Y").replace(" 0", " ")
    letter_master = config_mod.LETTER_MASTER_ID
    cv_master = config_mod.CV_MASTER_ID
    lt = compose.build_letter(
        db, letter, master_id=letter_master,
        title=_unique_title(db, f"KrishRaja_CoverLetter_{role.company}",
                            config_mod.LETTER_FOLDER_ID, slugify(role.title)),
        folder_id=config_mod.LETTER_FOLDER_ID, base=base, bases=bases, date_text=date_text,
        company=role.company, role=role.title, page_count=page_count, needs=plan.needs,
        entries=plan.entries, bank_words=plan.bank)
    cv_paras = house.snapshot(db.get(cv_master))
    anchor, count = compose.cv_summary_span(cv_paras)
    cv = compose.build_cv(
        db, master_id=cv_master,
        title=_unique_title(db, f"KrishRaja_CV_{role.company}", config_mod.CV_FOLDER_ID,
                            slugify(role.title)),
        folder_id=config_mod.CV_FOLDER_ID, base=base, bases=bases, page_count=page_count,
        heading_sizes=masters_mod._cv_heading_sizes(cv_paras), summary=letter.cv_summary,
        summary_anchor=anchor, summary_count=count)
    fails = lt.problems + cv.problems
    if not fails:
        pkg = run_gates(role, never_apply=never, package_texts=(
            _doc_text(db, lt.doc_id), _doc_text(db, cv.doc_id)), company_declines=company_declines)
        fails = [f"{g.gate}: {g.reason}" for g in pkg.failures() if g.gate not in BUILD_SOFT_GATES]
    if fails:
        final = bool(attempt) and router.retry_of(row.get("package_trigger")) == attempt
        letters_mod.mark(cfg, plan.draft["id"], "failed", fails)
        if rebuild:
            summary.append(f"REBUILD FAILED {job} at read-back: " + "; ".join(fails)
                           + "; the package built before is not sent")
            summary.extend(rebuild_did_not_land(cfg, row, f"house style read-back failed: {fails}"))
        else:
            db_patch(cfg, "hunter_seen_roles", {"job_id": job},
                     refused({"package_status": "blocked", "header_base": plan.base_key,
                              "rejection_reason": f"house style read-back failed: {fails}"},
                             final=final))
            summary.append(f"BLOCKED {job} at read-back: " + "; ".join(fails))
        for note in lt.notes + cv.notes:
            summary.append(f"  note: {note}")
        summary.append(f"  CL {doc_url(lt.doc_id)} (left for review)")
        summary.append(f"  CV {doc_url(cv.doc_id)} (left for review)")
        return False
    letter_pdf = db.upload_pdf(f"KrishRaja_CoverLetter_{role.company}.pdf",
                               config_mod.LETTER_FOLDER_ID, lt.pdf or db.export_pdf(lt.doc_id))
    cv_pdf = db.upload_pdf(f"KrishRaja_CV_{role.company}.pdf", config_mod.CV_FOLDER_ID,
                           cv.pdf or db.export_pdf(cv.doc_id))
    built = {
        "package_status": "built", "package_built_at": NOW(),
        "package_cv_url": doc_url(cv.doc_id), "package_letter_url": doc_url(lt.doc_id),
        "status": "staging" if row.get("status") in ("dead", "blocked") else row.get("status"),
        "rejection_reason": None, "header_base": plan.base_key,
        style_mod.FIELD: style_mod.HOUSE_STYLE,
        "package_personal": plan.personal_key or None,
        "package_asks": letters_mod.asks_summary(letter),
    }
    if trigger:
        built["package_trigger"] = trigger
    db_patch(cfg, "hunter_seen_roles", {"job_id": job}, built)
    back = db_get(cfg, "hunter_seen_roles", {"select": f"package_status,{style_mod.FIELD}",
                                             "job_id": f"eq.{job}"})
    if not back or back[0].get(style_mod.FIELD) != style_mod.HOUSE_STYLE:
        summary.append(f"  note {job}: the house style stamp did not read back, so no send "
                       f"path will offer this package")
    letters_mod.mark(cfg, plan.draft["id"], "used")
    if trigger:
        summary.extend(record_trigger(cfg, job, trigger))
    status = package_status_for(role, row)
    if srow:
        sheet.update_package_cells(
            srow.row_number, cv_url=doc_url(cv.doc_id), letter_url=doc_url(lt.doc_id),
            cv_pdf_url=pdf_url(cv_pdf), letter_pdf_url=pdf_url(letter_pdf),
            package_status=status, built_date=TODAY())
    try:
        amend.save_docs(cfg, job, letter_text=_doc_text(db, lt.doc_id),
                        cv_text=_doc_text(db, cv.doc_id))
        db_patch(cfg, "hunter_row_state", {"job_id": job}, {
            "letter_doc_id": lt.doc_id, "cv_doc_id": cv.doc_id,
            "letter_style": letters_mod.style_record(db.get(lt.doc_id)),
            "cv_style": letters_mod.style_record(db.get(cv.doc_id))})
    except Exception as e:
        summary.append(f"  note: could not record the documents as built "
                       f"({e.__class__.__name__}), so edits to them will not be read")
    try:
        db_insert(cfg, "hunter_jd_briefs", [letters_mod.brief_row(plan, letter, role.jd_text)],
                  on_conflict="jd_hash,hints_hash", merge=True)
    except Exception:
        pass
    carried = types.SimpleNamespace(summary="", hook=letter.hook if not view.empty else "")
    summary.extend(record_his_view(cfg, job, view, carried))
    notes = "; ".join(lt.notes + cv.notes + rflags) or "clean"
    asks = letters_mod.asks_summary(letter)
    summary.append(f"BUILT {job} house style, {plan.base_key.upper()} header, "
                   f"{len(letter.bullets)} bullets, asks {asks['answered']} of {asks['asks']}"
                   f"{', personal line' if plan.needs else ''}; notes={notes}")
    summary.append(f"  CV {doc_url(cv.doc_id)}")
    summary.append(f"  CL {doc_url(lt.doc_id)}")
    return True


PREVIEW_FOLDER = "hunter preview"


def cmd_build_preview(job_ids: list[str], *, queue: bool, folder: str, apply: bool,
                      preview_folder: bool = False, db: DocBuild | None = None,
                      page_count=None) -> int:
    """The scratch preview (his review before anything live changes): for each
    role, the letter is asked of the writer (--queue), and once written, the
    letter and CV are built in his house style into `folder` (--apply) from
    the house masters. Nothing else: no sheet, no email, no status, no fact
    asked of him, nothing recorded on the role. Links and codes only."""
    import types
    from . import letters as letters_mod
    from .package import compose, contact, house, masters as masters_mod
    from .package import his_view as his_view_mod
    from .package.build import doc_url
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    db = db or DocBuild(GoogleOAuth(cfg).access_token)
    if page_count is None:
        from .apply.merge import page_count
    bases = contact.load_bases(cfg)
    if apply and preview_folder and not folder:
        # His Drive's own top level, made by hunter's own login so the copies
        # can land in it; the same folder on every later run.
        folder = db.folder(PREVIEW_FOLDER)
        print(f"folder: https://drive.google.com/drive/folders/{folder}")
    asked: list[str] = []
    for job in job_ids:
        rec = db_get(cfg, "hunter_seen_roles", {"select": "*", "job_id": f"eq.{job}"})
        if not rec:
            print(f"{job}: no such role")
            continue
        r = rec[0]
        view = his_view_mod.his_view(cfg, job, None, record=r)
        text = his_view_mod.section(view, company=r.get("company") or "")
        plan = letters_mod.plan(cfg, canon, job, text, request_fact=lambda c: asked.append(c))
        if plan.reason and plan.reason != letters_mod.WAITING:
            print(f"{job}: held, {plan.reason}")
            continue
        if plan.draft is None:
            if not queue:
                print(f"{job}: {letters_mod.WAITING if plan.reason else 'not asked yet'}")
                continue
            ev = house_evidence(cfg, sheet, db, r.get("jd_text") or "")
            made = letters_mod.queue(
                cfg, plan, company=r.get("company") or "", title=r.get("title") or "",
                location=r.get("location") or "", jd_text=r.get("jd_text") or "",
                judge_answers=plan.judge_answers, view_text=text, model_letter=ev["model"],
                evidence_text=ev["text"], evidence=ev["haystack"], banned=ev["banned"])
            print(f"{job}: letter {'asked of the writer' if made else 'already asked'}")
            continue
        letter, problems = letters_mod.checked(plan)
        if problems:
            print(f"{job}: the written letter fails its check: " + "; ".join(problems))
            continue
        if not apply:
            print(f"{job}: written and checked; --apply builds it into the folder")
            continue
        base = bases[plan.base_key]
        role = types.SimpleNamespace(company=r.get("company") or "", title=r.get("title") or "")
        lt = compose.build_letter(
            db, letter, master_id=config_mod.HOUSE_LETTER_MASTER_ID,
            title=f"PREVIEW CoverLetter {role.company}", folder_id=folder, base=base,
            bases=bases, date_text=datetime.date.today().strftime("%B %d, %Y").replace(" 0", " "),
            company=role.company, role=role.title, page_count=page_count, needs=plan.needs,
            entries=plan.entries, bank_words=plan.bank)
        paras = house.snapshot(db.get(config_mod.HOUSE_CV_MASTER_ID))
        anchor, count = compose.cv_summary_span(paras)
        cv = compose.build_cv(
            db, master_id=config_mod.HOUSE_CV_MASTER_ID, title=f"PREVIEW CV {role.company}",
            folder_id=folder, base=base, bases=bases, page_count=page_count,
            heading_sizes=masters_mod._cv_heading_sizes(paras), summary=letter.cv_summary,
            summary_anchor=anchor, summary_count=count)
        # A baseline under its own key, so his edits to a preview become
        # pairs for the writer without touching the role's own record.
        try:
            amend.save_docs(cfg, f"preview:{job}", letter_text=_doc_text(db, lt.doc_id),
                            cv_text=_doc_text(db, cv.doc_id))
            db_patch(cfg, "hunter_row_state", {"job_id": f"preview:{job}"}, {
                "letter_doc_id": lt.doc_id, "cv_doc_id": cv.doc_id,
                "letter_style": letters_mod.style_record(db.get(lt.doc_id)),
                "cv_style": letters_mod.style_record(db.get(cv.doc_id))})
        except Exception as e:
            print(f"{job}: baseline not recorded ({e.__class__.__name__})")
        asks = letters_mod.asks_summary(letter)
        print(f"{job}: letter {lt.pages} page(s), CV {cv.pages} page(s), "
              f"{plan.base_key.upper()} header, asks {asks['answered']} of {asks['asks']}, "
              f"problems {len(lt.problems) + len(cv.problems)}")
        for f in lt.problems + cv.problems:
            print(f"  {f}")
        for n in lt.notes + cv.notes:
            print(f"  note: {n}")
        print(f"  CL {doc_url(lt.doc_id)}")
        print(f"  CV {doc_url(cv.doc_id)}")
    if asked:
        print(f"{len(set(asked))} kind(s) of business would ask him for a line; none asked "
              f"from a preview")
    return 0


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


# His ruling 2026-10-13: a Krish Score of 7 or more builds the package. The
# backlog that ruling found was 32 scored rows with no package; built inside
# process, which every full run calls before it sources, they would have
# added more than an hour to the batch run he is waiting on, and a retry of
# that run pays for its judging twice (CLAUDE.md 3e). So his scores build in
# their own pass, hourly, a few at a time.
SCORE_BUILDS_KEY = "hunter_max_score_builds_per_run"
SCORE_BUILDS_DEFAULT = 3


# The words canon 9.13 carries from v10, which Krish approves before the
# pass may build. Read from the live canon every run, not from this file.
CANON_SCORE_RULE = "a Krish Score of 7 or more in K"


def scores_build_in_canon(canon: Canon) -> bool:
    try:
        return CANON_SCORE_RULE in canon.section_text("9.13")
    except Exception:
        return False


def masters_fingerprint(cfg: Config) -> str | None:
    """What a package is tailored from, as 16 hex characters: the text of the
    master CV and the master letter, and his approved letter and summary
    blocks. None when any of it cannot be read, which is not "unchanged"."""
    import hashlib
    from .package.tailor import load_blocks
    try:
        letter_blocks, cv_blocks = load_blocks(cfg)
        db = DocBuild(GoogleOAuth(cfg).access_token)
        texts = [_doc_text(db, config_mod.CV_MASTER_ID),
                 _doc_text(db, config_mod.LETTER_MASTER_ID)]
    except Exception:
        return None
    blob = json.dumps([texts, letter_blocks, cv_blocks], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def score_build_step(cfg: Config, canon: Canon, sheet: Sheet, *, apply: bool,
                     cap: int, detail: list[str], masters=None,
                     company_declines: dict | None = None, gate=None) -> dict:
    """Build at most `cap` packages his score asked for, best first.

    Every number this returns is a count. What was built for which role is in
    `detail`, which the caller never prints: the repository and its Actions
    logs are public, and a role built here is a role he scored 7 or more.
    hunter_seen_roles holds the outcome per role (package_trigger,
    package_status, rejection_reason), and Control Center reads it there."""
    from .ats import discover as disc
    report: dict = {}
    todo = select_for_build(cfg, sheet, canon.sheet_headers, cap=0,
                            triggers=(router.SCORE,),
                            masters=masters or (lambda: masters_fingerprint(cfg)),
                            report=report)
    out = {"eligible": len(todo), "chosen": 0, "built": 0, "unverified": 0, "dead": 0,
           "blocked": 0, "skipped": 0, "failed": 0,
           "waiting": len(report.get("waiting") or []),
           "unpaired": report.get("unpaired", 0),
           "masters_unread": bool(report.get("masters_unread")),
           "preflight_failed": False}
    # What a dry run can say: the rows the pass would start on. A posting
    # found gone, or a row that changed under it, costs no tailor call and
    # uses no place, so the pass goes on down the list; read live on
    # 2026-10-14, the third row of the first pass was a posting already gone.
    out["chosen"] = min(cap, len(todo)) if cap > 0 else 0
    if not apply or not out["chosen"]:
        return out
    # The keys are proved before the first paid call, and only on an hour
    # that has something to build.
    if gate is not None and not gate():
        out["preflight_failed"] = True
        return out
    cache = disc.load_cache(cfg)
    out["chosen"] = 0
    for row in todo:
        if out["built"] + out["blocked"] + out["failed"] >= cap:
            break
        out["chosen"] += 1
        before = len(detail)
        try:
            if build_one(cfg, canon, sheet, row, detail, cache=cache,
                         company_declines=company_declines):
                out["built"] += 1
                if any("liveness unverified" in line for line in detail[before:]):
                    out["unverified"] += 1
                continue
            said = detail[before:]
            if any(line.startswith(f"DEAD {row['job_id']}") for line in said):
                out["dead"] += 1
            elif any(line.startswith(f"SKIPPED {row['job_id']}") for line in said):
                out["skipped"] += 1
            else:
                out["blocked"] += 1
        except Exception as e:
            detail.append(f"BUILD FAILED {row['job_id']}: {e.__class__.__name__}")
            # Counted as what the record and his row say now, as process does:
            # a package whose row reads back with its new links was built.
            outcome, lines = build_error_outcome(cfg, row["job_id"], e, sheet=sheet,
                                                 canon=canon, row=row)
            detail.extend(lines)
            out["built" if outcome == BUILD_KEPT else "failed"] += 1
    try:
        disc.save_cache(cfg, cache)
    except Exception:
        pass
    return out


# What a build that raised turned out to be, read back (build_error_outcome).
BUILD_KEPT, BUILD_RETRY, BUILD_BLOCKED, BUILD_UNKNOWN = "built", "retry", "blocked", "unknown"
# The status a record carries before its first build, and the one
# select_for_build builds again: anything but "built".
RETRY_STATUS = "none"


def build_error_outcome(cfg: Config, job_id: str, e: Exception, *,
                        sheet: Sheet | None = None, canon: Canon | None = None,
                        row: dict | None = None) -> tuple[str, list[str]]:
    """A build that raised, written to its record as an error and never as
    a refusal, and what the record says now (BUILD_*), with the lines to
    add to the private detail.

    No attempt fingerprint: a Sheets 429, a board's 5xx or a Docs error says
    nothing about the score, the link or the masters, so the role is tried
    again next hour, and an error that keeps happening turns every hourly
    run red (cmd_score_builds), which is the alarm. Before 2026-10-14 the
    fingerprint went on here too, and one ConnectionError parked a role he
    scored 9 until he changed the score, with the sheet still saying "Not
    started".

    A record build_one had already patched built is decided from his row as
    it reads now, because build_one records the package before it writes
    the links to the sheet, and that write can fail either way. The row
    links the new documents: the write landed and only its read-back
    failed, so the record stays built. The row links nothing: the write
    never landed (a 429 on the batchUpdate POST), and a record left built
    beside a row with no links was never selected again, so the Yes he
    wanted never reached him; the record goes back to RETRY_STATUS, which
    select_for_build builds again. The row still links an older package (a
    rebuild): the record is put back to the documents the row links, and
    stays built. A record or a row that cannot be read back is left as it
    is: unknown is not "not built". Lines only, for the private detail."""
    try:
        now = db_get(cfg, "hunter_seen_roles", {
            "select": "package_status,package_cv_url,package_letter_url",
            "job_id": f"eq.{job_id}", "limit": "1"})
    except Exception:
        return BUILD_UNKNOWN, [f"  the record of {job_id} could not be read after the "
                               f"error; left as it was"]
    if now and (now[0].get("package_status") or "") == "built":
        if sheet is None or canon is None or row is None:
            return BUILD_KEPT, [f"  {job_id} was built and recorded before the error; "
                                f"left as built"]
        want = (now[0].get("package_cv_url") or "", now[0].get("package_letter_url") or "")
        try:
            srow = _sheet_row_for(sheet, canon, row)
            landed = ((srow.package_urls.get("cv") or "", srow.package_urls.get("letter") or "")
                      if srow is not None else None)
        except Exception as x:
            # Neither the write nor its read-back could be seen: unknown, and
            # counted as unknown, never as built (CLAUDE.md 1).
            return BUILD_UNKNOWN, [f"  {job_id} was recorded built before the error, and "
                                   f"its row could not be read back ({x.__class__.__name__}); "
                                   f"left as it was, not known to have reached his row"]
        if landed is None:
            return BUILD_UNKNOWN, [f"  {job_id} was recorded built before the error, and its "
                                   f"row is not on Pipeline now; left as it was"]
        if landed == want:
            return BUILD_KEPT, [f"  {job_id} was built and its row reads back with the new "
                                f"links; left as built"]
        try:
            if landed[0] and landed[1]:
                db_patch(cfg, "hunter_seen_roles", {"job_id": job_id},
                         {"package_cv_url": landed[0], "package_letter_url": landed[1]})
                return BUILD_RETRY, ["  1 build whose row write did not land: its row "
                                     "still links the package before, and the record says "
                                     "so again"]
            # And the links to documents that never reached his row go with
            # it, so Control Center does not show a package that is not there.
            db_patch(cfg, "hunter_seen_roles", {"job_id": job_id},
                     {"package_status": RETRY_STATUS,
                      "package_cv_url": None, "package_letter_url": None,
                      "package_built_at": None,
                      "rejection_reason": f"build error: {e.__class__.__name__}; the row "
                                          f"write did not land"})
        except Exception:
            return BUILD_UNKNOWN, [f"  {job_id}: the row write did not land, and the record "
                                   f"could not be put back"]
        # Counts, without the role: said once for the run by the caller too.
        return BUILD_RETRY, ["  1 build whose row write did not land: the record is set "
                             "to be built again"]
    try:
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id},
                 {"package_status": "blocked",
                  "rejection_reason": f"build error: {e.__class__.__name__}"})
    except Exception:
        return BUILD_UNKNOWN, [f"  and the failure was not recorded on {job_id}"]
    return BUILD_BLOCKED, []


def record_build_error(cfg: Config, job_id: str, e: Exception, *,
                       sheet: Sheet | None = None, canon: Canon | None = None,
                       row: dict | None = None) -> list[str]:
    """build_error_outcome's lines alone."""
    return build_error_outcome(cfg, job_id, e, sheet=sheet, canon=canon, row=row)[1]


def score_build_lines(out: dict, *, apply: bool, cap: int) -> list[str]:
    """Counts, never a role: see score_build_step."""
    lines = [f"score builds: {out['eligible']} row(s) scored 7 or more with no package "
             f"that can be built now; this pass took {out['chosen']} (at most {cap} "
             f"builds; a posting found gone uses no place), "
             f"{max(out['eligible'] - out['chosen'], 0)} wait for the next"]
    if out["waiting"]:
        lines.append(f"score builds: {out['waiting']} refused or found gone before and "
                     f"unchanged since (same score, same link, same masters), not tried "
                     f"again until one of those changes")
    if out["unpaired"]:
        lines.append(f"score builds: {out['unpaired']} row(s) with no record hunter "
                     f"can build from")
    if out["masters_unread"]:
        lines.append("score builds: the master CV, letter or blocks could not be read, "
                     "so nothing his score asked for was built")
    if apply and out["chosen"]:
        lines.append(f"score builds: {out['built']} built ({out['unverified']} liveness "
                     f"unverified), {out['dead']} posting(s) gone, {out['blocked']} "
                     f"refused, {out['skipped']} changed before their turn, "
                     f"{out['failed']} failed; reasons are on each role's record")
    elif out["chosen"]:
        lines.append("score builds: dry run, nothing built. add --apply to build")
    return lines


def cmd_score_builds(apply: bool = False, max_builds: int | None = None) -> int:
    """The hourly pass for his score: packages for rows he scored 7 or more.

    Dry run unless --apply. Capped at hunter_max_score_builds_per_run (3
    unless set; 0 switches the pass off), or --max N. Builds nothing until
    canon 9.13 carries the rule (scores_build_in_canon). Never called from
    process or run."""
    from . import spend
    cfg, canon = build_context()
    cap = max_builds if max_builds is not None else int(
        cfg.optional(SCORE_BUILDS_KEY, str(SCORE_BUILDS_DEFAULT)))
    if cap <= 0:
        # Off means off: no sheet read, no masters read, no red run. The likely
        # reason to set 0 is a Docs or OAuth incident, and reading the masters
        # anyway would turn the hourly run red for the whole of it.
        print("score builds: switched off, the cap is 0")
        return 0
    if style_mod.builds_paused():
        # No sheet read, no masters read: nothing built now could be sent
        # (package/style.py).
        print(f"score builds: waiting, {style_mod.PAUSED}")
        return 0
    if not scores_build_in_canon(canon):
        # Canon supersedes code (assert_canon_alignment). Until canon 9.13 says
        # a score builds, it says Yes is the only value that does, and this
        # pass pays for nothing.
        print("score builds: waiting, canon 9.13 does not yet say a Krish Score "
              "of 7 or more builds a package")
        return 0
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    if apply:
        stop = spend.over_budget(cfg)
        if stop:
            # An honest empty answer (CLAUDE.md 3e): a package tailored with no
            # model is the approved blocks alone, and his score asked for his.
            print(f"score builds: waiting, {stop}")
            return 0
    detail: list[str] = []
    declines, _notes = stored_company_declines(cfg)

    def gate() -> bool:
        may, lines = preflight.gate(preflight.run(cfg))
        if not may:
            print("\n".join(lines))
        return may

    out = score_build_step(cfg, canon, sheet, apply=apply, cap=cap, detail=detail,
                           company_declines=declines, gate=gate)
    if out["preflight_failed"]:
        print("score builds: preflight failed; nothing built")
        return 1
    for line in score_build_lines(out, apply=apply, cap=cap):
        print(line)
    for line in spend.summary():
        print(line)
    try:
        line = spend.flush(cfg)
        if line:
            print(line)
    except Exception as e:
        print(f"spend: ledger not written ({e.__class__.__name__})")
    # Red when a build raised (recorded as an error, and tried again next
    # hour) or when the masters could not be read with rows waiting on them.
    # A refused or dead posting is an answer, not a fault.
    return 1 if out["failed"] or out["masters_unread"] else 0


def seen_identity_keys(cfg: Config) -> set:
    return seen_identity(cfg)[0]


def seen_identity(cfg: Config) -> tuple[set, dict]:
    """(every key under which a recorded role counts as seen, {job_id: the
    countries its location names} for every row). Identity is company, title
    AND country (sources.placed_identity_keys): until 2026-10-03 it was
    company and title alone, and Sierra's Regional VP, Sales in London was
    filed as the South Korea one and never reached him."""
    keys: set = set()
    id_countries: dict = {}
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,url,job_url,company,title,location,status,presented_at",
        "limit": ALL_ROWS})
    for r in rows:
        id_countries[r["job_id"]] = country_of(r.get("location") or "")
        # A row hunter could not resolve was never actually assessed. Counting
        # it as seen means it can never be reconsidered once resolution
        # improves, and 1451 LinkedIn postings were sitting in exactly that
        # state when board discovery arrived.
        #
        # Unless it reached his sheet. A posting first recorded unresolved
        # keeps that status when a later sweep stages it (the insert ignores
        # a duplicate job id), so it was never seen, came back on the next
        # sweep, and was appended again: four roles each sat on Pipeline
        # twice on 2026-10-13. A role he has been shown is seen, whatever its
        # status says.
        if (r.get("status") in ("unresolved", "judge_pending")
                and not r.get("presented_at")):
            # judge_pending: the judge did not get to it, or its answer failed
            # its checks. It is judged again next time it is seen.
            continue
        keys.add(r["job_id"])
        keys.update(placed_identity_keys(r.get("company") or "", r.get("title") or "",
                                         r.get("location") or "", seen=True))
        u = r.get("url") or r.get("job_url") or ""
        if u.startswith("http"):
            keys.add(norm_url(u))
            ak = ats_key(u)
            if ak:
                keys.add(ak)
    return keys, id_countries


READABLE_BOARDS = ("greenhouse", "ashby", "lever")


def route_board_postings(posts: list, cache: dict) -> tuple[list, int]:
    """Split a portfolio board's postings into the ones its company boards
    will carry and the ones only the portfolio board has.

    A posting whose link names a Greenhouse, Ashby or Lever board teaches
    hunter that board (written into `cache`, which the full sweep reads) and
    is left to that sweep, which brings the whole posting text. Everything
    else is returned to be staged as it is. A board already on record under
    another name is not learned twice, and a company already mapped to a
    different board keeps its mapping. Returns (board_only, boards learned).
    """
    from .ats import discover as disc
    known = {(h.get("ats"), (h.get("slug") or "").lower())
             for h in cache.values() if not disc.is_miss(h)}
    only, learned = [], 0
    for bp in posts:
        if not bp.title or not bp.company:
            continue
        key = ats_key(bp.url)
        # A gh_jid link carries the posting id and no board, so it teaches
        # nothing about where the board is.
        if key and key[2]:
            bp.ats, bp.ats_slug, bp.ats_posting_id = key
            if key[0] in READABLE_BOARDS:
                board = (key[0], key[1].lower())
                ck = slugify(bp.company)
                have = cache.get(ck)
                if board not in known and ck and (not have or disc.is_miss(have)):
                    cache[ck] = {"ats": key[0], "slug": key[1]}
                    known.add(board)
                    learned += 1
                if board in known:
                    continue
        only.append(bp)
    return only, learned


def fresh_postings(postings: list, seen_keys: set, id_countries: dict) -> list:
    """The postings not already on record, each counted once. Identity is
    company, title and country, never the bare job id: that id is company and
    title only, and matching on it would undo the country. A posting whose
    bare id belongs to the same title in another country gets a suffixed id."""
    fresh = []
    for p in postings:
        keys = placed_identity_keys(p.company, p.title, p.location or "", seen=False)
        exact = []
        if p.url:
            exact.append(norm_url(p.url))
            ak = ats_key(p.url)
            if ak:
                exact.append(ak)
        if any(k in seen_keys for k in keys + exact):
            continue
        countries = country_of(p.location or "")
        base = job_id(p.company, p.title)
        suffix = place_suffix(base, countries, p.location or "", p.url or "", id_countries)
        if suffix:
            p.raw = dict(p.raw or {}, id_suffix=suffix)
        id_countries[base + suffix] = countries
        seen_keys.update(placed_identity_keys(p.company, p.title, p.location or "", seen=True))
        seen_keys.update(exact + [base + suffix])
        fresh.append(p)
    return fresh


def place_suffix(base: str, countries: frozenset, location: str, url: str,
                 id_countries: dict) -> str:
    """'' when the bare job id is free or held by the same role; otherwise a
    suffix naming this role's country, so a London seat and a Seoul seat with
    one title are two rows rather than one silently dropped insert."""
    held = id_countries.get(base)
    if held is None or not held or not countries or (held & countries):
        return ""
    suffix = "-" + slugify(sorted(countries)[0])
    if base + suffix in id_countries:
        import hashlib
        suffix += "-" + hashlib.sha1((url or location).encode()).hexdigest()[:6]
    return suffix


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
    """Canon is never edited from code. The drift is filed as a proposal.

    Until 2026-10-08 this never landed: it wrote a "body" column the table
    does not have, a status ("open") its check constraint refuses, no
    proposal_type, and an on_conflict with no index behind it, and the
    exception was swallowed. Now it files once, refreshes the text only while
    the proposal is still waiting on him, and never touches his decision.
    """
    title = "canon 9.1 has drifted from the Target Companies tab"
    text = ("The tab is the list Krish edits and the one hunter now "
            "sweeps. Canon 9.1 should be rewritten to match it.\n\n"
            "Only on the tab: " + (", ".join(only_tab) or "none") +
            "\nOnly in canon: " + (", ".join(only_canon) or "none") +
            "\n\nFull list (" + str(len(named)) + "): " + ", ".join(named))
    try:
        have = db_get(cfg, "workflow_proposals", {
            "select": "id,status", "agent_id": "eq.hunter",
            "proposal_type": "eq.quality_improve", "title": f"eq.{title}",
            "order": "created_at.desc", "limit": "1"})
        if not have:
            db_insert(cfg, "workflow_proposals", [{
                "agent_id": "hunter", "proposal_type": "quality_improve",
                "title": title, "description": text, "status": "proposed"}])
        elif have[0].get("status") == "proposed":
            db_patch(cfg, "workflow_proposals", {"id": have[0]["id"]},
                     {"description": text, "updated_at": NOW()})
    except Exception as e:
        # A proposal that cannot be filed must not take the sourcing run with
        # it, and it must not vanish either.
        print(f"  canon drift proposal not filed: {e.__class__.__name__}: {str(e)[:160]}")


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

    # The company lane (universe.py, radar.py), decided 2026-10-03: his top
    # companies' own boards, every run, free. Only roles that pass his
    # top-company rules join the sweep; the Company Radar tab is rewritten.
    if cfg.optional("hunter_company_lane", "1") != "0":
        try:
            from . import radar
            from .sources import RolePosting
            lane = radar.build(cfg, sheet, max_usd=float(
                cfg.optional("hunter_lookalike_max_usd_per_run", "3")))
            have = {p.url for p in postings}
            added = 0
            for o in lane["openings"]:
                if not o.url or o.url in have:
                    continue
                have.add(o.url)
                postings.append(RolePosting(
                    company=o.company.name, title=o.title, url=o.url,
                    source=f"{o.ats}:{o.slug}", location=o.location, comp_text=o.comp,
                    ats=o.ats, ats_slug=o.slug, ats_posting_id=o.posting_id,
                    raw={"descriptionPlain": o.description, "isRemote": o.remote,
                         "lane": "top company"}))
                added += 1
            summary.append(
                f"company lane: {lane['top']} top companies, {lane['swept']} boards "
                f"read, {len(lane['openings'])} roles pass his rules, {added} new to "
                f"this sweep; {lane['scored']} companies scored for ${lane['usd']:.2f}")
            summary.extend(f"company lane: {p}" for p in lane["problems"][:3])
        except Exception as e:
            summary.append(f"company lane failed: {e.__class__.__name__}: {e}")
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
            # board_key: a gh_jid link's key holds a posting id where the
            # slug goes, and learning it would file that id as the board.
            key = board_key(bp.url)
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

    # Sequoia's portfolio board, as Krish asked on 2026-10-05, and any other
    # firm on the Consider platform (sources/consider.py). Unlike the a16z
    # page its API pages through every open role, so it does both jobs at
    # once. Every Greenhouse, Ashby or Lever board an apply link names is
    # learned and swept in full below, with the full posting text. A role at a
    # company whose board hunter cannot read is taken from the Sequoia board
    # itself, with its pay, and added after the full sweep so a role both
    # carry arrives once, with its posting.
    board_only: list = []
    try:
        from .sources import consider
        cache = disc.load_cache(cfg)
        for firm in consider.BOARDS:
            jobs = consider.fetch_jobs(firm)
            posts = [consider.to_posting(j, firm) for j in jobs]
            only, learned_here = route_board_postings(posts, cache)
            board_only.extend(only)
            summary.append(f"{firm} board: {len(jobs)} open roles, {learned_here} new "
                           f"ATS boards learned, {len(only)} roles taken from the board "
                           f"itself (no readable company board)")
        disc.save_cache(cfg, cache)
    except Exception as e:
        summary.append(f"Consider boards skipped: {e.__class__.__name__}: {e}")

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
    postings.extend(board_only)

    spend = SpendTracker(cap_usd=float(cfg.optional("hunter_apify_max_usd_per_run", "5.00")))
    urls = linkedin_search_urls(cfg, sheet)
    if urls:
        from .sources.apify_linkedin import (ApifyStartFailed, last_sweep_started,
                                             sweep_window)
        cap = float(cfg.optional("hunter_apify_max_usd_per_call", "2.00"))
        per_source = int(cfg.optional("hunter_linkedin_limit_per_source", "200"))
        min_gap = datetime.timedelta(
            hours=float(cfg.optional("hunter_linkedin_min_hours", "20")))
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            last = last_sweep_started(cfg, cfg.require("hunter_apify_token"))
        except Exception:
            last = None
        if last is not None and now - last < min_gap:
            # A second press within the day re-buys the same market. The free
            # boards above were still swept.
            summary.append(f"apify linkedin: skipped, the last sweep started "
                           f"{(now - last).total_seconds() / 3600:.1f}h ago "
                           f"(minimum gap {min_gap.total_seconds() / 3600:.0f}h)")
        else:
            window = sweep_window(last, now)
            # Only a failed START is retried: no run exists then. Anything after
            # a run has started is final, because a retry would be a second
            # paid run of the same searches.
            for attempt in (1, 2):
                try:
                    postings.extend(sweep_linkedin(cfg, urls, spend=spend,
                                                   max_charge_usd=cap,
                                                   limit_per_source=per_source,
                                                   window=window))
                    summary.append(
                        f"apify linkedin: {len(urls)} search urls swept, window "
                        f"{window.total_seconds() / 3600:.0f}h, up to {per_source} "
                        f"per search, cap ${cap:.2f}")
                    break
                except ApifyStartFailed as e:
                    if attempt == 1:
                        time.sleep(5)
                        continue
                    summary.append(f"apify linkedin sweep failed to start: {e}")
                    break
                except Exception as e:
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
        notes: list[str] = []
        batches = batchstats.measure(cfg, tiers, notes=notes)
        if save:
            # A failed write is its own line. It used to raise into the
            # handler below, which said "accept rate not measured" about a
            # rate that had been measured, and dropped the lines with it.
            try:
                written = batchstats.save(cfg, batches)
                if written:
                    notes.append(written)
            except Exception as e:
                notes.append(str(e) if isinstance(e, batchstats.NotSaved)
                             else f"{batchstats.TABLE} NOT written: {e.__class__.__name__}")
        if into is not None:
            into.extend(batches)
        if summary is not None:
            summary.extend(batchstats.lines(batches))
            summary.extend(notes)
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
            if verdicts.is_accept(e.get("verdict") or ""):
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
    # set_aside is a role he was shown and scored low (leave_step): it moved
    # off "staging" without ever stopping being a company he has seen.
    try:
        rows = db_get(cfg, "hunter_seen_roles",
                      {"select": "company", "status": f"in.(staging,{SET_ASIDE_STATUS})",
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
    seen_keys, id_countries = seen_identity(cfg)
    fresh = fresh_postings(senior, seen_keys, id_countries)
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
    # The judge (judge_stage.py). Off, shadow or gate; shadow changes nothing
    # that reaches him and records what the judge would have done.
    from . import judge_stage
    judge_mode = judge_stage.mode_of(cfg)
    candidates: list = []

    # His top companies (universe.py): his list, his Target Companies, his Yes
    # companies and lookalikes. Their roles go through his top-company rules
    # in the gates and first into the judge's queue.
    from . import universe as universe_mod
    try:
        top_keys = set(universe_mod.top_notes(cfg))
    except Exception:
        top_keys = set()

    def is_top(company: str) -> bool:
        return universe_mod.key_of(company or "") in top_keys

    def rank_of(role, result) -> float:
        cs = company_view.get(company_key(role.company) or slugify(role.company))
        company_part = cs.total if cs and cs.status != COMPANY_UNKNOWN else 5.0
        return -((result.score or 0) + company_part + (100 if is_top(role.company) else 0))
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
                            comp=band, liveness=liveness,
                            id_suffix=(p.raw or {}).get("id_suffix", ""))
        # Score BEFORE gating. G12 and G13 need the role's merit, which is
        # its score with the employer left out, to decide whether a company
        # he declined is overridden by an exceptional role.
        result = score_role(role, universe=canon.universe,
                            employer_index=employer_index)
        report = run_gates(role, never_apply=never, company_declines=company_declines,
                           employer_index=employer_index, merit=result.merit,
                           top_company=is_top(role.company))
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
        if judge_mode != "off" and not judge_stage.hard_failure(report):
            candidates.append(judge_stage.Candidate(
                role=role, result=result, row=row, rank=rank_of(role, result)))
        if status == "staging" and judge_mode != "gate":
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

    judged = None
    if judge_mode != "off" and candidates:
        # The company bar (companybar.py): a business scored once against his
        # list, and a role at one that is plainly not his kind costs no call.
        from . import companybar
        candidates, _, fn_lines = companybar.function_cut(
            candidates, mark=judge_mode == "gate")
        summary.extend(fn_lines)
        try:
            candidates, _, bar_lines = companybar.apply(
                cfg, candidates, top_keys=top_keys, mark=judge_mode == "gate",
                max_usd=float(cfg.optional("hunter_company_bar_max_usd", "1")))
            summary.extend(bar_lines)
        except Exception as e:
            summary.append(f"company bar did not run: {e.__class__.__name__}: {e}")
        try:
            judged = judge_stage.run(cfg, canon, sheet, candidates, mode=judge_mode)
            summary.extend(judged.lines)
        except Exception as e:
            # The judge failing must never become the judge deciding: in gate
            # mode nothing is staged on a judge that did not run.
            summary.append(f"judge did not run: {e.__class__.__name__}: {e}")
            judged = None
    if judge_mode == "shadow" and judged is not None:
        old_ids = {r.job_id for r, _, _, _ in staged_rows}
        new_ids = {c.role.job_id for c in judged.present}
        summary.append(f"judge shadow: old rules staged {len(old_ids)}, the judge would "
                       f"present {len(new_ids)}, both {len(old_ids & new_ids)}")
        for c in candidates:
            if c.role.job_id in old_ids - new_ids:
                summary.append(f"  old rules only: {c.role.company} / {c.role.title}: "
                               f"{judge.reason_line(c.judgement)[:120]}")
        for c in judged.present:
            if c.role.job_id not in old_ids:
                summary.append(f"  judge only, fit {c.judgement.fit}: "
                               f"{c.role.company} / {c.role.title}")
    judge_decided = judge_mode == "gate"
    if judge_decided:
        staged_rows = []
        if judged is not None:
            judge_stage.apply_gate(judged, candidates)
            from . import drafts
            shown = judged.present + judged.audit
            if drafts.writer(cfg) == "routine":
                # The case is written on his subscription (drafts.py). The
                # judge's own reason goes on the row now and is replaced when a
                # checked case comes back; nothing waits on the routine.
                cases, queued = {}, 0
                for c in shown:
                    meta = {"score": c.result.score,
                            "audit_prefix": (f"{judge_stage.AUDIT_PREFIX} (fit {c.judgement.fit})."
                                             if c in judged.audit else ""),
                            "note": open_application_note(opens, c.role.company) or ""}
                    try:
                        queued += drafts.queue_case(cfg, judged.system, c.role, meta=meta)
                    except Exception as e:
                        summary.append(f"case not queued for {c.role.job_id}: {e.__class__.__name__}")
                summary.append(f"judge: {queued} case(s) queued for the writing routine")
            else:
                cases, case_usd = judge_stage.cases_for(
                    cfg, judged.system, [c.role for c in shown])
                summary.append(f"judge: the case written for {len(cases)} row(s), ${case_usd:.2f}")
            for c in judged.present + judged.audit:
                why, snippet = cases.get(c.role.job_id) or (
                    c.judgement.why_it_fits, c.judgement.snippet)
                if not why:
                    why, snippet, _ = write_rationale_and_snippet(
                        cfg, canon, company=c.role.company, title=c.role.title,
                        jd=c.role.jd_text, score=c.result.score,
                        score_reason=c.result.why_it_fits,
                        location=c.role.location, comp=c.role.comp)
                if c in judged.audit:
                    why = f"{judge_stage.AUDIT_PREFIX} (fit {c.judgement.fit}). {why}"[:900]
                note = open_application_note(opens, c.role.company)
                if note:
                    why = f"{why} {note}"[:900]
                c.row["why_it_fits"] = why
                staged_rows.append((c.role, c.result, snippet, why))
        else:
            for row in inserts:
                if row.get("status") == "staging":
                    row["status"] = "judge_pending"
    if inserts:
        # A role the judge left pending last run is judged again now; its row
        # exists, so it is updated rather than silently skipped.
        pending_ids = {r["job_id"] for r in db_get(cfg, "hunter_seen_roles", {
            "select": "job_id", "status": "eq.judge_pending", "limit": ALL_ROWS})}
        again = [r for r in inserts if r["job_id"] in pending_ids]
        first = [r for r in inserts if r["job_id"] not in pending_ids]
        if first:
            db_insert(cfg, "hunter_seen_roles", first, on_conflict="job_id",
                      ignore_duplicates=True)
        if again:
            db_insert(cfg, "hunter_seen_roles", again, on_conflict="job_id", merge=True)
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

    if staged_rows and judge_decided:
        new_rows = [make_row(company=role.company, role=role.title,
                             jd_url=role.jd_url, score=result.score,
                             why_it_fits=why,
                             location=role.location, comp=role.comp,
                             source=role.source, jd_snippet=snippet,
                             jd_verified=(role.liveness != "unverified"))
                    for role, result, snippet, why in staged_rows]
        sheet.append_rows(new_rows)
        shown = PresentedMarker(cfg, summary)
        for role, result, _, _ in staged_rows:
            shown.mark(role.job_id, {"presented_at": NOW()}, result.score)
        shown.report()
        counts["staged"] = len(staged_rows)
    elif staged_rows:
        # Ranked on the company AND the seat, not the seat alone. Under the
        # old key a role at a company he named tied with a role at an
        # advertising holding company, and at the staging cap the tie was
        # broken by whichever leg happened to run first.
        def rank(entry):
            return rank_of(entry[0], entry[1])

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
        # The rows that reach his sheet get their case from everything he has
        # written, when the judge's context is in hand; the text written above
        # stays wherever a case fails its checks.
        if judged is not None and judged.system:
            cases, case_usd = judge_stage.cases_for(
                cfg, judged.system, [role for role, _, _, _ in staged_rows])
            summary.append(f"the case written for {len(cases)} of {len(staged_rows)} "
                           f"row(s) from his whole record, ${case_usd:.2f}")
            rewritten = []
            for role, result, snippet, why in staged_rows:
                if role.job_id in cases:
                    why, snippet = cases[role.job_id]
                    note = open_application_note(opens, role.company)
                    if note:
                        why = f"{why} {note}"[:900]
                rewritten.append((role, result, snippet, why))
            staged_rows = rewritten
        new_rows = [make_row(company=role.company, role=role.title,
                             jd_url=role.jd_url, score=result.score,
                             why_it_fits=why,
                             location=role.location, comp=role.comp,
                             source=role.source, jd_snippet=snippet,
                             jd_verified=(role.liveness != "unverified"))
                    for role, result, snippet, why in staged_rows]
        sheet.append_rows(new_rows)
        shown = PresentedMarker(cfg, summary)
        for role, result, _, why in staged_rows:
            shown.mark(role.job_id, {"presented_at": NOW(), "why_it_fits": why},
                       result.score)
        shown.report()
        counts["staged"] = len(staged_rows)

    return counts


class PresentedMarker:
    """presented_at and the AI Score he is shown, written in one patch.

    The score is the integer make_row put in column J, recorded at the moment
    the row reaches him, because J does not stay still: regate and the
    drafts pass rewrite it, and a score read back after he has seen the row
    cannot measure whether it predicted him. Recorded once, here, it can.

    If the column is not there yet (the control-center migration not
    applied), presented_at is written alone and the summary says so: a role
    with no presented_at is staged again next run, which is the worse
    failure by far."""

    COLUMN = "ai_score_shown"

    def __init__(self, cfg: Config, summary: list[str]):
        self.cfg, self.summary = cfg, summary
        self.missing = False
        self.without = 0

    def mark(self, job_id: str, patch: dict, shown: int) -> None:
        if not self.missing:
            try:
                db_patch(self.cfg, "hunter_seen_roles", {"job_id": job_id},
                         dict(patch, **{self.COLUMN: int(shown)}))
                return
            except requests.HTTPError as e:
                body = getattr(getattr(e, "response", None), "text", "") or ""
                if self.COLUMN not in body:
                    raise
                self.missing = True
        db_patch(self.cfg, "hunter_seen_roles", {"job_id": job_id}, patch)
        self.without += 1

    def report(self) -> None:
        if self.without:
            self.summary.append(
                f"{self.COLUMN} not recorded for {self.without} staged row(s): the "
                f"column does not exist yet (control-center migration); presented_at "
                f"was written")


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

    # The approval ledger is deliberately NOT read here any more. Marking a role
    # Applied from the ledger, without the sheet, made close-submitted skip it
    # as already done, so a submission the extension reported between two runs
    # never reached his sheet. record_applied is the one writer of
    # ledger-driven state, and it writes the database only after the sheet.

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
        summary.append(f"document amendments skipped: {e.__class__.__name__}")

    n = amend.record(cfg, found)
    if n:
        summary.append(f"amendments read from your edits: {n}")
        # Counts and field names only. The before and after are his letter,
        # his CV and his own words in Why It Fits, beside the company, and
        # this summary is printed in a public log; the record holds them.
        from collections import Counter
        for field, k in sorted(Counter(a.get("field") or "?" for a in found).items()):
            summary.append(f"  {field}: {k} edit(s)")
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
    """Move every Applied and Declined row to the Applied tab: leave
    .should_leave asked with no record of his, so column A alone decides.
    Yes and New stay here. The rows his score moves leave in leave_step,
    which reads his record first; process runs this one after the invariant
    repairs, whose duplicate stamp is what makes a twin a decided row."""
    rows = sheet.read_pipeline(canon.sheet_headers)
    movers, lines = [], []
    for r in rows:
        if leave_mod.should_leave(r, None):
            movers.append(r)
            lines.append(f"{r.company} / {r.role} [{r.verdict}]")
    if not movers:
        return 0, lines
    moved = sheet.archive_rows(movers, archive_tab=config_mod.ARCHIVE_TAB,
                               archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                               headers=canon.sheet_headers)
    return moved, lines


def _pg_in(values) -> str:
    """A PostgREST in.() list, each value quoted, so a job id is read as the
    one value it is whatever it holds."""
    quoted = ['"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values]
    return "in.(" + ",".join(quoted) + ")"


def _roles_by_id(cfg: Config, job_ids) -> dict[str, dict]:
    """The hunter_seen_roles rows of these roles, read by id in small pages
    rather than all 9,500 rows, because the leave pass runs every hour."""
    ids = sorted({j for j in job_ids if j})
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 60):
        for d in db_get(cfg, "hunter_seen_roles", {
                "select": "job_id,company,title,location,url,job_url,status,"
                          "package_trigger,krish_verdict",
                "job_id": _pg_in(ids[i:i + 60]), "limit": ALL_ROWS}):
            if d.get("job_id"):
                out[d["job_id"]] = d
    return out


def _pipeline_holds(pipeline: list[SheetRow], rated: dict, roles: dict,
                    filed: tuple = ()) -> dict[int, set]:
    """row number -> the roles that row, still on Pipeline, holds, whatever
    its K and M say. A role is held by a row his record files under it, by a
    row carrying its link (same_link against url and job_url), and by a row
    of its company, title and country (sources.placed_identity_keys, the
    identity pair_sheet files two rows of one role by), read off the role's
    record and off every row the record files under it elsewhere (`filed`:
    job ids, company, title, location), since a title the database holds is
    not always the one on the sheet.

    Not from his record alone: a blank twin still pending is never stored,
    nor is an "Awaiting reply" twin or a twin he edited since the last sync,
    and each is still that role on his list. Counting only rows the record
    held set a role aside while one of its rows waited on Pipeline, and
    nothing ever put it back."""
    index: dict[tuple, set] = {}
    named = [((jid,), d.get("company"), d.get("title"), d.get("location"))
             for jid, d in roles.items()] + list(filed)
    for jids, company, title, location in named:
        if company and title:
            loc = (location or "").strip()
            for k in placed_identity_keys(company, title, "" if loc == "Not stated" else loc,
                                          seen=True):
                index.setdefault(k, set()).update(jids)
    out: dict[int, set] = {}
    for x in pipeline:
        got = set(rated[x.row_number].job_ids) if rated.get(x.row_number) else set()
        loc = (x.cell("Location") or "").strip()
        for k in placed_identity_keys(x.company, x.role, "" if loc == "Not stated" else loc,
                                      seen=False):
            got |= index.get(k, set())
        got |= {jid for jid, d in roles.items()
                if any(same_link(x.jd_url, u) for u in (d.get("url"), d.get("job_url")) if u)}
        out[x.row_number] = got
    return out


def leave_step(cfg: Config, canon: Canon, sheet: Sheet, summary: list[str], *,
               apply: bool, out_path: str | None = None) -> dict:
    """What leaves Pipeline this pass, in one move, and what comes back.

    His rulings 2026-10-13 (leave.py has the whole rule): rows he scored 1 to
    5, rows with only a reaction, blanks he has scored past and rows he called
    Duplicate leave Pipeline for Applied with K and M as he wrote them, and
    his score wins over an older Yes. Column A Applied and Declined leave as
    they always have, in the SAME archive_rows call, from the one read the
    decision is made on: a second call after the first would delete by row
    numbers the first had shifted.

    In order, each step needing the one before:

    1. Pipeline, read once. His record (hunter_krish_ratings) is read when
       canon 9.13 carries the rule (leave.in_canon), and on a dry run, which
       lists what the rule would move before he has approved it. Without
       the rule only column A moves a row, exactly as before.
    2. The move (sheet.archive_rows): every target re-read before the copy
       and again before the delete, a changed row refused, his cells and the
       stamp written RAW and read back, a row already on Applied not copied
       twice. Column A is never written.
    3. The Applied tab, read once, for what the move leaves to the record:
       every role his score has set aside there gets status set_aside
       (counted as seen, so it is never staged again or appended back) and
       its rating moved_at; a Yes his score overrode has every approval
       still awaiting him or approved for its posting cancelled, with his
       score as the reason. All read back. Done from Applied rather than
       from the rows just moved, so a write that failed once is made on the
       next pass: nothing here depends on the move having happened this hour.
    4. The way back: a row on Applied, column A New, blank or Yes, that he
       has since scored 7 or more goes back to Pipeline as he left it, its
       status back to staging, so the score builds.

    Never writes column A or krish_verdict. Every summary line is a count;
    out_path, a local file for the dry run, is the only place names go.
    """
    from . import ratings
    from .apply import approval
    rule = leave_mod.in_canon(canon)
    out: dict = {"rule": rule, "records": False, "decided": 0, "by_score": {},
                 "moved": 0, "refused": 0, "already": 0, "changed": 0, "unrecorded": 0,
                 "set_aside": 0, "moved_at": 0, "approvals_cancelled": 0,
                 "would_cancel": 0, "back": 0, "back_both": 0, "restored": 0,
                 "unconfirmed": 0, "applied_tab": "unread"}
    detail: dict = {"rule_in_canon": rule, "apply": apply, "leave": [], "back": [],
                    "set_aside_on_applied": []}
    rows = sheet.read_pipeline(canon.sheet_headers)
    records = None
    if rule or not apply:
        try:
            records = leave_mod.Records.of(db_get(cfg, ratings.TABLE, {
                "select": "*", "order": "id.asc", "limit": ALL_ROWS}))
            out["records"] = True
        except Exception as e:
            summary.append(f"leave: his ratings could not be read ({ratings.safe_error(e)}); "
                           f"only rows column A decided move this pass")
    rated: dict = records.rate(rows) if records else {}
    roles: dict = _roles_by_id(cfg, [j for rr in rated.values() for j in rr.job_ids]) \
        if rated else {}
    rated = leave_mod.mark_score_built(rows, rated, roles)
    moves = []
    for r in rows:
        why = leave_mod.should_leave(r, rated.get(r.row_number))
        if why:
            moves.append((r, why))
        elif records is not None and r.row_number not in rated and leave_mod.unrecorded(r):
            out["unrecorded"] += 1
    out["decided"] = sum(1 for _, w in moves if w in leave_mod.DECIDED_REASONS)
    by_score = {w: sum(1 for _, x in moves if x == w) for w in leave_mod.SCORE_REASONS}
    out["by_score"] = by_score

    # Approvals still open, read at most once a pass and only when a Yes is
    # leaving or set aside: open_approvals_for then finds each posting's.
    memo: list = []

    def any_open() -> bool:
        if not memo:
            memo.append(db_get(cfg, approval.TABLE, {
                "select": "token,job_id,state",
                "state": f"in.({approval.AWAITING},{approval.APPROVED})", "limit": "500"}))
        return bool(memo[0])

    def waiting_for(rr) -> list[dict]:
        if rr is None or not any_open():
            return []
        seen, found = set(), []
        for jid in rr.job_ids:
            d = roles.get(jid)
            if not d:
                continue
            for o in open_approvals_for(cfg, d):
                if o.get("token") and o["token"] not in seen:
                    seen.add(o["token"])
                    found.append(o)
        return found

    # 2. the move
    rep: dict = {}
    pipe_now = rows
    if apply and moves:
        # His edits to a row his score moves are read before it leaves the tab
        # amend reads, as settle reads them on the rows column A decided.
        scored_out = [(r, rated[r.row_number]) for r, w in moves
                      if w in leave_mod.SCORE_REASONS and rated.get(r.row_number)]
        if scored_out:
            try:
                snapshot = amend.load_state(cfg)
                if snapshot:
                    found = amend.detect([r for r, _ in scored_out],
                                         {r.row_number: {"job_id": rr.job_ids[0]}
                                          for r, rr in scored_out}, snapshot)
                    out["amendments"] = amend.record(cfg, found)
            except Exception as e:
                summary.append(f"leave: edits on the rows leaving not read "
                               f"({e.__class__.__name__})")
        out["moved"] = sheet.archive_rows([r for r, _ in moves],
                                          archive_tab=config_mod.ARCHIVE_TAB,
                                          archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                                          headers=canon.sheet_headers, report=rep)
        out["refused"] = len(rep.get("refused") or [])
        out["already"] = len(rep.get("already") or [])
        out["changed"] = len(rep.get("changed") or [])
        pipe_now = sheet.read_pipeline(canon.sheet_headers)
        left = [r for r in pipe_now if leave_mod.should_leave(r, None)]
        if left:
            summary.append(f"leave: {len(left)} decided row(s) still on Pipeline after "
                           f"the move: " + "; ".join(f"{r.company} / {r.role}"
                                                     for r in left[:5]))
    # What stays on Pipeline: re-read after a move, and in a dry run every
    # row the plan does not move.
    staying = pipe_now if apply else [r for r in rows if not any(r is m for m, _ in moves)]

    # 3 and 4, from the Applied tab
    archived: list = []
    if records is not None:
        try:
            grid = sheet.read_archive_grid()
            state = sheet_mod.archive_layout(grid)
            out["applied_tab"] = state or "unrecognised"
            archived = sheet.read_archive() if state == "current" else []
        except Exception as e:
            out["applied_tab"] = f"unread ({e.__class__.__name__})"
    arated = records.rate(archived) if records and archived else {}
    more = [j for rr in arated.values() for j in rr.job_ids if j not in roles]
    if more:
        roles.update(_roles_by_id(cfg, more))
    arated = leave_mod.mark_score_built(archived, arated, roles)

    # The roles a row still on Pipeline holds (_pipeline_holds). Two rows of
    # one role can part: a Duplicate he typed leaves while the twin he scored
    # 9 stays, or a 3 leaves while its twin in another office is still blank.
    # The role is still live through the row that stays: it is never set
    # aside and its rating not marked moved, and one already set aside goes
    # back to staging. An application is a posting's, and it is kept only
    # while a row that stays still asks for it (`asking`).
    holds = _pipeline_holds(staying, records.rate(staying) if records else {}, roles,
                            filed=tuple((arated[a.row_number].job_ids, a.company, a.role,
                                         a.cell("Location"))
                                        for a in archived if a.row_number in arated))
    held = set().union(*holds.values()) if holds else set()

    def asking(job_ids) -> list:
        want = set(job_ids)
        return [x for x in staying if router.build_trigger(x)
                and holds.get(x.row_number, set()) & want]

    for r, why in moves:
        rr = rated.get(r.row_number)
        waiting = (waiting_for(rr) if why in leave_mod.YES_REASONS and rr
                   and not asking(rr.job_ids) else [])
        out["would_cancel"] += len(waiting)
        detail["leave"].append({
            "row": r.row_number, "company": r.company, "title": r.role,
            "location": r.cell("Location"), "column_a": r.verdict,
            "krish_score": r.cell(sheet_mod.KRISH_SCORE), "reason": why,
            "kind": rr.kind if rr else None, "job_ids": list(rr.job_ids) if rr else [],
            "statuses": {j: (roles.get(j) or {}).get("status") for j in (rr.job_ids if rr else ())},
            "approvals_cancelled": [{"job_id": o.get("job_id"), "state": o.get("state")}
                                    for o in waiting]})
    aside = [(a, w) for a in archived
             if (w := leave_mod.should_leave(a, arated.get(a.row_number)))
             in leave_mod.SCORE_REASONS]
    back = [a for a in archived if leave_mod.comes_back(a, arated.get(a.row_number))]
    for a in back:
        rr = arated[a.row_number]
        detail["back"].append({
            "applied_row": a.row_number, "company": a.company, "title": a.role,
            "location": a.cell("Location"), "column_a": a.verdict,
            "krish_score": a.cell(sheet_mod.KRISH_SCORE), "job_ids": list(rr.job_ids),
            "statuses": {j: (roles.get(j) or {}).get("status") for j in rr.job_ids}})
    out["back_planned"] = len(back)
    for a, why in aside:
        rr = arated[a.row_number]
        stale = [] if held & set(rr.job_ids) else [
            j for j in rr.job_ids if (roles.get(j) or {}).get("status") in leave_mod.LIVE_STATUSES]
        waiting = (waiting_for(rr) if why in leave_mod.YES_REASONS and not apply
                   and not asking(rr.job_ids) else [])
        if stale or waiting:
            detail["set_aside_on_applied"].append({
                "applied_row": a.row_number, "company": a.company, "title": a.role,
                "reason": why, "job_ids": stale,
                "approvals_cancelled": [{"job_id": o.get("job_id"), "state": o.get("state")}
                                        for o in waiting]})
            out["would_cancel"] += len(waiting)

    if apply and rule and records is not None:
        _set_aside_step(cfg, records, roles, aside, arated, out, waiting_for=waiting_for,
                        asking=asking, held=held)
        _held_restore_step(cfg, roles, held, out)
        _way_back_step(cfg, canon, sheet, roles, back, arated, out)

    _leave_lines(out, summary, apply=apply)
    if out_path:
        import pathlib
        detail["counts"] = {k: v for k, v in out.items() if k != "by_score"}
        detail["counts"]["by_score"] = dict(by_score)
        pathlib.Path(out_path).write_text(json.dumps(detail, indent=1, default=str))
    return out


def _set_aside_step(cfg: Config, records, roles: dict, aside: list, arated: dict,
                    out: dict, *, waiting_for, asking, held: set) -> None:
    """Step 3 of leave_step: the record of every row his score has set aside
    on Applied. Status first, because it is what keeps the role from being
    staged again; then moved_at; then, for a Yes his score overrode, what was
    waiting on him for it. Each write is read back.

    A role a row still on Pipeline holds (`held`) keeps its status and its
    rating: it is still on his list. The application is decided apart from
    that, by `asking` alone: an approval is for a posting, not a row
    (open_approvals_for), so it is left while a row that stays still asks for
    a package for the role (router.build_trigger), and cancelled otherwise,
    whatever else holds the role. A twin he left New with a 6 does not ask
    for the application his score took back from the Yes."""
    from . import ratings
    patched, stamped = [], []
    by_rid = {r.get("id"): r for r in records.latest.values() if r.get("id") is not None}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for a, why in aside:
        rr = arated[a.row_number]
        if held & set(rr.job_ids):
            out["held_by_pipeline"] = out.get("held_by_pipeline", 0) + 1
        else:
            for jid in rr.job_ids:
                if (roles.get(jid) or {}).get("status") not in leave_mod.LIVE_STATUSES:
                    continue
                if jid in patched:
                    continue  # two rows of one role: one role set aside, counted once
                db_patch_where(cfg, "hunter_seen_roles",
                               {"job_id": f"eq.{jid}",
                                "status": _pg_in(leave_mod.LIVE_STATUSES)},
                               {"status": leave_mod.SET_ASIDE})
                patched.append(jid)
            for rid in rr.record_ids:
                if by_rid.get(rid, {}).get("moved_at") or rid in stamped:
                    continue
                db_patch(cfg, ratings.TABLE, {"id": str(rid)}, {"moved_at": now})
                stamped.append(rid)
        if why in leave_mod.YES_REASONS:
            if asking(rr.job_ids):
                out["kept_for_twin"] = out.get("kept_for_twin", 0) + 1
                continue
            tokens = [o["token"] for o in waiting_for(rr)]
            _cancel_and_read_back(cfg, tokens, leave_mod.cancel_reason(a), out)
    if patched:
        got = _roles_by_id(cfg, patched)
        ok = [j for j in patched if (got.get(j) or {}).get("status") == leave_mod.SET_ASIDE]
        out["set_aside"] += len(ok)
        out["unconfirmed"] += len(patched) - len(ok)
        for j in ok:
            roles.setdefault(j, {})["status"] = leave_mod.SET_ASIDE
    if stamped:
        back = db_get(cfg, ratings.TABLE, {"select": "id,moved_at", "id": _pg_in(stamped),
                                           "limit": ALL_ROWS})
        ok = {b.get("id") for b in back if b.get("moved_at")}
        out["moved_at"] += sum(1 for rid in stamped if rid in ok)
        out["unconfirmed"] += sum(1 for rid in stamped if rid not in ok)


def _held_restore_step(cfg: Config, roles: dict, held: set, out: dict) -> None:
    """A role set aside while a row of it is still on Pipeline goes back to
    staging, read back. Set aside when its last row left and then held again
    (a twin he has since scored, a row the way back returned under another
    job id), or set aside before this pass knew a pending twin held it: the
    status says the role has left his sheet, and a row of it is on his sheet.
    Without this a twin he scored 8 stayed set aside for good, out of Roles to
    rule on and the warm path, with its bridges purged as decided."""
    relive = sorted(j for j in held if (roles.get(j) or {}).get("status") == leave_mod.SET_ASIDE)
    if not relive:
        return
    for jid in relive:
        db_patch_where(cfg, "hunter_seen_roles",
                       {"job_id": f"eq.{jid}", "status": f"eq.{leave_mod.SET_ASIDE}"},
                       {"status": leave_mod.BACK_STATUS})
    got = _roles_by_id(cfg, relive)
    ok = [j for j in relive if (got.get(j) or {}).get("status") == leave_mod.BACK_STATUS]
    out["held_restored"] = out.get("held_restored", 0) + len(ok)
    out["unconfirmed"] += len(relive) - len(ok)
    for j in ok:
        roles.setdefault(j, {})["status"] = leave_mod.BACK_STATUS


def _way_back_step(cfg: Config, canon: Canon, sheet: Sheet, roles: dict, back: list,
                   arated: dict, out: dict) -> None:
    """Step 4 of leave_step: a row on Applied he has since scored 7 or more
    goes back to Pipeline, his cells and column A as he left them (a blank A
    is the fill default New), and comes off Applied only once Pipeline has
    read it back. A row already on Pipeline under the same company, role,
    location and link is not appended twice: its Applied copy goes, if his
    cells agree, and is left for him to look at if they do not."""
    if not back:
        return
    pipe = sheet.read_pipeline(canon.sheet_headers)
    to_append, only_delete = [], []
    for a in back:
        twin = next((p for p in pipe if leave_mod.same_place(p, a)), None)
        if twin is None:
            to_append.append(a)
        elif sheet_mod.recheck_cells(twin.cells)[3:] == sheet_mod.recheck_cells(a.cells)[3:]:
            only_delete.append(a)
        else:
            out["back_both"] += 1
    if to_append:
        # The same safety as the move out: the row as Applied reads it now,
        # against what it was chosen on.
        now = {x.row_number: x for x in sheet.read_archive()}
        fresh = [a for a in to_append if a.row_number in now and
                 sheet_mod.recheck_cells(now[a.row_number].cells)
                 == sheet_mod.recheck_cells(a.cells)]
        out["refused"] += len(to_append) - len(fresh)
        # A row Pipeline's own checks would refuse stays on Applied and is
        # counted, rather than failing every hour's pass for the rest.
        to_append = []
        for a in fresh:
            cells = leave_mod.pipeline_cells(a)
            yes = leave_mod.is_yes(cells[sheet_mod.COLS["Verdict"]])
            if sheet_mod.validate_row(cells, is_append=not yes):
                out["back_invalid"] = out.get("back_invalid", 0) + 1
            else:
                to_append.append(a)
        if to_append:
            sheet.append_rows([leave_mod.pipeline_cells(a) for a in to_append],
                              carry_his=True, carry_verdict=True)
    done = to_append + only_delete
    if not done:
        return
    sheet.delete_archive_rows([a.row_number for a in done],
                              archive_sheet_id=config_mod.ARCHIVE_SHEET_ID,
                              expect=[a.company for a in done],
                              expect_cells=[sheet_mod.recheck_cells(a.cells) for a in done])
    out["back"] += len(done)
    restored = []
    for a in done:
        for jid in arated[a.row_number].job_ids:
            if (roles.get(jid) or {}).get("status") != leave_mod.SET_ASIDE:
                continue
            db_patch_where(cfg, "hunter_seen_roles",
                           {"job_id": f"eq.{jid}", "status": f"eq.{leave_mod.SET_ASIDE}"},
                           {"status": leave_mod.BACK_STATUS})
            restored.append(jid)
    if restored:
        got = _roles_by_id(cfg, restored)
        ok = [j for j in restored if (got.get(j) or {}).get("status") == leave_mod.BACK_STATUS]
        out["restored"] += len(ok)
        out["unconfirmed"] += len(restored) - len(ok)


def _leave_lines(out: dict, summary: list[str], *, apply: bool) -> None:
    """Counts only: a row his score moved is a row he scored, and these lines
    go to public Actions logs."""
    s = out["by_score"]
    n_score = sum(s.values())
    yes = s.get("yes_scored_low", 0) + s.get("yes_reaction_only", 0)
    parts = (f"{s.get('scored_low', 0)} scored 1 to 5, {s.get('reaction_only', 0)} with "
             f"only a reaction, {s.get('blank_scored_past', 0)} blank and scored past, "
             f"{s.get('duplicate', 0)} called Duplicate, {s.get(leave_mod.GONE, 0)} scored "
             f"whose posting is gone, {yes} Yes his score overrode")
    if not out["rule"]:
        summary.append(f"leave: {out['decided']} decided row(s) "
                       + ("moved" if apply else "would move")
                       + f"; canon 9.13 does not carry the rule for his score yet, so "
                       f"none moves for it" + (f" (with it, {n_score} would: {parts})"
                                              if not apply and out["records"] else ""))
        return
    if not out["records"]:
        summary.append(f"leave: {out['decided']} decided row(s) "
                       + ("moved" if apply else "would move")
                       + "; his record unread, so none moves for his score")
        return
    verb = "left Pipeline" if apply else "would leave Pipeline (dry run)"
    summary.append(f"leave: {out['decided']} decided row(s) and {n_score} by his score "
                   f"({parts}) {verb}" + (f", {out['moved']} moved" if apply else ""))
    if apply and (out["refused"] or out["already"] or out["changed"]):
        summary.append(f"leave: {out['refused']} refused because a cell changed since "
                       f"the read, {out['changed']} changed during the move and stayed, "
                       f"{out['already']} already on {config_mod.ARCHIVE_TAB} and not "
                       f"copied again")
    if out["unrecorded"]:
        summary.append(f"leave: {out['unrecorded']} row(s) his K and M say leave are not "
                       f"in his record as they read now; they stay until a sync records them")
    if apply:
        summary.append(f"leave: {out['set_aside']} role(s) set aside, {out['moved_at']} "
                       f"rating(s) marked moved, {out['approvals_cancelled']} waiting "
                       f"application(s) cancelled, {out['back']} row(s) back on Pipeline "
                       f"on a score of 7 or more ({out['restored']} role(s) restored)")
        if out["back_both"]:
            summary.append(f"leave: {out['back_both']} row(s) on both tabs with different "
                           f"K or M, left for him")
        if out.get("held_by_pipeline"):
            summary.append(f"leave: {out['held_by_pipeline']} row(s) moved whose role "
                           f"another row still holds on Pipeline; that role is left live")
        if out.get("kept_for_twin"):
            summary.append(f"leave: {out['kept_for_twin']} Yes row(s) left with their "
                           f"application, because another row on Pipeline for the same "
                           f"role still asks for it")
        if out.get("held_restored"):
            summary.append(f"leave: {out['held_restored']} role(s) set aside while a row of "
                           f"it was still on Pipeline put back to staging")
        if out.get("back_invalid"):
            summary.append(f"leave: {out['back_invalid']} row(s) his score asks back could "
                           f"not go back as they read on {config_mod.ARCHIVE_TAB}; left there")
        if out["unconfirmed"]:
            summary.append(f"leave: {out['unconfirmed']} write(s) did not read back as made")
    else:
        summary.append(f"leave: {out['would_cancel']} waiting application(s) would be "
                       f"cancelled, {out.get('back_planned', 0)} row(s) would come back "
                       f"to Pipeline on a score of 7 or more")


def open_approvals_for(cfg: Config, d: dict) -> list[dict]:
    """Every approval still awaiting him or approved for this role's posting:
    the same posting_key, whichever job_id it was filed under, because one
    posting has been recorded under two ids and each got its own token."""
    from .apply import approval
    key = posting_key(d)
    open_rows = db_get(cfg, approval.TABLE, {
        "select": "token,job_id,state",
        "state": f"in.({approval.AWAITING},{approval.APPROVED})", "limit": "500"})
    same = {d["job_id"]}
    if key:
        ids = sorted({o["job_id"] for o in open_rows if o.get("job_id")})
        if ids:
            for x in db_get(cfg, "hunter_seen_roles", {
                    "select": "job_id,job_url,url",
                    "job_id": f"in.({','.join(ids)})", "limit": "500"}):
                if posting_key(x) == key:
                    same.add(x["job_id"])
    return [o for o in open_rows if o.get("job_id") in same]


def _rows_for(d: dict, rows: list[SheetRow]) -> list[SheetRow]:
    """The sheet rows that are this record: its link first (same_link), then
    company and title (sheet_identity) only when exactly one row carries
    them. Two rows with one company and title are two postings, the same
    seat in two countries as often as not, and either answer would be a
    guess: the K of the wrong one let an application go out for a role he
    had lowered. Not match_rows, which pairs the sheet against the whole
    database and has a fuzzy phase that a short list of records gives the
    wrong answer to."""
    urls = [u for u in (d.get("url"), d.get("job_url")) if u]
    hit = [r for r in rows if any(same_link(r.jd_url, u) for u in urls)]
    if hit:
        return hit
    ident = sheet_identity(d.get("company") or "", d.get("title") or "")
    same = [r for r in rows if sheet_identity(r.company, r.role) == ident]
    return same if len(same) == 1 else []


def withdrawn_because(row: SheetRow) -> str | None:
    """Why a row no longer asks for its package, in his columns' words, or
    None when it has not said so. The record is private; nothing here is
    printed beside the role.

    None is K holding something that is not a whole number ("8.5", "8/10",
    "9?"). That is an absent observation, not a lower score (CLAUDE.md 1.3):
    cancelling on it destroyed an APPROVE he had given, and fixing K back
    did not restore it. Declined and Duplicate still say no whatever K holds."""
    from .ratings import whole_score
    if verdicts.parse(row.verdict or "")[0] == "rejection":
        return "column A Declined"
    if sheet_mod.says_duplicate(row.cell(sheet_mod.KRISH_REACTION)):
        return f"{sheet_mod.KRISH_REACTION} says Duplicate"
    k = (row.cell(sheet_mod.KRISH_SCORE) or "").strip()
    if not k:
        return f"{sheet_mod.KRISH_SCORE} cleared"
    score = whole_score(k)
    return f"{sheet_mod.KRISH_SCORE} now {score}" if score is not None else None


def score_package(d: dict) -> bool:
    """A package his score built, by its trigger and its documents.

    Not by package_status: reconcile writes "blocked" on a row whose column A
    has become a Decline, and so does settle, and either can run before the
    hourly withdrawal sees the row. Watching package_status "built" alone
    left the approval for a role he had declined waiting for good."""
    t = d.get("package_trigger") or ""
    if router.built_on_score(t) is None and not t.startswith(router.WITHDRAWN):
        return False
    return ((d.get("package_status") or "") == "built"
            or bool(d.get("package_cv_url")) or bool(d.get("package_built_at")))


def score_withdrawals_step(cfg: Config, canon: Canon, sheet: Sheet,
                           summary: list[str], *, apply: bool) -> dict:
    """A package his score built, after his score stopped asking for it.

    His ruling 2026-10-13: 7 or more builds. A built package then sits with
    an application waiting on him (an awaiting or approved token) until he
    acts, and nothing undid it when he lowered the score: the watchdog would
    have nagged him about an application for a role he had just marked
    down. So, hourly, before settle moves anything: a role whose package his
    score built (score_package), whose rows now read below 7, cleared,
    Duplicate or Declined, has every open approval for its posting
    cancelled, the reason recorded on the approval and on package_trigger,
    and both read back. The documents stay where they are: they are his,
    and a score he raises again puts the role back (package_trigger
    reinstated) for Prepare. A K hunter cannot read as a whole number is
    left alone and counted (withdrawn_because). An approval made after the
    withdrawal, from a Hunt lane that had not caught up, is cancelled too
    while the row still does not ask for it.

    Column A Applied is left to settle, which closes those as submitted. A
    row hunter cannot find on either tab is left alone and counted: a row he
    deleted is not a score he lowered. A Yes in column A keeps the package
    here whatever K says: Yes builds. A Yes he scores 1 to 5, or answers with
    only a reaction, leaves Pipeline in leave_step once canon carries that
    ruling, and that pass cancels what waits on him for it. Counts only,
    because these lines are public.
    """
    from .apply import approval
    out = {"watched": 0, "withdrawn": 0, "reinstated": 0, "approvals_cancelled": 0,
           "unconfirmed": 0, "not_on_sheet": 0, "unreadable": 0, "late_cancelled": 0}
    try:
        recs = db_get(cfg, "hunter_seen_roles", {
            "select": "job_id,company,title,url,job_url,package_status,package_trigger,"
                      "package_cv_url,package_built_at",
            "package_trigger": "not.is.null", "limit": ALL_ROWS})
    except Exception as e:
        summary.append(f"score withdrawals: not checked, the records could not be "
                       f"read ({e.__class__.__name__})")
        return out
    watched = [d for d in recs if score_package(d)]
    out["watched"] = len(watched)
    if not watched:
        return out
    pipeline = sheet.read_pipeline(canon.sheet_headers)
    try:
        layout = sheet_mod.archive_layout(sheet.read_archive_grid())
        archived = sheet.read_archive() if layout != "empty" else []
    except Exception:
        layout, archived = "unread", []
    for d in watched:
        trig = d.get("package_trigger") or ""
        rows = _rows_for(d, pipeline)
        if rows:
            if any(verdicts.parse(r.verdict or "")[0] == "applied" for r in rows):
                continue
            now = [t for t in (router.build_trigger(r) for r in rows) if t]
            if now:
                if trig.startswith(router.WITHDRAWN):
                    back = next((t for t in now if t != router.YES_TRIGGER), now[0])
                    out["reinstated"] += 1
                    if apply:
                        db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]},
                                 {"package_trigger": back})
                        mark = db_get(cfg, "hunter_seen_roles", {
                            "select": "package_trigger", "job_id": f"eq.{d['job_id']}",
                            "limit": "1"})
                        if not mark or mark[0].get("package_trigger") != back:
                            out["unconfirmed"] += 1
                continue
            reason = withdrawn_because(rows[0])
        else:
            arows = _rows_for(d, archived)
            if not arows:
                out["not_on_sheet"] += 1
                continue
            kinds = {verdicts.parse(r.verdict or "")[0] for r in arows}
            if "applied" in kinds:
                continue
            if "rejection" in kinds:
                reason = "column A Declined"
            elif layout != "current":
                # Applied on its previous layout has no K to read: unknown is
                # not lower.
                continue
            elif any(router.build_trigger(r) for r in arows):
                continue
            else:
                reason = withdrawn_because(arows[0])
        if reason is None:
            out["unreadable"] += 1
            continue
        tokens = [o["token"] for o in open_approvals_for(cfg, d)]
        if trig.startswith(router.WITHDRAWN):
            # Withdrawn before, and still not asked for. A token here was made
            # after the withdrawal (Prepare from a lane that had not caught
            # up) and is cancelled on the reason already recorded.
            if not tokens:
                continue
            out["late_cancelled"] += len(tokens)
            if apply:
                _cancel_and_read_back(cfg, tokens, trig, out)
            continue
        record = f"{router.WITHDRAWN}: {reason} (built on {trig})"
        out["withdrawn"] += 1
        if not apply:
            out["approvals_cancelled"] += len(tokens)
            continue
        _cancel_and_read_back(cfg, tokens, record, out)
        db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]},
                 {"package_trigger": record})
        mark = db_get(cfg, "hunter_seen_roles", {"select": "package_trigger",
                                                 "job_id": f"eq.{d['job_id']}", "limit": "1"})
        if not mark or mark[0].get("package_trigger") != record:
            out["unconfirmed"] += 1
    verb = "" if apply else " (dry run, nothing changed)"
    summary.append(
        f"score withdrawals: {out['watched']} package(s) built on a score watched, "
        f"{out['withdrawn']} no longer asked for (below 7, cleared, Duplicate or "
        f"Declined) and {out['approvals_cancelled']} waiting application(s) cancelled, "
        f"{out['reinstated']} asked for again, {out['not_on_sheet']} not on the sheet "
        f"and left alone, {out['unreadable']} with a {sheet_mod.KRISH_SCORE} that is "
        f"not a whole number and left alone{verb}")
    if out["late_cancelled"]:
        summary.append(f"score withdrawals: {out['late_cancelled']} application(s) "
                       f"prepared after the role was withdrawn, cancelled{verb}")
    if out["unconfirmed"]:
        summary.append(f"score withdrawals: {out['unconfirmed']} write(s) did not read "
                       f"back as made")
    return out


def _cancel_and_read_back(cfg: Config, tokens: list[str], reason: str, out: dict) -> None:
    """Cancel these approvals and read them back. A cancel set_state refused
    (a submit Control Center recorded first is final) is said, not counted."""
    from .apply import approval
    if not tokens:
        return
    for t in tokens:
        approval.set_state(cfg, t, approval.CANCELLED, failure_reason=reason)
    gone = db_get(cfg, approval.TABLE, {
        "select": "token,state", "token": f"in.({','.join(tokens)})", "limit": "500"})
    done = sum(1 for g in gone if g.get("state") == approval.CANCELLED)
    out["approvals_cancelled"] += done
    out["unconfirmed"] += len(tokens) - done


def settle_step(cfg: Config, canon: Canon, sheet: Sheet,
                summary: list[str], *, apply: bool = True, archive: bool = True) -> dict:
    """What column A asks for that must not wait for the next batch.

    Typing Applied or Declined in column A used to do nothing until the next
    full run, and a full run could be days away or, on 27 September, never.
    He marked openrouter and BOI Applied on 3 October and both would have sat
    on Pipeline reading as live work. This runs hourly. For each decided row,
    in this order, because each step needs the row still where it is:

    1. his edits to the row are recorded (amend.detect), since an archived row
       is no longer compared;
    2. his verdict is recorded. Reconcile only ever recorded a row's FIRST
       verdict, so a role that went from Yes to Applied stayed Yes in the
       database and the learning loop never heard he applied;
    3. an Applied row is marked applied, on the sheet and in the database, and
       any approval still open for that posting is closed on his word;
    4. the row moves to the Applied tab, and the sheet is read back.

    archive=False leaves step 4 to the caller. cmd_settle does that, since
    his rulings of 2026-10-13: the decided rows move in the same one move as
    the rows his score sends to Applied (leave_step), after his scores have
    been read into the record, so nothing he wrote leaves Pipeline unread.
    """
    from .apply import approval
    out = {"decided": 0, "amendments": 0, "verdicts": 0, "applied": 0,
           "approvals_closed": 0, "archived": 0}
    rows = sheet.read_pipeline(canon.sheet_headers)
    decided = []
    for r in rows:
        kind, code = verdicts.parse(r.verdict)
        if kind in ("applied", "rejection"):
            decided.append((r, kind, code))
    out["decided"] = len(decided)
    if not decided:
        return out
    paired = _pair_sheet_to_db(cfg, [r for r, _, _ in decided])

    # 1. his edits, before the row leaves the tab amend reads
    try:
        state = amend.load_state(cfg)
        if state:
            found = amend.detect([r for r, _, _ in decided], paired, state)
            out["amendments"] = amend.record(cfg, found) if apply else len(found)
    except Exception as e:
        summary.append(f"settle: amendments skipped: {e.__class__.__name__}: {e}")

    # 2. the verdict, including a change from an earlier one
    unpaired = []
    for r, kind, code in decided:
        d = paired.get(r.row_number)
        if d is None:
            unpaired.append(r)
            continue
        if (d.get("krish_verdict") or "").strip() == r.verdict.strip():
            continue
        patch = {"krish_verdict": r.verdict, "verdict_at": NOW(),
                 "verdict_source": "sheet column A"}
        if code:
            patch["rejection_code"] = code
        if kind == "rejection":
            patch.update({"rejection_reason": r.verdict, "status": "dropped",
                          "package_status": "blocked"})
        if apply:
            db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]}, patch)
            record_verdict_event(cfg, d, r.verdict, kind, code)
        out["verdicts"] += 1
    events = learn.from_sheet_rows(unpaired, source="sheet column A, settled hourly")
    if events and apply:
        db_insert(cfg, "hunter_verdict_events", events,
                  on_conflict="job_id,verdict,reason_code,reason_text",
                  ignore_duplicates=True)
    out["verdicts"] += len(events)

    # 3. applied: the sheet first, then the database, then the open approvals
    today = datetime.date.today().isoformat()
    for r, kind, _ in decided:
        if kind != "applied":
            continue
        d = paired.get(r.row_number)
        if apply:
            try:
                if (r.cell("Application Status") or "").strip() != sheet_mod.APPLIED_STATUS:
                    sheet.mark_applied(r.row_number, when=today)
            except Exception as e:
                summary.append(f"settle: {r.company} / {r.role}: Application Status "
                               f"not written ({e.__class__.__name__}: {e})")
                continue
        out["applied"] += 1
        if not d:
            continue
        if apply:
            db_patch(cfg, "hunter_seen_roles", {"job_id": d["job_id"]},
                     {"application_state": APPLIED_STATE, "applied_at": NOW()})
        for o in open_approvals_for(cfg, d):
            if apply:
                approval.set_state(cfg, o["token"], approval.SUBMITTED,
                                   submitted_at=NOW(),
                                   failure_reason=f"{SAID_SO} (column A)")
            out["approvals_closed"] += 1

    # 4. off Pipeline, and read back
    if apply and archive:
        moved, _lines = archive_decided(sheet, canon)
        out["archived"] = moved
        left = [r for r in sheet.read_pipeline(canon.sheet_headers)
                if verdicts.parse(r.verdict)[0] in ("applied", "rejection")]
        if left:
            summary.append(f"settle: {len(left)} decided row(s) still on Pipeline "
                           f"after the archive: " + "; ".join(
                               f"{r.company} / {r.role}" for r in left[:5]))
    summary.append(
        f"settle: {out['decided']} decided, {out['verdicts']} verdict(s) recorded, "
        f"{out['amendments']} edit(s) read, {out['applied']} marked applied, "
        f"{out['approvals_closed']} approval(s) closed, "
        + (f"{out['archived']} moved to {config_mod.ARCHIVE_TAB}" if archive else
           f"moved to {config_mod.ARCHIVE_TAB} below, after his scores are read"))
    return out


def cmd_judge_eval(dev: bool = False, threshold: int | None = None,
                   replicas: int | None = None) -> int:
    """Measure the judge against his rulings. Spends money (capped at $25).
    --dev scores the development window; --threshold N scores the holdout at a
    threshold chosen there, never one chosen on the holdout itself."""
    from . import judge_eval
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    result = judge_eval.run(cfg, sheet, canon, dev=dev, threshold=threshold,
                            replicas=replicas)
    print("\n".join(judge_eval.report(result)))
    return 0


def cmd_blind_set(refresh: bool = False) -> int:
    """Draw the blind set and write its tab. Spends money (capped at $10).
    --refresh rewrites the drawn set as a Pipeline tab without judging again."""
    from . import blindset
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    if refresh:
        rec = blindset.refresh(cfg, sheet, canon)
        weak = [p["company"] for p in rec["picks"] if p.get("case_problems")]
        print(f"blind set: {len(rec['picks'])} roles rewritten as a Pipeline tab; "
              f"{len(weak)} without a grounded case" + (f" ({', '.join(weak)})" if weak else ""))
        return 0
    rec = blindset.draw(cfg, sheet, canon)
    print(f"blind set: {len(rec['picks'])} roles on the {blindset.TAB} tab, drawn from "
          f"{rec['judged']} judged of {rec['pool']} blocked, ${rec['usd']:.2f}; "
          f"strata {rec['strata']}")
    return 0


def cmd_blind_eval(apply: bool = False) -> int:
    """Score the blind set from his column A; --apply puts his Yes roles on
    Pipeline. Quiet when there is no blind set."""
    from . import blindset
    if not blindset.RECORD.exists():
        print("blind set: none drawn")
        return 0
    record = json.loads(blindset.RECORD.read_text())
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    try:
        labels = blindset.read(sheet)
    except Exception as e:
        print(f"blind set: the {blindset.TAB} tab could not be read ({e.__class__.__name__})")
        return 0
    print("\n".join(blindset.report(record, blindset.evaluate(record, labels))))
    if apply:
        moved = blindset.apply_yes(cfg, sheet, record, labels)
        print(f"blind set: {len(moved)} Yes role(s) moved onto Pipeline")
    return 0


def archive_columns_step(sheet: Sheet, *, apply: bool) -> str:
    """Applied gains his two columns before anything writes to it, in one
    line for the summary. Raises on a header it does not recognise."""
    rep = sheet.ensure_archive_columns(apply=apply, archive_tab=config_mod.ARCHIVE_TAB,
                                       archive_sheet_id=config_mod.ARCHIVE_SHEET_ID)
    tab = config_mod.ARCHIVE_TAB
    if rep["noop"]:
        return f"{tab} columns: {rep['state']} layout, nothing to change"
    if rep.get("applied"):
        return (f"{tab} columns: {sheet_mod.KRISH_SCORE} and {sheet_mod.KRISH_REACTION} "
                f"inserted, {rep['rows']} row(s) read back unchanged")
    return (f"{tab} columns: still the previous layout, {rep['rows']} row(s); "
            f"dry run, add --apply to insert {sheet_mod.KRISH_SCORE} and "
            f"{sheet_mod.KRISH_REACTION}")


def cmd_settle(apply: bool = False) -> int:
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    summary: list[str] = []
    # Here and not inside settle_step, which returns at once when nothing is
    # decided, as it did on every hourly run after he inserted his columns.
    columns_ok = True
    try:
        print(archive_columns_step(sheet, apply=apply))
    except Exception as e:
        columns_ok = False
        print(f"{config_mod.ARCHIVE_TAB} columns NOT checked: "
              f"{e.__class__.__name__}: {e}")
    # Before settle, which moves a Declined row off Pipeline: a package his
    # score built is withdrawn while its row is still where it can be read.
    withdrawals_ok = True
    try:
        score_withdrawals_step(cfg, canon, sheet, summary, apply=apply)
    except Exception as e:
        withdrawals_ok = False
        summary.append(f"score withdrawals failed: {e.__class__.__name__}")
    out = settle_step(cfg, canon, sheet, summary, apply=apply, archive=False)
    # After settle, whether or not settle found anything decided: it returns
    # early on most hours, and his scores are read every hour regardless.
    ratings_step(cfg, canon, sheet, summary, apply=apply)
    # Then the one move: what column A decided and what his score sends to
    # Applied, after the record has read his scores back.
    leave_ok = True
    try:
        leave_step(cfg, canon, sheet, summary, apply=apply)
    except Exception as e:
        leave_ok = False
        # The class only: a move's error names the row it refused, and a row
        # his score moves is a row he scored.
        summary.append(f"leave pass failed: {e.__class__.__name__}")
    print("\n".join(summary))
    if not apply and out["decided"]:
        print("dry run. add --apply to record and move these rows")
    return 0 if columns_ok and withdrawals_ok and leave_ok else 1


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

    # Applied gains his two columns before this pass reads it (reconcile,
    # below) or writes it (the archive).
    try:
        summary.append(archive_columns_step(sheet, apply=True))
    except Exception as e:
        summary.append(f"{config_mod.ARCHIVE_TAB} columns NOT checked: "
                       f"{e.__class__.__name__}: {e}")

    # His edits are read BEFORE anything of hunter's is written, because the
    # first build of the pass would overwrite the very cells that carry them.
    try:
        counts["amendments"] = amendment_step(cfg, canon, sheet, summary)
    except Exception as e:
        summary.append(f"amendment pass skipped: {e.__class__.__name__}")

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

    # His scores after his column A verdicts, so the duplicate path sees the
    # roles the learning step just read. Nothing here feeds G12.
    rated = ratings_step(cfg, canon, sheet, summary, apply=True, roles=out.get("roles"))
    counts["ratings_new"] = rated.get("new", 0)

    # What column A decided and what his score sends to Applied leave in one
    # move, now that the record has read his scores: before the builds, so a
    # Yes he has scored 3 is not built for in the same pass, and before the
    # invariants, whose twin repair reads the rows that stay.
    try:
        lv = leave_step(cfg, canon, sheet, summary, apply=True)
        counts["archived"] += lv.get("moved", 0)
        counts["set_aside"] = lv.get("set_aside", 0)
        counts["back_on_pipeline"] = lv.get("back", 0)
    except Exception as e:
        summary.append(f"leave pass failed: {e.__class__.__name__}")

    # packages for every Yes without one
    cache = disc.load_cache(cfg)
    try:
        todo = select_for_build(cfg, sheet, canon.sheet_headers,
                                cap=max_packages, retry_dead=retry_dead,
                                score_wins=leave_mod.in_canon(canon))
    except Exception as e:
        todo = []
        summary.append(f"package selection failed: {e.__class__.__name__}: {e}")
    summary.append(f"packages to build: {len(todo)}")
    if todo and style_mod.builds_paused():
        # His ruling 2026-10-15: no new package until it can be built in his
        # house style (package/style.py). Nothing is fetched, paid for or
        # written, so each is chosen again on the first run that can build it.
        counts["waiting_style"] = len(todo)
        summary.append(f"  none built: {style_mod.PAUSED}")
        todo = []
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
            elif any(line.startswith(f"SKIPPED {row['job_id']}") or line.startswith("SKIPPED:")
                     for line in summary[before:]):
                # Its row changed under the pass: nothing was fetched or paid
                # for, and it is not a refusal.
                counts["skipped"] = counts.get("skipped", 0) + 1
            else:
                counts["blocked"] += 1
        except Exception as e:
            # The class only, as score-builds and rebuild-packages print it:
            # a message can quote the letter (a paragraph anchor taken from
            # the hook, written from his view), and this summary is public.
            summary.append(f"BUILD FAILED {row['job_id']}: {e.__class__.__name__}")
            # build_one records the package as built before it writes the
            # links to his row, and that write reads back twice: a 429 there
            # used to overwrite a built, paid-for package as blocked, and
            # every path that sends an application skips a blocked one. What
            # is counted is what the record and his row say now.
            outcome, lines = build_error_outcome(cfg, row["job_id"], e, sheet=sheet,
                                                 canon=canon, row=row)
            summary.extend(lines)
            if outcome == BUILD_KEPT:
                counts["built"] += 1
            elif outcome == BUILD_BLOCKED:
                counts["blocked"] += 1
            else:
                # Raised, and set to be built again (or left as it was when
                # nothing could be read back): a failure, not a refusal.
                counts["failed"] = counts.get("failed", 0) + 1
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

    # The invariants run BEFORE this archive, because the repair that stamps a
    # duplicate row is what makes it a decided row the archive can then move.
    # The rows his score moves, and the rows column A decided, left above.
    try:
        inv = invariants.enforce(sheet, canon.sheet_headers, apply=True,
                                 batches=funnel_batches(cfg, sheet, summary))
        counts["repairs"] = len(inv["repaired"])
        counts["still_broken"] = len(inv["still_broken"])
        summary.extend(inv["lines"])
    except Exception as e:
        summary.append(f"invariant pass failed: {e.__class__.__name__}: {e}")

    # Rows the invariant repairs just decided (a twin stamped Declined -
    # duplicate row) leave too, from a read of their own; the rest sort.
    try:
        moved, lines = archive_decided(sheet, canon)
        counts["archived"] += moved
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
        summary.append(f"layout verified: {lay['visible']} columns visible, "
                       f"{lay['hidden']} hidden, {lay['width_after']}px wide"
                       + (" (unchanged)" if lay.get("noop") else "")
                       + (f", {lay['score_colours']} score colour rule(s) narrowed "
                          f"to {sheet_mod.AI_SCORE}" if lay.get("score_colours") else ""))
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
    failed = (f"{counts['failed']} failed and tried again next pass, "
              if counts.get("failed") else "")
    waiting = (f"{counts['waiting_style']} waiting for his house style, "
               if counts.get("waiting_style") else "")
    return (f"{counts.get('built', 0)} built ({counts.get('unverified', 0)} unverified), "
            f"{counts.get('dead', 0)} dead, {counts.get('blocked', 0)} blocked, {waiting}{failed}"
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
        # What the run's model calls cost, by job, read from what each call
        # reported. The judge's own rows are already in the ledger; the rest
        # are written here.
        from . import spend
        summary.extend(spend.summary())
        try:
            line = spend.flush(load())
            if line:
                summary.append(line)
        except Exception as e:
            summary.append(f"spend: ledger not written ({e.__class__.__name__})")
        try:
            cfg2 = load()
            send_summary(cfg2, "\n".join(summary))
        except Exception as e:
            print(f"notify failed: {e}")
            print("\n".join(summary))
    return 1 if failed else 0


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
        if counts["recorded"] == 0 and not pcounts.get("reconciled"):
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
            # His edits are recorded (amend.py), and nothing learns from them
            # yet. This said they were "fed back into the scorer" until
            # 2026-10-15, which was never true, so it says nothing now.
            # Said only while it is true: the move runs once canon carries it.
            if leave_mod.in_canon(canon):
                notes.append("A role you score 1 to 5, or answer with only a reaction, "
                             "moves to the Applied tab with your score and reaction; "
                             "score it 7 or more there and it comes back.")
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
        # What the run's model calls cost, by job, read from what each call
        # reported. The judge's own rows are already in the ledger; the rest
        # are written here.
        from . import spend
        summary.extend(spend.summary())
        try:
            line = spend.flush(load())
            if line:
                summary.append(line)
        except Exception as e:
            summary.append(f"spend: ledger not written ({e.__class__.__name__})")
        try:
            cfg2 = load()
            send_summary(cfg2, "\n".join(summary))
        except Exception as e:
            print(f"notify failed: {e}")
            print("\n".join(summary))
    return 1 if failed else 0


def cmd_spend(days: int = 31) -> int:
    """Read only. What the model calls cost, by day and by job, from the
    ledger every call writes to (spend.py). The Console shows the bill; this
    shows which part of hunter ran it up."""
    from . import spend
    cfg = load()
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).isoformat()
    rows = db_get(cfg, spend.TABLE, {"select": "id,at,purpose,model,usd",
                                      "at": f"gte.{since}", "limit": ALL_ROWS})
    by_day: dict = {}
    for r in rows:
        key = (str(r["at"])[:10], r.get("purpose") or "?", r.get("model") or "?")
        n, usd = by_day.get(key, (0, 0.0))
        by_day[key] = (n + 1, usd + float(r.get("usd") or 0))
    total = sum(u for _, u in by_day.values())
    print(f"model spend recorded in the last {days} days: ${total:.2f} "
          f"in {len(rows)} call(s)")
    for (day, purpose, model), (n, usd) in sorted(by_day.items()):
        print(f"  {day}  {purpose:<13} {model:<20} {n:>4} call(s)  ${usd:.2f}")
    stop = spend.over_budget(cfg)
    print(stop or f"under this month's ceiling "
                  f"(hunter_llm_max_usd_per_month, default ${spend.DEFAULT_MONTH_CAP})")
    # Control Center drafts approaches for the cards hunter lands in
    # pilot_deals, on its own key. It meters every call in meter_daily; read it
    # here so this report covers the whole loop without recording it twice.
    try:
        cc = db_get(cfg, "meter_daily", {
            "select": "unit_key,usd,runs", "provider": "eq.anthropic",
            "unit_key": "in.(pilots,pilot-trigger,pilot-draft)",
            "day": f"gte.{since[:10]}", "limit": ALL_ROWS})
        cc_usd = sum(float(r.get("usd") or 0) for r in cc)
        cc_runs = sum(int(r.get("runs") or 0) for r in cc)
        print(f"Control Center pilot drafting, same window: ${cc_usd:.2f} in "
              f"{cc_runs} call(s) (meter_daily, its own key, outside this ceiling)")
    except Exception as e:
        print(f"Control Center pilot drafting: not read ({e.__class__.__name__})")
    return 0


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
            spec = form_for(url, board_key)
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
    private, note = bankseed.private_seeds(cfg)
    changes, problems = bankseed.plan_changes(rows, bankseed.seeds() + private)

    print(f"{INFO_TAB}: {len(rows)} rows read, {len(changes)} cell(s) to write"
          f"{'' if apply else ' (dry run, pass --apply to write)'}")
    print(f"{note}\n")
    for c in sorted(changes, key=lambda x: (x.row, x.column)):
        tag = "NEW ROW" if c.is_new else "       "
        print(f"  {tag} {c.column}{c.row:<4} {c.field_name[:46]}")
        if c.private:
            print("          (his own answer, not printed)")
            continue
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


def approved_essays(approval_row: dict | None) -> dict[str, str] | None:
    """The essays he approved, from the plan stored with his approval.

    Pressing an application used to redraft every essay: paid for twice, and
    a fresh draft is different words, which changes the plan hash and can make
    submit refuse the plan he approved. None when nothing was stored."""
    raw = (approval_row or {}).get("fill_plan")
    try:
        plan = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return None
    essays = plan.get("essays") if isinstance(plan, dict) else None
    return dict(essays) if isinstance(essays, dict) else None


def his_view_for(cfg: Config, sheet: Sheet, row: dict, *, pipeline=None,
                 latest: dict | None = None):
    """His view of the role behind a record (package.his_view): his row for
    its posting (own_sheet_row, never a twin), or what was recorded when that
    row cannot be found or the sheet cannot be read."""
    from .package import his_view as his_view_mod
    try:
        rows = pipeline if pipeline is not None else sheet.read_pipeline(sheet_mod.HEADERS)
        srow = own_sheet_row(rows, row)
    except Exception:
        srow = None
    return his_view_mod.his_view(cfg, row.get("job_id") or "", srow, record=row,
                                 latest=latest)


class ContactMismatch(RuntimeError):
    """A fill plan whose contact fields disagree with the role's header base.
    Raised, so the send path counts a failure and sends nothing; its message
    names bases and never a digit."""


def contact_for(cfg: Config, row: dict):
    """(the role's header base as a package.contact.Base or None, every base
    system_config holds). The base is the one recorded on the role
    (header_base), never worked out again here: the letter and the CV were
    built with that header, and the form must agree with them."""
    from .package import contact as contact_mod
    bases, _note = contact_mod.load_bases(cfg)
    return bases.get((row.get("header_base") or "").strip()), bases


def build_fill_plan(cfg: Config, sheet: Sheet, row: dict, *,
                    bank=None, summary: str = "", hook: str = "",
                    draft_essays: bool = True, essays: dict[str, str] | None = None,
                    view=None):
    """(FillPlan, Audit) for one approved role. No network writes, no mail.

    Shared by approvals and, later, submit, so the plan the email shows and the
    plan the browser fills are produced by the same code. A second implementation
    is how the plan hash stops meaning anything.

    view is his view of the role (his_view_for), which the essays are written
    from; looked up here when the caller has not, and only when there is an
    open question to answer.
    """
    from .apply import audit as audit_mod
    from .apply import fetch, fill
    from .ats import discover as disc
    role, _relink, _flags = resolve_for_build(cfg, row, disc.load_cache(cfg))
    bank = bank if bank is not None else _answer_bank(sheet)
    spec = fetch.form_for(role.jd_url, board_key)
    from .apply.resolve import NEEDS_BASE
    from .package import contact as contact_mod
    contact, bases = contact_for(cfg, row)
    au = audit_mod.audit(spec, bank, role_location=role.location, contact=contact)
    plan = fill.build_payload(spec, bank, company=role.company, role=role.title,
                              jd_url=role.jd_url, role_location=role.location,
                              summary=summary, hook=hook,
                              attachment_style=au.attachment_style, contact=contact)
    if any((f.reason or "").startswith(NEEDS_BASE) for f in plan.blocking):
        # Refused before any essay is drafted and paid for: the form needs
        # his phone or address, and which ones is his header's call.
        return plan, au, role
    # The open questions. FillPlan has carried an `essays` slot since it was
    # written and nothing ever filled it, so "What makes you excited about the
    # ElevenLabs mission?" came back blank on every form since the first
    # application. Drafted from the same evidence the cover letter is traced
    # against and put through the same voice gate, then flagged, because these
    # are the sentences most likely to need his judgement.
    if essays is not None:
        # The essays already approved: reused, never redrafted.
        if essays:
            plan = fill.build_payload(
                spec, bank, company=role.company, role=role.title,
                jd_url=role.jd_url, role_location=role.location,
                summary=summary, hook=hook, essays=essays,
                attachment_style=au.attachment_style, contact=contact)
            plan.notes.append(f"reused {len(essays)} approved essay(s); none redrafted")
    elif draft_essays:
        from .apply.resolve import NEEDS_ESSAY
        questions = [f.label for f in plan.fields
                     if f.unresolved
                     and (f.reason or "").startswith(NEEDS_ESSAY)]
        if questions:
            notes: list[str] = []
            if view is None:
                view = his_view_for(cfg, sheet, row)
            drafted = _draft_essays(cfg, sheet, role, questions, notes, view=view)
            for line in notes:
                plan.notes.append(line)
            if drafted:
                plan = fill.build_payload(
                    spec, bank, company=role.company, role=role.title,
                    jd_url=role.jd_url, role_location=role.location,
                    summary=summary, hook=hook, essays=drafted,
                    attachment_style=au.attachment_style, contact=contact)
                plan.notes.extend(notes)
    if contact is not None:
        # Read back from the plan itself: every phone field carries this
        # base's number, and nothing carries the other base's number or place.
        problems = contact_mod.check_plan(plan, contact, bases)
        if problems:
            raise ContactMismatch("; ".join(problems))
    return plan, au, role


def _draft_essays(cfg: Config, sheet: Sheet, role, questions: list[str],
                  notes: list[str], *, view=None) -> dict[str, str]:
    """Answers to the open questions, or nothing and a reason for each.
    view is his view of the role, which the answers are written from under
    the package's rules (essays.draft_one)."""
    from .apply import essays as essays_mod
    from .apply.infobank import load_bank
    from .docbuild import DocBuild
    from .package.build import read_master_facts
    from .package.voicegate import build_evidence, package_banned
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
        banned = package_banned(bank.banned_phrases)
    except Exception as e:
        notes.append(f"NOT drafted: evidence unreadable "
                     f"({e.__class__.__name__}: {str(e)[:120]})")
        return {}
    return essays_mod.draft_all(cfg, questions, company=role.company,
                                role=role.title, jd_text=role.jd_text or "",
                                evidence=evidence, banned_phrases=banned,
                                notes=notes, his_view=view)


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


# The query parameters that name a posting on a careers page, kept with
# their values. Every other parameter is dropped: an allow-list, because the
# parameters that only say where a click came from are open-ended. LinkedIn
# adds position and pageNum to every link it hands out, so two sweeps of one
# job came back with two links, and a deny-list of tracking parameters made
# them two postings: two "Apply by hand" emails for one job.
POSTING_PARAMS = frozenset({
    "gh_jid", "id", "jid", "jobid", "job_id", "job", "posting", "postingid",
    "posting_id", "reqid", "req_id", "requisitionid", "requisition_id", "jobreqid",
    "opening", "openingid"})
_LINKEDIN_VIEW = re.compile(r"(?:^|\.)linkedin\.com$")
_LINKEDIN_JOB = re.compile(r"^/jobs/view/(?:[^/]*?-)?(\d+)/?$")


def posting_key(row: dict) -> str:
    """One posting, however many role rows point at it.

    The employer's form is what is being applied to, so the posting's own
    identity is what matters. job_id is not: the scheme changed between two
    sweeps and one Harvey posting ended up under
    `harvey:head-gtm-strategy-ops-amer` and
    `harvey:head-of-gtm-strategy-operations-amer`, which are two ids for one
    application.

    The board and its posting id where the link names one (board_key, the
    posting id without any query or fragment), the Greenhouse posting id on
    a careers page that carries it (gh_jid), a LinkedIn job by the number
    at the end of its path, otherwise the link itself with its scheme,
    fragment and trailing slash taken off and only the query parameters
    that name a posting (POSTING_PARAMS) kept. It cut everything after "?"
    until 2026-10-14, which made every posting on one careers path one
    posting: the second wanted role was never mailed, the first one's
    approval blocked the second for good, and his AMEND words for one were
    written into the other's letter.
    """
    url = (row.get("job_url") or row.get("url") or "").strip()
    if not url:
        return ""
    key = board_key(url)
    if key:
        # The Workday pattern's posting id runs to the end of the link, so a
        # ?source=LinkedIn on one copy made it another posting.
        ats, slug, pid = key
        pid = pid.split("#")[0].split("?")[0].rstrip("/")
        return "|".join((ats, slug, pid)).lower()
    jid = gh_jid(url)
    if jid:
        return f"greenhouse-jid|{jid}"
    parts = urlsplit(url.lower())
    if _LINKEDIN_VIEW.search(parts.netloc):
        m = _LINKEDIN_JOB.match(parts.path)
        if m:
            return f"linkedin|{m.group(1)}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k in POSTING_PARAMS])
    out = parts.netloc + parts.path.rstrip("/")
    return out + ("?" + query if query else "")


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


def sheet_identity(company: str, role: str) -> tuple[str, str]:
    """How a row on the sheet and a row in the database are decided to be the
    same posting, without pairing, fuzz, or any dependence on what else is in
    the list.

    One definition, because cmd_approvals and cmd_prune_sheet both need it and
    cmd_approvals locally imports a DIFFERENT slugify (package.build's) that
    merely happens to agree with this one on today's company names.
    """
    return (slugify(company or ""), _norm_title(role or ""))


def posting_is_gone(row: dict) -> bool | None:
    """Has the board stopped serving this posting. None means hunter cannot
    tell, which is a third answer and never collapses into either of the
    other two.

    This exists because "hunter cannot read the form" and "the posting has
    gone" are different facts and were conflated. apply/fetch returns
    unreadable() for BOTH a LinkedIn URL needing a login AND an ATS whose
    form adapter has not been written, and the second one says in its own
    words "the posting is readable but its form is not". On 2026-09-24 that
    conflation marked Versapay and Rembrand dead on his sheet, two live Lever
    postings he had said Yes to, one of them an hour earlier.

    So liveness is ASKED of the board here rather than inferred from whether
    a form could be parsed. No answer is not an answer.
    """
    from .ats import ashby, greenhouse, lever, workday
    # board_key: a gh_jid link names no board to ask, so it has no answer.
    key = board_key(row.get("url") or row.get("job_url") or "")
    if not key:
        return None
    ats, slug, pid = key
    fetchers = {"greenhouse": greenhouse.fetch_posting,
                "lever": lever.fetch_posting, "ashby": ashby.fetch_posting,
                "workday": workday.fetch_posting}
    fn = fetchers.get(ats)
    if fn is None:
        return None
    try:
        live, _, _ = fetch_with_retry(fn, slug, pid)
    except Exception:
        # Unreachable is not dead, and must never be reported as such.
        return None
    return not live


def cmd_approvals(apply: bool = False, job_id: str = "", prefill: bool = True, *,
                  chosen: list[dict] | None = None, tally: dict | None = None,
                  failures: list[str] | None = None) -> int:
    """Build and, with --apply, send one approval email per built package.

    Dry run by default: it builds the whole application as the email would show
    it and mints no token, so nothing can be approved by accident. A token is
    recorded only when the mail is actually sent, because a token with no email
    behind it is a live approval Krish never saw.

    What it prints is counts and neutral outcomes, never the email itself, on
    any path, dry run or not. Prepare in Control Center runs this in the hourly
    drain, whose Actions log is public, and the email carries the open link
    (the token and its key, which hand the fill payload, his CV and every
    recorded answer to whoever holds them), his address, the essays drafted
    from his reaction and every field value. The email is what he reads; the
    log says that it went.

    chosen is the records cmd_approvals_wanted picked (wanted_for_approval):
    built packages for the roles he wants, one per posting. They replace the
    query below and are sent whatever built them, because asking for them is
    what he did, each only while his row for it, found by its link, still
    asks for it now (router.wants). A form hunter cannot fill and one
    refused for a missing answer are marked as dealt with (mark_prepared),
    the refusal against his answer bank, and one waiting on an essay that
    did not draft is marked for a day, so the next batch moves on. tally, when given, counts each outcome, for
    a caller that prints counts and nothing else; failures, when given, gets
    the class of each error that stopped one (actions._prepare's note).
    """
    def count(outcome: str) -> None:
        if tally is not None:
            tally[outcome] = tally.get(outcome, 0) + 1

    def failed_as(e: BaseException) -> None:
        count("failed")
        if failures is not None:
            failures.append(e.__class__.__name__)

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
              # two Harvey emails went out for one posting. A role his score
              # set aside has left his sheet on a 1 to 5 or a reaction, and
              # the sheet check below goes by company and title: a Yes row of
              # the same title in another country let it be prepared again.
              "status": f"not.in.(duplicate,{SET_ASIDE_STATUS})",
              "order": "package_built_at.desc", "limit": "50"}
    if job_id:
        params["job_id"] = f"eq.{job_id}"
    rows = list(chosen) if chosen is not None else db_get(cfg, "hunter_seen_roles", params)
    if not rows:
        count(NOT_BUILT)
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
    # Identity, not match_rows.
    #
    # The first version of this guard called match_rows(srows, list(rows)), and
    # on 2026-09-24 Confidential and Strativ Group got past it and were only
    # stopped further down by the LinkedIn branch. Why they got past it is NOT
    # established: a fuzzy-mispairing reproduction was written and did not
    # reproduce, and invariants has no path that re-adds a row. So this does
    # not claim a cause.
    #
    # What it does is stop depending on match_rows here at all. That function
    # has a fuzzy phase and is built to pair the sheet against the WHOLE
    # database; handed only the handful of built packages, it is being asked a
    # question it was not written for. The comparison below is the exact one
    # cmd_prune_sheet uses, it cannot pair anything to anything, and it does
    # not change behaviour with the size of the list it is given.
    srows = sheet.read_pipeline(canon.sheet_headers)
    on_sheet = {sheet_identity(r.company, r.role) for r in srows}
    if chosen is not None:
        # Chosen by link (wanted_for_approval pairs his row to its record by
        # link first), so found by link, never by the record's company and
        # title, which he may have corrected on the row. And asked for still,
        # read off the row now: a batch takes minutes a role, he scores the
        # sheet while it runs, and settle cannot cancel what this run sends
        # until it ends (CLAUDE.md 3f: "only while the row still asks for it").
        kept = []
        for r in rows:
            mine = own_sheet_row(srows, r)
            # Counts, without the role: a row leaves Pipeline, and stops
            # asking, on his score.
            if mine is None:
                count("off_pipeline")
                print("skip: a row no longer on the Pipeline sheet")
            elif router.wants(mine) is None:
                count("no longer asked for")
                print("skip: a row that no longer asks for an application")
            else:
                kept.append(r)
        if not kept:
            return 1
        rows, gone = kept, []
    else:
        gone = [r for r in rows
                if sheet_identity(r.get("company"), r.get("title")) not in on_sheet]
    if gone:
        for r in gone:
            count("off_pipeline")
        # A row leaves Pipeline on his score as well as on column A, so which
        # ones did is not said here: the log is public.
        print(f"skip {len(gone)} package(s) whose row is no longer on the Pipeline sheet")
        drop = {r.get("job_id") for r in gone}
        rows = [r for r in rows if r.get("job_id") not in drop]
    if not rows:
        print("every built package belongs to a row that has left the sheet")
        return 1

    # A package his score built, or one withdrawn when he lowered the score,
    # goes out one at a time, from Prepare in Control Center, never in a
    # batch. A batch run mails every built package still on Pipeline with
    # no live token, up to 50, and on 2026-09-15 that was 57 emails of
    # which 37 were later cancelled; the scored backlog is about 30. With
    # --job-id, the row must still ask for it at this moment: a Yes in
    # column A or 7 or more in K, read off the sheet now.
    def his_score_built(r: dict) -> bool:
        t = r.get("package_trigger") or ""
        return router.built_on_score(t) is not None or t.startswith(router.WITHDRAWN)

    scored = [r for r in rows if his_score_built(r)]
    if scored and not job_id and chosen is None:
        print(f"skip {len(scored)} package(s) his Krish Score built: each is sent "
              f"when he presses Prepare on it in Control Center")
        rows = [r for r in rows if not his_score_built(r)]
        if not rows:
            print("nothing else to send")
            return 0
    elif scored and chosen is None:
        # His row for THIS posting, by its link (_rows_for), never any row of
        # the same company and title: the same seat in another country still
        # scored 9 let the application for the one he lowered to 4 go out.
        asking = [x for r in scored for x in _rows_for(r, srows) if router.build_trigger(x)]
        if not asking:
            # Said without the role or the score: this log is public.
            count("no longer asked for")
            print("REFUSING: the row no longer asks for an application, so none "
                  "is prepared")
            return 1
    if job_id and chosen is None and leave_mod.in_canon(canon):
        # Prepare on a Yes his score has since taken back (K 1 to 5, or only
        # a reaction): his score wins over the older Yes (canon 9.13), and
        # the essays would be drafted from his no. Whatever built the
        # package: one his score built on a row that now reads Yes with K 3
        # got past the score-built branch above, which asks build_trigger,
        # and build_trigger says Yes for any Yes. A Yes with K 6 or K blank
        # is still his to send; pressing Prepare is asking for it. Another
        # row of the same posting that still asks (router.wants) holds it.
        taken_back = [r for r in rows
                      if any(leave_mod.yes_overridden(x) for x in _rows_for(r, srows))
                      and not any(router.wants(x) for x in _rows_for(r, srows))]
        if taken_back:
            count(TAKEN_BACK)
            print("REFUSING: his score has since taken this Yes back, so no "
                  "application is prepared")
            return 1
    if not job_id and chosen is None:
        # The bulk run, by hand: the same question every other path asks of
        # his row now (router.wants), so a Yes he has since scored 3, or a
        # score he lowered, is not mailed to him while it waits to leave.
        # Counted, without the role.
        asked = [r for r in rows if any(router.wants(x) for x in _rows_for(r, srows))]
        if len(asked) < len(rows):
            for _ in range(len(rows) - len(asked)):
                count("no longer asked for")
            print(f"skip {len(rows) - len(asked)} package(s) whose row no longer asks "
                  f"for an application")
            rows = asked
            if not rows:
                print("nothing else to send")
                return 0

    # His house style (package/style.py), on every path that sends: the bulk
    # run, approvals --wanted, Prepare, the amend in approvals-drain and the
    # by-hand email, which all come through here. A package built before it
    # is never sent, and never prepared either, since a fill plan drafts
    # essays and those are paid for. Its own outcome, counted without the
    # role: not a missing answer, so never marked against his answer bank.
    old = [r for r in rows if not style_mod.stamped(r)]
    if old:
        for _ in old:
            count(style_mod.OLD_STYLE)
        print(f"skip {len(old)} package(s) {style_mod.OLD_STYLE}: none is sent "
              f"until it is rebuilt in it")
        rows = [r for r in rows if style_mod.stamped(r)]
        if not rows:
            return 1 if job_id else 0

    try:
        to = notify.mailbox(cfg)
    except Exception as e:
        # The class only: an address refused by the allow-list is named in
        # the message.
        failed_as(e)
        print(f"REFUSING: his mailbox could not be read ({e.__class__.__name__})")
        return 1
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
    # Delivered, just not fillable. Kept apart from `failed` because these two
    # things exit differently: a red run has to mean something went wrong, and
    # a LinkedIn Easy Apply role will be unfillable on every run for ever. One
    # permanently red workflow teaches him to stop reading the colour.
    by_hand: list[str] = []
    for row in rows:
        key = posting_key(row)
        if key and key in taken:
            count("taken")
            # The state, never the token: a token is half of the open link.
            print(f"skip {row['job_id']}: an approval is already "
                  f"{taken[key][1]} for this same posting")
            continue
        # One posting must never take the batch with it. On 2026-09-20 a
        # single Ashby form carrying an EducationHistory block raised on row
        # 14 of 38 and the other 24 applications, already built and waiting,
        # were never sent. Whatever one form does, the rest still go, and
        # every failure is named at the end rather than ending the run.
        try:
            # His view of the role, from his row for this posting, so the
            # open questions are answered from his reasons (essays.draft_one).
            plan, au, role = build_fill_plan(
                cfg, sheet, row, bank=bank,
                view=his_view_for(cfg, sheet, row, pipeline=srows))
        except Exception as e:
            failed_as(e)
            # The class only: a message from the fill plan can carry a
            # field value, an answer of his or an essay.
            failed.append(f"{row['job_id']}: {e.__class__.__name__}")
            print(f"  SKIPPED {row['job_id']}: {e.__class__.__name__}")
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
            try:
                cv_pdf, letter_pdf = db.export_pdf(cv_id), db.export_pdf(letter_id)
            except Exception as e:
                # The class only: a Drive error names the document in its URL.
                failed_as(e)
                failed.append(f"{row['job_id']}: the documents could not be exported "
                              f"({e.__class__.__name__})")
                print(f"  SKIPPED {row['job_id']}: the documents could not be exported "
                      f"({e.__class__.__name__})")
                taken.pop(key, None)
                continue
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
                print("  form not filled in advance")

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
            print(f"  no fill payload for this one ({e.__class__.__name__})")
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

        print(f"  application built: fields {len(plan.fields)}, essays "
              f"{len(plan.essays or {})}, blocking {len(plan.blocking)}, flagged "
              f"{len(plan.flagged)}, attachments "
              f"{'one merged file' if merged_name else 'two links'}")
        if not apply:
            count("dry")
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
            print("  REFUSING to send: the form could not be read")
            # Whether the POSTING has gone is a separate question from whether
            # its form could be parsed, and it is asked of the board rather
            # than guessed from the parse. A first attempt at this listed the
            # ats names that mean "cannot enumerate" and missed the branch
            # that carries the real name, so "no form adapter for lever yet;
            # the posting is READABLE but its form is not" was read as death
            # and retired two live roles he had approved.
            gone = posting_is_gone(row) if apply else None
            if gone:
                count("retired")
                retire_dead_posting(cfg, canon, sheet, row["job_id"],
                                    company=role.company, role=role.title,
                                    why=why)
                continue
            where = plan.jd_url or row.get("url") or "the posting"
            print(f"    the row stays: hunter could not read the form, and the "
                  f"posting is {'still live' if gone is False else 'not known to be gone'}. "
                  f"Apply by hand from the email")
            # Send him the application anyway. It was written, gated and built;
            # refusing to send meant it never reached him at all.
            if apply:
                try:
                    delivered = send_apply_by_hand(
                        cfg, company=role.company, role=role.title,
                        jd_url=where,
                        cv_url=row.get("package_cv_url") or "",
                        letter_url=row.get("package_letter_url") or "",
                        essays=plan.essays, why=why,
                        attachments=attachments or None)
                    if not (delivered or {}).get("sent"):
                        count(NOT_SENT)
                        failed.append(f"{row['job_id']}: the documents were not "
                                      f"emailed (no mail channel)")
                        print("    the documents were not emailed (no mail channel)")
                        continue
                    print("    documents emailed to him to apply by hand")
                except Exception as e:
                    # The send failing IS a failure: the application did not
                    # reach him, which is the only thing this step is for.
                    # Only a delivered one is allowed to keep the run green.
                    print(f"    could not email the documents ({e.__class__.__name__})")
                    failed_as(e)
                    failed.append(f"{row['job_id']}: documents could not be "
                                  f"emailed: {e.__class__.__name__}")
                    continue
            count(BY_HAND)
            note = mark_prepared(cfg, row, BY_HAND)
            if note:
                print(note)
            by_hand.append(f"{row['job_id']}: {plan.ats} form cannot be "
                           f"filled automatically, documents emailed")
            continue
        if not plan.ready:
            print(f"  REFUSING to send: {len(plan.blocking)} required field(s) "
                  f"have no answer")
            # Marked as dealt with for good only when every missing answer
            # is one his answer bank has to give, and against that bank, so
            # the answer he adds re-opens it. An essay that did not draft is
            # hunter's own failure and may come through later: marked for a
            # day (ESSAY_WAIT_HOURS), so the next batch moves on and the
            # posting is tried again after it.
            from .apply.resolve import NEEDS_BASE, NEEDS_ESSAY
            if any((getattr(f, "reason", "") or "").startswith(NEEDS_BASE)
                   for f in plan.blocking):
                # Which header the role takes is his call (US or UK), never a
                # missing answer of his: not marked, so it is prepared again
                # the moment the role has a base.
                count(NO_BASE)
                continue
            if any((getattr(f, "reason", "") or "").startswith(NEEDS_ESSAY)
                   for f in plan.blocking):
                count(ESSAY_NOT_DRAFTED)
                note = mark_prepared(cfg, row, ESSAY_NOT_DRAFTED)
                if note:
                    print(note)
                continue
            count(REFUSED_BLOCKING)
            note = mark_prepared(cfg, row, REFUSED_BLOCKING, bank=bank)
            if note:
                print(note)
            continue
        # The send and the token's record, each by class only on failure: a
        # Gmail error quotes its response, an address refused names it, and
        # a failed insert echoes the row it could not write, token first.
        # The log is public; the run still goes on to the next posting and
        # exits 1.
        try:
            out = notify.send_email(cfg, email.subject, email.html, to=to,
                                   text=email.text,
                                   attachments=attachments or None)
        except Exception as e:
            failed_as(e)
            failed.append(f"{row['job_id']}: the Apply email was not sent "
                          f"({e.__class__.__name__})")
            print(f"  the Apply email was not sent ({e.__class__.__name__})")
            taken.pop(key, None)
            continue
        if not (out or {}).get("sent"):
            # No channel took it (gmail.send not granted). Recording a token
            # here would leave an approval waiting for an email that does not
            # exist, and every later Prepare would say one is waiting.
            count(NOT_SENT)
            failed.append(f"{row['job_id']}: the Apply email was not sent (no mail channel)")
            print("  the Apply email was not sent (no mail channel)")
            taken.pop(key, None)
            continue
        try:
            approval.record_sent(cfg, token=token, job_id=row["job_id"],
                                 company=role.company, role=role.title,
                                 fill_plan=plan.as_dict(),
                                 message_id=out.get("id", ""))
        except Exception as e:
            # The email went and its approval was not recorded: a reply to it
            # finds no row. Said, and the run is red.
            failed_as(e)
            count(SENT_UNRECORDED)
            failed.append(f"{row['job_id']}: the Apply email went, and its approval "
                          f"was not recorded ({e.__class__.__name__})")
            print(f"  the Apply email went, and its approval was not recorded "
                  f"({e.__class__.__name__})")
            continue
        try:
            db_patch(cfg, approval.TABLE, {"token": token},
                     {"fill_payload": pay, "open_key": open_key})
        except Exception as e:
            print(f"  could not store the fill payload ({e.__class__.__name__})")
        sent += 1
        count("sent")
        print(f"  Apply email sent: 1, fields {len(plan.fields)}, essays "
              f"{len(plan.essays or {})}")
    if apply:
        print(f"\n{sent} approval email(s) sent")
    if failed:
        print(f"\n{len(failed)} posting(s) could not be prepared, and the rest "
              f"were sent anyway:")
        for line in failed:
            print(f"  {line}")
    if by_hand:
        print(f"\n{len(by_hand)} posting(s) hunter cannot fill; the documents "
              f"were emailed so he can apply himself:")
        for line in by_hand:
            print(f"  {line}")
    # A failure that stopped an application reaching him is a failing run, but
    # only after every one that could be sent has been. A role he was sent the
    # documents for is NOT a failure: it reached him, which is the whole job,
    # and a LinkedIn Easy Apply posting would otherwise paint the run red on
    # every run for ever until the colour meant nothing.
    return 1 if failed else 0


# ---------- his reasons in every package, and every application he wants ----------
#
# His words, 2026-10-14: "I think there are at least 20 roles in there that I
# want to apply for pretty clearly. Do your thing where you build all the
# packages for me to open with one click and the Hunter extension can fill out
# the forms as usual. Make sure that, when you're creating all the packages,
# you include all the rationales that I've put in the new column, as well as
# the combined rationale."
#
# Two commands, both dry runs unless --apply, both printing counts only: a
# role either one names is a role he scored 7 or more or said Yes to, and the
# repository and its Actions logs are public (CLAUDE.md 3f).
#
#   rebuild-packages  every package built before his reasons reached the
#                     prompt, for a role he now scores 7 or more and has not
#                     acted on, rebuilt from them (build_one, rebuild=True)
#   approvals --wanted
#                     the Apply email, one per posting, for every built
#                     package of a role he wants (cmd_approvals, chosen=)

# What a role he has acted on looks like, in the order the reason is given.
# Each one means his application is under way, and a package swapped under it
# would change what he is about to send or has sent.
ACTED_ON = ("column A Applied", "application status", "applied on record",
            "form opened", "submitted", "approved", "awaiting, email sent", "awaiting")

LIGHT_SELECT = ("job_id,company,title,url,job_url,score,comp,location,warm_path_person,"
                "warm_path_tier,package_status,krish_verdict,rejection_reason,status,"
                "presented_at,package_trigger,package_built_at,package_cv_url,"
                "package_letter_url,application_state,why_it_fits")


def _known_with_view(cfg: Config) -> tuple[list[dict], bool]:
    """(every recorded role, whether package_his_view could be read). The
    column arrives with migration cc6; without it nothing can say which
    packages carry his reasons, and the caller has to know that."""
    from .package import his_view as his_view_mod
    try:
        return db_get(cfg, "hunter_seen_roles", {
            "select": LIGHT_SELECT + "," + his_view_mod.FIELD, "limit": ALL_ROWS}), True
    except Exception:
        return db_get(cfg, "hunter_seen_roles", {"select": LIGHT_SELECT,
                                                 "limit": ALL_ROWS}), False


TOKEN_SELECT = "token,job_id,state,sent_at,opened_at,decided_at,feedback"


def _tokens_by_posting(cfg: Config, known: list[dict],
                       toks: list[dict] | None = None) -> dict[str, list[dict]]:
    """posting key (or job id where a record has no link) -> every approval
    filed against it, in any state, with when it was sent and opened."""
    from .apply import approval
    if toks is None:
        toks = db_get(cfg, approval.TABLE, {"select": TOKEN_SELECT, "limit": ALL_ROWS})
    by_id = {d["job_id"]: d for d in known if d.get("job_id")}
    out: dict[str, list[dict]] = {}
    for t in toks:
        jid = t.get("job_id") or ""
        posting = posting_key(by_id.get(jid) or {})
        key = posting or jid
        # Each token carries the posting it was filed against, so a reader
        # can tell a twin of this posting from anything else under the key.
        t = dict(t, _posting=posting)
        out.setdefault(key, []).append(t)
        if key != jid:
            out.setdefault(jid, []).append(t)
    return out


def acted_on(srow: SheetRow | None, d: dict, tokens: dict[str, list[dict]]) -> str | None:
    """Why his application for this posting is under way, or None. Any one
    reason is enough: the row says Applied or a status other than "Not
    applied", the record says applied, or an approval for the posting was
    opened in his browser, submitted, approved, or is waiting on him. An
    approval waiting with no email recorded still counts: what it holds
    would no longer be what was built.

    An AMEND is not here, because nothing records one as a state:
    approvals-drain cancels the token he answered and keeps his words on it
    as `feedback` (approval.AMENDING is never written). His words are carried
    into the rebuild instead (amend_feedback)."""
    if srow is not None:
        if verdicts.parse(srow.verdict or "")[0] == "applied":
            return "column A Applied"
        status = (srow.cell("Application Status") or "").strip()
        if status and status != sheet_mod.DEFAULTS_BY_NAME["Application Status"]:
            return "application status"
    if (d.get("application_state") or "").strip():
        return "applied on record"
    mine = tokens.get(posting_key(d) or d.get("job_id") or "", []) \
        + tokens.get(d.get("job_id") or "", [])
    from .apply import approval
    if any(t.get("opened_at") for t in mine):
        return "form opened"
    states = {t.get("state") for t in mine}
    for state, why in ((approval.SUBMITTED, "submitted"), (approval.APPROVED, "approved")):
        if state in states:
            return why
    waiting = [t for t in mine if t.get("state") == approval.AWAITING]
    if waiting:
        return ("awaiting, email sent" if any(t.get("sent_at") for t in waiting)
                else "awaiting")
    return None


def amend_feedback(d: dict, tokens: dict[str, list[dict]]) -> str:
    """His newest AMEND instructions for this posting, or "". approvals-drain
    cancels the token he answered, keeps his words on it as feedback and
    rebuilds from them. A rebuild for his reasons that left them out would
    replace the package he asked for with one he did not, and nothing would
    say so; with them, it is the package he asked for with his reasons in it.
    The newest only: each amend rebuilds from its own words alone, so the
    newest is what the package on his row was written from."""
    # Only a token filed under this record, or under a twin record of this
    # same posting (posting_key): his words for another posting at the same
    # company are about another letter.
    jid, key = d.get("job_id") or "", posting_key(d)
    mine = tokens.get(key or jid, []) + tokens.get(jid, [])
    said = [t for t in mine if (t.get("feedback") or "").strip()
            and (t.get("job_id") == jid or (key and t.get("_posting") == key))]
    if not said:
        return ""
    newest = max(said, key=lambda t: (t.get("decided_at") or "", t.get("sent_at") or ""))
    return newest["feedback"].strip()


# A package he has opened and edited is his. amend.py exists because he edits
# the letter in Docs before he sends it, and a rebuild writes new documents
# over the links on his row: the email that follows would carry hunter's new
# letter without his paragraph, and amendment_step, comparing the new Doc
# with the new baseline, would never see his edit at all. So what each linked
# document says now is read against what hunter wrote into it at the build
# (hunter_row_state), and a package whose documents differ, cannot be read,
# or have no record to compare with is left alone: unknown is not unedited
# (CLAUDE.md 1.3).
DOCS_EDITED = "he edited the documents"
DOCS_UNREAD = "his documents could not be read"
DOCS_NO_BASELINE = "no record of what hunter wrote into the documents"


def _safe_doc_reader(cfg: Config):
    """read_doc(url) -> the document's text, or "" when it cannot be read.
    One that reads nothing when Docs cannot be reached at all, which every
    caller here takes as unreadable, never as unedited."""
    try:
        docs = DocBuild(GoogleOAuth(cfg).access_token)
    except Exception:
        return lambda url: ""
    return lambda url: doc_plain_text(docs, url)


def doc_baselines(cfg: Config, job_ids) -> dict[str, dict]:
    """job_id -> what hunter wrote into its documents (hunter_row_state), read
    by id: the whole table carries every letter and CV it ever wrote."""
    out: dict[str, dict] = {}
    ids = sorted({j for j in job_ids if j})
    for i in range(0, len(ids), 40):
        for x in db_get(cfg, amend.STATE_TABLE, {
                "select": "job_id,letter_text,cv_text",
                "job_id": f"in.({','.join(ids[i:i + 40])})", "limit": "100"}):
            out[x["job_id"]] = x
    return out


def his_docs(srow: SheetRow | None, d: dict) -> dict[str, str]:
    """The documents this package is, keyed by where hunter stored the text
    it wrote into each: the links on his row first, since that is what he
    opens, and the record's where the row has none."""
    out: dict[str, str] = {}
    for stored, cell, link in (("letter_text", "Cover Letter Doc", "package_letter_url"),
                               ("cv_text", "CV Doc", "package_cv_url")):
        url = (amend._url_of(srow.cell(cell)) if srow is not None else "") \
            or (d.get(link) or "")
        if url:
            out[stored] = url
    return out


def docs_edited(read_doc, docs: dict[str, str], baseline: dict | None) -> str | None:
    """Why these documents may now hold his own words, or None when every one
    reads back as what hunter wrote into it."""
    for stored, url in docs.items():
        was = (baseline or {}).get(stored)
        if not was:
            return DOCS_NO_BASELINE
        now = read_doc(url)
        if not now:
            return DOCS_UNREAD
        if amend.norm(now) != amend.norm(was):
            return DOCS_EDITED
    return None


def keep_his_edits(cfg: Config, sheet: Sheet, canon: Canon, job_id: str) -> tuple[int, str]:
    """(documents of this package he has edited, a note when they could not
    be checked), each edit recorded in hunter_amendments (amend.record) with
    what hunter wrote and what the document says now. Called before an AMEND
    supersedes the package, so his hand edits outlive it. The documents are
    the links on his row, and the record's where the row has none. The note
    names a class only: the log is public."""
    if not job_id:
        return 0, ""
    try:
        recs = db_get(cfg, "hunter_seen_roles", {
            "select": "job_id,company,title,url,job_url,package_cv_url,package_letter_url",
            "job_id": f"eq.{job_id}", "limit": "1"})
        if not recs:
            return 0, ""
        d = recs[0]
        try:
            srow = _sheet_row_for(sheet, canon, d)
        except Exception:
            srow = None
        docs = his_docs(srow, d)
        pairs = amend.edit_pairs(_safe_doc_reader(cfg), docs,
                                 doc_baselines(cfg, [job_id]).get(job_id),
                                 job_id=job_id, company=d.get("company") or "",
                                 title=d.get("title") or "")
        amend.record(cfg, pairs)
    except Exception as e:
        return 0, (f"his edits to the documents could not be checked "
                   f"({e.__class__.__name__})")
    return len(pairs), ""


def acted_on_now(cfg: Config, row: dict) -> str | None:
    """acted_on for one chosen role, read again from the database: its own
    record and every approval, mapped to postings through the records of the
    roles they were filed under. Two small reads and one by id, not the
    whole of hunter_seen_roles."""
    from .apply import approval
    mine = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,url,job_url,application_state",
        "job_id": f"eq.{row['job_id']}", "limit": "1"})
    d = dict(row, **(mine[0] if mine else {}))
    toks = db_get(cfg, approval.TABLE, {"select": TOKEN_SELECT, "limit": ALL_ROWS})
    ids = sorted({t["job_id"] for t in toks if t.get("job_id")} - {d["job_id"]})
    others = []
    for i in range(0, len(ids), 40):
        others += db_get(cfg, "hunter_seen_roles", {
            "select": "job_id,url,job_url",
            "job_id": f"in.({','.join(ids[i:i + 40])})", "limit": "100"})
    return acted_on(None, d, _tokens_by_posting(cfg, others + [d], toks))


def rebuild_targets(cfg: Config, sheet: Sheet, canon: Canon, *, job_id: str = "",
                    report: dict | None = None, detail: list | None = None,
                    read_doc=None) -> list[dict]:
    """Packages to rebuild with his reasons in them, best first.

    A role is one when his row is one he wants an application for
    (router.wants: a score of 7 or more, or Yes with K at 7 or more or
    blank) and carries something of his to write from (a score or a
    reaction), its package is built, the record does not say the package
    carries his view or was held unchanged (unless job_id names it), he has
    not acted on it (acted_on), and its documents read back as what hunter
    wrote into them (docs_edited: an edit of his, an unreadable document or
    no record to compare with leaves it alone). One per posting. A target
    carries his newest AMEND instructions, if any (amend_feedback). The same roles approvals --wanted prepares,
    so every application it sends carries his reasons where he gave any:
    a Yes with only a reaction in M is one, which is wider than "scored 7
    or more", because his words that day were "include all the rationales
    that I've put in the new column". `report`
    gets counts only, by reason; report["view_column"] is False when the
    record cannot say which packages carry his view, and then every built
    package is counted as not carrying it, which is true of every package
    built before this change. `detail`, when given, gets one line per role
    left alone, for a private listing; nothing that prints reads it."""
    from collections import Counter
    from .package import his_view as his_view_mod
    from .ratings import whole_score
    rep = report if report is not None else {}
    skips: Counter = Counter()

    def skip(why: str, srow=None, d=None) -> None:
        # detail is for a private listing; nothing that prints reads it.
        skips[why] += 1
        if detail is not None:
            detail.append({"job_id": (d or {}).get("job_id"),
                           "company": srow.company if srow else (d or {}).get("company"),
                           "title": srow.role if srow else (d or {}).get("title"),
                           "row": srow.row_number if srow else None, "reason": why})
    rows = sheet.read_pipeline(canon.sheet_headers)
    asked = []
    for r in rows:
        if his_view_mod.from_row(r).empty:
            continue   # nothing of his to write it from
        k = whole_score(r.cell(sheet_mod.KRISH_SCORE))
        kind = verdicts.parse(r.verdict or "")[0]
        if kind == "applied":
            skip("column A Applied", r)
            continue
        trig = router.wants(r)
        if trig is None:
            # Declined, Duplicate, or a Yes he has since scored below 7. A
            # row he scored below 7 and never said Yes to is not counted:
            # it was never a role he wanted.
            if kind in ("go", "rejection") or (k is not None and k >= router.SCORE_BUILDS):
                skip("not wanted now", r)
            continue
        asked.append((r, trig, k))
    known, view_column = _known_with_view(cfg)
    rep["view_column"] = view_column
    by_row = router.pair_records([a[0] for a in asked], known)
    tokens = _tokens_by_posting(cfg, known)
    score_rule = scores_build_in_canon(canon)
    picked = []
    # One job id, or several separated by commas: the packages a read-back
    # found wanting are rebuilt in one run, not one dispatch each.
    named = {j.strip() for j in (job_id or "").split(",") if j.strip()}
    for r, trig, k in asked:
        d = by_row.get(id(r))
        if d is None:
            skip("no record", r)
            continue
        if named and d.get("job_id") not in named:
            continue
        if d.get("status") == "duplicate":
            skip("duplicate record", r, d)
            continue
        if (r.cell("Package Status").strip() == sheet_mod.PKG_DEAD
                or d.get("status") == "dead" or d.get("package_status") == "dead"):
            skip("posting dead", r, d)
            continue
        status = d.get("package_status") or ""
        if status != "built" and not (status == "blocked" and (
                d.get("rejection_reason") or "").startswith(REBUILD_REFUSED)):
            # A package whose last rebuild did not land is blocked and may be
            # tried again; held_mark below keeps it from being paid for twice
            # on the same inputs.
            skip("not built yet", r, d)
            continue
        carried = (d.get(his_view_mod.FIELD) or "") if view_column else ""
        if carried and not named:
            # Named with --job-id, it is rebuilt anyway: the way to bring a
            # reaction he has since rewritten into a package already built
            # from an earlier one, or to try a held one again. Never past the
            # acted-on and edited-documents checks below.
            if not carried.startswith(his_view_mod.HELD):
                skip("carries his view", r, d)
                continue
            if carried == his_view_mod.held_mark(his_view_mod.from_row(r).fingerprint,
                                                 d.get("package_built_at"), r.jd_url):
                skip("held before, nothing changed since", r, d)
                continue
        why = acted_on(r, d, tokens)
        if why:
            skip(why, r, d)
            continue
        if trig != router.YES_TRIGGER and not score_rule:
            skip("waiting on canon 9.13", r, d)
            continue
        picked.append(dict(d, build_trigger=trig, krish_score=k,
                           sheet_row=r.row_number, sheet_link=r.jd_url or "",
                           his_docs=his_docs(r, d),
                           amend_feedback=amend_feedback(d, tokens)))
    picked.sort(key=router.build_order)
    out, seen = [], set()
    for d in picked:
        key = posting_key(d) or d["job_id"]
        if key in seen or d["job_id"] in seen:
            skip("same posting", None, d)
            continue
        seen.update({key, d["job_id"]})
        out.append(d)
    if out:
        # Last, on the few left: two document reads each.
        if read_doc is None:
            read_doc = _safe_doc_reader(cfg)
        base = doc_baselines(cfg, [d["job_id"] for d in out])
        kept = []
        for d in out:
            why = docs_edited(read_doc, d["his_docs"], base.get(d["job_id"]))
            if why:
                skip(why, None, d)
                continue
            kept.append(d)
        out = kept
    rep.update({"asked": len(asked), "targets": len(out), "skips": dict(skips),
                "with_amend": sum(1 for d in out if d.get("amend_feedback"))})
    return out


# A by-hand delivery and a refusal for a missing answer mint no token
# (send_apply_by_hand: there is nothing to approve), so nothing told the next
# --wanted batch that they had been dealt with. They sorted to the head of
# every batch: he was mailed the same "Apply by hand" again each time, a
# refused form had its essays drafted and paid for again, and with --max at
# or below their number no batch ever reached the rest. Each is marked
# against its posting and the package it was for, in hunter_alerts beside
# the other once-only marks (kind TROUBLE, under a prefix of its own, as the
# ratings sync's marks are), and is not prepared again until the package
# changes. A refusal's mark carries his answer bank too (bank_hash), so the
# answer he adds re-opens it. approvals --job-id reads no mark: naming one
# is asking again.
PREPARED_MARK = "prepared:"
BY_HAND, REFUSED_BLOCKING = "by_hand", "refused_blocking"
# An essay that did not draft (the month at its ceiling, a model error, the
# gate refusing every try) is hunter's own failure and may come through
# later, so it is never marked for good: marked for a day (ESSAY_WAIT_HOURS),
# so the next batch moves on to other postings rather than paying for the
# same refusal at the head of every batch, and tried again after it.
ESSAY_NOT_DRAFTED = "essay not drafted, tried again after a day"
ESSAY_WAIT_HOURS = 24
# Counted by Prepare (approvals --job-id) for the note he reads on its card.
TAKEN_BACK, NOT_BUILT = "his score took it back", "no built package"
# A form that asks for his phone or address, for a role with no header base
# (package/contact.py). Never marked: it goes as soon as the role has one.
NO_BASE = "no header base"
# The two outcomes a send can have that are neither sent nor not sent: Gmail
# took no email (send_email's stdout fallback, gmail.send not granted), or
# the email went and its approval was not recorded, so its open link and a
# reply to it find no row. Each is counted apart, so the note he reads says
# what happened (CLAUDE.md 1).
NOT_SENT, SENT_UNRECORDED = "email not sent", "sent, approval not recorded"
PREPARED_SKIPS = {BY_HAND: "sent to apply by hand before, package unchanged",
                  REFUSED_BLOCKING: "refused before for a missing answer, package and "
                                    "answer bank unchanged",
                  ESSAY_NOT_DRAFTED: "an essay did not draft within the last day"}


def bank_hash(bank) -> str:
    """16 hex characters of his answer bank as cmd_approvals reads it
    (_answer_bank): any answer he adds, changes or clears changes it."""
    import dataclasses
    import hashlib
    data = dataclasses.asdict(bank) if dataclasses.is_dataclass(bank) else bank
    blob = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def prepared_fingerprint(row: dict, outcome: str = BY_HAND, *, bank: str = "") -> str:
    """The mark's key: the posting and the package it was for, and for a
    refusal the answer bank it was refused on (bank, a bank_hash), for an
    essay the outcome, so the two never share a key with a by-hand mark."""
    import hashlib
    parts = [posting_key(row) or row.get("job_id") or "", row.get("package_built_at") or ""]
    if outcome == REFUSED_BLOCKING:
        parts.append("bank:" + bank)
    elif outcome == ESSAY_NOT_DRAFTED:
        parts.append(outcome)
    blob = json.dumps(parts)
    return PREPARED_MARK + hashlib.sha1(blob.encode()).hexdigest()[:16]


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def prepared_marks(cfg: Config) -> dict[str, dict]:
    """fingerprint -> the mark's detail (outcome, job id, and for an essay
    when it failed), for every posting a --wanted batch has dealt with and
    minted no token for."""
    out: dict[str, dict] = {}
    for x in db_get(cfg, alerts.TABLE, {"select": "fingerprint,detail",
                                        "kind": f"eq.{alerts.TROUBLE}",
                                        "fingerprint": f"like.{PREPARED_MARK}*",
                                        "limit": ALL_ROWS}):
        detail = x.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        out[x.get("fingerprint") or ""] = detail if isinstance(detail, dict) else {}
    return out


def essay_wait_open(detail: dict | None) -> bool:
    """An essay mark still inside its day. One whose time cannot be read is
    over: unknown is not "failed today", and the posting is tried again."""
    try:
        at = datetime.datetime.fromisoformat(str((detail or {}).get("at") or "")
                                             .replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    return _now_utc() - at < datetime.timedelta(hours=ESSAY_WAIT_HOURS)


def mark_prepared(cfg: Config, row: dict, outcome: str, *, bank=None) -> str:
    """Record that this posting's package was dealt with and read it back.
    "" when it landed, otherwise the line to print. Never his words, and no
    company: the detail is the outcome and the job id. bank is the answer
    bank a refusal was made on. An essay mark is written over the one
    before, so its day runs from the latest failure."""
    fp = prepared_fingerprint(row, outcome, bank=bank_hash(bank) if bank is not None else "")
    detail = {"outcome": outcome, "job_id": row.get("job_id")}
    essay = outcome == ESSAY_NOT_DRAFTED
    if essay:
        detail["at"] = _now_utc().isoformat()
    try:
        db_insert(cfg, alerts.TABLE, [{"kind": alerts.TROUBLE, "fingerprint": fp,
                                       "detail": detail}],
                  on_conflict="kind,fingerprint", merge=essay, ignore_duplicates=not essay)
        back = db_get(cfg, alerts.TABLE, {"select": "id,detail", "kind": f"eq.{alerts.TROUBLE}",
                                          "fingerprint": f"eq.{fp}", "limit": "1"})
    except Exception as e:
        return (f"    not recorded as dealt with ({e.__class__.__name__}); the next "
                f"batch prepares it again")
    got = (back[0].get("detail") if back else None) or {}
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            got = {}
    if not back or (essay and (got or {}).get("at") != detail["at"]):
        return "    not recorded as dealt with (did not read back); the next batch prepares it again"
    return ""


def wanted_for_approval(cfg: Config, sheet: Sheet, canon: Canon, *,
                        report: dict | None = None, detail: list | None = None) -> list[dict]:
    """Every built package for a role he wants (router.wants), one per
    posting, best first, as the full records cmd_approvals sends from.

    Skipped, and counted: a posting with an approval already waiting,
    approved or submitted (live_postings), one already sent to apply by hand
    or refused for a missing answer for this same package (prepared_marks),
    a dead posting, a role he has applied for, a package not built yet, a
    Yes he called a Duplicate, and, among the built packages, any whose row
    has left Pipeline. `report` gets counts only; `detail` as in
    rebuild_targets."""
    from collections import Counter
    from .ratings import whole_score
    rep = report if report is not None else {}
    skips: Counter = Counter()

    def skip(why: str, srow=None, d=None) -> None:
        # detail is for a private listing; nothing that prints reads it.
        skips[why] += 1
        if detail is not None:
            detail.append({"job_id": (d or {}).get("job_id"),
                           "company": srow.company if srow else (d or {}).get("company"),
                           "title": srow.role if srow else (d or {}).get("title"),
                           "row": srow.row_number if srow else None, "reason": why})
    rows = sheet.read_pipeline(canon.sheet_headers)
    asked = []
    for r in rows:
        trig = router.wants(r)
        if trig is None:
            if router.build_trigger(r) == router.YES_TRIGGER:
                skip("Yes, called a duplicate" if sheet_mod.says_duplicate(
                    r.cell(sheet_mod.KRISH_REACTION)) else "Yes, scored below 7 or unreadable", r)
            continue
        asked.append((r, trig))
    known, _ = _known_with_view(cfg)
    on_sheet = {sheet_identity(r.company, r.role) for r in rows}
    for d in known:
        if ((d.get("package_status") or "") == "built" and d.get("status") != "duplicate"
                and sheet_identity(d.get("company"), d.get("title")) not in on_sheet):
            skip("built, off Pipeline", None, d)
    by_row = router.pair_records([a[0] for a in asked], known)
    live = live_postings(cfg)
    # And by job id, for a record with no link, which has no posting key, and
    # with an amend in flight, which approvals-drain is about to send again.
    from .apply import approval
    in_play = (approval.AWAITING, approval.APPROVED, approval.AMENDING, approval.SUBMITTED)
    live_ids = {t.get("job_id") for t in db_get(cfg, approval.TABLE, {
        "select": "job_id", "state": f"in.({','.join(in_play)})", "limit": ALL_ROWS})}
    handled = prepared_marks(cfg)
    # His answer bank, read only when a refusal is on record: a refusal's
    # mark holds the bank it was refused on. Unreadable, no refusal matches,
    # and cmd_approvals, which reads the bank before anything is paid for,
    # stops on it.
    refusals = any(m.get("outcome") == REFUSED_BLOCKING for m in handled.values())
    memo: list[str] = []

    def bank_now() -> str:
        if not memo:
            try:
                memo.append(bank_hash(_answer_bank(sheet)))
            except Exception:
                memo.append("unreadable")
        return memo[0]
    picked = []
    for r, trig in asked:
        d = by_row.get(id(r))
        if d is None:
            skip("no record", r)
            continue
        if d.get("status") == "duplicate":
            skip("duplicate record", r, d)
            continue
        if (r.cell("Package Status").strip() == sheet_mod.PKG_DEAD
                or d.get("status") == "dead" or d.get("package_status") == "dead"):
            skip("posting dead", r, d)
            continue
        if (d.get("package_status") or "") != "built":
            skip("not built yet", r, d)
            continue
        status = (r.cell("Application Status") or "").strip()
        if (status and status != sheet_mod.DEFAULTS_BY_NAME["Application Status"]) \
                or (d.get("application_state") or "").strip():
            skip("applied", r, d)
            continue
        key = posting_key(d) or d["job_id"]
        if key in live or d["job_id"] in live_ids:
            skip("approval already waiting or approved", r, d)
            continue
        # By hand, for this package. A mark under the same key with another
        # outcome is a refusal made before the answer bank was in its key,
        # and is not read as one made on today's bank.
        if (handled.get(prepared_fingerprint(d)) or {}).get("outcome") == BY_HAND:
            skip(PREPARED_SKIPS[BY_HAND], r, d)
            continue
        if refusals and prepared_fingerprint(
                d, REFUSED_BLOCKING, bank=bank_now()) in handled:
            skip(PREPARED_SKIPS[REFUSED_BLOCKING], r, d)
            continue
        if essay_wait_open(handled.get(prepared_fingerprint(d, ESSAY_NOT_DRAFTED))):
            skip(PREPARED_SKIPS[ESSAY_NOT_DRAFTED], r, d)
            continue
        picked.append(dict(d, build_trigger=trig,
                           krish_score=whole_score(r.cell(sheet_mod.KRISH_SCORE)),
                           sheet_row=r.row_number, sheet_link=r.jd_url or ""))
    picked.sort(key=router.build_order)
    chosen, seen = [], set()
    for d in picked:
        key = posting_key(d) or d["job_id"]
        if key in seen or d["job_id"] in seen:
            skip("same posting", None, d)
            continue
        seen.update({key, d["job_id"]})
        chosen.append(d)
    full = {}
    ids = [d["job_id"] for d in chosen]
    for i in range(0, len(ids), 40):
        part = ids[i:i + 40]
        for x in db_get(cfg, "hunter_seen_roles", {
                "select": "*", "job_id": f"in.({','.join(part)})", "limit": "100"}):
            full[x["job_id"]] = x
    out = []
    for d in chosen:
        x = full.get(d["job_id"])
        if x is None:
            skip("record unreadable", None, d)
            continue
        if not style_mod.stamped(x):
            # Read off the whole record, which carries the stamp once the
            # column exists and reads unstamped before it (package/style.py).
            skip(style_mod.OLD_STYLE, None, d)
            continue
        out.append(dict(x, build_trigger=d["build_trigger"], krish_score=d["krish_score"],
                        sheet_row=d["sheet_row"], sheet_link=d["sheet_link"]))
    rep.update({"asked": len(asked), "chosen": len(out), "skips": dict(skips)})
    return out


def _counts_line(prefix: str, counts: dict) -> str:
    parts = [f"{n} {why}" for why, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
             if n]
    return f"{prefix}: " + (", ".join(parts) if parts else "none")


def rebuild_lines(rep: dict, *, apply: bool, cap: int) -> list[str]:
    """Counts, never a role."""
    lines = [f"rebuild: {rep.get('asked', 0)} row(s) he wants an application for that "
             f"carry his score or his reaction; {rep.get('targets', 0)} package(s) built "
             f"without his reasons and not acted on, to rebuild"
             + (f" (at most {cap} this run)" if cap else ""),
             _counts_line("rebuild: left alone", rep.get("skips") or {})]
    if rep.get("with_amend"):
        lines.append(f"rebuild: {rep['with_amend']} of them carry his AMEND instructions, "
                     f"and are rebuilt with them")
    if not rep.get("view_column", True):
        lines.append("rebuild: hunter_seen_roles has no package_his_view column yet "
                     "(migration cc6), so no package is known to carry his reasons; "
                     "counted as none, which is true of every package built before "
                     "this change")
    if not apply:
        lines.append("rebuild: dry run, nothing built. add --apply to rebuild")
    return lines


def cmd_rebuild_packages(apply: bool = False, max_builds: int = 0, job_id: str = "") -> int:
    """Rebuild, from his reasons, the packages built before they reached the
    prompt, for the roles he wants and has not acted on (rebuild_targets).

    Dry run unless --apply. --max N caps the rebuilds (each is a paid tailor
    call); --job-id J rebuilds that one role, if it is a target. Every
    rebuild goes through build_one, which re-reads his row before it pays,
    makes new documents (the old ones stay in Drive, as an amend leaves
    them), writes the new links over the old on his row and reads them back,
    and records that the package carries his view. A rebuild whose prose did
    not come through, or whose letter failed verification, leaves the earlier
    package's documents where they are and takes it out of every send path
    (rebuild_did_not_land), and one the gate held or verification failed is
    recorded as tried, so the next run does not pay for it again unchanged.
    Nothing is built while builds wait for his house style (package/style.py).
    A package whose documents he has edited is never rebuilt, read again
    just before it pays, and his AMEND instructions go into the rebuild.
    Counts only on stdout."""
    from collections import Counter
    from . import spend
    from .ats import discover as disc
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rep: dict = {}
    read_doc = _safe_doc_reader(cfg)
    try:
        targets = rebuild_targets(cfg, sheet, canon, job_id=job_id, report=rep,
                                  read_doc=read_doc)
    except Exception as e:
        # The class only. A Supabase error's message carries the request URL,
        # and these requests name every target by job id: roles he scored 7
        # or more or said Yes to, in a log that is public.
        print(f"rebuild: the targets could not be read ({e.__class__.__name__}); "
              f"nothing built")
        return 1
    for line in rebuild_lines(rep, apply=apply, cap=max_builds):
        print(line)
    if not apply or not targets:
        return 0
    if style_mod.builds_paused():
        print(f"rebuild: waiting, {style_mod.PAUSED}; nothing built")
        return 0
    if not rep.get("view_column", True):
        # A rebuild that cannot be recorded as carrying his reasons would be
        # chosen again by the next run, and paid for again, every run.
        print("rebuild: REFUSING to build until package_his_view exists; apply "
              "migration cc6 first")
        return 1
    stop = spend.over_budget(cfg)
    if stop:
        print(f"rebuild: waiting, {stop}")
        return 0
    may, lines = preflight.gate(preflight.run(cfg))
    if not may:
        print("\n".join(lines))
        print("rebuild: preflight failed; nothing built")
        return 1
    # Neither raises: a declines read that fails degrades to none, and the
    # cache is read off cfg.
    declines, _notes = stored_company_declines(cfg)
    cache = disc.load_cache(cfg)
    out: Counter = Counter()
    detail: list[str] = []   # private: names roles, never printed
    todo = targets[:max_builds] if max_builds else targets
    for row in todo:
        # Read again just before paying: Prepare in Control Center, his
        # extension opening the form, or his editing the letter may have
        # happened since the selection, which can be an hour behind by now.
        try:
            # His row too: a Yes he scored 3, or a score he lowered to 6,
            # while the run worked through the targets above it is no longer
            # a role he wants an application for (router.wants), and a
            # package written from that view would be mailed to him later.
            mine = _sheet_row_for(sheet, canon, row)
            if mine is None or router.wants(mine) is None:
                out["no longer asked for since it was chosen"] += 1
                continue
            if acted_on_now(cfg, row):
                out["acted on since it was chosen"] += 1
                continue
            if docs_edited(read_doc, row.get("his_docs") or {},
                           doc_baselines(cfg, [row["job_id"]]).get(row["job_id"])):
                out["documents changed since it was chosen"] += 1
                continue
        except Exception:
            out["not checked again, so not rebuilt"] += 1
            continue
        before = len(detail)
        try:
            # His AMEND instructions, where he gave any, go in with his
            # reasons: they wrote the package this one replaces.
            ok = build_one(cfg, canon, sheet, row, detail, cache=cache,
                           company_declines=declines, rebuild=True,
                           feedback=row.get("amend_feedback") or "")
        except Exception as e:
            out["failed"] += 1
            detail.append(f"BUILD FAILED {row['job_id']}: {e.__class__.__name__}")
            detail.extend(record_build_error(cfg, row["job_id"], e, sheet=sheet,
                                             canon=canon, row=row))
            continue
        said = detail[before:]
        jid = row["job_id"]
        if ok:
            out["rebuilt"] += 1
            if any("reasons this package carries" in x for x in said):
                out["rebuilt, his view not recorded"] += 1
        elif any(x.startswith(f"HELD {jid}") for x in said):
            out["held, the package before is not sent"] += 1
        elif any(x.startswith(f"REBUILD FAILED {jid}") for x in said):
            out["failed verification, the package before is not sent"] += 1
        elif any(x.startswith(f"DEAD {jid}") for x in said):
            out["posting gone"] += 1
        elif any(x.startswith(f"SKIPPED {jid}") or x.startswith("SKIPPED:") for x in said):
            out["row changed before its turn"] += 1
        else:
            out["refused at a gate"] += 1
    try:
        disc.save_cache(cfg, cache)
    except Exception:
        pass
    print(_counts_line("rebuild: done", dict(out)))
    for line in spend.summary():
        print(line)
    try:
        line = spend.flush(cfg)
        if line:
            print(line)
    except Exception as e:
        print(f"spend: ledger not written ({e.__class__.__name__})")
    return 1 if out["failed"] else 0


def cmd_approvals_wanted(apply: bool = False, max_n: int = 0) -> int:
    """The Apply email, with its one-click link to the filled form, for every
    built package of a role he wants: one per posting, through cmd_approvals
    one role at a time, so a form that raises never takes the rest with it,
    and the token each one mints is read back by the next as the posting's
    live approval. Dry run unless --apply, and a dry run prepares nothing (a
    fill plan drafts essays, which are paid). Counts only on stdout: what
    cmd_approvals prints per role is the application itself."""
    import contextlib
    import io
    from collections import Counter
    from . import spend
    cfg, canon = build_context()
    # Before anything is read or paid for: each form drafts its essays, and
    # with the month at its ceiling every one would come back empty and the
    # whole batch would be refused for answers it only lacked for the day.
    stop = spend.over_budget(cfg)
    if stop:
        print(f"prepare: waiting, {stop}")
        return 0
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    rep: dict = {}
    try:
        chosen = wanted_for_approval(cfg, sheet, canon, report=rep)
    except Exception as e:
        print(f"prepare: the roles he wants could not be read ({e.__class__.__name__}); "
              f"nothing prepared")
        return 1
    todo = chosen[:max_n] if max_n else chosen
    print(f"prepare: {rep.get('asked', 0)} row(s) he wants an application for; "
          f"{len(chosen)} built and not yet waiting on him, one per posting"
          + (f"; {len(todo)} this run" if max_n else ""))
    print(_counts_line("prepare: left alone", rep.get("skips") or {}))
    if not apply:
        print("prepare: dry run, nothing prepared and no email sent. add --apply")
        return 0
    tally: Counter = Counter()
    errors: Counter = Counter()
    for row in todo:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_approvals(apply=True, chosen=[row], tally=tally)
        except Exception as e:
            tally["failed"] += 1
            errors[e.__class__.__name__] += 1
    print(_counts_line("prepare: outcome", dict(tally)))
    if errors:
        print(_counts_line("prepare: errors", dict(errors)))
    # What the essays cost, in counts and dollars (CLAUDE.md 3e).
    for line in spend.summary():
        print(line)
    try:
        line = spend.flush(cfg)
        if line:
            print(line)
    except Exception as e:
        print(f"spend: ledger not written ({e.__class__.__name__})")
    return 1 if tally["failed"] else 0


def cmd_approvals_drain(apply: bool = False) -> int:
    """Read Krish's replies and act on each one. Dry run by default.

    Three outcomes, and every reply gets exactly one:
      approve  the token moves to approved. He opens the filled form from
               Control Center or the email, his extension fills it, and he
               presses Submit himself. Nothing in hunter presses it
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
    errors = 0
    for r in replies:
        # Every call keyed by the token, by class only on failure: a
        # Supabase error names its request, token=eq.<token>, and a failed
        # insert echoes the row. This log is public. The next reply is still
        # read, and the drain exits 1.
        try:
            row = approval.get_row(cfg, r["token"]) or {}
            action, detail = inbox.classify(r, row, cfg)
            # The job, never the token (half of the open link to his filled form),
            # and his own words on an approve or an amend only as a length: this
            # log is public. A skip or a reject says why in hunter's words.
            print(f"{action.upper():<8} {row.get('job_id') or 'no approval row'}")
            if detail and action in ("approve", "amend"):
                print(f"         his reply carries {len(detail)} characters of feedback")
            elif detail and action == "skip":
                print(f"         {detail[:300]}")
            elif action == "reject":
                # Not the sender's address: only that it was not acted on.
                print("         not acted on: not from him, or no APPROVE and no feedback")
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
                # Approved. He opens the filled form from Control Center or the
                # email and his extension fills it. Nothing here presses anything.
                print("         approved; open it from Control Center or the email")
                continue

            # amend. His own edits to the documents first: the rebuild links
            # new documents on his row, and the ones he edited would never be
            # read again. Counts only: the pair is his letter.
            kept, note = keep_his_edits(cfg, sheet, canon, row.get("job_id") or "")
            if kept:
                print(f"         his edits to {kept} document(s) kept before anything "
                      f"replaces them")
            if note:
                print("         " + note)
            # The old token dies first: if the rebuild fails, the worst case is
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
            if style_mod.builds_paused():
                # Not built in the layout he rewrote, and not sent: his words
                # stay on the token he answered (amend_feedback), and the
                # rebuild in his house style carries them (package/style.py).
                acted += 1
                print(f"         SUPERSEDED; his words are kept and the package is "
                      f"rebuilt from them once it can be ({style_mod.PAUSED})")
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
        except Exception as e:
            errors += 1
            print(f"         this reply stopped at an error ({e.__class__.__name__})")
            continue

    if apply:
        print(f"\n{acted} reply(ies) acted on")
    if errors:
        print(f"{errors} reply(ies) stopped at an error")
    report_stalled_approvals(cfg, apply=apply)
    return 1 if errors else 0


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
            # Never the token: it is half of the open link, and this prints
            # in the drain's public log.
            stuck.append(f"{r['state']} since {when[:19] or 'unknown'}: "
                         f"{r.get('company')} {r.get('role')}")
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
    if not hits:
        # The text did not match: a hand edit to Business or Role, or the sheet's
        # abbreviation of a long title. Fall back to the one thing that is the
        # same on both sides, the posting itself, and still demand exactly one.
        try:
            known = db_get(cfg, "hunter_seen_roles", {
                "select": "job_id,job_url,url", "job_id": f"eq.{job_id}", "limit": "1"})
        except Exception:
            known = []
        key = posting_key(known[0]) if known else ""
        if key:
            hits = [r for r in rows if r.jd_url and posting_key({"url": r.jd_url}) == key]
            if len(hits) == 1:
                note.append(f"matched {company} / {role} to sheet row "
                            f"{hits[0].row_number} by its posting URL")
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
    if confirmation.startswith(SAID_SO):
        # His own word, from column A or the extension's button. True, and worth
        # recording, but it is not the form speaking and must never read as if it
        # were.
        proof = ("<p style='border-left:3px solid #6e7781;background:#f6f8fa;"
                 "padding:10px 12px;margin:0 0 14px'><strong>Recorded on your "
                 "word.</strong> The form did not confirm it to hunter.</p>")
    elif confirmation:
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
         ("Recorded on your word. The form did not confirm it to hunter."
          if confirmation.startswith(SAID_SO) else
          f"The form acknowledged it: {confirmation}" if confirmation else
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


def send_apply_by_hand(cfg: Config, *, company: str, role: str, jd_url: str,
                       cv_url: str, letter_url: str, essays: dict,
                       why: str, attachments=None) -> dict:
    """The application, for a form hunter cannot fill. Returns what
    notify.send_email returned, so the caller counts a delivery only when a
    channel took it.

    Krish, 2026-09-24, about BOI: "BOI I can apply myself (still give me the
    application via email)". Before this the run refused and sent nothing, so
    a package that had been written, gated and built never reached him at all.

    Deliberately NOT an approval. No token is minted and no ledger row is
    written, because there is nothing to approve: replying APPROVE would move a
    token into a submit path that has no form to drive and would sit there
    waiting for a browser that can never open it. This is a delivery. What
    cmd_approvals records of it is a once-only mark in hunter_alerts
    (mark_prepared), so a --wanted batch does not send it again for the same
    package.

    It never claims the form was filled. That claim is what this repository
    exists to avoid, and it is the exact shape of the "Filled all 17 fields"
    banner over an untouched section.
    """
    from . import notify
    bits = [f"<p>{html_mod.escape(role)} at <b>{html_mod.escape(company)}</b></p>",
            f"<p><b>Hunter cannot fill this form.</b> {html_mod.escape(why)}</p>",
            "<p>The documents are written and attached. Apply in your own "
            "browser and paste the answers below.</p>"]
    if jd_url:
        bits.append(f'<p><a href="{html_mod.escape(jd_url)}">The posting</a></p>')
    links = [(n, u) for n, u in (("CV", cv_url), ("Letter", letter_url)) if u]
    if links:
        bits.append("<p>" + " &middot; ".join(
            f'<a href="{html_mod.escape(u)}">{n}</a>' for n, u in links) + "</p>")
    for q, a in (essays or {}).items():
        bits.append(f"<p><b>{html_mod.escape(str(q))}</b></p>"
                    f"<p>{html_mod.escape(str(a)).replace(chr(10), '<br>')}</p>")
    # The email used to name a button that never existed. Column A is the one
    # place he already marks things, and settle reads it every hour.
    bits.append("<p>Nothing was submitted and no approval is pending for this "
                "one. Once you have sent it, type <b>Applied</b> in column A of "
                "its Pipeline row. Hunter moves it within the hour.</p>")
    return notify.send_email(
        cfg, f"Apply by hand: {company} {role}",
        "".join(bits), to=notify.mailbox(cfg),
        text=f"[hunter-outbound]\nApply by hand: {role} at {company}. "
             f"{why} Documents attached.",
        attachments=attachments or None)


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
        said = (f"<span style='color:#6e7781'>{_esc(confirmation)}; the form "
                f"did not confirm it</span>"
                if confirmation.startswith(SAID_SO) else
                f"<span style='color:#1a7f37'>{_esc(confirmation)}</span>"
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
    if "sequoia" in s:
        return "sequoia portfolio"
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
        if verdicts.is_accept(v):
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
            print("no approval row for that token")
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
        note.append(f"could not mark the role row dead: {e.__class__.__name__}")

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
            note.append(f"sheet row {rn} not retired: {e.__class__.__name__}")
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
    plan: list[tuple[dict, str]] = []
    for jid in named:
        row = by_id.get(jid)
        if row is None:
            # Never a silent skip: a typo leaves a role he asked to be rid of
            # sitting there while the run reports success.
            print(f"no role matched job id {jid!r}")
            return 1
        key = board_key(row.get("url") or row.get("job_url") or "")
        if key:
            # One liveness rule, shared with the approvals refusal branch.
            gone = posting_is_gone(row)
            if gone is None:
                print(f"REFUSING {jid}: could not reach the board to check "
                      f"whether the posting is live")
                return 1
            if not gone:
                print(f"REFUSING {jid}: the posting is still live on "
                      f"{key[0]}/{key[1]}. Retiring it would be a lie on "
                      f"the sheet.")
                return 1
            why = f"posting gone from {key[0]}/{key[1]}"
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


# Column A's undecided value, the dropdown's first entry (canon 9.13).
UNDECIDED = "New"


def cmd_unretire(job_ids: str, apply: bool = False) -> int:
    """Undo a retirement that should never have happened.

    The inverse of cmd_retire, and it exists because on 2026-09-24 hunter
    retired Versapay and Rembrand, two live Lever postings Krish had marked
    Yes, on the strength of a message that said in its own words "the posting
    is readable but its form is not". The rule that did that is fixed. This
    puts the rows back, and it will be needed again the next time something
    is retired wrongly.

    It REFUSES a posting the board says is really gone, which is the same
    evidence check cmd_retire makes, pointing the other way. Un-retiring a
    genuinely dead role would put a row he cannot act on back in front of him.

    His verdict is restored from krish_verdict, which survives a retirement
    because it was written from column A and retire only rewrites the sheet.
    With no verdict of his on record, column A goes back to New, never to
    Yes: a role his score built has no krish_verdict by design (a score is
    not a Yes), and writing Yes for it would be a verdict he never gave,
    which the learning loop would then read as his. His K is untouched and
    still asks for the package.
    """
    cfg, canon = build_context()
    sheet = Sheet(GoogleServiceAccount(cfg).access_token)
    named = [j.strip() for j in (job_ids or "").split(",") if j.strip()]
    if not named:
        print("nothing named; pass --job-id a,b")
        return 2
    rows = db_get(cfg, "hunter_seen_roles",
                  {"select": "job_id,company,title,url,job_url,status,"
                             "package_status,krish_verdict,package_cv_url,"
                             "package_letter_url", "limit": ALL_ROWS})
    by_id = {r.get("job_id"): r for r in rows}
    srows = sheet.read_pipeline(canon.sheet_headers)
    plan: list[tuple[dict, int, str, str]] = []
    for jid in named:
        row = by_id.get(jid)
        if row is None:
            print(f"no role matched job id {jid!r}")
            return 1
        gone = posting_is_gone(row)
        if gone:
            print(f"REFUSING {jid}: the board says this posting really has "
                  f"gone. Putting it back would hand him a row he cannot act on.")
            return 1
        want = ((row.get("company") or "").strip().lower(),
                (row.get("title") or "").strip().lower())
        hits = [r for r in srows
                if ((r.company or "").strip().lower(),
                    (r.role or "").strip().lower()) == want]
        if len(hits) != 1:
            print(f"REFUSING {jid}: {len(hits)} Pipeline rows match; "
                  f"this needs a person")
            return 1
        verdict = (row.get("krish_verdict") or "").strip() or UNDECIDED
        built = bool(row.get("package_cv_url") and row.get("package_letter_url"))
        plan.append((row, hits[0].row_number, verdict, "built" if built else "none"))
    print(f"{len(plan)} row(s) to put back:")
    for row, rn, verdict, pkg in plan:
        print(f"  row {rn:>3}  {row['company'][:22]:24} "
              f"{row['title'][:34]:36} -> {verdict} ({pkg})")
    if not apply:
        print("\ndry run. add --apply to put these back")
        return 0
    for row, rn, verdict, pkg in plan:
        sheet.set_verdicts({rn: verdict})
        if pkg == "built":
            sheet.update_package_status(rn, sheet_mod.PKG_BUILT_DIRECT)
        db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                 {"status": "staging", "package_status": pkg,
                  "rejection_reason": None, "rejection_code": None})
        print(f"  row {rn} restored to {verdict}, package_status {pkg}")
    # Read it back rather than trusting the writes above.
    fresh = {r.row_number: r for r in sheet.read_pipeline(canon.sheet_headers)}
    bad = [rn for _, rn, verdict, _ in plan
           if (fresh.get(rn).verdict if fresh.get(rn) else "") != verdict]
    if bad:
        print(f"rows {bad} did not read back with the verdict written")
        return 1
    print(f"put back {len(plan)} row(s), all read back correct")
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
    # Open means the SHEET has not caught up, whatever the database says. The
    # database used to be marked Applied by a run that never wrote the sheet,
    # and a check on the database alone then skipped the role for ever: the
    # submission was recorded and his Pipeline row went on reading Yes.
    on_pipeline = {((p.company or "").strip().lower(), (p.role or "").strip().lower())
                   for p in sheet.read_pipeline(canon.sheet_headers)
                   if (p.cell("Application Status") or "").strip() != sheet_mod.APPLIED_STATUS}

    def sheet_behind(r: dict) -> bool:
        return ((r.get("company") or "").strip().lower(),
                (r.get("role") or "").strip().lower()) in on_pipeline

    open_ones = [r for r in rows
                 if (state.get(r["job_id"], {}).get("application_state") or "")
                 not in APPLIED_STATES or sheet_behind(r)]

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
    # AWAITING is the normal state of an application he filled through the
    # extension link without replying APPROVE, which is how he applies. Reading
    # only APPROVED and SUBMITTED meant an employer's receipt for the most common
    # case was never even looked at.
    for state in (approval.AWAITING, approval.APPROVED, approval.SUBMITTED):
        open_rows += db_get(cfg, approval.TABLE,
                            {"select": "token,company,role,state,job_id,submitted_at,sent_at",
                             "state": f"eq.{state}", "limit": "100"})
    # An AWAITING row is closed only on strong evidence: the receipt arrived
    # after the approval was sent, and it is the only open application at that
    # company, so one receipt cannot close the wrong role.
    per_company: dict[str, int] = {}
    for r in open_rows:
        if r["state"] != approval.SUBMITTED:
            k = (r.get("company") or "").strip().lower()
            per_company[k] = per_company.get(k, 0) + 1

    def awaiting_ok(msg: dict, row: dict) -> bool:
        if row["state"] != approval.AWAITING:
            return True
        if per_company.get((row.get("company") or "").strip().lower(), 0) != 1:
            return False
        try:
            from email.utils import parsedate_to_datetime
            got = parsedate_to_datetime(msg.get("date") or "")
            sent = datetime.datetime.fromisoformat(
                str(row.get("sent_at") or "").replace("Z", "+00:00"))
            return got >= sent
        except (TypeError, ValueError):
            return False
    if not open_rows:
        print("no applications waiting on a receipt")
        return 0
    who = confirmations.mailbox(cfg)
    if who.get("error"):
        print(f"mailbox: {who['error']}")
    else:
        # Not the address: this prints in a public Actions log.
        print(f"reading {'his mailbox' if who['address'] else 'an unnamed mailbox'} "
              f"({who['total']} messages in it)")
    try:
        messages = confirmations.fetch(cfg)
    except confirmations.ConfirmError as e:
        print(str(e))
        return 1
    pairs = [(m, r) for m, r in confirmations.match(messages, open_rows)
             if awaiting_ok(m, r)]
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
    from .package import contact as contact_mod
    bases, _note = contact_mod.load_bases(cfg)
    result = simulate.run(bank, to=to,
                          contact=bases.get(contact_mod.base_for("New York, NY")[0]))

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


def cmd_letter_master(source: str, apply: bool = False, check: bool = False) -> int:
    """Letter master v5 from the letter he edited (package/masters.py). Dry
    run unless --apply: reads his letter and plans the slots. With --apply it
    copies the letter into the letters folder, brings the margins in until
    his own words fit one page, writes the slots, and reads the copy back.
    Counts and the new document's id only: the log is public, and the letter
    is his. Nothing switches to it here; config and canon 9.9 change together,
    on his yes, when the composer that fills it lands."""
    from .apply.merge import page_count
    from .package import house, masters
    cfg = load()
    db = DocBuild(GoogleOAuth(cfg).access_token)
    if check:
        # Read only: a master made before, read back again.
        problems = masters.template_problems(house.snapshot(db.get(source)))
        print(f"letter-master: checked, {len(problems)} problem(s)")
        for line in problems:
            print(f"letter-master: PROBLEM {line}")
        return 1 if problems else 0
    try:
        out = masters.build_v5(db, source, folder_id=config_mod.LETTER_FOLDER_ID,
                               page_count=page_count, apply=apply)
    except masters.MasterError as e:
        print(f"letter-master: refused, {e}")
        return 1
    except Exception as e:
        print(f"letter-master: failed ({e.__class__.__name__})")
        return 1
    print(f"letter-master: {out['slots']} slots, {out['bullets']} bullets"
          + ("" if apply else " (dry run, pass --apply to make it)"))
    if out["made"]:
        step = out["margin_step"]
        print(f"letter-master: made {out['made']}"
              + (f", margins in by {step:g}pt" if step is not None else ""))
    for line in out["problems"]:
        print(f"letter-master: PROBLEM {line}")
    return 1 if out["problems"] else 0


def cmd_cv_master(letter: str, apply: bool = False) -> int:
    """The CV master in his house style (package/masters.py): a copy of the
    CV master, every run in Arial with its weight kept, his letter's header,
    link row and banner, a 9pt body, the headings at their own sizes, fitted
    to two pages. Dry run unless --apply. letter is the letter whose link row
    and banner are copied. Counts and the new id only."""
    from .apply.merge import page_count
    from .package import masters
    cfg = load()
    db = DocBuild(GoogleOAuth(cfg).access_token)
    try:
        out = masters.build_cv_master(db, config_mod.CV_MASTER_ID, letter,
                                      folder_id=config_mod.CV_FOLDER_ID,
                                      page_count=page_count, apply=apply)
    except masters.MasterError as e:
        print(f"cv-master: refused, {e}")
        return 1
    except Exception as e:
        print(f"cv-master: failed ({e.__class__.__name__})")
        return 1
    print(f"cv-master: {out['runs']} runs to restyle, {out['headings']} headings"
          + ("" if apply else " (dry run, pass --apply to make it)"))
    if out["made"]:
        step = out["margin_step"]
        print(f"cv-master: made {out['made']}"
              + (f", margins in by {step:g}pt" if step is not None else "")
              + ("" if out["banner_kept"] else ", banner left off to fit two pages"))
    for line in out["problems"]:
        print(f"cv-master: PROBLEM {line}")
    return 1 if out["problems"] else 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "recon"
    if cmd == "cv-master":
        i = argv.index("--letter") if "--letter" in argv else -1
        src = argv[i + 1] if 0 <= i < len(argv) - 1 else ""
        if not src:
            print("usage: python -m hunter.run cv-master --letter <doc_id> [--apply]")
            return 2
        return cmd_cv_master(src, apply="--apply" in argv)
    if cmd == "letter-master":
        i = argv.index("--from") if "--from" in argv else -1
        src = argv[i + 1] if 0 <= i < len(argv) - 1 else ""
        if "--check" in argv:
            j = argv.index("--check")
            src = argv[j + 1] if j < len(argv) - 1 else ""
            if src:
                return cmd_letter_master(src, check=True)
        if not src:
            print("usage: python -m hunter.run letter-master --from <doc_id> [--apply] "
                  "| --check <doc_id>")
            return 2
        return cmd_letter_master(src, apply="--apply" in argv)
    if cmd == "run":
        return cmd_run()
    if cmd == "process":
        mx = int(argv[argv.index("--max") + 1]) if "--max" in argv else 0
        return cmd_process(max_packages=mx, retry_dead="--retry-dead" in argv)
    if cmd == "reconcile":
        return cmd_reconcile()
    if cmd == "build":
        if len(argv) < 3 or argv[1] != "--job-id":
            print("usage: python -m hunter.run build --job-id <job_id>")
            return 2
        return cmd_build(argv[2])
    if cmd == "recon":
        return cmd_recon()
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
    if cmd == "spend":
        days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 31
        return cmd_spend(days)
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
    if cmd == "unretire":
        jids = ""
        if "--job-id" in argv:
            i = argv.index("--job-id")
            if i + 1 < len(argv):
                jids = argv[i + 1]
        if not jids:
            print("usage: python -m hunter.run unretire --job-id a,b [--apply]")
            return 2
        return cmd_unretire(jids, apply="--apply" in argv)
    if cmd == "bank-seed":
        return cmd_bank_seed(apply="--apply" in argv)
    if cmd == "gtm-seed":
        return cmd_gtm_seed(apply="--apply" in argv)
    def _flag(name: str, default: str = "") -> str:
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv):
                return argv[i + 1]
        return default

    if cmd == "settle":
        return cmd_settle(apply="--apply" in argv)
    if cmd == "score-builds":
        mx = _flag("--max")
        return cmd_score_builds(apply="--apply" in argv, max_builds=int(mx) if mx else None)
    if cmd == "rebuild-packages":
        mx = _flag("--max")
        return cmd_rebuild_packages(apply="--apply" in argv, max_builds=int(mx) if mx else 0,
                                    job_id=_flag("--job-id"))
    if cmd == "blind-set":
        return cmd_blind_set(refresh="--refresh" in argv)
    if cmd == "blind-eval":
        return cmd_blind_eval(apply="--apply" in argv)
    if cmd == "judge-eval":
        t, n = _flag("--threshold"), _flag("--replicas")
        return cmd_judge_eval(dev="--dev" in argv, threshold=int(t) if t else None,
                              replicas=int(n) if n else None)
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
        return cmd_approvals_drain(apply="--apply" in argv)
    if cmd == "approvals":
        if "--wanted" in argv:
            mx = _flag("--max")
            return cmd_approvals_wanted(apply="--apply" in argv, max_n=int(mx) if mx else 0)
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
    if cmd == "archive":
        return cmd_archive(apply="--apply" in argv)
    if cmd == "leave":
        return cmd_leave(apply="--apply" in argv, out=_flag("--out"))
    if cmd == "set-dropdown":
        return cmd_set_dropdown()
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
        print(f"{'batch':12} {'staged':>7} {'ruled':>6} {'yes':>4} {'rate':>6} "
              f"{'scored':>7} {'7+':>5} {'AI rho':>7}")
        for b in batches:
            rate = f"{round(100 * b.rate)}%" if b.rate is not None else "-"
            share = (f"{round(100 * b.share_7_plus)}%" if b.share_7_plus is not None
                     else "-")
            rho = f"{b.spearman_ai:+.2f}" if b.spearman_ai is not None else "-"
            print(f"{b.key:12} {b.staged:7} {b.verdicted:6} {b.accepted:4} {rate:>6} "
                  f"{b.scored:7} {share:>5} {rho:>7}")
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
            note = batchstats.save(cfg, batches)
            print(f"\nwrote {len(batches)} batch(es) to {batchstats.TABLE}"
                  + (f"; {note}" if note else ""))
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
        # The Pipeline and Applied headers are checked here because this step
        # runs even when every other step has failed on them. Opening the
        # sheet is inside a try: a broken service account key would otherwise
        # take down the database checks too, at the moment every sheet step
        # is failing the same way.
        sheet, opened = None, None
        try:
            sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        except Exception as e:
            opened = e
        observed: dict = {}
        problems = alerts.trouble_checks(cfg, sheet=sheet, observed=observed,
                                         sheet_error=opened, check_canon=True)
        for p in problems:
            print(f"  {p['kind']}: {p['detail']}")
        if not problems:
            if "--send" in argv:
                alerts.forget_cleared(cfg, observed)
            if observed["unread"]:
                print(f"  nothing wrong in what was read, but NOT read: "
                      f"{', '.join(observed['unread'])}")
            else:
                print("  nothing wrong; the loop is turning")
            return 0
        if "--send" in argv:
            print(alerts.send_trouble(cfg, force="--force" in argv, sheet=sheet,
                                      sheet_error=opened, check_canon=True))
        else:
            print("\ndry run. add --send to mail these.")
        return 0
    if cmd == "review-email":
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        rows = sheet.read_pipeline(canon.sheet_headers)
        skipped = alerts.passed_over(cfg)
        state = alerts.batch_state(rows, skipped)
        subject, _html, text = alerts.review_email(rows, state["to_review"],
                                                   state=dict(state, skipped=skipped))
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
    if cmd == "actions":
        # Buttons he pressed in Control Center (actions.py). Dry run unless --apply.
        from . import actions
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        for line in actions.apply_queued(cfg, sheet, canon.sheet_headers,
                                         apply="--apply" in argv):
            print(line)
        return 0
    if cmd == "build-preview":
        # The scratch preview: --job-id (repeatable), --queue to ask the
        # writer, --apply --folder <id> to build. Links and codes only.
        jobs = [argv[i + 1] for i, a in enumerate(argv) if a == "--job-id" and i + 1 < len(argv)]
        folder = argv[argv.index("--folder") + 1] if "--folder" in argv else ""
        if "--apply" in argv and not folder and "--preview-folder" not in argv:
            print("build-preview --apply needs --folder <drive folder id> or --preview-folder")
            return 2
        return cmd_build_preview(jobs, queue="--queue" in argv, folder=folder,
                                 apply="--apply" in argv,
                                 preview_folder="--preview-folder" in argv)
    if cmd == "facts":
        # A personal line a letter needs (facts.py): queued asks are mailed to
        # him, and his replies are written to canon 9.14 as he wrote them,
        # confirmed back to him, and taken back on UNDO. Counts only here:
        # his words never reach this log. Dry run unless --apply.
        from . import canonwrite, facts, notify
        from .apply import inbox
        cfg, _canon = build_context()
        if "--apply" not in argv:
            open_rows = db_get(cfg, facts.TABLE, {"select": "status"})
            by: dict[str, int] = {}
            for r in open_rows:
                by[r["status"]] = by.get(r["status"], 0) + 1
            print(f"facts (dry run): {dict(sorted(by.items()))}; 9.14 in canon: "
                  f"{bool(canonwrite.section_body(cfg))}")
            return 0
        sent = facts.ask(cfg, notify.send_email)
        try:
            got = facts.read(cfg, fetch=facts.fetch_replies, send=notify.send_email).counts
        except inbox.InboxError as exc:
            print(f"facts: replies not read ({type(exc).__name__})")
            got = {}
        print(f"facts: asked {sent['asked']}, not sent {sent['not_sent']}; "
              f"replies {dict((k, v) for k, v in got.items() if v)}")
        return 0
    if cmd == "drafts":
        # Use the subscription routine's checked answers; fall back to the API
        # for requests it left unanswered (drafts.py). Dry run unless --apply.
        from . import drafts
        cfg, canon = build_context()
        sheet = Sheet(GoogleServiceAccount(cfg).access_token)
        if "--requeue" in argv:
            # The repair for a batch whose cases never queued: ask again for
            # every role presented in the last N days.
            days = int(argv[argv.index("--requeue") + 1])
            for line in drafts.requeue_presented(cfg, sheet, canon, days=days):
                print(line)
            return 0
        for line in drafts.settle(cfg, apply="--apply" in argv, sheet=sheet,
                                  canon_headers=canon.sheet_headers):
            print(line)
        return 0
    if cmd == "door-coverage":
        # docs/DOOR_IN.md phase 1: of his top companies, how many have a named
        # leader AND a warm route. Reads only; spends nothing; writes nothing
        # but the optional JSON file.
        import json as _json
        from . import doorin
        data = (doorin.load_dump(argv[argv.index("--from-dump") + 1])
                if "--from-dump" in argv else doorin.load(load()))
        rep = doorin.report(data)
        print(_json.dumps({"summary": rep["summary"], "wedge": rep["wedge"]}, indent=2))
        if "--json" in argv:
            with open(argv[argv.index("--json") + 1], "w", encoding="utf-8") as f:
                _json.dump(rep, f, indent=1)
        return 0
    if cmd == "door-cards":
        # docs/DOOR_IN.md phase 2: a leader card per door, landing in Control
        # Center's pilot_deals as `listed`. Dry run unless --apply. Nothing is
        # ever sent. --observe drafts the opening line (a model call each,
        # capped, costed in the ledger). --skip names companies to hold back.
        from . import doorin
        skip = set()
        if "--skip" in argv:
            skip = {s.strip().lower() for s in argv[argv.index("--skip") + 1].split(",") if s.strip()}
        if "--from-dump" in argv:
            if "--apply" in argv or "--observe" in argv:
                print("--from-dump is a dry run: it has no database to write to or model to ask")
                return 2
            cfg, data = None, doorin.load_dump(argv[argv.index("--from-dump") + 1])
        else:
            cfg = load()
            data = doorin.load(cfg)
        observe_with = None
        if "--observe" in argv:
            from . import drafts
            observe_with = (drafts.queue_observation
                            if cfg is not None and drafts.writer(cfg) == "routine"
                            else doorin.observe)
        res = doorin.run_cards(cfg, data, skip=skip,
                               observe_with=observe_with,
                               apply="--apply" in argv,
                               refresh_only="--refresh-only" in argv)
        for c in res["cards"]:
            seat = f" | now: {c.trigger.what}" if c.trigger else ""
            print(f"{c.company}: {c.leader.title} [{c.ask_kind}, {c.door}] via {c.route.kind}{seat}")
            if c.observation:
                print(f"    opener: {c.observation}")
            elif c.observation_note:
                print(f"    no opener: {c.observation_note}")
        for h in res["held"]:
            print(f"held: {h['company']}: {h['reason']}")
        landed = res["landed"]
        if landed is not None:
            print(f"pilot_deals: {len(landed['write'])} new, {len(landed['update'])} refreshed, "
                  f"{len(landed['theirs'])} left alone (his), "
                  f"{len(landed['not_in_control_center'])} not in Control Center, "
                  f"{len(landed['blocked_words'])} blocked for a banned word"
                  + ("" if "--apply" in argv else " (dry run, nothing written)"))
            if landed.get("new_not_written"):
                print("new doors not written (--refresh-only): " + ", ".join(landed["new_not_written"]))
            if landed["read_back"] is not None:
                rb = landed["read_back"]
                print(f"read back: {rb['found']} of {rb['expected']} rows are listed and tagged")
                if rb["found"] != rb["expected"]:
                    return 1
        return 0
    print(f"unknown command {cmd!r}; commands: process [--max N] [--retry-dead], "
          f"run, reconcile, "
          f"build --job-id X, recon, learn [--apply], drain [--id X], verify, "
          f"bank-check, audit-forms [--apply] [--limit N], simulate [--send], bank-seed [--apply], "
          "settle [--apply], score-builds [--apply] [--max N], "
          "rebuild-packages [--apply] [--max N] [--job-id X], "
          "gtm-seed [--apply], approvals [--apply] [--job-id X], "
          "approvals --wanted [--apply] [--max N], "
          "approvals-drain [--apply], actions [--apply], drafts [--apply], "
          "newsletter [--apply] [--limit N], "
          "bridges [--ingest DIR], prune-sheet [--apply], regate, archive, "
          "layout, invariants [--apply], amendments, preflight, "
          "watchdog [--send], review-email [--send], doctor [--offline], "
          "door-coverage [--from-dump DIR] [--json PATH], "
          "door-cards [--from-dump DIR] [--skip a,b] [--observe] [--apply] [--refresh-only]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
