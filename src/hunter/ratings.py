"""His score is the record: K Krish Score and M Krish Reaction, read off the sheet.

Krish inserted the two columns on 2026-10-11 and said hunter should learn from
them rather than from column A. Until this module nothing recorded them:
column A was the only ruling hunter kept, and he had stopped typing it on
most rows (one column A verdict among the 17 roles of the 10-11 batch). This reads both tabs, pairs every row to the role it is, says what kind
of ruling each row carries, and keeps a history in hunter_krish_ratings, one
row per distinct thing he wrote about a role.

It records. It decides nothing: since 2026-10-14 a build reads K itself,
through router.build_trigger with this module's whole_score, so "7.5" is
unreadable in both places; staging reads column A as before (PR4 moves the
rows he scored low, with his approval).

What it must never do, because each one has already been measured to go wrong:

- Send his reaction anywhere a verdict goes. verdicts.parse reads any free
  text as a rejection, so all 68 reactions on the sheet on 2026-10-13 read as
  declines, 41 of them on rows he scored 7 or more; learn.infer coded six of
  them as company-level declines, which would have closed G12 on a company he
  scored 7 on another role. M reaches none of verdicts.parse, judgedata
  .label_of, learn.*, hunter_verdict_events or hunter_seen_roles.krish_verdict.
  Column A is still read with verdicts.parse, because column A is a verdict.
- Print what he wrote. The repository and its Actions logs are public, so
  every line this module returns is counts, tab names and row numbers.
- Guess a score. "7.5" is score_unreadable, kept as typed, and counts nowhere.
- Call a row he has not reached "not wanted". A blank is pending until he has
  scored a row from a strictly later staging run and that first score is a
  day old, and a row hunter cannot date is never anything but pending.
- Keep a rating he took back. A role whose row is on the sheet with nothing
  rated, where a rating was recorded, gets a CLEARED row, and latest() reads
  that as no rating at all.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field

from .amend import norm
from .config import ALL_ROWS, Config, db_get, db_insert
from .sheet import (AI_SCORE, KRISH_REACTION, KRISH_SCORE, SheetRow, archive_layout,
                    says_duplicate)
from .sources import country_of, job_id as mint, norm_title, placed_identity_keys

TABLE = "hunter_krish_ratings"

# How a sheet row was tied to the role it is. Stored, so a minted id is
# visible on read-back rather than passing for a role hunter recorded.
MATCHED, IDENTITY, MINTED = "matched", "identity", "minted"
PAIRINGS = (MATCHED, IDENTITY, MINTED)

SCORED = "scored"
UNREADABLE = "score_unreadable"
REACTION_ONLY = "reaction_only"
UNSCORED = "unscored_after_review"
YES_UNSCORED = "yes_unscored"
DUPLICATE = "duplicate"
# One ruling per role per sync, and when two sheet rows are one role this is
# the order that decides which of them speaks for it. His number first: on
# 2026-10-13 one pair of twins was a row reading "Duplicate" above a row he
# had scored high, and "latest" between rows written in the same second is
# undefined, so without an order the score could have been filed as a
# duplicate.
PRECEDENCE = (SCORED, UNREADABLE, REACTION_ONLY, UNSCORED, YES_UNSCORED, DUPLICATE)
KINDS = frozenset(PRECEDENCE)
# What a row that carries no rating is, for the counts only. Neither is
# stored: a pending row is a decision he has not made yet, and a row with only
# column A is ruled on in column A, which stays the record for it.
PENDING = "pending"
COLUMN_A_ONLY = "column_a_only"
# A role whose row is on the sheet and carries none of the above now, where
# the record holds a rating for it: he cleared K or M, or a blank he had
# passed over is pending again. Written, not left out: a rating that is no
# longer there would otherwise still be the newest row for the role, so a 9
# he typed on the wrong row and cleared would keep counting as a Yes company
# and in the batch's share at 7 or more. latest() reads it as no rating.
CLEARED = "cleared"

# A Duplicate he typed loses to a score of this or more on the same row. He
# does not score a row he means to throw away; a 6 or more beside the word
# says the word is about the other row.
DUPLICATE_LOSES_TO = 6
# He reads the batch from the top and scores as he goes, and the sheet is
# sorted by the AI score, so the first score on a new batch says nothing yet
# about the rows he has not reached. A day after it, a blank in an earlier
# batch is one he has had in front of him and passed over.
REVIEW_WAIT = datetime.timedelta(hours=24)

_WHOLE = re.compile(r"^(?:[1-9]|10)$")
_AI = re.compile(r"^(?:\d|10)$")


class RatingsError(RuntimeError):
    """A failure described without anything he wrote in it. A PostgREST
    error body can echo the failing row, reaction and all, and these lines go
    to public logs."""


def safe_error(e: Exception) -> str:
    """Class and status code only, never the server's text."""
    if isinstance(e, RatingsError):
        return str(e)
    status = getattr(getattr(e, "response", None), "status_code", None)
    return f"{e.__class__.__name__}" + (f" {status}" if status else "")


