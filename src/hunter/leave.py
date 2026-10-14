"""What leaves Pipeline for the Applied tab, and what comes back.

Until 2026-10-13 one rule decided it: column A Applied or Declined leaves,
everything else stays. Krish then started ruling in K (Krish Score) and M
(Krish Reaction) instead of column A, and on 2026-10-13 ruled on the rows
that left Pipeline holding 45 of 88 rows he had already answered: "Move 1-5
and skipped blanks", and, where his score and an older column A Yes
disagree, "My score wins".

So a row leaves when, by his record (hunter_krish_ratings, the latest rating
of its role) and the row as it reads now:

  column A Applied or Declined                  as it always has
  column A New or blank and
    K a whole number 1 to 5                     he scored it low
    a reaction and no score                     he said no in words
    a blank he has scored past                  ratings.UNSCORED: a later batch
                                                scored, and a day gone since
    M reading Duplicate, K under 6              he called it a duplicate
  column A Yes and
    K a whole number 1 to 5, or a reaction      his score wins over the Yes
    that is not Duplicate, and no score

and nothing else leaves for its score. A Yes with K and M blank stays: he
has not scored it, and the Yes is still his ruling. A Yes he has only called
Duplicate stays too: the word says the row is a twin, not that the role is a
no, and canon 9.13 says so in those terms. K 6 or more stays. A K
hunter cannot read as a whole number ("7.5") never leaves: unknown is not
low, and a score is never guessed. A row whose package his score built stays
unless K falls to 5 or below, because the package is the answer his 7 or
more gave and a cleared cell is not a new one.

The gone rule overrides that hold (GONE, his choice 2026-10-14, "Move the
gone ones"): a row he scored, with column A undecided, whose Package
Status reads Posting dead leaves whatever built its package, because a
posting that cannot be applied to has no package worth keeping on
Pipeline. A Yes marked dead stays: his Yes is his to clear.

The role a row leaves is set aside (SET_ASIDE) only once no row of it is
left on Pipeline (run._pipeline_holds), and goes back to staging when one
is: two rows of one role part when he scores one 3 and has not reached the
other.

A row on Applied that he scores 7 or more comes back, because a score he
raises later has to be able to build. Only a row column A leaves undecided
or Yes: the Applied tab's Applied and Declined rows are his column A
rulings, and K beside them does not undo one.

What this module must never do:

- Move a row his record does not hold. The ruling is read off the record,
  not off the cell alone: a row leaves only when the latest rating of its
  role is exactly what the row says now, so what he wrote is recorded before
  the row goes, and a change he made since the last sync waits for the next.
- Write column A, or krish_verdict. Moving a row is not a verdict (canon
  9.13: every taste verdict is his). Column A goes to Applied as he left it.
- Say anything beside a company. Every line run.py prints from here is a
  count, because the Actions logs are public and a moved row is a row he
  scored.
"""
from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass, field, replace

from . import ratings, router, verdicts
from .amend import norm
from .sheet import (AI_SCORE, COLS, DATE_COLS, HIS_IDX, KRISH_REACTION, KRISH_SCORE, SheetRow,
                    as_text, PKG_DEAD)

# The words canon 9.13 carries from v11, which Krish approves before any row
# leaves for its score. Read from the live canon every pass, not from here.
CANON_RULE = "Rows Krish scored 1 to 5 in K also move to the Applied tab"

# Why a row leaves. Counted in the summary; never printed beside a role.
APPLIED = "applied"
DECLINED = "declined"
SCORED_LOW = "scored_low"
REACTION = "reaction_only"
SCORED_PAST = "blank_scored_past"
DUPLICATE = "duplicate"
YES_SCORED_LOW = "yes_scored_low"
YES_REACTION = "yes_reaction_only"
# His ruling 2026-10-14, asked whether roles he scored 7 or more whose
# postings are gone should leave Pipeline: "Move the gone ones". A row that
# can never be applied to is not a decision still waiting on him.
GONE = "posting_gone"
DECIDED_REASONS = (APPLIED, DECLINED)
YES_REASONS = (YES_SCORED_LOW, YES_REACTION)
SCORE_REASONS = (SCORED_LOW, REACTION, SCORED_PAST, DUPLICATE, GONE) + YES_REASONS
REASONS = DECIDED_REASONS + SCORE_REASONS

