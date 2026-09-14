#!/usr/bin/env python3
"""One time Google consent for hunter. Run this on the machine with your browser.

    python grant_gmail.py

STANDARD LIBRARY ONLY, ON PURPOSE. No pip install, no PYTHONPATH, no repo
import. The first version of this lived at src/hunter/oauth_grant.py and Krish hit
"ModuleNotFoundError: No module named 'hunter'" on a bare
C:\\Python314\\python.exe, which was the right error to get: the repo uses a src
layout, so that module only resolves after `pip install -e .`. A one time consent
is the wrong place to require a working dev environment, and everything this needs
is plain HTTPS, so urllib does the job.

WHAT IT DOES
  Opens Google's consent screen, catches the callback on localhost, exchanges the
  code, and writes the new refresh token into Supabase system_config.

THE RISK IT EXISTS TO PREVENT
  hunter's Google token drives four different planes: Docs and Drive build the CV
  and letter, Sheets is the Pipeline, and Gmail is the approval email. A consent
  that grants gmail.send while quietly dropping spreadsheets would break the sheet
  writer with no visible error until the next scheduled run. So all five scopes
  are requested together, all five are verified present, Gmail and Sheets are both
  called for real, and only then is anything written. If a single scope is short,
  NOTHING is written and your existing token keeps working.

Verified 2026-09-14: the Gmail API is already enabled on the project (a Gmail call
returns ACCESS_TOKEN_SCOPE_INSUFFICIENT, not SERVICE_DISABLED), and the OAuth
client is a Desktop type that accepts loopback redirects while rejecting the
out-of-band and playground flows. So this flow is the only one available.
"""
from __future__ import annotations

import getpass
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GMAIL_PROFILE = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# Not a secret. Cross checked against canon 9.9 at every hunter run.
WORKBOOK_ID = "1AQ8OyprIyJmJ9K7ezjIxkW0uzjGT0TqzRjKtG-NXNOk"

DOCS = "https://www.googleapis.com/auth/documents"
DRIVE = "https://www.googleapis.com/auth/drive"
SHEETS = "https://www.googleapis.com/auth/spreadsheets"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"

# Must stay identical to hunter.oauth_grant.REQUIRED_SCOPES. A test asserts it.
REQUIRED_SCOPES = (DOCS, DRIVE, SHEETS, GMAIL_SEND, GMAIL_READ)

CONFIG_KEY = "hunter_google_oauth_refresh_token"
CLIENT_ID_KEY = "hunter_google_oauth_client_id"
CLIENT_SECRET_KEY = "hunter_google_oauth_client_secret"


class GrantError(RuntimeError):
    pass


# ---------- tiny HTTP helpers, so there is no requests dependency ----------

def _request(url: str, *, method: str = "GET", data: dict | None = None,
             json_body=None, headers: dict | None = None, timeout: int = 45):
    body = None
    h = dict(headers or {})
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        body = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:400]}
    except urllib.error.URLError as e:
        raise GrantError(f"network error reaching {url}: {e.reason}") from e


def missing_scopes(granted: str) -> list[str]:
    """Which REQUIRED_SCOPES are absent. Compared as a set, because Google
    returns them space separated in its own order."""
    have = set((granted or "").split())
    return [s for s in REQUIRED_SCOPES if s not in have]


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """access_type=offline and prompt=consent are both required to be issued a
    refresh token at all. Without them Google returns an access token only, which
    is the most common way this flow silently fails."""
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": " ".join(REQUIRED_SCOPES),
        "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state})


# ---------- Supabase, read and write, over plain HTTP ----------

def supabase_credentials() -> tuple[str, str]:
    """Environment first, prompt second. The key is read with getpass so it never
    lands in shell history, which matters more on Windows where the console buffer
    persists."""
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url:
        print("SUPABASE_URL is not set in this shell.")
        url = input("  Supabase project URL: ").strip().rstrip("/")
    if not key:
        print("SUPABASE_SERVICE_ROLE_KEY is not set in this shell.")
        key = getpass.getpass("  Supabase service role key (hidden): ").strip()
    if not url or not key:
        raise GrantError("both the Supabase URL and the service role key are "
                         "needed to read the OAuth client and store the token")
    return url, key


def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": "Bearer " + key}


def read_config(url: str, key: str, wanted: tuple[str, ...]) -> dict:
    q = urllib.parse.urlencode({"select": "key,value",
                                "key": f"in.({','.join(wanted)})"})
    status, payload = _request(f"{url}/rest/v1/system_config?{q}",
                               headers=_sb_headers(key))
    if status != 200:
        raise GrantError(f"could not read system_config ({status}): "
                         f"{str(payload)[:200]}")
    got = {row["key"]: (row.get("value") or "") for row in payload}
    for k in wanted:
        if not got.get(k):
            raise GrantError(f"system_config is missing {k!r}")
    return got