# ---------- pairing, shared with judgedata.collect ----------

@dataclass
class Paired:
    job_id: str
    pairing: str
    db: dict | None = None
    # The record run.match_rows gave this row, trusted or not. judgedata
    # .collect reads this and nothing else, so the judge's context and
    # judge_eval.json come out exactly as they did before this module,
    # including the London row match_rows gave its New York twin's record.
    raw: dict | None = None


def _location(row: SheetRow) -> str:
    loc = (row.cell("Location") or "").strip()
    return "" if loc == "Not stated" else loc


def _crosses_country(row: SheetRow, d: dict) -> bool:
    """A pair match_rows made between two named, different countries. It
    never looks at location, so a London row above its New York twin took the
    New York record on job id alone."""
    mine, theirs = country_of(_location(row)), country_of(d.get("location") or "")
    return bool(mine and theirs and not (mine & theirs))


def _choose(hits: set, by_id: dict, base: str, title: str) -> str | None:
    """One role from the identity hits, or None. Several records of one role
    are the incumbent's hash-suffixed copies; the one hunter showed him is the
    role, then the bare id, then the one whose title is the row's own. A tie
    after that is left unresolved rather than guessed."""
    if len(hits) == 1:
        return next(iter(hits))
    pool = sorted(hits)
    shown = [j for j in pool if (by_id[j].get("presented_at") or "")]
    pool = shown or pool
    if len(pool) == 1:
        return pool[0]
    if base in pool:
        return base
    exact = [j for j in pool if norm_title(by_id[j].get("title") or "") == norm_title(title)]
    return exact[0] if len(exact) == 1 else None


def pair_sheet(rows: list[SheetRow], db: list[dict]) -> list[Paired]:
    """The role behind each sheet row, in the order given.

    1. run.match_rows over the rows as given: the pairing reconcile uses.
       A pair it made across two named countries is not trusted.
    2. A row it left unmatched or ambiguous is looked up by
       sources.placed_identity_keys over EVERY database row, not only the ones
       match_rows had left: a sheet twin's database row was taken by the row
       above it, and it is still the role.
    3. No hit is a minted id with run.place_suffix semantics against every
       id already known, so a London row and a New York row of one title are
       two roles and never one.

    A record of unknown place matches a row of any country, which is right
    for one posting seen twice and wrong for two rows in two countries, so an
    id already filed under a row in one named country is never given to a
    row in another: that row is minted its own.
    """
    from .run import match_rows, place_suffix
    rows = list(rows)
    pairs, _unmatched, _db_only, _ambiguous = match_rows(rows, list(db))
    by_row: dict[int, Paired] = {}
    raw = {id(s): d for s, d in pairs}
    # job_id -> the countries of the sheet rows already filed under it
    filed: dict[str, frozenset] = {}
    for s, d in pairs:
        if d.get("job_id") and not _crosses_country(s, d):
            by_row[id(s)] = Paired(d["job_id"], MATCHED, d, raw=d)
            filed[d["job_id"]] = filed.get(d["job_id"], frozenset()) | country_of(_location(s))

    def clash(jid: str, countries: frozenset) -> bool:
        held = filed.get(jid)
        return bool(held and countries and not (held & countries))

    by_id: dict[str, dict] = {}
    index: dict[tuple, set] = {}
    for d in db:
        jid = d.get("job_id")
        if not jid:
            continue
        by_id.setdefault(jid, d)
        for k in placed_identity_keys(d.get("company") or "", d.get("title") or "",
                                      d.get("location") or "", seen=True):
            index.setdefault(k, set()).add(jid)
    id_countries = {jid: country_of(d.get("location") or "") for jid, d in by_id.items()}
    out: list[Paired] = []
    for s in rows:
        p = by_row.get(id(s))
        if p is None:
            loc = _location(s)
            countries = country_of(loc)
            hits: set = set()
            for k in placed_identity_keys(s.company, s.role, loc, seen=False):
                hits |= index.get(k, set())
            base = mint(s.company, s.role)
            jid = _choose(hits, by_id, base, s.role) if hits else None
            if jid and not clash(jid, countries):
                p = Paired(jid, IDENTITY, by_id[jid])
            else:
                held = dict(id_countries)
                for j, c in filed.items():
                    held[j] = (held.get(j) or frozenset()) | c
                jid = base + place_suffix(base, countries, loc, s.jd_url or "", held)
                id_countries.setdefault(jid, countries)
                p = Paired(jid, MINTED, None)
            filed[p.job_id] = filed.get(p.job_id, frozenset()) | countries
            p.raw = raw.get(id(s))
        out.append(p)
    return out


