"""When a full run is due, decided from the record, not from the cron clock.

GitHub's scheduler is best effort and on this repository it ran hours late.
Sunday 27 September: the 12:00 UTC entry started at 15:54, the London-clock
guard in the workflow read "16:00, not 13:00" and skipped it, and the 13:00
entry never fired at all. No batch that week, and nothing said so. Thursday
1 October started seven hours late.

So the slot is no longer something a tick has to land on. Every drain, from
whichever trigger fires it (Control Center's on-the-minute cron, or the GitHub
fallback), asks one question: has the most recent slot had its full run? If
not, this drain runs it. A late trigger delays a batch by an hour; it can no
longer drop one.

Three things stop that from becoming a loop that pays for the same sweep over
and over:

- a successful full run since the slot means done, whoever started it;
- a run already in progress since the slot means wait, unless it has been
  "running" for longer than any real run takes (a killed job leaves its row
  behind);
- two attempts at one slot is the limit. A third would be paying again for a
  failure nobody has looked at, so it stops and the watchdog says so.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
UTC = datetime.timezone.utc

# Krish's two batches. Sunday 13:00 in London (moved from Monday on
# 2026-09-19 so the batch waits for him at the start of the week), and
# Thursday 08:27 UTC.
SUNDAY, THURSDAY = 6, 3
SUNDAY_LOCAL = datetime.time(13, 0)
THURSDAY_UTC = datetime.time(8, 27)

# The longest a real full run has taken is about 65 minutes; the job itself is
# killed at the workflow's timeout. A row still "running" after this is a
# corpse, not a run.
STALE_AFTER = datetime.timedelta(hours=3)
MAX_ATTEMPTS = 2


def slots_between(start: datetime.datetime, end: datetime.datetime) -> list[datetime.datetime]:
    """Every slot instant in [start, end], in UTC, oldest first."""
    out = []
    day = (start - datetime.timedelta(days=1)).date()
    while day <= end.date() + datetime.timedelta(days=1):
        if day.weekday() == SUNDAY:
            local = datetime.datetime.combine(day, SUNDAY_LOCAL, tzinfo=LONDON)
            out.append(local.astimezone(UTC))
        if day.weekday() == THURSDAY:
            out.append(datetime.datetime.combine(day, THURSDAY_UTC, tzinfo=UTC))
        day += datetime.timedelta(days=1)
    return sorted(s for s in out if start <= s <= end)


def latest_slot(now: datetime.datetime) -> datetime.datetime:
    """The most recent slot at or before now."""
    return slots_between(now - datetime.timedelta(days=8), now)[-1]


def _ts(value) -> datetime.datetime | None:
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        got = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Decision:
    due: bool
    slot: datetime.datetime
    why: str
    stuck: bool = False   # the attempt limit was hit; the watchdog reports it


def decide(now: datetime.datetime, commands: list[dict],
           full_runs: list[dict]) -> Decision:
    """Is the latest slot's full run still owed?

    commands: hunter_commands rows for command 'run' (requested_at, state,
    started_at). full_runs: workflow_runs rows that were full runs (run_at,
    status). Either list may include rows from before the slot; they are
    ignored.
    """
    slot = latest_slot(now)
    for r in full_runs:
        at = _ts(r.get("run_at"))
        if at and at >= slot and str(r.get("status") or "").lower() == "success":
            return Decision(False, slot, f"the {_label(slot)} run finished at {at:%a %H:%M} UTC")
    mine = [c for c in commands if (_ts(c.get("requested_at")) or slot) >= slot]
    for c in mine:
        if c.get("state") == "done":
            return Decision(False, slot, f"the {_label(slot)} run is recorded as done")
    for c in mine:
        if c.get("state") in ("running", "queued"):
            began = _ts(c.get("started_at")) or _ts(c.get("requested_at")) or now
            if now - began < STALE_AFTER:
                return Decision(False, slot, f"the {_label(slot)} run is in progress "
                                             f"since {began:%H:%M} UTC")
    if len(mine) >= MAX_ATTEMPTS:
        return Decision(False, slot, f"the {_label(slot)} run has failed {len(mine)} times; "
                                     f"not paying for a third attempt until someone looks",
                        stuck=True)
    return Decision(True, slot, f"the {_label(slot)} run is owed"
                                + (f" (attempt {len(mine) + 1} of {MAX_ATTEMPTS})" if mine else ""))


def _label(slot: datetime.datetime) -> str:
    return f"{slot.astimezone(LONDON):%a %d %b %H:%M} London"