def write_refresh_token(url: str, key: str, token: str) -> None:
    q = urllib.parse.urlencode({"key": f"eq.{CONFIG_KEY}"})
    h = _sb_headers(key)
    h["Prefer"] = "return=minimal"
    status, payload = _request(f"{url}/rest/v1/system_config?{q}",
                               method="PATCH", json_body={"value": token},
                               headers=h)
    if status not in (200, 204):
        raise GrantError(f"writing the token failed ({status}): "
                         f"{str(payload)[:200]}")
    back = read_config(url, key, (CONFIG_KEY,))
    if back[CONFIG_KEY] != token:
        raise GrantError("the token did not read back after writing; rerun with "
                         "--print and paste it into system_config by hand")


# ---------- the loopback consent ----------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def capture_code(port: int, state: str, timeout: int = 300) -> str:
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if q.get("state", [""])[0] != state:
                got["error"] = "state mismatch, possible cross site request"
            elif "error" in q:
                got["error"] = q["error"][0]
            elif "code" in q:
                got["code"] = q["code"][0]
            body = ("<html><body style='font:16px system-ui;padding:3rem'>"
                    "<h2>hunter</h2><p>Consent captured. Close this tab and go "
                    "back to the terminal.</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
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
    status, payload = _request(TOKEN_URL, method="POST", data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    if status != 200:
        raise GrantError(f"token exchange failed ({status}): "
                         f"{str(payload)[:300]}")
    if not payload.get("refresh_token"):
        raise GrantError(
            "Google issued no refresh token. That happens when the consent "
            "screen is skipped because the app is already authorised. Rerun so "
            "prompt=consent is honoured.")
    return payload


def verify(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Prove the new token does everything the old one did, plus Gmail. Raises on
    any failure, and the caller must not write a token when this raises."""
    status, payload = _request(TOKEN_URL, method="POST", data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if status != 200:
        raise GrantError(f"the new refresh token will not refresh: "
                         f"{str(payload)[:200]}")
    access = payload["access_token"]

    _, info = _request(f"{TOKENINFO_URL}?"
                       + urllib.parse.urlencode({"access_token": access}))
    gone = missing_scopes(info.get("scope", ""))
    if gone:
        raise GrantError(
            "refusing to write: the new grant is missing "
            + ", ".join(s.rsplit("/", 1)[-1] for s in gone)
            + ".\n  Your existing token is untouched. Rerun and tick EVERY box "
              "on the consent screen.")

    auth = {"Authorization": "Bearer " + access}
    status, prof = _request(GMAIL_PROFILE, headers=auth)
    if status != 200:
        raise GrantError(f"gmail.send was granted but Gmail refused the call "
                         f"({status}): {str(prof)[:200]}")
    # The plane that already worked must still work. This is the regression the
    # whole script exists to catch.
    status, book = _request(
        f"{SHEETS_API}/{WORKBOOK_ID}?"
        + urllib.parse.urlencode({"fields": "properties.title"}), headers=auth)
    if status != 200:
        raise GrantError(f"the Sheets plane regressed ({status}); not writing "
                         f"the token")
    return {"mailbox": prof.get("emailAddress", "unknown"),
            "workbook": (book.get("properties") or {}).get("title", "")}


def main(argv: list[str]) -> int:
    show_only = "--print" in argv
    print("hunter: one time Google consent\n")

    sb_url, sb_key = supabase_credentials()
    conf = read_config(sb_url, sb_key, (CLIENT_ID_KEY, CLIENT_SECRET_KEY))
    client_id = conf[CLIENT_ID_KEY]
    client_secret = conf[CLIENT_SECRET_KEY]

    port = free_port()
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(24)
    url = auth_url(client_id, redirect_uri, state)

    print(f"\nApprove ALL {len(REQUIRED_SCOPES)} scopes on the consent screen:")
    for s in REQUIRED_SCOPES:
        print("  -", s.rsplit("/", 1)[-1])
    print("\nGoogle will warn that the app is unverified. It is your own "
          "project, so continue through it.")
    print("\nIf the browser does not open, paste this into it:\n")
    print(url, "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("Waiting for the consent callback...")

    code = exchange(client_id, client_secret,
                    capture_code(port, state), redirect_uri)["refresh_token"]
    checks = verify(client_id, client_secret, code)
    print(f"\nverified: {len(REQUIRED_SCOPES)} of {len(REQUIRED_SCOPES)} scopes "
          f"present")
    print(f"  mailbox:  {checks['mailbox']}")
    print(f"  workbook: {checks['workbook']!r} still readable")

    if show_only:
        print(f"\n--print given, nothing written. Paste this into "
              f"system_config.{CONFIG_KEY}:\n")
        print(code)
        return 0
    write_refresh_token(sb_url, sb_key, code)
    print(f"\nwritten to system_config.{CONFIG_KEY} and read back. The token was "
          f"not printed.")
    print("\nNext, from the repo: python -m hunter.run bank-check")
    return 0


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except GrantError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        rc = 1
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        rc = 130
    # A double clicked window on Windows closes the moment the script ends,
    # taking the error message with it, so hold it open.
    if os.name == "nt" and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close.")
        except EOFError:
            pass
    sys.exit(rc)