# ---------- what one row says ----------

def is_duplicate(reaction: str) -> bool:
    """M reads exactly "Duplicate" (sheet.says_duplicate, shared with the
    sheet's twin repair). Not "same role as" or "listed above": a reaction
    that points at another office's posting of the same title is a taste no
    about a distinct posting, and one on the sheet on 2026-10-13 did exactly
    that."""
    return says_duplicate(reaction)


def whole_score(k: str) -> int | None:
    k = (k or "").strip()
    return int(k) if _WHOLE.match(k) else None


def ai_score(cell: str) -> int | None:
    """The AI Score cell as the number it shows, or None. A hand edit that is
    not a whole number from 0 to 10 is not something to record as hunter's."""
    c = (cell or "").strip()
    return int(c) if _AI.match(c) else None


def kind_of(k: str, m: str, column_a: str, *, reviewed_past: bool) -> str:
    """Exactly one kind for one row, from K, M and column A.

    reviewed_past: he has scored a row from a strictly later staging run and
    the first of those scores is REVIEW_WAIT old. Only a blank row asks."""
    from .verdicts import parse
    k = (k or "").strip()
    m = norm(m)
    dup = is_duplicate(m)
    score = whole_score(k)
    if k and score is None:
        # Never guessed, and never thrown away as a duplicate either: a
        # number-like K beside "Duplicate" is something he typed on purpose.
        return UNREADABLE
    if score is not None:
        return DUPLICATE if dup and score < DUPLICATE_LOSES_TO else SCORED
    if m:
        return DUPLICATE if dup else REACTION_ONLY
    verdict = parse(column_a)[0]
    if verdict == "none":
        return UNSCORED if reviewed_past else PENDING
    if verdict == "go":
        return YES_UNSCORED
    return COLUMN_A_ONLY


# ---------- reading the tabs ----------

@dataclass
class Read:
    rows: list            # every row, Pipeline then Applied: the pairing set
    rated: list           # (tab, row) for the rows whose K and M can be read
    applied: str          # "rated", "skipped: previous layout", "empty"
    fingerprint: str = ""


def _ident(s: SheetRow) -> list:
    return [norm(s.company).lower(), norm(s.role).lower(), norm(_location(s)).lower()]


def ident_of_row(s: SheetRow) -> tuple:
    """A sheet row as the record keys it: company, title and location as
    typed, normalised. The review email uses it to leave out the blanks this
    record says he has scored past, without pairing the sheet again."""
    return tuple(_ident(s))


