"""Routing: which roles get packages, and what Package Status says.

Verdict vocabulary observed in hunter_seen_roles and the sheet: go, y, yes
(case-insensitive) mean build; applied means Krish already applied and no
build is queued; anything else in column A is his free-text rejection and is
quoted verbatim, never paraphrased. Since 2026-10-13 a Krish Score of 7 or
more in K builds too, on a row column A leaves undecided (build_trigger).
"""
from __future__ import annotations

import hashlib
import json
import re

from . import verdicts
from .config import ALL_ROWS, Config, db_get
from .sheet import (KRISH_REACTION, KRISH_SCORE, PKG_BUILT_BRIDGE, PKG_BUILT_DIRECT,
                    PKG_DEAD, says_duplicate)

GO_WORDS = {"go", "y", "yes", "build"}

# The retired incumbent wrote sentences into warm_path_person ("None
# identified with a current connection", "None. No connections at Mutiny").
# A placeholder is not a person.
NO_PERSON = re.compile(r"^\s*(none|n/a|nobody|no\b|not found|unknown|-)", re.I)


def classify_verdict(text: str) -> str:
    """'go' | 'applied' | 'rejection' | 'none'. The vocabulary lives in
    verdicts.py, which also carries the reason code; this keeps the older
    call sites that only want the verdict."""
    return verdicts.parse(text)[0]


def is_warm_path(role_row: dict) -> bool:
    person = (role_row.get("warm_path_person") or "").strip()
    return bool(person) and not NO_PERSON.match(person)


# What built a package, written to hunter_seen_roles.package_trigger, which
# Control Center reads to put Prepare on the role (api/hunter/roles.ts).
#
#   "column A Yes"       his Yes in column A
#   "Krish Score 8"      his score: 7 or more builds, his ruling 2026-10-13
#   "Krish Score 8, not built [<attempt>]"
#                        his score asked and the build was refused or found
#                        the posting gone; <attempt> fingerprints what it saw,
#                        and nothing is tried again until that changes
#   "Krish Score 8, not built, one more try [<attempt>]"
#                        a letter or CV that failed verification once: a
#                        second tailor call can come out differently, so it
#                        gets one more try on the same inputs before it waits
#   "withdrawn: ..."     his score went below 7 after the build, so what was
#                        waiting on him for it was cancelled (run.py)
#
# Hunter never writes column A or krish_verdict because of a score. Canon
# 9.13: every taste verdict is his. The trigger is where the reason lives.
YES_TRIGGER = "column A Yes"
SCORE_TRIGGER = "Krish Score"
WITHDRAWN = "withdrawn"
SCORE_BUILDS = 7
YES, SCORE = "yes", "score"

ONE_MORE_TRY = "one more try"

_SCORE_BUILT = re.compile(r"^Krish Score (\d+)$")
_ATTEMPT = re.compile(r"^Krish Score (\d+), not built \[([0-9a-f]{16})\]$")
_RETRY = re.compile(r"^Krish Score (\d+), not built, one more try \[([0-9a-f]{16})\]$")


def score_trigger(score: int) -> str:
    return f"{SCORE_TRIGGER} {score}"


def not_built(trigger: str, attempt: str, *, final: bool = True) -> str:
    if final:
        return f"{trigger}, not built [{attempt}]"
    return f"{trigger}, not built, {ONE_MORE_TRY} [{attempt}]"


def built_on_score(trigger: str | None) -> int | None:
    """The score a package was built on, or None when his score did not build
    it (a Yes, a refused attempt, a withdrawn one, or nothing recorded)."""
    m = _SCORE_BUILT.match((trigger or "").strip())
    return int(m.group(1)) if m else None


def attempt_of(trigger: str | None) -> str | None:
    """The fingerprint of a score build that was refused for good on those
    inputs, or None. A refusal with one more try to come is not one."""
    m = _ATTEMPT.match((trigger or "").strip())
    return m.group(2) if m else None


def retry_of(trigger: str | None) -> str | None:
    """The fingerprint of a score build whose package failed verification
    once and has one more try on the same inputs, or None."""
    m = _RETRY.match((trigger or "").strip())
    return m.group(2) if m else None


