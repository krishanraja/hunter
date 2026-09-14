"""One shot Google consent re-grant, run by Krish on his own machine.

Why this exists: hunter's only outbound module could not send anything. The
OAuth refresh token in system_config carries documents, drive and spreadsheets
and nothing else, so notify.py had no way to reach Krish. Adding gmail.send
needs a fresh consent; a refresh token cannot gain a scope by being refreshed.

Verified 2026-09-14 before writing this:
  - The Gmail API is already ENABLED on the project. A Gmail call with the
    current token returns ACCESS_TOKEN_SCOPE_INSUFFICIENT, not SERVICE_DISABLED,
    so there is no Cloud Console work to do, only a consent.
  - The OAuth client is a Desktop type: http://localhost and
    http://localhost:<port> are accepted at the authorize endpoint, while
    urn:ietf:wg:oauth:2.0:oob and the OAuth playground are blocked. So the
    loopback flow below works and a paste-the-code flow would not.

THE RISK THIS MODULE EXISTS TO PREVENT: a consent that grants gmail.send but
drops one of the three existing scopes would silently break the CV builder and
the sheet writer, and the failure would not show up until the next run. So all
four scopes are requested together and nothing is written unless all four come
back AND both planes are proven live.

Not part of the GitHub Actions runtime. Nothing in hunter imports it.
"""
from __future__ import annotations

import http.server
import json
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser

import requests

from . import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GMAIL_PROFILE = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

DOCS = "https://www.googleapis.com/auth/documents"
DRIVE = "https://www.googleapis.com/auth/drive"
SHEETS = "https://www.googleapis.com/auth/spreadsheets"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
# Reading Krish's replies is how the approve-or-amend loop works. gmail.readonly
# is the narrowest scope that can read a reply body: gmail.metadata cannot see
# one, and gmail.modify would be broader for no gain, because idempotency comes
# from a Supabase ledger of processed message ids rather than from Gmail labels.
# It does grant read of the whole mailbox, which is worth stating plainly: hunter
# only ever queries on its own subject token and never stores a body beyond the
# instruction it extracts.
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"

# All five, always. Three of these already work; losing one is the failure mode,
# and it would not surface until the next scheduled run.
REQUIRED_SCOPES = (DOCS, DRIVE, SHEETS, GMAIL_SEND, GMAIL_READ)
CONFIG_KEY = "hunter_google_oauth_refresh_token"


class GrantError(RuntimeError):
    pass