def ident_of_record(r: dict) -> tuple:
    """The same key read off a stored rating, which keeps the row's own
    company, title and location (_payload)."""
    return (norm(r.get("company") or "").lower(), norm(r.get("title") or "").lower(),
            norm(r.get("location") or "").lower())


def read_tabs(sheet, canon_headers: list[str]) -> Read:
    """Pipeline then Applied, the order judgedata.collect reads them in, so the
    ids agree with the judge's context. K and M are taken by header name on
    both; Applied still on the previous layout has neither column, so its rows
    are paired but never rated, and the summary says so."""
    from .verdicts import parse
    pipeline = sheet.read_pipeline(canon_headers)
    grid = sheet.read_archive_grid()
    layout = archive_layout(grid)
    archived = sheet.read_archive() if layout != "empty" else []
    rated = [("Pipeline", s) for s in pipeline]
    if layout == "current":
        rated += [("Applied", s) for s in archived]
        applied = "rated"
    elif layout == "previous":
        applied = "skipped: previous layout, no Krish Score or Krish Reaction"
    else:
        applied = "empty"
    # Sorted, so a re-sort by AI score alone is not a change. Column A is
    # in it as the kind it parses to, because a blank row's kind turns on it.
    lines = sorted(json.dumps(
        [tab] + _ident(s) + [parse(s.verdict)[0], (s.cell(KRISH_SCORE) or "").strip(),
                             norm(s.cell(KRISH_REACTION))])
        for tab, s in rated)
    lines += [json.dumps(["Applied rows paired only"] + sorted(
        json.dumps(_ident(s)) for s in archived))] if layout == "previous" else []
    fp = hashlib.sha256(("\n".join([applied] + lines)).encode()).hexdigest()[:32]
    return Read(rows=list(pipeline) + list(archived), rated=rated, applied=applied,
                fingerprint=fp)


# ---------- time ----------

def _when(value) -> datetime.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)\.(\d+)(.*)$", text)
    if m:
        text = f"{m.group(1)}.{(m.group(2) + '000000')[:6]}{m.group(3)}"
    try:
        got = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=datetime.timezone.utc)


def _batch(presented_at) -> str:
    """The staging run a row came from, as batchstats keys it: the UTC date of
    presented_at. Rows staged in one run differ by milliseconds, so comparing
    the timestamps themselves would call each one later than the last."""
    return str(presented_at or "")[:10]


def _first_scores(stored: list[dict]) -> dict[str, datetime.datetime]:
    """batch -> when his first score on it was first seen. Every scored row
    counts, a superseded one included, so changing an 8 to a 9 keeps the
    batch's first moment; a role whose latest rating is CLEARED counts not at
    all, because a 9 typed on the wrong row and cleared is not him having
    reached that batch."""
    gone = {jid for jid, r in latest_of(stored).items() if r.get("kind") == CLEARED}
    first: dict[str, datetime.datetime] = {}
    for r in stored:
        if r.get("kind") != SCORED or r.get("job_id") in gone:
            continue
        b, t = _batch(r.get("presented_at")), _when(r.get("first_seen_at"))
        if b and t is not None:
            first[b] = min(first.get(b, t), t)
    return first


def reviewed_batches(stored: list[dict], now: datetime.datetime) -> dict[str, datetime.datetime]:
    """batch -> when his first score on it was first seen, for batches where
    that was REVIEW_WAIT or more ago. From hunter_krish_ratings.first_seen_at
    only: a score this sync is seeing for the first time is minutes old, and
    a table that does not exist yet knows of no score at all."""
    return {b: t for b, t in _first_scores(stored).items() if now - t >= REVIEW_WAIT}


def next_due(stored: list[dict], after: datetime.datetime) -> datetime.datetime | None:
    """The first moment after `after` at which a batch becomes reviewed, so a
    sync whose K and M have not changed still re-reads when a blank row's
    standing changes with nothing but time."""
    dues = sorted(t + REVIEW_WAIT for t in _first_scores(stored).values()
                  if t + REVIEW_WAIT > after)
    return dues[0] if dues else None


# ---------- one rating per role ----------