# 1 to 5 leaves (his ruling). 6 stays: it is neither the no he gave 1 to 5
# nor the yes 7 builds on.
LEAVES_AT_OR_BELOW = 5
COMES_BACK_AT = router.SCORE_BUILDS

# hunter_seen_roles.status for a role his score set aside. Counted as seen
# by staging dedupe (run.seen_identity counts every status but an
# unpresented unresolved or judge_pending) and reconcile reads it, so the
# role is never staged again or appended back; it is outside staging and
# presented, so Control Center's Roles to rule on drops it. Not "dropped",
# which reads as his decline, and not "duplicate" or "removed", which take
# a role out of reconcile altogether.
SET_ASIDE = "set_aside"
# The statuses a role on his list carries, and the only ones a move replaces.
# dead, blocked, dropped, duplicate and removed say more than set_aside does.
LIVE_STATUSES = ("staging", "presented", "unresolved", "judge_pending")
# What a role coming back is restored to: the status every staged role
# carries, which Roles to rule on, reconcile and the warm path pass read.
BACK_STATUS = "staging"


def in_canon(canon) -> bool:
    try:
        return CANON_RULE in canon.section_text("9.13")
    except Exception:
        return False


def undecided(column_a: str) -> bool:
    """Column A reads New or is blank: the two values he has not ruled with.
    Not verdicts.parse's "none", which also takes "Awaiting ..." text."""
    return (column_a or "").strip().lower() in ("", "new")


def is_yes(column_a: str) -> bool:
    return verdicts.parse(column_a or "")[0] == "go"


@dataclass(frozen=True)
class RowRating:
    """What his record holds for one sheet row, as it reads now.

    kind: the ratings kind of this row's observation, set only when the
    latest rating of its role is that observation (or carries it as a twin).
    job_ids: the roles the record files the row under. record_ids: those
    ratings' ids, for moved_at. score_built: the row is the one a package
    his score built sits on."""
    kind: str
    job_ids: tuple = ()
    record_ids: tuple = ()
    score_built: bool = False


def should_leave(row: SheetRow, rating: RowRating | None) -> str | None:
    """Why this row leaves Pipeline, or None. The one predicate: settle,
    process and the archive command all ask it. rating None means his record
    was not consulted (the canon gate is shut, or the record is unreadable):
    then only column A moves a row, as before 2026-10-13."""
    kind, _code = verdicts.parse(row.verdict or "")
    if kind == "applied":
        return APPLIED
    if kind == "rejection":
        return DECLINED
    if rating is None or not rating.kind:
        return None
    yes = kind == "go"
    if not yes and not undecided(row.verdict):
        return None
    k = (row.cell(KRISH_SCORE) or "").strip()
    score = ratings.whole_score(k)
    if k and score is None:
        return None
    low = score is not None and score <= LEAVES_AT_OR_BELOW
    if not yes and score is not None and not low and _gone(row):
        # Before the score-built guard: a posting that cannot be applied to
        # leaves whatever built its package. rebuild-packages re-checks
        # liveness on packages his score built, and one it found gone was
        # kept on Pipeline as dead with stale links for good.
        return GONE
    if rating.score_built and not low:
        return None
    if yes:
        if low:
            return YES_SCORED_LOW
        if not k and rating.kind == ratings.REACTION_ONLY:
            return YES_REACTION
        return None
    if low:
        return SCORED_LOW
    if score is not None:
        # Only hunter writes "Posting dead", and only after its own check
        # found the posting gone. A column A Yes it marked dead stays, as
        # before: the gh_jid bug once marked a live Yes dead, and his Yes is
        # his to clear.
        return GONE if _gone(row) else None
    if rating.kind == ratings.REACTION_ONLY:
        return REACTION
    if rating.kind == ratings.UNSCORED:
        return SCORED_PAST
    if rating.kind == ratings.DUPLICATE:
        return DUPLICATE
    return None


def comes_back(row: SheetRow, rating: RowRating | None) -> bool:
    """A row on Applied that his score now asks to be on Pipeline: column A
    New, blank or Yes, K a whole number 7 or more, and that score the latest
    rating of its role."""
    if rating is None or rating.kind != ratings.SCORED:
        return False
    if not (undecided(row.verdict) or is_yes(row.verdict)):
        return False
    if _gone(row):
        return False  # its posting is gone: back on Pipeline it could only leave again
    score = ratings.whole_score(row.cell(KRISH_SCORE))
    return score is not None and score >= COMES_BACK_AT


