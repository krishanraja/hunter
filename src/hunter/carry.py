"""Roles the judge never reached, brought back to it.

A role the judge left pending (judge_pending: over a run's limit or its
budget) was judged again only when a later sweep happened to find the same
posting again. A LinkedIn keyword sweep rarely does, so they piled up: 395 on
2026-11-05, the oldest from 27 August, and a NYC VP role scored 8 had waited
four weeks without being read by anything. Read back that day, 213 of the 395
postings had closed.

Each sourcing run now takes up to `hunter_judge_carry_per_run` of them
(default 40, 0 is off), reads each posting where it lives, records a closed one
as dead, and hands the open ones to the judge beside the run's own roles,
ranked with them. A posting it could not read stays pending: unknown is not
closed.

Which 40: those with posting text to judge, not read in the last
RECHECK_DAYS, in an order shuffled by the day. The first version took the
least recently read first, and on its first run 27 of the 40 were rows it
could not use (no posting text, or LinkedIn refusing the read); nothing marks
those read, so they would have headed every run and kept the rest waiting.
"""
from __future__ import annotations

import datetime
import hashlib
import re

from .config import ALL_ROWS, Config, db_get, db_patch

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"
RECHECK_DAYS = 3
MIN_TEXT = 200

# Every column the staging insert writes, so the merge that writes a carried
# row back never nulls one it left out (db_insert fills gaps with NULL).
COLUMNS = ("job_id,title,company,url,job_url,score,employer_url,status,"
           "auto_rejected,rejection_reason,source,location,comp,sweep_date,"
           "why_it_fits,jd_text,last_verified_at,posted_at,posted_source,"
           "scanned_at,presented_at")

_LI_NUMBER = re.compile(r"linkedin\.com/jobs/view/(?:[^/?]*?-)?(\d{8,})")
_LI_CLOSED = "No longer accepting applications"
LI_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


def linkedin_state(status: int, html: str) -> str:
    """The public posting page, read as it stands. A 404 or 410 is a removed
    posting; anything else unreadable (429, 5xx) is unknown."""
    if status == 200:
        return CLOSED if _LI_CLOSED in (html or "") else OPEN
    return CLOSED if status in (404, 410) else UNKNOWN


def posting_state(row: dict, *, board_key, fetchers: dict, get) -> str:
    """OPEN, CLOSED or UNKNOWN for one stored role, read from its own board.

    The employer's link first, then the posting link: a Greenhouse, Ashby or
    Lever posting is asked of its board's API; a LinkedIn one of its public
    page. `get(url) -> (status, text)` and the fetchers are injected, so the
    tests never open a socket."""
    for link in (row.get("employer_url"), row.get("job_url"), row.get("url")):
        key = board_key(link)
        if key and key[0] in fetchers:
            try:
                live, _jd, _url = fetchers[key[0]](key[1], key[2])
            except Exception:
                return UNKNOWN
            return OPEN if live else CLOSED
    for link in (row.get("job_url"), row.get("url")):
        m = _LI_NUMBER.search(link or "")
        if m:
            try:
                status, html = get(LI_GUEST.format(m.group(1)))
            except Exception:
                return UNKNOWN
            return linkedin_state(status, html)
    return UNKNOWN


def pending_rows(cfg: Config, *, limit: int, exclude: set, today: str = "") -> list[dict]:
    """Pending roles he has never been shown, with posting text, not read in
    the last RECHECK_DAYS, shuffled by the day so every one is reached in turn
    and one that cannot be read never blocks the rest."""
    if limit <= 0:
        return []
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": COLUMNS, "status": "eq.judge_pending",
        "presented_at": "is.null", "jd_text": "not.is.null", "limit": ALL_ROWS})
    day = today or datetime.date.today().isoformat()
    try:
        cutoff = (datetime.date.fromisoformat(day[:10])
                  - datetime.timedelta(days=RECHECK_DAYS)).isoformat()
    except ValueError:
        cutoff = ""
    fit = [r for r in rows if r["job_id"] not in exclude
           and len((r.get("jd_text") or "").strip()) >= MIN_TEXT
           and (r.get("last_verified_at") or "")[:10] <= cutoff]
    fit.sort(key=lambda r: hashlib.sha1(f"{day}:{r['job_id']}".encode()).hexdigest())
    return fit[:limit]


def carry(cfg: Config, *, limit: int, exclude: set, board_key, fetchers: dict, get,
          today: str, apply: bool = True) -> tuple[list[dict], dict]:
    """The open pending rows to judge again, and the counts. A closed one is
    recorded dead with the date it was read, never left to wait again."""
    counts = {OPEN: 0, CLOSED: 0, UNKNOWN: 0}
    out = []
    for row in pending_rows(cfg, limit=limit, exclude=exclude, today=today):
        state = posting_state(row, board_key=board_key, fetchers=fetchers, get=get)
        counts[state] += 1
        if state == OPEN:
            out.append(row)
        elif state == CLOSED and apply:
            db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                     {"status": "dead",
                      "rejection_reason": f"posting closed, read {today}, never judged"})
    return out, counts


def carry_line(counts: dict, limit: int) -> str:
    return (f"judge carry-over: {sum(counts.values())} pending role(s) read of "
            f"{limit} a run: {counts[OPEN]} open and judged with this run, "
            f"{counts[CLOSED]} closed and recorded dead, {counts[UNKNOWN]} unknown, "
            f"left pending")
