"""Reading Krish's replies, and deciding approve versus amend.

THE RULE THIS MODULE EXISTS TO ENFORCE: approval is an exact match on the word
APPROVE, alone on the first line Krish actually wrote. There is no model call
here and no intent inference. "Approved!", "approve please", "yes go ahead" and
an empty reply all fail to approve, and a reply that fails to approve is treated
as feedback or reported, never as consent.

That asymmetry is deliberate. Misreading feedback as feedback costs a round trip.
Misreading anything as approval sends an application in his name that he never
agreed to, so the ambiguous case must always fall on the safe side.

Quoted text is stripped before parsing, because the previous email's own body
contains the word APPROVE in its instructions, and a naive substring search over
a reply would approve every single time.
"""
from __future__ import annotations

import base64
import re

import requests

from ..config import Config, GoogleOAuth
from . import approval

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"

# One query, scoped to hunter's own subject token. Never a broad mailbox read.
QUERY = 'subject:"[hunter #" newer_than:14d'

APPROVE = "APPROVE"

# hunter sends FROM Krish's own account TO the same mailbox, so its own approval
# email comes back on the next poll looking exactly like a reply from him: the
# sender check passes and the body is long enough to read as an instruction.
# Unattended that is an endless rebuild-and-resend loop into his inbox, found by
# the first live send on 2026-09-14. Two independent guards, because one of them
# failing silently is what the loop would cost:
#   1. the send records its own Gmail message id on the approval row, which is
#      exact
#   2. this marker sits in every outbound body, which survives a lost id
# A genuine reply never carries the marker in Krish's own lines, because quoted
# text is stripped before parsing.
OUTBOUND_MARKER = "[hunter-outbound]"

# Below both thresholds a reply carries no instruction to act on, so it is
# reported rather than triggering a rebuild. Never affects whether it approves.
MIN_FEEDBACK_WORDS = 3
MIN_FEEDBACK_CHARS = 16

# Lines a mail client adds or quotes, which are not Krish writing.
_QUOTE_PREFIX = re.compile(r"^\s*(>|\|)")
_QUOTE_HEADER = re.compile(
    r"^\s*(on .+wrote:|from:|sent:|to:|subject:|-{2,}\s*original message|"
    r"_{5,}|sent from my )", re.I)
_SIGNATURE = re.compile(r"^\s*(--\s*$|sent from my )", re.I)


class InboxError(RuntimeError):
    pass


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
        "utf-8", "replace")


def extract_plain(payload: dict) -> str:
    """The text/plain part, falling back to stripping tags off text/html."""
    def walk(part):
        mime = part.get("mimeType", "")
        body = (part.get("body") or {}).get("data")
        if mime == "text/plain" and body:
            return _b64(body)
        for sub in part.get("parts") or []:
            got = walk(sub)
            if got:
                return got
        if mime == "text/html" and body:
            return re.sub(r"<[^>]+>", " ", _b64(body))
        return ""
    return walk(payload or {})


def krishs_own_lines(body: str) -> list[str]:
    """Everything before the quoted original, with signatures dropped."""
    out: list[str] = []
    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip("  \t")
        if _QUOTE_PREFIX.match(raw) or _QUOTE_HEADER.match(line):
            break
        if _SIGNATURE.match(line):
            break
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return out


def is_our_own_email(body: str) -> bool:
    """True for a message hunter sent, judged on the lines Krish would have
    written rather than on the quoted original."""
    return any(OUTBOUND_MARKER in line for line in krishs_own_lines(body))


def read_instruction(body: str) -> tuple[str, str]:
    """(decision, feedback). decision is "approve", "amend" or "unclear".

    approve requires the first written line to be exactly APPROVE, case
    insensitive, with nothing else on it.
    """
    lines = [l for l in krishs_own_lines(body) if l.strip()]
    if not lines:
        return "unclear", ""
    first = lines[0].strip().strip(".!,;:")
    if first.upper() == APPROVE:
        # A bare APPROVE followed by more words is still an approval of this
        # application; the extra words are kept for the record but change nothing.
        return "approve", "\n".join(lines[1:]).strip()
    # Anything that merely mentions approval is NOT approval. This is the case
    # that must never resolve the other way.
    feedback = "\n".join(lines).strip()
    # A reply that is only the prefilled template with nothing filled in tells us
    # nothing, so it is not feedback either.
    stripped = re.sub(r"[-*\s]", "", feedback)
    boilerplate = re.sub(r"[-*\s]", "",
                         "Change the following, then resend for approval:"
                         "(Write freely. Your words are passed through verbatim.)")
    if not stripped or stripped == boilerplate:
        return "unclear", ""
    # "ok" is not an instruction. Treating it as feedback would burn a model call
    # and mail him a near-identical application, so a reply too short to act on is
    # reported instead. It still does not approve, which is the property that
    # matters; this only decides between amending and asking.
    if len(feedback.split()) < MIN_FEEDBACK_WORDS \
            and len(stripped) < MIN_FEEDBACK_CHARS:
        return "unclear", feedback
    return "amend", feedback


def fetch_replies(cfg: Config, *, query: str = QUERY,
                  limit: int = 50) -> list[dict]:
    """[{message_id, thread_id, subject, token, body, from_me}]."""
    token = GoogleOAuth(cfg).access_token()
    h = {"Authorization": "Bearer " + token}
    r = requests.get(f"{GMAIL}/messages", headers=h, timeout=45,
                     params={"q": query, "maxResults": limit})
    if r.status_code == 403:
        raise InboxError(
            "Gmail read is not granted yet. Run: python -m hunter.oauth_grant")
    r.raise_for_status()
    out = []
    for stub in r.json().get("messages", []) or []:
        m = requests.get(f"{GMAIL}/messages/{stub['id']}", headers=h, timeout=45,
                         params={"format": "full"})
        if m.status_code != 200:
            continue
        msg = m.json()
        headers = {x["name"].lower(): x["value"]
                   for x in (msg.get("payload", {}).get("headers") or [])}
        subject = headers.get("subject", "")
        tok = approval.token_from_subject(subject)
        if not tok:
            continue
        out.append({
            "message_id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "subject": subject,
            "token": tok,
            "from": headers.get("from", ""),
            "body": extract_plain(msg.get("payload") or {}),
            "labels": msg.get("labelIds") or [],
        })
    return out


def is_from_krish(sender: str) -> bool:
    """A reply only counts if Krish sent it. An approval has to come from him,
    not from anyone who learns a token."""
    low = (sender or "").lower()
    from ..notify import ALLOWED_RECIPIENTS
    return any(addr in low for addr in ALLOWED_RECIPIENTS)


def classify(reply: dict, row: dict) -> tuple[str, str]:
    """(action, detail). action in approve | amend | skip | reject."""
    if reply["message_id"] in (row.get("processed_message_ids") or []):
        return "skip", "already processed"
    if is_our_own_email(reply.get("body", "")):
        return "skip", "this is hunter's own outbound email, not a reply"
    if not is_from_krish(reply.get("from", "")):
        return "reject", f"reply is not from Krish: {reply.get('from', '')!r}"
    state = row.get("state")
    if state in (approval.SUBMITTED, approval.CANCELLED):
        return "skip", f"token is already {state}"
    decision, feedback = read_instruction(reply.get("body", ""))
    if decision == "approve":
        return "approve", feedback
    if decision == "amend":
        return "amend", feedback
    return "reject", "reply did not contain APPROVE or any feedback"
