"""What employers said back, read from his mailbox and matched to applications.

Until 2026-11-04 nothing in hunter knew whether an application got an answer.
confirmations.py reads "thanks for applying" receipts and deliberately skips
rejections; the only other writer was a button he had to press. So "0 percent
first interviews" could not be split into rejections and silence, and nothing
could say which kind of application did better.

The same narrowness as confirmations.py: this never invents an application. It
reads replies to roles already on record as applied, matches them by company,
and only after the date he applied. A message that reads as both a rejection
and an invitation is a rejection ("we will not be scheduling an interview").
A message that reads as neither is not counted, never guessed.

Silence is a result too, and it is computed, not written: an application with
no reply after SILENT_AFTER_DAYS is reported as silent.
"""
from __future__ import annotations

import datetime as dt
import re
from email.utils import parsedate_to_datetime

from . import confirmations

REJECTED = "rejected"
NEXT_STEP = "next_step"
RECEIPT = "receipt"

# What his record calls each, the same values the Control Center outcome
# button writes (actions.OUTCOMES).
STATE = {REJECTED: "Rejected by employer", NEXT_STEP: "Interviewing"}
APPLIED = "Applied"
# A state this module may move a row out of. An Offer or a Withdrew he
# recorded himself is never overwritten, and an Interviewing row only ever
# moves on to a rejection.
MOVES = {APPLIED: {REJECTED, NEXT_STEP}, "submitted": {REJECTED, NEXT_STEP},
         "Interviewing": {REJECTED}}

SILENT_AFTER_DAYS = 21

# Replies only, never a broad mailbox read.
QUERY = ('(unfortunately OR "not moving forward" OR "move forward" OR "moving forward" '
         'OR "next steps" OR interview OR "schedule a call" OR "your availability" '
         'OR "other candidates" OR "your application" OR "thank you for applying" '
         'OR "thanks for applying") newer_than:120d -from:me')