def attempt_fingerprint(score: int | None, sheet_url: str | None, db_url: str | None,
                        masters: str) -> str:
    """What a refused score build saw: his score, the link on his row, the
    link hunter holds, and the masters it would have tailored from. A build
    refused at a gate, or a posting found gone, fails the same way on the
    same inputs, and each try costs a board read or a paid tailor call, so
    it is tried again only when one of these has changed. Verification is
    the exception that gets one more try (not_built final=False): the
    letter is a model's output, and a second call can come in at one page
    where the first did not. A build that raised is never fingerprinted at
    all (run.score_build_step): a 503 is not an answer about the inputs."""
    from .run import norm_url
    blob = json.dumps([score, norm_url(sheet_url) or "", norm_url(db_url) or "", masters])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build_trigger(row) -> str | None:
    """Why this sheet row should have a package, or None.

    Column A Yes builds, as it always has. A row column A leaves undecided
    builds when K is a whole number from 7 to 10 and M does not call it a
    duplicate. Column A Applied or Declined always wins over K: he has
    already acted, or said no in the column hunter learns from.

    K goes through ratings.whole_score, the parser his record is kept with,
    so "7.5" or "8/10" builds nothing: it is recorded as unreadable and is
    never guessed into a number. M reading Duplicate stops a build at any
    score. The record (ratings.kind_of) files a 6 or more beside the word as
    his score, because a score is what it learns from; a build is paid for,
    and the twin he did not call a duplicate is the row that builds.
    """
    from .ratings import whole_score
    kind = classify_verdict(row.verdict or "")
    if kind == "go":
        return YES_TRIGGER
    if kind != "none":
        return None
    score = whole_score(row.cell(KRISH_SCORE))
    if score is None or score < SCORE_BUILDS:
        return None
    if says_duplicate(row.cell(KRISH_REACTION)):
        return None
    return score_trigger(score)


def build_order(d: dict) -> tuple:
    """His Yes first, then his score from highest, then the oldest on his
    sheet, because the oldest postings close first. Not hunter's rule score:
    measured against his own scores it ranks at Spearman -0.19, so under a
    cap it would build the roles he likes least first."""
    k = d.get("krish_score")
    shown = d.get("presented_at") or ""
    return (d.get("build_trigger") != YES_TRIGGER, k is None, -(k or 0), not shown, shown)


def select_for_build(cfg: Config, sheet=None, headers=None, *,
                     cap: int | None = None, retry_dead: bool = False,
                     triggers: tuple = (YES,), masters=None,
                     report: dict | None = None, score_wins: bool = False) -> list[dict]:
    """Rows to build packages for.

    Authority is the sheet as it reads now, not a DB field. Krish's ruling
    2026-09-02: he will set every verdict himself once he trusts the system,
    and twelve rows still carry a 'go' the retired incumbent wrote weeks ago.
    Building from those would produce packages he never asked for. When the
    sheet is unavailable the function returns nothing rather than falling
    back to the DB, because a wrong build is worse than a missed one.

    triggers says which reasons to build count (build_trigger): YES, his
    column A, or SCORE, his Krish Score of 7 or more. Process builds YES
    only, uncapped. SCORE is the hourly score pass (run.cmd_score_builds),
    capped, so the backlog of rows he has scored never lands inside one
    batch run, and it needs `masters`, a callable giving the fingerprint of
    the master documents, or None when they cannot be read.

    cap None reads hunter_max_packages_per_run (the packages button); cap 0
    means every eligible row (process). A Yes row whose Package Status
    already says the posting is dead is skipped unless retry_dead, so a dead
    role is not re-fetched on every run. A score row has its fingerprint for
    that instead: one marked dead with no attempt recorded (before this rule,
    or by the gh_jid misreading) is checked once, which costs a board read
    and no tailor call, and is then recorded like any other refusal, tried
    again once its score, its link or the masters have changed. `report`,
    when given, is filled with counts only.

    score_wins: canon 9.13 carries his ruling that his score wins over an
    older Yes (leave.in_canon). A Yes row he has scored 1 to 5, or answered
    with only a reaction, is then not built (leave.yes_overridden): it is on
    its way off Pipeline, and the packages button pressed before the hourly
    pass moved it paid for a build and opened an application his score had
    already declined. Counted in report["overridden"], never named.

    Every row returned carries build_trigger, krish_score, the sheet row it
    was chosen from (sheet_row, sheet_link: build_one builds onto that row
    and no other, because two postings of one title in two countries are
    two rows) and, for a score row, attempt (attempt_fingerprint) and
    attempt_masters, which build_one records.
    """
    rep = report if report is not None else {}
    rep.update({"candidates": 0, "eligible": 0, "waiting": [],
                "unpaired": 0, "masters_unread": False, "overridden": 0})
    if sheet is None or headers is None:
        return []
    if cap is None:
        cap = int(cfg.optional("hunter_max_packages_per_run", "5"))
    from .ratings import whole_score
    want = set(triggers)
    # Read the masters once a call at most, and only if a score row needs it:
    # each read is two Docs requests.
    memo: list = []

    def masters_once():
        if not memo:
            memo.append(masters() if masters else None)
        return memo[0]
    candidates = []
    for r in sheet.read_pipeline(headers):
        trig = build_trigger(r)
        if trig is None:
            continue
        kind = YES if trig == YES_TRIGGER else SCORE
        if kind not in want:
            continue
        if r.package_urls.get("cv") and r.package_urls.get("letter"):
            continue  # links already on the sheet
        dead = r.cell("Package Status").strip() == PKG_DEAD
        if dead and not retry_dead and kind == YES:
            continue
        if score_wins and kind == YES:
            from .leave import yes_overridden
            if yes_overridden(r):
                rep["overridden"] += 1
                continue
        candidates.append((r, trig, kind, dead))
    rep["candidates"] = len(candidates)
    if not candidates:
        return []
    from .run import match_rows
    if want == {SCORE} and masters and _all_waiting(cfg, candidates, masters_once, rep):
        return []
    known = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,comp,location,"
                  "warm_path_person,warm_path_tier,package_status,krish_verdict,"
                  "rejection_reason,status,presented_at,package_trigger",
        "limit": ALL_ROWS})
    pairs, _, _, ambiguous = match_rows([c[0] for c in candidates], list(known))
    by_row = {id(s): d for s, d in pairs}
    # The incumbent recorded some postings twice under two job_ids with one
    # URL, so the matcher calls them ambiguous and nothing was ever built for
    # ElevenLabs GM UK, Cloudflare Chief of Staff or Harvey AMER. The same
    # ATS posting is the same posting; take the row with the most standing.
    for srow, _n in ambiguous:
        d = same_posting(srow, known)
        if d:
            by_row[id(srow)] = d
    picked = []
    for srow, trig, kind, _dead in candidates:
        d = by_row.get(id(srow))
        if d is None:
            rep["unpaired"] += 1
            continue
        if (d.get("package_status") or "none") == "built":
            continue
        item = dict(d, build_trigger=trig, krish_score=whole_score(srow.cell(KRISH_SCORE)),
                    sheet_row=srow.row_number, sheet_link=srow.jd_url or "")
        if kind == SCORE:
            m = masters_once()
            if m is None:
                # No fingerprint means no way to tell a changed attempt from
                # a repeat, and the build reads the same masters it could not.
                rep["masters_unread"] = True
                continue
            seen = attempt_fingerprint(item["krish_score"], srow.jd_url,
                                       d.get("url") or d.get("job_url"), m)
            if attempt_of(d.get("package_trigger")) == seen:
                rep["waiting"].append(d["job_id"])
                continue
            item["attempt"], item["attempt_masters"] = seen, m
        picked.append(item)
    picked.sort(key=build_order)
    out, taken = [], set()
    for d in picked:
        # Two sheet rows can come to one record (a twin the matcher called
        # ambiguous); the best placed of them builds, once.
        if d["job_id"] in taken:
            continue
        taken.add(d["job_id"])
        out.append(d)
    rep["eligible"] = len(out)
    return out[:cap] if cap else out