def observation_key(kind: str, score: int | None, score_text: str | None,
                    reaction: str | None) -> str:
    """What makes one observation distinct, as 64 hex characters. The unique
    index holds this and never his text: a reaction is as long as a cell
    holds and a btree entry is at most 2,704 bytes. Measured on Postgres 16
    with the raw columns as the key, one reaction of about 4,000 characters
    refused the whole write, so every hourly sync would have failed until he
    shortened his own words."""
    return hashlib.sha256(json.dumps([kind, score, score_text, reaction]).encode()).hexdigest()


@dataclass
class Rating:
    tab: str
    row: SheetRow
    paired: Paired
    kind: str
    score: int | None = None
    score_text: str | None = None
    reaction: str | None = None
    twins: list = field(default_factory=list)

    @property
    def observation(self) -> str:
        return observation_key(self.kind, self.score, self.score_text, self.reaction)

    @property
    def key(self) -> tuple:
        return (self.paired.job_id, self.observation)


def rate(read: Read, paired: dict[int, Paired], stored: list[dict],
         now: datetime.datetime) -> tuple[list[Rating], Counter, set]:
    """(one Rating per sheet row that carries one, rows by kind, the job ids
    of the pending rows). Every row gets exactly one kind; pending and
    column-A-only rows are counted and not rated."""
    reviewed = reviewed_batches(stored, now)
    out: list[Rating] = []
    by_kind: Counter = Counter()
    pending_roles: set = set()
    for tab, s in read.rated:
        p = paired[id(s)]
        presented = _batch((p.db or {}).get("presented_at"))
        # Strictly later: his scores on the batch this row came in with say
        # nothing about the rows of it he has not reached yet.
        past = bool(presented) and any(b > presented for b in reviewed)
        k = (s.cell(KRISH_SCORE) or "").strip()
        m = norm(s.cell(KRISH_REACTION))
        kind = kind_of(k, m, s.verdict, reviewed_past=past)
        by_kind[kind] += 1
        if kind == PENDING:
            pending_roles.add(p.job_id)
            continue
        if kind == COLUMN_A_ONLY:
            continue
        score = whole_score(k)
        out.append(Rating(tab=tab, row=s, paired=p, kind=kind,
                          score=score if kind in (SCORED, DUPLICATE) else None,
                          score_text=k if kind == UNREADABLE else None,
                          reaction=m or None))
    return out, by_kind, pending_roles


def collapse(ratings: list[Rating]) -> list[Rating]:
    """One Rating per job id, by PRECEDENCE and then sheet order (Pipeline
    first, top to bottom). The others are kept on the winner as twins."""
    best: dict[str, Rating] = {}
    order = {k: i for i, k in enumerate(PRECEDENCE)}
    for r in ratings:
        jid = r.paired.job_id
        have = best.get(jid)
        if have is None:
            best[jid] = r
        elif order[r.kind] < order[have.kind]:
            r.twins = have.twins + [have]
            have.twins = []
            best[jid] = r
        else:
            have.twins.append(r)
    return list(best.values())


def cleared(read: Read, paired: dict[int, Paired], roles: list[Rating],
            stored_latest: dict[str, dict]) -> list[Rating]:
    """A CLEARED Rating for every role whose row is on a rated tab this sync,
    whose rows carry nothing a Rating is made of, and which the record holds
    a rating for. Only roles on the sheet: a row he deleted, or one on
    Applied's previous layout, is not him taking a score back. One already
    CLEARED is written again, as every current rating is on a re-pair."""
    rated = {r.paired.job_id for r in roles}
    first: dict[str, tuple] = {}
    for tab, s in read.rated:
        first.setdefault(paired[id(s)].job_id, (tab, s))
    return [Rating(tab=tab, row=s, paired=paired[id(s)], kind=CLEARED)
            for jid, (tab, s) in first.items()
            if jid not in rated and jid in stored_latest]


def _twin_record(t: Rating) -> dict:
    return {"tab": t.tab, "row": t.row.row_number, "kind": t.kind, "score": t.score,
            "score_text": t.score_text, "reaction": t.reaction,
            "location": _location(t.row) or None}


# ---------- the table ----------