REJECTION = confirmations.NOT_ACK + (
    "other candidates", "another candidate", "not be moving forward",
    "won't be moving forward", "will not be moving forward", "not to move forward",
    "not to proceed", "not be progressing", "position has been filled",
    "role has been filled", "we regret", "regret to inform", "not selected",
    "pursue other", "not a match", "not the right fit", "decided to move forward with",
    "not able to offer", "closed the role", "no longer hiring",
)
INVITATION = (
    "schedule a call", "schedule an interview", "schedule a time", "set up a call",
    "set up a time", "your availability", "times that work", "time that works",
    "would love to chat", "would like to chat", "love to speak", "like to speak with you",
    "invite you to", "next step in our process", "next stage", "move you forward",
    "moving you forward", "advance you", "calendly.com", "book a time", "phone screen",
    "recruiter screen", "first round", "hiring manager would like",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def classify(subject: str, snippet: str, sender: str) -> str | None:
    """REJECTED, NEXT_STEP, RECEIPT, or None when the words say none of them.

    Hunter's own mail is never an employer's reply.
    """
    if "[hunter" in (subject or "").lower() or "[hunter-outbound]" in _norm(snippet):
        return None
    hay = f"{_norm(subject)} {_norm(snippet)}"
    if any(p in hay for p in REJECTION):
        return REJECTED
    if any(p in hay for p in INVITATION):
        return NEXT_STEP
    if confirmations.looks_like_ack(subject, snippet, sender):
        return RECEIPT
    return None


def _when(msg: dict) -> dt.datetime | None:
    try:
        got = parsedate_to_datetime(msg.get("date") or "")
    except (TypeError, ValueError):
        return None
    if got is None:
        return None
    return got if got.tzinfo else got.replace(tzinfo=dt.timezone.utc)


def _applied(row: dict) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(row.get("applied_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _title_hit(title: str, subject: str, snippet: str) -> bool:
    words = {w for w in re.findall(r"[a-z]+", _norm(title)) if len(w) > 3}
    hay = f"{_norm(subject)} {_norm(snippet)}"
    return bool(words) and sum(w in hay for w in words) >= min(2, len(words))


def match(messages: list[dict], applied: list[dict]) -> list[tuple[dict, dict, str]]:
    """(message, row, kind) for each reply that answers exactly one application.

    A company with two applications is matched only when the reply names the
    role, so one rejection never closes the wrong one. A reply dated before he
    applied answers something else.
    """
    by_company: dict[str, list[dict]] = {}
    for r in applied:
        by_company.setdefault(_norm(r.get("company")), []).append(r)
    out: list[tuple[dict, dict, str]] = []
    for msg in messages:
        kind = classify(msg["subject"], msg["snippet"], msg["sender"])
        if kind is None:
            continue
        got = _when(msg)
        for _, rows in by_company.items():
            cands = [r for r in rows
                     if confirmations.matches_company(r.get("company") or "", msg["subject"],
                                                      msg["snippet"], msg["sender"])]
            if not cands:
                continue
            if got is not None:
                cands = [r for r in cands if (_applied(r) is None or got >= _applied(r))]
            if len(cands) > 1:
                cands = [r for r in cands
                         if _title_hit(r.get("title") or "", msg["subject"], msg["snippet"])]
            if len(cands) == 1:
                out.append((msg, cands[0], kind))
    return out


# Stronger news wins when one application has several replies.
_RANK = {RECEIPT: 0, REJECTED: 1, NEXT_STEP: 2}


def outcome_by_job(pairs: list[tuple[dict, dict, str]]) -> dict[str, str]:
    """One outcome per application: the latest decision, an invitation over a
    receipt. A rejection dated after an invitation is the later word."""
    best: dict[str, tuple[dt.datetime, int, str]] = {}
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    for msg, row, kind in pairs:
        jid = row["job_id"]
        when = _when(msg) or floor
        key = (when if kind != RECEIPT else floor, _RANK[kind], kind)
        if jid not in best or key[:2] > best[jid][:2]:
            best[jid] = key
    return {j: k[2] for j, k in best.items()}


def may_move(current: str | None, kind: str) -> bool:
    return kind in MOVES.get((current or "").strip(), set())


def bucket_days(days: float | None) -> str:
    if days is None:
        return "unknown"
    if days <= 3:
        return "0-3 days"
    if days <= 10:
        return "4-10 days"
    return "11+ days"


def result_of(row: dict, found: str | None, now: dt.datetime) -> str:
    """What one application has come to, for the report."""
    state = (row.get("application_state") or "").strip()
    if found == NEXT_STEP or state in ("Interviewing", "Offer"):
        return "next step"
    if found == REJECTED or state == "Rejected by employer":
        return "rejected"
    applied = _applied(row)
    if applied and (now - applied).days >= SILENT_AFTER_DAYS:
        return "silent"
    return "too early"


RESULTS = ("next step", "rejected", "silent", "too early")


def report(rows: list[dict], found: dict[str, str], now: dt.datetime) -> list[str]:
    """Counts only: the repo and its logs are public."""
    def days_to_apply(r):
        a = _applied(r)
        seen = r.get("presented_at") or r.get("scanned_at")
        try:
            s = dt.datetime.fromisoformat(str(seen).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (a - s).total_seconds() / 86400 if a else None

    cuts = {
        "all": lambda r: "all",
        "speed (shown to applied)": lambda r: bucket_days(days_to_apply(r)),
        "named contact on the sheet": lambda r: "yes" if (r.get("warm_path_person") or "").strip() else "no",
        "house style": lambda r: "yes" if r.get("package_style") else "no",
    }
    lines = []
    for name, key in cuts.items():
        groups: dict[str, dict[str, int]] = {}
        for r in rows:
            g = groups.setdefault(key(r), {k: 0 for k in RESULTS})
            g[result_of(r, found.get(r["job_id"]), now)] += 1
        lines.append(name)
        for g, c in sorted(groups.items()):
            total = sum(c.values())
            lines.append(f"  {g}: {total} applied, " + ", ".join(f"{c[k]} {k}" for k in RESULTS))
    return lines
