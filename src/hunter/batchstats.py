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

TABLE = "hunter_batch_stats"

# Below this, across two batches he has finished ruling on, something is
# wrong with the funnel rather than with one week's luck. Chosen from his
# own record: his four best batches ran 77, 55, 50 and 43 percent, and the
# collapse quarters he complained about ran 27, 25, 15.
FLOOR = 0.35
# One batch can be unlucky. Two in a row is a trend, and waiting for a third
# is six more weeks of his life.
BATCHES_CONSIDERED = 2
# A batch he has barely started judging says nothing yet.
MIN_VERDICTED = 6


@dataclass
class Batch:
    key: str                      # the sweep date, which is how a batch is identified
    staged: int = 0
    verdicted: int = 0
    accepted: int = 0
    by_source: dict = field(default_factory=dict)
    by_tier: dict = field(default_factory=dict)

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


def _is_yes(verdict: str) -> bool:
    return (verdict or "").strip().lower() in ("yes", "go", "approved")


def _is_ruled(verdict: str) -> bool:
    v = (verdict or "").strip().lower()
    return bool(v) and not v.startswith("new")


def measure(cfg: Config, tiers: dict[str, int] | None = None) -> list[Batch]:
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
    roles = db_get(cfg, "hunter_seen_roles",
                   {"select": "job_id,company,sweep_date,presented_at,status,"
                              "krish_verdict,source", "limit": ALL_ROWS})
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
        b = batches.setdefault(key, Batch(key=key))
        b.staged += 1
        source = (r.get("source") or "unknown").strip() or "unknown"
        tier = tiers.get(company_key(r.get("company") or ""))
        tier_key = f"tier {tier}" if tier else "unscored"
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


def save(cfg: Config, batches: list[Batch]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"batch_key": b.key, "staged": b.staged, "verdicted": b.verdicted,
             "accepted": b.accepted,
             "accept_rate": round(b.rate, 4) if b.rate is not None else None,
             "by_source": b.by_source, "by_tier": b.by_tier,
             "measured_at": now}
            for b in batches]
    if rows:
        db_insert(cfg, TABLE, rows, on_conflict="batch_key", merge=True)


def trend(batches: list[Batch], limit: int = 8) -> str:
    """The line itself, in the words the weekly email uses."""
    settled = [b for b in batches if b.settled][-limit:]
    if not settled:
        return "no batch has enough verdicts yet to have an accept rate"
    parts = [f"{b.key[5:]} {round(100 * b.rate)}%" for b in settled]
    return "accept rate by batch: " + ", ".join(parts)


def falling(batches: list[Batch]) -> str:
    """The sentence to put in front of him, or an empty string.

    Empty means the funnel is doing its job, and an empty string is the
    honest answer: this raises nothing when there is nothing to raise.
    """
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
    out = [trend(batches)]
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