def _gone(row: SheetRow) -> bool:
    return (row.cell("Package Status") or "").strip() == PKG_DEAD


def cancel_reason(row: SheetRow) -> str:
    """Recorded on a cancelled approval. Private to the database: no log line
    carries it, because it is his score beside a role."""
    score = ratings.whole_score(row.cell(KRISH_SCORE))
    if score is not None:
        return f"his {KRISH_SCORE} {score}"
    return "his reaction, no score"


# ---------- his record, read the way a sheet row is found in it ----------

def live_kind(row: SheetRow) -> str:
    """The kind the record would give this row, a blank one taken as past its
    wait: whether it is, only the record can say (a pending blank is never
    stored, so it finds no rating and stays)."""
    return ratings.kind_of(row.cell(KRISH_SCORE), row.cell(KRISH_REACTION), row.verdict,
                           reviewed_past=True)


def observation(row: SheetRow, kind: str) -> str:
    """The row's observation key, built exactly as ratings.rate builds it."""
    k = (row.cell(KRISH_SCORE) or "").strip()
    m = norm(row.cell(KRISH_REACTION))
    score = ratings.whole_score(k)
    return ratings.observation_key(
        kind, score if kind in (ratings.SCORED, ratings.DUPLICATE) else None,
        k if kind == ratings.UNREADABLE else None, m or None)


def _stored_observation(r: dict) -> str:
    return r.get("observation_key") or ratings.observation_key(
        r.get("kind"), r.get("score"), r.get("score_text"), r.get("reaction"))


def _twin_ident(r: dict, t: dict) -> tuple:
    """A twin's key, the way ratings.ident_of_row keys the row it came from:
    its own company and title (ratings._twin_record keeps them since
    2026-10-14) and its own location. A twin recorded before then carries
    only its location and is keyed under the winner's company and title, as
    it was."""
    return (norm(t.get("company") or r.get("company") or "").lower(),
            norm(t.get("title") or r.get("title") or "").lower(),
            norm(t.get("location") or "").lower())


@dataclass
class Records:
    """The latest rating of every role, indexed by the company, title and
    location it was recorded under. A row is found in it without pairing the
    sheet against hunter_seen_roles again, which is a read of about 9,500
    rows that ratings.sync takes only when K and M have changed."""
    latest: dict = field(default_factory=dict)
    by_ident: dict = field(default_factory=dict)

    @classmethod
    def of(cls, stored: list[dict]) -> "Records":
        latest = {jid: r for jid, r in ratings.latest_of(stored).items()
                  if r.get("kind") != ratings.CLEARED}
        by_ident: dict = {}
        for r in latest.values():
            keys = {ratings.ident_of_record(r)}
            # A twin is filed under its own company, title and location: two
            # rows of one role are often one seat listed in two offices, or
            # one title typed two ways ("VP, Sales" and "VP Sales"), and the
            # row is looked up by what it reads, not by the winner's text.
            for t in r.get("twins") or []:
                if isinstance(t, dict):
                    keys.add(_twin_ident(r, t))
            for k in keys:
                by_ident.setdefault(k, []).append(r)
        return cls(latest=latest, by_ident=by_ident)

    def find(self, row: SheetRow) -> tuple[str, list[dict]]:
        """(the row's kind as it reads now, the latest ratings that are this
        observation). A twin the record carries counts: two rows of one role
        are recorded as one rating with the other row kept beside it, and a
        row he marked Duplicate beside a twin he scored is exactly that."""
        kind = live_kind(row)
        if kind not in ratings.KINDS:
            return kind, []
        obs = observation(row, kind)
        ident = ratings.ident_of_row(row)
        hits = []
        for r in self.by_ident.get(ident, []):
            if _stored_observation(r) == obs:
                hits.append(r)
                continue
            for t in r.get("twins") or []:
                if not isinstance(t, dict):
                    continue
                if (ratings.observation_key(t.get("kind"), t.get("score"),
                                            t.get("score_text"), t.get("reaction")) == obs
                        and _twin_ident(r, t) == ident):
                    hits.append(r)
                    break
        return kind, hits

    def rate(self, rows: list[SheetRow]) -> dict[int, RowRating]:
        """row number -> RowRating for every row his record holds as it reads
        now. score_built is filled in by mark_score_built."""
        out = {}
        for s in rows:
            kind, hits = self.find(s)
            if hits:
                out[s.row_number] = RowRating(
                    kind=kind, job_ids=tuple(sorted({h["job_id"] for h in hits})),
                    record_ids=tuple(sorted(h["id"] for h in hits if h.get("id") is not None)))
        return out