def missing_scopes(granted: str) -> list[str]:
    """Which REQUIRED_SCOPES are absent from a tokeninfo scope string.

    Google returns the scopes space separated and in its own order, so this
    compares as a set rather than by position.
    """
    have = set((granted or "").split())
    return [s for s in REQUIRED_SCOPES if s not in have]


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """access_type=offline and prompt=consent are both required to be issued a
    refresh token; without them Google returns an access token only."""
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(REQUIRED_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _capture_code(port: int, state: str, timeout: int = 300) -> str:
    """Serve exactly one loopback request and return the authorization code."""
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if q.get("state", [""])[0] != state:
                got["error"] = "state mismatch; possible cross site request"
            elif "error" in q:
                got["error"] = q["error"][0]
            elif "code" in q:
                got["code"] = q["code"][0]
            body = (b"<html><body style='font:16px system-ui;padding:3rem'>"
                    b"<h2>hunter</h2><p>Consent captured. You can close this "
                    b"tab and return to the terminal.</p></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # keep the terminal clean
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = timeout
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(timeout)
    server.server_close()
    if got.get("error"):
        raise GrantError(f"consent failed: {got['error']}")
    if not got.get("code"):
        raise GrantError("no authorization code arrived before the timeout")
    return got["code"]


def exchange(client_id: str, client_secret: str, code: str,
             redirect_uri: str) -> dict:
    r = requests.post(config.OAUTH_TOKEN_URL, timeout=30, data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    if r.status_code != 200:
        raise GrantError(f"token exchange failed ({r.status_code}): {r.text[:300]}")
    payload = r.json()
    if not payload.get("refresh_token"):
        raise GrantError(
            "Google issued no refresh token. That happens when the consent "
            "screen was skipped; rerun so prompt=consent is honoured.")
    return payload


def verify(cfg: config.Config, refresh_token: str) -> dict:
    """Prove the new token does everything the old one did, plus Gmail.

    Returns the checks so the caller can print them. Raises on any failure, and
    the caller must not write a token when this raises.
    """
    client_id = cfg.require("hunter_google_oauth_client_id")
    client_secret = cfg.require("hunter_google_oauth_client_secret")
    r = requests.post(config.OAUTH_TOKEN_URL, timeout=30, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise GrantError(f"the new refresh token will not refresh: {r.text[:200]}")
    access = r.json()["access_token"]

    granted = requests.get(TOKENINFO_URL, params={"access_token": access},
                           timeout=30).json().get("scope", "")
    gone = missing_scopes(granted)
    if gone:
        raise GrantError(
            "refusing to write: the new grant is missing "
            + ", ".join(s.rsplit("/", 1)[-1] for s in gone)
            + ". The existing token is untouched. Rerun and tick every box on "
              "the consent screen.")

    auth = {"Authorization": "Bearer " + access}
    gmail = requests.get(GMAIL_PROFILE, headers=auth, timeout=30)
    if gmail.status_code != 200:
        raise GrantError(f"gmail.send granted but Gmail refused the call "
                         f"({gmail.status_code}): {gmail.text[:200]}")
    # The plane that already worked must still work. This is the regression
    # this whole module is built to catch.
    sheets = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{config.WORKBOOK_ID}",
        headers=auth, params={"fields": "properties.title"}, timeout=30)
    if sheets.status_code != 200:
        raise GrantError(f"the Sheets plane regressed ({sheets.status_code}); "
                         f"not writing the token")
    return {"scopes": len(REQUIRED_SCOPES),
            "mailbox": gmail.json().get("emailAddress", "unknown"),
            "workbook": sheets.json().get("properties", {}).get("title", "")}


def main(argv: list[str]) -> int:
    show_only = "--print" in argv
    cfg = config.load()
    client_id = cfg.require("hunter_google_oauth_client_id")
    client_secret = cfg.require("hunter_google_oauth_client_secret")

    port = _free_port()
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(24)
    url = auth_url(client_id, redirect_uri, state)

    print(f"Opening the Google consent screen. Approve ALL "
          f"{len(REQUIRED_SCOPES)} scopes:")
    for s in REQUIRED_SCOPES:
        print("  -", s)
    print("\nGoogle will warn that the app is unverified. It is your own "
          "project, so continue through it.")
    print("\nIf the browser does not open, paste this into it:\n")
    print(url, "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    code = _capture_code(port, state)
    payload = exchange(client_id, client_secret, code, redirect_uri)
    refresh_token = payload["refresh_token"]

    checks = verify(cfg, refresh_token)
    print(f"verified: {checks['scopes']} of {len(REQUIRED_SCOPES)} scopes "
          f"present, mailbox {checks['mailbox']}, "
          f"workbook {checks['workbook']!r} still readable")

    if show_only:
        print("\n--print given, not writing. Paste this into system_config."
              f"{CONFIG_KEY}:\n")
        print(refresh_token)
        return 0
    config.db_patch(cfg, "system_config", {"key": CONFIG_KEY},
                    {"value": refresh_token})
    back = config.db_get(cfg, "system_config",
                         {"select": "value", "key": f"eq.{CONFIG_KEY}"})
    if not back or back[0].get("value") != refresh_token:
        raise GrantError("the write to system_config did not read back; rerun "
                         "with --print and paste it by hand")
    print(f"written to system_config.{CONFIG_KEY} and read back. "
          f"The token was not printed.")
    print("\nNext: python -m hunter.run bank-check, to confirm Docs, Drive and "
          "Sheets still work from the new token.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