def load(cfg: Config) -> list[dict]:
    return db_get(cfg, TABLE, {"select": "*", "order": "id.asc", "limit": ALL_ROWS})


def latest_of(rows: list[dict]) -> dict[str, dict]:
    """job_id -> the rating with the newest last_seen_at, CLEARED included.
    A score he changed and changed back is the row seen most recently, which
    the unique key alone could not say."""
    out: dict[str, dict] = {}
    for r in rows:
        jid = r.get("job_id")
        if not jid:
            continue
        have = out.get(jid)
        key = (_when(r.get("last_seen_at")) or datetime.datetime.min.replace(
            tzinfo=datetime.timezone.utc), r.get("id") or 0)
        if have is None or key > have[0]:
            out[jid] = (key, r)
    return {jid: r for jid, (_, r) in out.items()}


def latest(cfg: Config) -> dict[str, dict]:
    """The current rating per job_id, for everything outside this module. A
    role whose latest is CLEARED has none: it is left out here, once, so no
    reader can count a score he took back."""
    return {jid: r for jid, r in latest_of(load(cfg)).items() if r.get("kind") != CLEARED}


def first_of(rows: list[dict]) -> dict[str, dict]:
    """job_id -> the earliest rating hunter holds for it, whatever its key."""
    never = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)
    out: dict[str, tuple] = {}
    for r in rows:
        jid = r.get("job_id")
        if not jid:
            continue
        key = (_when(r.get("first_seen_at")) or never, r.get("id") or 0)
        if jid not in out or key < out[jid][0]:
            out[jid] = (key, r)
    return {jid: r for jid, (_, r) in out.items()}


THEN = ("ai_score_then", "judge_fit_then", "judge_prompt_version_then")


def _payload(r: Rating, first: dict | None, now_iso: str, fp: str) -> dict:
    s, d = r.row, r.paired.db or {}
    was = first or {}
    comp = (s.cell("Comp") or "").strip()
    row = {
        "job_id": r.paired.job_id, "pairing": r.paired.pairing,
        "company": s.company.strip(), "title": s.role.strip(),
        "location": _location(s) or None,
        "comp": None if comp in ("", "Not disclosed") else comp,
        "score": r.score, "score_text": r.score_text, "reaction": r.reaction,
        "observation_key": r.observation,
        "kind": r.kind, "twins": [_twin_record(t) for t in r.twins],
        "presented_at": d.get("presented_at") or was.get("presented_at"),
        "last_seen_at": now_iso, "sheet_hash": fp,
    }
    # What the AI said and what the judge thought when he first wrote
    # anything about this role, frozen per role and not per observation. J
    # is rewritten by regate and the drafts pass, and the judge refits; a
    # changed score or an edited reaction is a new observation, and taking
    # J afresh for it paired his new score with a J he was never shown.
    if first is not None:
        for k in THEN:
            row[k] = was.get(k)
    else:
        row["ai_score_then"] = ai_score(s.cell(AI_SCORE))
        fit = d.get("judge_fit")
        row["judge_fit_then"] = int(fit) if isinstance(fit, (int, float)) else None
        row["judge_prompt_version_then"] = d.get("judge_prompt_version") or None
    return row


ON_CONFLICT = "job_id,observation_key"
DB_SELECT = ("job_id,company,title,url,job_url,location,presented_at,"
             "judge_fit,judge_prompt_version")


def _stored_key(x: dict) -> tuple:
    """A stored row's unique key: the column the index holds, or the same
    hash of its fields for a row that predates it."""
    return (x.get("job_id"), x.get("observation_key") or observation_key(
        x.get("kind"), x.get("score"), x.get("score_text"), x.get("reaction")))


