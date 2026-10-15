"""Accept rate, per batch, because nobody was watching it.

Between 6 August and 20 September the share of staged roles Krish said yes
to fell from 77 percent to 15 percent. Every one of those batches was
recorded: the roles, their scores, his verdicts. The RATE was not, so the
slide ran for six weeks while 905 tests stayed green and nobody, hunter
included, could see the line going down.

This module is that line. It is deliberately small and it asserts nothing
about quality: it counts what was staged, counts what he ruled on, and
divides. The judgement stays his, but from now on the number is written
down, carried in the weekly email, and raised by the invariants layer when
it falls through the floor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import ALL_ROWS, Config, db_get, db_insert
from .sources import company_key
from .verdicts import is_accept

TABLE = "hunter_batch_stats"

# Below this, across two batches he has finished ruling on, something is
# wrong with the funnel rather than with one week's luck. Chosen from his
# own record: his four best batches ran 77, 55, 50 and 43 percent, and the
# collapse quarters he complained about ran 27, 25, 15. Those were counted
# before an Applied verdict counted as a yes (verdicts.is_accept, 2026-10-14);
# with it every batch reads higher, so this floor now fires less often, never
# more.
FLOOR = 0.35
# One batch can be unlucky. Two in a row is a trend, and waiting for a third
# is six more weeks of his life.
BATCHES_CONSIDERED = 2
# A batch he has barely started judging says nothing yet.
MIN_VERDICTED = 6
# The same alarm on his Krish Score, which is the ruling he gives now that he
# has mostly stopped typing column A: the share of the roles he scored that
# he scored 7 or more. His four scored batches ran 68, 60, 71 and 79 percent
# (10-03 17 of 25, 10-04 6 of 10, 10-08 5 of 7, 10-11 11 of 14), so 40
# percent is twenty points under the worst of them: at that worst rate, two
# batches of seven (his smallest) both fall under it by chance about once in
# a hundred. Paired by ratings.py on 2026-10-14, 10-04 reads 6 of 11 (55
# percent) and the chance at that rate is one in thirty to forty, still a
# rare false alarm.
SCORE_FLOOR = 0.40
GOOD_SCORE = 7


@dataclass
class Batch:
    key: str                      # the sweep date, which is how a batch is identified
    staged: int = 0
    verdicted: int = 0
    accepted: int = 0
    by_source: dict = field(default_factory=dict)
    by_tier: dict = field(default_factory=dict)
    # His Krish Score on each role of the batch whose latest rating is a
    # score, and (AI score, his score) where the AI score was recorded.
    scores: list = field(default_factory=list)
    ai_pairs: list = field(default_factory=list)
    # Of ai_pairs, the ones where the AI score he scored against is the one
    # recorded when the row reached him (hunter_seen_roles.ai_score_shown): a
    # true holdout, because nothing could have tuned it to his answer.
    prospective: int = 0
    # Whether hunter_krish_ratings, and hunter_seen_roles.ai_score_shown,
    # were read for this measurement. False is not zero scores: save leaves
    # the stored columns alone, and the summary says the scores were not read.
    ratings_read: bool = True
    shown_read: bool = True
    # What his edits say about the letters (docedits.py): of the packages
    # built in his house style for this batch, how many he edited, the
    # median size of an edit in characters, and how many touched formatting.
    # edits_read False is not zero edits: save leaves those columns alone.
    letters_built: int = 0
    letters_edited: int = 0
    edit_chars: list = field(default_factory=list)
    formatting_edited: int = 0
    edits_read: bool = True

    @property
    def edit_chars_median(self) -> int | None:
        if not self.edit_chars:
            return None
        xs = sorted(self.edit_chars)
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) // 2

    @property
    def scored(self) -> int:
        return len(self.scores)

    @property
    def score_settled(self) -> bool:
        return self.scored >= MIN_VERDICTED

    @property
    def mean_score(self) -> float | None:
        """None, as the accept rate is, until he has scored enough of it."""
        return sum(self.scores) / self.scored if self.score_settled else None

    @property
    def share_7_plus(self) -> float | None:
        if not self.score_settled:
            return None
        return sum(1 for x in self.scores if x >= GOOD_SCORE) / self.scored

    @property
    def spearman_ai(self) -> float | None:
        return spearman([a for a, _ in self.ai_pairs], [b for _, b in self.ai_pairs])

    @property
    def spearman_n(self) -> int:
        return len(self.ai_pairs)

    @property
    def rate(self) -> float | None:
        """None, not zero, when he has not ruled on enough of it yet.

        Zero would read as "he rejected everything" and would drag a mean
        down, which is the same defect as scoring an absent observation.
        """
        if self.verdicted < MIN_VERDICTED:
            return None
        return self.accepted / self.verdicted

    @property
    def settled(self) -> bool:
        return self.rate is not None


def _ranks(values: list[float]) -> list[float]:
    """1-based ranks, ties sharing the mean of the ranks they span. His scores
    are whole numbers from 1 to 10 across a couple of dozen roles, so most of
    them tie, and a rank that broke ties by position would invent an order."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float], *, min_n: int = MIN_VERDICTED
             ) -> float | None:
    """Spearman's rho with average ranks: Pearson's r on the ranks. The one
    rank correlation hunter has, so every number called a Spearman means the
    same thing. None, never zero, when there are fewer than min_n pairs or
    either side does not vary: a constant has no order to agree with."""
    if len(xs) != len(ys):
        raise ValueError("spearman needs paired values")
    n = len(xs)
    if n < min_n or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / (vx * vy) ** 0.5


