"""The ONLY outbound module in the repo, by design and by guard test.

Krish's ruling 2026-09-02: Telegram is gone, email only.

History that matters for reading this file. Until 2026-09-14 this module only
called print(), on the basis that a Claude Routine mailed that stdout to Krish
as the run's completion notification. Those Routines were retired on 2026-09-07
and the runtime became GitHub Actions, so the summary had been landing in an
Actions log nobody reads. The channel was, in effect, disconnected.

Krish's ruling 2026-09-13: every application is emailed to him for approval
before anything is sent, with links to the CV and cover letter. Profile row 60
already said this ("never submit without screenshot approval, both gates"), so
this module now has a real channel: Gmail users.messages.send on the existing
OAuth plane.

THE RECIPIENT IS NOT A PARAMETER A CALLER CAN AIM. It is resolved from the
workbook's own Application Info Bank and anything else raises. The promise this
module makes is that it can only reach Krish, and a promise like that belongs in
code, not in a docstring. A guard test greps the tree for any other outbound
path, and this file is the one place allowed to have one.
"""
from __future__ import annotations

import base64
import re
from email.message import EmailMessage

import requests

from .config import Config, GoogleOAuth

GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# Krish's job search address, as Profile and the Info Bank both record it. The
# allowlist is deliberately tiny and hard coded: a typo in a sheet cell must not
# be able to redirect an approval email to a stranger.
ALLOWED_RECIPIENTS = frozenset({
    "hello@krishraja.com",
    "krishanraja@gmail.com",
})


class NotifyError(RuntimeError):
    pass


def send_summary(cfg: Config, text: str) -> dict:
    """Emit the run summary to stdout, which the Actions run carries."""
    print(text)
    return {"sent": True, "channel": "stdout, carried by the Actions run log"}


def _check_recipient(to: str) -> str:
    addr = (to or "").strip().lower()
    if addr not in ALLOWED_RECIPIENTS:
        raise NotifyError(
            f"refusing to send to {to!r}. This module can only reach Krish; "
            f"allowed: {sorted(ALLOWED_RECIPIENTS)}")
    return addr


def build_message(to: str, subject: str, html: str, *,
                  text: str | None = None) -> str:
    """A base64url encoded MIME message, which is what the Gmail API takes.

    Standard base64 is not interchangeable here: the API rejects + and /.
    """
    _check_recipient(to)
    # Written as an escape on purpose: a literal one here would trip
    # test_no_em_dash_anywhere, which scans this file too.
    em_dash = "\u2014"
    if em_dash in subject or em_dash in html:
        raise NotifyError("em dash in an outbound email")
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or re.sub(r"<[^>]+>", " ", html))
    msg.add_alternative(html, subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def scope_missing(exc_text: str) -> bool:
    return "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in exc_text or \
        "insufficient authentication scopes" in exc_text.lower()


def send_email(cfg: Config, subject: str, html: str, *,
               to: str = "hello@krishraja.com",
               text: str | None = None) -> dict:
    """Mail Krish. Degrades to stdout rather than failing a run.

    Until the gmail.send grant exists (python -m hunter.oauth_grant), the token
    carries documents, drive and spreadsheets only, so this falls back and says
    so. That keeps every caller testable before the grant lands.
    """
    to = _check_recipient(to)
    raw = build_message(to, subject, html, text=text)
    token = GoogleOAuth(cfg).access_token()
    r = requests.post(GMAIL_SEND, timeout=45,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"},
                      json={"raw": raw})
    if r.status_code == 200:
        return {"sent": True, "channel": "gmail", "to": to,
                "id": r.json().get("id", "")}
    if r.status_code == 403 and scope_missing(r.text):
        print(f"[notify] gmail.send is not granted yet, so this went nowhere. "
              f"Run: python -m hunter.oauth_grant\n\nSubject: {subject}\n")
        print(text or re.sub(r"<[^>]+>", " ", html))
        return {"sent": False, "channel": "stdout fallback",
                "reason": "gmail.send not granted"}
    raise NotifyError(f"gmail send failed ({r.status_code}): {r.text[:300]}")