def _all_waiting(cfg: Config, candidates: list, masters, rep: dict) -> bool:
    """True when every candidate is a refused score build that nothing has
    changed for, read from the few records that carry a trigger rather than
    from all of hunter_seen_roles (about 9,500 rows). Once the backlog has
    cleared, the rows left are postings found gone and builds refused, and
    the hourly pass would otherwise read every recorded role each hour to
    learn that again. Any doubt (no record, a different fingerprint, masters
    unread) is False, and the full read decides."""
    from .run import norm_url, sheet_identity
    try:
        marked = db_get(cfg, "hunter_seen_roles", {
            "select": "job_id,company,title,url,job_url,package_trigger",
            "package_trigger": "like.Krish Score*", "limit": ALL_ROWS})
    except Exception:
        return False
    m = masters()
    if m is None:
        return False
    from .ratings import whole_score
    waiting = []
    for srow, _trig, _kind, _dead in candidates:
        mine = norm_url(srow.jd_url)
        hit = [d for d in marked if mine and mine in {norm_url(d.get("url")),
                                                      norm_url(d.get("job_url"))}]
        hit = hit or [d for d in marked if sheet_identity(d.get("company") or "", d.get("title") or "")
                      == sheet_identity(srow.company, srow.role)]
        if len(hit) != 1:
            return False
        d = hit[0]
        seen = attempt_fingerprint(whole_score(srow.cell(KRISH_SCORE)), srow.jd_url,
                                   d.get("url") or d.get("job_url"), m)
        if attempt_of(d.get("package_trigger")) != seen:
            return False
        waiting.append(d["job_id"])
    rep["waiting"] = waiting
    return True


def same_posting(srow, known: list[dict]) -> dict | None:
    """The DB row that is this sheet row's posting, among twins sharing its
    ATS key. Standing decides: his verdict, then a package his score built,
    then a package, then presented. invariants._standing ranks the sheet's
    twins the same way."""
    from .run import ats_key
    key = ats_key(srow.jd_url)
    if not key:
        return None
    twins = [d for d in known if ats_key(d.get("url") or d.get("job_url")) == key
             and d.get("status") != "duplicate"]
    if not twins:
        return None

    def rank(d):
        return (bool((d.get("krish_verdict") or "").strip()),
                built_on_score(d.get("package_trigger")) is not None,
                (d.get("package_status") or "none") != "none",
                bool(d.get("presented_at")))
    return sorted(twins, key=rank, reverse=True)[0]


def route_status(role_row: dict) -> str:
    """Package Status on build: bridge first where a warm path exists."""
    if is_warm_path(role_row):
        return PKG_BUILT_BRIDGE
    return PKG_BUILT_DIRECT