def sync(cfg: Config, sheet, *, apply: bool, canon_headers: list[str],
         force: bool = False, now: datetime.datetime | None = None) -> dict:
    """Read both tabs, pair, rate, collapse, and write what is new.

    Re-pairs only when the sheet's K and M, with the identity and column A of
    every row, hash differently from the last sync's, or when a batch has
    since become reviewed, or when `force` says to (process does, so its
    duplicate check never depends on whether an hourly settle saw the edit
    first). The hash lives on the rows the last sync wrote (sheet_hash, found
    by last_seen_at): hunter_row_state is per role and system_config is
    configuration, and an hourly read of every recorded role to learn that
    nothing changed is what the hash exists to avoid.

    Returns counts only. Nothing returned or raised carries what he wrote.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = {"apply": apply, "unchanged": False, "applied_tab": "", "rows_read": 0,
           "rows": Counter(), "roles": Counter(), "pairing": Counter(),
           "rated_pairing": Counter(), "new": 0, "seen_again": 0, "pending_roles": 0,
           "cleared": 0, "sheet_twins": [], "duplicate_events": [],
           "stored_readable": True, "reviewed_batches": 0}
    read = read_tabs(sheet, canon_headers)
    out["applied_tab"] = read.applied
    out["rows_read"] = len(read.rows)
    try:
        stored = load(cfg)
    except Exception as e:
        if apply:
            raise RatingsError(f"{TABLE} could not be read ({safe_error(e)})") from None
        # A dry run before the table exists: nothing is known of when he
        # first scored anything, so no blank row can be past its wait.
        stored, out["stored_readable"] = [], False
    stored_latest = latest_of(stored)

    if apply and stored and not force:
        last = max((_when(r.get("last_seen_at")) for r in stored if r.get("last_seen_at")),
                   default=None)
        last_rows = [r for r in stored if _when(r.get("last_seen_at")) == last]
        due = next_due(stored, last) if last else None
        if (last and last_rows and all(r.get("sheet_hash") == read.fingerprint
                                       for r in last_rows)
                and (due is None or due > now)):
            out["unchanged"] = True
            out["since"] = last.isoformat()
            current = [r for r in stored_latest.values() if _when(r.get("last_seen_at")) == last]
            out["roles"] = Counter(r["kind"] for r in current if r["kind"] != CLEARED)
            out["cleared"] = sum(1 for r in current if r["kind"] == CLEARED)
            return out

    try:
        db = db_get(cfg, "hunter_seen_roles", {"select": DB_SELECT, "limit": ALL_ROWS})
    except Exception as e:
        raise RatingsError(f"hunter_seen_roles could not be read ({safe_error(e)})") from None
    pairs = pair_sheet(read.rows, db)
    paired = {id(s): p for s, p in zip(read.rows, pairs)}
    out["pairing"] = Counter(p.pairing for p in pairs)
    out["reviewed_batches"] = len(reviewed_batches(stored, now))

    ratings, by_kind, pending = rate(read, paired, stored, now)
    out["rows"] = by_kind
    roles = collapse(ratings)
    # A role is pending only when no row of it carries a rating: a blank twin
    # beside a row he scored is that role scored, not one still to decide.
    out["pending_roles"] = len(pending - {r.paired.job_id for r in roles})
    out["roles"] = Counter(r.kind for r in roles)
    out["rated_pairing"] = Counter(r.paired.pairing for r in roles)
    gone = cleared(read, paired, roles, stored_latest)
    out["cleared"] = len(gone)

    # Every sheet row's role, rated or not: a Duplicate beside a blank twin
    # is as much a sheet twin as one beside a scored twin.
    holders = Counter(p.job_id for p in pairs)
    for r in roles:
        dups = ([t for t in r.twins if t.kind == DUPLICATE]
                + ([r] if r.kind == DUPLICATE else []))
        if dups and holders[r.paired.job_id] > 1:
            # A twin on the sheet, not in the database: two rows of one role,
            # one of them marked Duplicate. Reported for him to delete; nothing
            # is patched, because the database already holds one role, and
            # the duplicate check would read it as a different role at a
            # company with an application open, which it is not.
            out["sheet_twins"] += [(t.tab, t.row.row_number) for t in dups]
        elif r.kind == DUPLICATE:
            # In memory only, for learning_step's duplicate path. Never written
            # to hunter_verdict_events: it is his reaction, not a column A
            # verdict, and the event log is what G12 reads.
            out["duplicate_events"].append({
                "job_id": r.paired.job_id, "company": r.row.company,
                "title": r.row.role, "verdict": "rejection",
                "reason_code": "duplicate_row", "reason_text": "Duplicate",
                "source": "Krish Reaction"})

    stored_keys = {_stored_key(x) for x in stored}
    firsts = first_of(stored)
    now_iso = now.isoformat()
    writes = roles + gone
    payload = [_payload(r, firsts.get(r.paired.job_id), now_iso, read.fingerprint)
               for r in writes]
    out["new"] = sum(1 for r in writes if r.key not in stored_keys)
    out["seen_again"] = len(writes) - out["new"]
    if not apply or not payload:
        return out
    try:
        db_insert(cfg, TABLE, payload, on_conflict=ON_CONFLICT, merge=True)
    except Exception as e:
        raise RatingsError(f"{TABLE} write failed ({safe_error(e)})") from None
    # Read back what landed, by the timestamp this sync wrote on every row.
    try:
        back = db_get(cfg, TABLE, {"select": "job_id,kind", "last_seen_at": f"eq.{now_iso}",
                                   "limit": ALL_ROWS})
    except Exception as e:
        raise RatingsError(f"{TABLE} read-back failed ({safe_error(e)})") from None
    want = Counter((p["job_id"], p["kind"]) for p in payload)
    got = Counter((b.get("job_id"), b.get("kind")) for b in back)
    if want != got:
        raise RatingsError(f"{TABLE} read back {sum(got.values())} of {len(payload)} "
                           f"rating(s) written; {sum((want - got).values())} missing")
    out["written"] = len(payload)
    return out


def _counts(c: Counter, order) -> str:
    return ", ".join(f"{k} {c.get(k, 0)}" for k in order)


def lines(out: dict) -> list[str]:
    """The run summary's account of his two columns. Counts, tab names and
    row numbers; never a company beside a score, never a word he wrote."""
    if out.get("unchanged"):
        return [f"Krish Score: K and M unchanged since {out.get('since', '')[:16]}, "
                f"nothing re-paired; latest ratings by kind: "
                + _counts(out["roles"], PRECEDENCE) + f"; {out.get('cleared', 0)} cleared"]
    rows = out["rows"]
    res = [
        f"Krish Score: {out['rows_read']} sheet row(s) read; Applied {out['applied_tab']}",
        "Krish Score rows: " + _counts(rows, PRECEDENCE + (PENDING, COLUMN_A_ONLY)),
        "Krish Score roles (one per role): " + _counts(out["roles"], PRECEDENCE)
        + f"; {out['pending_roles']} pending; {out.get('cleared', 0)} cleared (on the "
          f"sheet with nothing rated where a rating was recorded)",
        "Krish Score pairing: " + _counts(out["pairing"], PAIRINGS)
        + " (rated roles: " + _counts(out["rated_pairing"], PAIRINGS) + ")",
    ]
    if not out.get("stored_readable", True):
        res.append(f"Krish Score: {TABLE} not readable, treated as empty; with no "
                   f"first score on record no blank row is past its "
                   f"{int(REVIEW_WAIT.total_seconds() // 3600)} hour wait")
    else:
        res.append(f"Krish Score: {out['reviewed_batches']} batch(es) reviewed "
                   f"{int(REVIEW_WAIT.total_seconds() // 3600)} hours or more; a blank "
                   f"row is not wanted only behind one of them")
    if out["sheet_twins"]:
        where = ", ".join(f"{tab} row {n}" for tab, n in out["sheet_twins"][:6])
        res.append(f"Krish Score: {len(out['sheet_twins'])} Duplicate row(s) share a "
                   f"role with a row you rated ({where}); no database change, safe to "
                   f"delete")
    if out["duplicate_events"]:
        # What happens to them is the caller's to say: process checks them,
        # settle does not.
        res.append(f"Krish Score: {len(out['duplicate_events'])} role(s) marked "
                   f"Duplicate with no twin on the sheet")
    verb = "written" if out.get("apply") else "would be written (dry run)"
    res.append(f"Krish Score: {out['new']} new rating(s), {out['seen_again']} seen again, "
               f"{verb}")
    return res
