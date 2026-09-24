"""Employer acknowledgements, read from the mailbox, matched to applications.

The step Krish would otherwise do by hand and would forget: telling hunter that
an application he pressed himself actually went. He should not have to. Every ATS
sends "thanks for applying" within a minute or two, and that email is better
evidence than anything hunter can observe from its own side, because it comes
from the employer rather than from the browser that pressed the button.

Deliberately narrow. This never decides that an application happened; it only
recognises an acknowledgement for one hunter already has on record as approved or
pressed, and matches it by company name. An email from a company with no live
application is not evidence of anything and is ignored.
"""
from __future__ import annotations

import re

import requests

from ..config import Config, GoogleOAuth

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"

# Scoped to the shape of an acknowledgement, never a broad mailbox read: hunter
# reads Krish's mail and should see as little of it as the job allows.
QUERY = ('(subject:(application OR applying OR applied OR "we received") '
         'OR "thank you for applying" OR "thanks for applying" '
         'OR "received your application" OR "application received") '
         'newer_than:14d')

# The words an acknowledgement actually uses. Checked against the subject and the
# snippet, so a rejection weeks later does not read as a receipt.
ACK_PHRASES = (
    "thank you for applying", "thanks for applying",
    "received your application", "application received",
    "we have received your application", "we've received your application",
    "your application has been received", "application submitted",
    "thanks for your application", "thank you for your application",
    "thanks for your interest", "thank you for your interest",
)

# A reply that is NOT a receipt, however friendly the wording.
NOT_ACK = ("unfortunately", "not moving forward", "will not be progressing",
           "decided not to", "unsuccessful", "no longer under consideration")


class ConfirmError(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def company_tokens(company: str) -> set[str]:
    """The words that identify an employer, minus the ones every company shares.

    "Harvey" matches harvey.ai and Harvey's Ashby sender; "Inc" matches nothing
    useful and would match everything.
    """
    stop = {"inc", "ltd", "llc", "limited", "corp", "corporation", "group",
            "holdings", "the", "co", "plc", "gmbh", "sa", "ag", "labs", "ai",
            "technologies", "tech", "software", "company"}
    words = re.findall(r"[a-z0-9]+", _norm(company))
    return {w for w in words if len(w) > 2 and w not in stop}


def looks_like_ack(subject: str, snippet: str, sender: str) -> bool:
    """An acknowledgement, not a rejection and not hunter's own mail."""
    hay = f"{_norm(subject)} {_norm(snippet)}"
    if "[hunter #" in (subject or "") or "[hunter-outbound]" in hay:
        return False
    if any(n in hay for n in NOT_ACK):
        return False
    return any(p in hay for p in ACK_PHRASES)


def matches_company(company: str, subject: str, snippet: str, sender: str) -> bool:
    tokens = company_tokens(company)
    if not tokens:
        return False
    hay = f"{_norm(subject)} {_norm(snippet)} {_norm(sender)}"
    return any(t in hay for t in tokens)


def fetch(cfg: Config, *, query: str = QUERY, limit: int = 40) -> list[dict]:
    """[{message_id, subject, snippet, sender, date}] for likely acknowledgements."""
    token = GoogleOAuth(cfg).access_token()
    h = {"Authorization": "Bearer " + token}
    r = requests.get(f"{GMAIL}/messages", headers=h, timeout=45,
                     params={"q": query, "maxResults": limit})
    if r.status_code == 403:
        raise ConfirmError(
            "Gmail read is not granted yet. Run: python -m hunter.oauth_grant")
    r.raise_for_status()
    out = []
    for stub in r.json().get("messages", []) or []:
        m = requests.get(f"{GMAIL}/messages/{stub['id']}", headers=h, timeout=45,
                         params={"format": "metadata",
                                 "metadataHeaders": ["Subject", "From", "Date"]})
        if m.status_code != 200:
            continue
        msg = m.json()
        headers = {x["name"].lower(): x["value"]
                   for x in (msg.get("payload", {}).get("headers") or [])}
        out.append({"message_id": msg["id"],
                    "subject": headers.get("subject", ""),
                    "sender": headers.get("from", ""),
                    "date": headers.get("date", ""),
                    "snippet": msg.get("snippet", "")})
    return out


def match(messages: list[dict], open_rows: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each acknowledgement with the application it acknowledges.

    One message answers at most one application, and an application is answered
    once: two roles at the same company would otherwise both be closed by a single
    receipt for one of them.
    """
    used: set[str] = set()
    pairs: list[tuple[dict, dict]] = []
    for msg in messages:
        if not looks_like_ack(msg["subject"], msg["snippet"], msg["sender"]):
            continue
        for row in open_rows:
            if row["token"] in used:
                continue
            if matches_company(row.get("company") or "", msg["subject"],
                               msg["snippet"], msg["sender"]):
                pairs.append((msg, row))
                used.add(row["token"])
                break
    return pairs


def mailbox(cfg: Config) -> dict:
    """Which inbox is actually being read, and how much is in it.

    On 2026-09-24 this step reported "0 candidate message(s)" against twelve
    open applications. Twelve applications produce twelve employer receipts, so
    zero meant the search was looking somewhere other than where they land, and
    nothing in the output said where that was. An address and a total make the
    difference between an empty inbox and the wrong one visible at a glance.
    """
    token = GoogleOAuth(cfg).access_token()
    h = {"Authorization": "Bearer " + token}
    out = {"address": "", "total": -1, "error": ""}
    try:
        r = requests.get(f"{GMAIL}/profile", headers=h, timeout=30)
        if r.status_code == 403:
            out["error"] = ("Gmail read is not granted for this token; "
                            "run python -m hunter.oauth_grant")
            return out
        r.raise_for_status()
        j = r.json()
        out["address"] = j.get("emailAddress", "")
        out["total"] = int(j.get("messagesTotal", -1))
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {str(e)[:120]}"
    return out