def _is_yes(verdict: str) -> bool:
    return is_accept(verdict)


def _is_ruled(verdict: str) -> bool:
    v = (verdict or "").strip().lower()
    return bool(v) and not v.startswith("new")


def _roles(cfg: Config) -> tuple[list[dict], bool]:
    """(the roles, whether ai_score_shown was read with them)."""
    base = "job_id,company,sweep_date,presented_at,status,krish_verdict,source,package_style"
    try:
        return db_get(cfg, "hunter_seen_roles",
                      {"select": base + ",ai_score_shown", "limit": ALL_ROWS}), True
    except Exception:
        # Before the control-center migration adds ai_score_shown, or a read
        # that failed for any other reason. Either way no prospective pair
        # was observed, which is not the same as there being none.
        return db_get(cfg, "hunter_seen_roles", {"select": base, "limit": ALL_ROWS}), False


def measure(cfg: Config, tiers: dict[str, int] | None = None,
            notes: list[str] | None = None) -> list[Batch]:
    """One Batch per sweep date, newest last.

    The verdict comes from hunter_verdict_events, not from
    hunter_seen_roles.krish_verdict. That column is only written when
    reconcile has managed to pair a sheet row to a database row, and
    CLAUDE.md already records what that costs: 89 of his declines never
    became learning events for exactly this reason, so G12 ran on 11
    companies when it should have run on 36. Measuring the funnel off that
    column measures the pairing, and the first run of this module reported
    100 percent for a batch he had mostly declined.

    The batch is the day the role was put in front of him, which is
    presented_at and nothing else.
    """
    roles, shown_read = _roles(cfg)
    try:
        events = db_get(cfg, "hunter_verdict_events",
                        {"select": "job_id,verdict,source,recorded_at",
                         "limit": ALL_ROWS})
    except Exception:
        events = []
    # His own ruling wins over anything hunter wrote about itself.
    ruled: dict[str, str] = {}
    for e in sorted(events, key=lambda x: x.get("recorded_at") or ""):
        src = (e.get("source") or "").lower()
        if "krish" not in src and "column a" not in src:
            continue
        if e.get("job_id"):
            ruled[e["job_id"]] = e.get("verdict") or ""
    # His Krish Score, one latest rating per role (ratings.latest). Read
    # separately so a table that cannot be read leaves the accept rate
    # standing and says so, rather than reading as a batch he never scored.
    ratings_read = True
    try:
        from . import ratings
        rated = ratings.latest(cfg)
    except Exception as e:
        rated, ratings_read = {}, False
        if notes is not None:
            notes.append(f"Krish Score by batch not measured: hunter_krish_ratings "
                         f"not read ({e.__class__.__name__})")
    # His edits to the letters, one summary per role: the largest edit of any
    # of its documents, and whether any touched formatting. Previews are his
    # review of the composer, not of a batch, so they are left out.
    edits_read = True
    edited: dict[str, dict] = {}
    try:
        for e in db_get(cfg, "hunter_doc_edits", {"select": "job_id,edit_chars,style_changed,scratch",
                                                  "limit": ALL_ROWS}):
            if e.get("scratch"):
                continue
            d = edited.setdefault(e.get("job_id") or "", {"chars": 0, "style": False})
            d["chars"] = max(d["chars"], int(e.get("edit_chars") or 0))
            d["style"] = d["style"] or bool(e.get("style_changed"))
    except Exception as e:
        edits_read = False
        if notes is not None:
            notes.append(f"letter edits by batch not measured: hunter_doc_edits not read "
                         f"({e.__class__.__name__})")
    from .package import style as style_mod
    tiers = tiers or {}
    batches: dict[str, Batch] = {}
    for r in roles:
        # presented_at, not status. A row's status keeps moving after he
        # sees it (dead, duplicate, applied), so filtering on "staging"
        # counted only the rows still awaiting a verdict and reported the
        # 6 August batch at 100 percent because its declines had moved on.
        # presented_at is set once, when the row reached his sheet, which is
        # the only moment that defines a batch.
        presented = (r.get("presented_at") or "")[:10]
        if not presented:
            continue
        key = presented
        b = batches.setdefault(key, Batch(key=key, ratings_read=ratings_read,
                                          shown_read=shown_read, edits_read=edits_read))
        b.staged += 1
        if style_mod.stamped(r):
            b.letters_built += 1
            ed = edited.get(r.get("job_id") or "")
            if ed:
                b.letters_edited += 1
                b.edit_chars.append(ed["chars"])
                b.formatting_edited += ed["style"]
        source = (r.get("source") or "unknown").strip() or "unknown"
        tier = tiers.get(company_key(r.get("company") or ""))
        tier_key = f"tier {tier}" if tier else "unscored"
        rating = rated.get(r.get("job_id") or "")
        if rating and rating.get("kind") == "scored" and rating.get("score") is not None:
            b.scores.append(int(rating["score"]))
            ai = rating.get("ai_score_then")
            if ai is not None:
                b.ai_pairs.append((int(ai), int(rating["score"])))
                if r.get("ai_score_shown") is not None and int(r["ai_score_shown"]) == int(ai):
                    b.prospective += 1
        verdict = ruled.get(r.get("job_id") or "") or (r.get("krish_verdict") or "")
        if not _is_ruled(verdict):
            continue
        b.verdicted += 1
        s = b.by_source.setdefault(source, {"verdicted": 0, "accepted": 0})
        tk = b.by_tier.setdefault(tier_key, {"verdicted": 0, "accepted": 0})
        s["verdicted"] += 1
        tk["verdicted"] += 1
        if _is_yes(verdict):
            b.accepted += 1
            s["accepted"] += 1
            tk["accepted"] += 1
    return [batches[k] for k in sorted(batches)]