def mark_score_built(rows: list[SheetRow], rated: dict[int, RowRating],
                     roles: dict[str, dict]) -> dict[int, RowRating]:
    """score_built on the row a package his score built sits on: its role's
    package_trigger reads "Krish Score N", and the row carries the package's
    links or is the only row of the role on the tab. A twin he marked
    Duplicate beside the row his 9 built is not that row, and leaves."""
    by_role = Counter(j for rr in rated.values() for j in rr.job_ids)
    out = dict(rated)
    for s in rows:
        rr = rated.get(s.row_number)
        if rr is None:
            continue
        built = [j for j in rr.job_ids
                 if router.built_on_score((roles.get(j) or {}).get("package_trigger"))
                 is not None]
        if not built:
            continue
        own = bool(s.package_urls.get("cv") or s.package_urls.get("letter"))
        alone = all(by_role[j] == 1 for j in built)
        out[s.row_number] = replace(rr, score_built=own or alone)
    return out


def yes_overridden(row: SheetRow) -> bool:
    """A Yes his score has taken back, read off the row's cells alone: K a
    whole number 1 to 5, or K blank and a reaction that is not Duplicate.
    What should_leave says of the row as it reads now, so a Yes he scored 3
    is not built for while it waits for the hourly pass to move it, or for a
    sync that failed to record the 3. Not the record: a build is paid for,
    and a wrong build is worse than a missed one."""
    return should_leave(row, RowRating(kind=live_kind(row))) in YES_REASONS


def unrecorded(row: SheetRow) -> bool:
    """K and M say this row leaves, and his record does not hold it yet. A
    blank is not counted: a blank no rating names is pending, not missed."""
    kind = live_kind(row)
    if kind not in (ratings.SCORED, ratings.REACTION_ONLY, ratings.DUPLICATE):
        return False
    return should_leave(row, RowRating(kind=kind)) in SCORE_REASONS


# ---------- the way back ----------

_EPOCH = datetime.date(1899, 12, 30)


# Cells a row going back keeps as they read, never quoted: column A is his,
# the AI Score is a number Pipeline sorts on, JD URL Verified is TRUE or
# FALSE, a date goes RAW (sheet.append_rows) and his two cells RAW.
_AS_READ = frozenset({COLS["Verdict"], COLS[AI_SCORE], COLS["JD URL Verified"]}
                     | set(DATE_COLS) | set(HIS_IDX))


def pipeline_cells(row: SheetRow) -> list[str]:
    """An Applied row's cells as Pipeline takes them back, so the row lands
    as the text it reads. Column A blank is the canon fill default New, which
    is what an untouched row reads and what the empty-cell repair would write
    on Pipeline anyway; New and Yes are carried as he left them. A date the
    Applied tab holds as a serial (a row archived USER_ENTERED, and a formula
    read returns a date as its serial) goes back as the YYYY-MM-DD it was.
    Every other cell that is not a formula goes as_text, as make_row writes
    Business, Comp and the rest: "$225,000" back through USER_ENTERED is
    225000, a Comp he never wrote, which the next amendment pass would read
    as his correction."""
    cells = list(row.cells)
    if not cells[COLS["Verdict"]].strip():
        cells[COLS["Verdict"]] = "New"
    for i in DATE_COLS:
        v = str(cells[i]).strip()
        if v.isdigit() and 20000 < int(v) < 80000:
            cells[i] = (_EPOCH + datetime.timedelta(days=int(v))).isoformat()
    for i, c in enumerate(cells):
        if i not in _AS_READ and not str(c).startswith("="):
            cells[i] = as_text(str(c))
    return cells


def same_place(a: SheetRow, b: SheetRow) -> bool:
    """Two rows that are one row on two tabs: company, role, location and
    link."""
    def ident(s):
        return (ratings.ident_of_row(s), (s.jd_url or "").strip())
    return ident(a) == ident(b)