RATING_COLUMNS = ("scored", "mean_score", "share_7_plus", "spearman_ai", "spearman_n",
                  "prospective_n")
# What his edits say about the letters, from the control-center migration of
# 2026-10-15; a server that does not have them yet is treated as the Krish
# Score columns are.
EDIT_COLUMNS = ("letters_built", "letters_edited", "edit_chars_median", "formatting_edited")


class NotSaved(RuntimeError):
    pass


def _round(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def _row(b: Batch, now: str) -> dict:
    """One batch as stored. A measurement that did not read his scores, or
    did not read ai_score_shown, leaves those columns out, so the merge
    upsert keeps what an earlier run observed rather than writing zero
    scored over it."""
    row = {"batch_key": b.key, "staged": b.staged, "verdicted": b.verdicted,
           "accepted": b.accepted,
           "accept_rate": _round(b.rate),
           "by_source": b.by_source, "by_tier": b.by_tier, "measured_at": now}
    if b.ratings_read:
        row.update({"scored": b.scored, "mean_score": _round(b.mean_score),
                    "share_7_plus": _round(b.share_7_plus),
                    "spearman_ai": _round(b.spearman_ai), "spearman_n": b.spearman_n})
        if b.shown_read:
            row["prospective_n"] = b.prospective
    if b.edits_read:
        row.update({"letters_built": b.letters_built, "letters_edited": b.letters_edited,
                    "edit_chars_median": b.edit_chars_median,
                    "formatting_edited": b.formatting_edited})
    return row


def _names_rating_column(e: Exception) -> bool:
    """The server refused a Krish Score column by name: PostgREST's PGRST204
    "Could not find the 'scored' column", or Postgres's 42703 'column
    "scored" ... does not exist'. Nothing else is evidence that the migration
    is missing."""
    body = getattr(getattr(e, "response", None), "text", "") or str(e)
    return any(f"'{c}'" in body or f'"{c}"' in body for c in RATING_COLUMNS + EDIT_COLUMNS)


def save(cfg: Config, batches: list[Batch]) -> str:
    """Write every batch. Returns "" when all of it landed, or the sentence
    saying what did not; raises NotSaved when nothing did.

    The caller used to catch any failure here and print "accept rate not
    measured", which was false (it was measured, and printed) and hid the
    failed write. The Krish Score columns arrive with a control-center
    migration; while the server names one of them as missing, the accept
    rate is written without them and the summary says so. Any other failure
    is that failure, and is raised as one."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [_row(b, now) for b in batches]
    if not rows:
        return ""
    try:
        db_insert(cfg, TABLE, rows, on_conflict="batch_key", merge=True)
        return ""
    except Exception as e:
        first = f"{e.__class__.__name__}: {str(e)[:160]}"
        if not _names_rating_column(e):
            raise NotSaved(f"{TABLE} NOT written ({first})") from None
    try:
        db_insert(cfg, TABLE, [{k: v for k, v in r.items()
                                if k not in RATING_COLUMNS + EDIT_COLUMNS}
                               for r in rows], on_conflict="batch_key", merge=True)
    except Exception as e:
        raise NotSaved(f"{TABLE} NOT written ({first}; and without the Krish Score "
                       f"columns: {e.__class__.__name__}: {str(e)[:160]})") from None
    return (f"{TABLE} written without the Krish Score columns ({first}); the "
            f"control-center migration that adds them has not been applied")


def trend(batches: list[Batch], limit: int = 8) -> str:
    """The line itself, in the words the weekly email uses."""
    settled = [b for b in batches if b.settled][-limit:]
    if not settled:
        return "no batch has enough verdicts yet to have an accept rate"
    parts = [f"{b.key[5:]} {round(100 * b.rate)}%" for b in settled]
    return "accept rate by batch: " + ", ".join(parts)


def score_trend(batches: list[Batch], limit: int = 8) -> str:
    """His Krish Score by batch, and whether hunter's AI score ranked the
    batch the way he did."""
    if any(not b.ratings_read for b in batches):
        return ("Krish Score by batch not measured this run: hunter_krish_ratings "
                "was not read")
    settled = [b for b in batches if b.score_settled][-limit:]
    if not settled:
        return "no batch has enough Krish Scores yet to have a share"
    parts = []
    for b in settled:
        rho = (f", AI rho {b.spearman_ai:+.2f} over {b.spearman_n}"
               + (f" ({b.prospective} prospective)" if b.prospective else "")
               if b.spearman_ai is not None else "")
        parts.append(f"{b.key[5:]} {sum(1 for x in b.scores if x >= GOOD_SCORE)}/{b.scored} "
                     f"at {GOOD_SCORE}+ (mean {b.mean_score:.1f}{rho})")
    return "Krish Score by batch: " + "; ".join(parts)


def column_a_line(batches: list[Batch], limit: int = 8) -> str:
    """How many column A verdicts each batch drew. G12, the employer
    approvals and the Yes and No on the Target Companies tab learn from
    column A alone; as he scores in K instead, this falls toward zero and
    those stop learning. The line makes that visible rather than silent."""
    recent = [b for b in batches if b.staged][-limit:]
    if not recent:
        return ""
    return ("column A verdicts by batch (what G12 and the Target Companies Yes and "
            "No learn from): " + ", ".join(f"{b.key[5:]} {b.verdicted}/{b.staged}"
                                           for b in recent))


def score_falling(batches: list[Batch]) -> str:
    """The Krish Score's drift alarm: fewer than SCORE_FLOOR of the roles he
    scored at 7 or more, for BATCHES_CONSIDERED settled batches running."""
    recent = [b for b in batches if b.score_settled][-BATCHES_CONSIDERED:]
    if len(recent) < BATCHES_CONSIDERED:
        return ""
    if any(b.share_7_plus >= SCORE_FLOOR for b in recent):
        return ""
    worst = ", ".join(f"{b.key} at {round(100 * b.share_7_plus)}%" for b in recent)
    return (f"Fewer than {round(100 * SCORE_FLOOR)}% of the roles you scored got "
            f"{GOOD_SCORE} or more for {len(recent)} batches ({worst}). The sourcing "
            f"is filling the sheet with roles you do not want, and the fix is "
            f"upstream of the sheet.")


def falling(batches: list[Batch]) -> str:
    """The sentence to put in front of him, or an empty string: the accept
    rate's alarm, his Krish Score's, or both.

    Empty means the funnel is doing its job, and an empty string is the
    honest answer: this raises nothing when there is nothing to raise.
    """
    return " ".join(x for x in (accept_falling(batches), score_falling(batches)) if x)


def accept_falling(batches: list[Batch]) -> str:
    settled = [b for b in batches if b.settled]
    recent = settled[-BATCHES_CONSIDERED:]
    if len(recent) < BATCHES_CONSIDERED:
        return ""
    if any(b.rate >= FLOOR for b in recent):
        return ""
    worst = ", ".join(f"{b.key} at {round(100 * b.rate)}%" for b in recent)
    return (f"Accept rate has been below {round(100 * FLOOR)}% for "
            f"{len(recent)} batches ({worst}). That is the shape of the "
            f"drift in September: the sourcing is filling the sheet with "
            f"roles you do not want, and the fix is upstream of the sheet.")


def lines(batches: list[Batch]) -> list[str]:
    """What the run summary says about the funnel."""
    out = [trend(batches), score_trend(batches), column_a_line(batches)]
    settled = [b for b in batches if b.settled]
    if settled:
        last = settled[-1]
        best = sorted(last.by_tier.items(),
                      key=lambda kv: -(kv[1]["accepted"] / max(1, kv[1]["verdicted"])))
        if best:
            out.append("last batch by company tier: " + ", ".join(
                f"{k} {v['accepted']}/{v['verdicted']}" for k, v in best))
    warn = falling(batches)
    if warn:
        out.append(warn)
    return [x for x in out if x]


def by_source(batches: list[Batch]) -> list[tuple[str, int, int]]:
    """[(source, accepted, verdicted)] across every batch, worst rate last.

    measure() has collected this since the day it was written and nothing ever
    printed it. Krish, 2026-09-24: "there are still way too many boring
    financial services and healthcare jobs being put in here". Which supply leg
    is producing them is the whole question, and the answer was already in
    memory, thrown away at the end of every run.
    """
    total: dict[str, dict] = {}
    for b in batches:
        for src, v in b.by_source.items():
            t = total.setdefault(src, {"verdicted": 0, "accepted": 0})
            t["verdicted"] += v["verdicted"]
            t["accepted"] += v["accepted"]
    rows = [(s, v["accepted"], v["verdicted"]) for s, v in total.items()]
    # Most judged first, so a leg with two verdicts does not head the table.
    return sorted(rows, key=lambda r: (-r[2], r[0]))


def source_lines(batches: list[Batch]) -> list[str]:
    rows = by_source(batches)
    if not rows:
        return []
    out = ["accept rate by source, all batches:"]
    for src, acc, jud in rows:
        pct = f"{round(100 * acc / jud)}%" if jud else "no verdicts"
        out.append(f"  {src:<28} {acc:>3}/{jud:<4} {pct}")
    return out


def by_leg(batches: list[Batch], leg_of) -> list[tuple[str, int, int]]:
    """by_source, grouped into supply legs.

    The same leg has reached the sheet under four labels, which made it look
    like four small sources rather than the one carrying most of the funnel.
    leg_of is injected so batchstats stays free of run's imports.
    """
    total: dict[str, dict] = {}
    for b in batches:
        for src, v in b.by_source.items():
            leg = leg_of(src)
            t = total.setdefault(leg, {"verdicted": 0, "accepted": 0})
            t["verdicted"] += v["verdicted"]
            t["accepted"] += v["accepted"]
    rows = [(l, v["accepted"], v["verdicted"]) for l, v in total.items()]
    return sorted(rows, key=lambda r: (-r[2], r[0]))


def leg_lines(batches: list[Batch], leg_of) -> list[str]:
    rows = by_leg(batches, leg_of)
    if not rows:
        return []
    judged = sum(r[2] for r in rows)
    out = [f"accept rate by supply leg ({judged} verdicts in total):"]
    for leg, acc, jud in rows:
        pct = f"{round(100 * acc / jud)}%" if jud else "no verdicts"
        share = f"{round(100 * jud / judged)}%" if judged else "-"
        out.append(f"  {leg:<24} {acc:>3}/{jud:<4} {pct:>5}   {share:>4} of the funnel")
    return out
