"""Does hunter still have the keys to its own house. Run before anything paid.

Every credential in this system can be revoked by someone who is not Krish and
does not tell him. Google can invalidate an OAuth refresh token. A service
account can lose its share on the workbook. A GitHub secret can be rotated. An
Apify balance can run out. None of those fail loudly: the run starts, does a
third of its work, and dies somewhere in the middle with a stack trace in a log
nobody reads, having already spent money.

So the first thing a run does is prove it can still do the run: read the canon,
read the workbook, mint a Docs token, open both masters, and confirm it can
still send him mail. A failure here costs nothing and says exactly which key
died. A failure fifty steps later costs the whole run and says ValueError.

The Gmail check is a warning rather than a failure on purpose. Without it he
gets no alerts, which is serious, but the sheet work is still worth doing and
refusing to do it would be a second failure on top of the first.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config as config_mod
from .config import Config, GoogleOAuth, GoogleServiceAccount, db_get

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}


@dataclass
class Check:
    name: str
    state: str
    detail: str
    fix: str = ""

    def line(self) -> str:
        return (f"{MARK[self.state]} {self.name}: {self.detail}"
                + (f" (fix: {self.fix})" if self.fix and self.state != OK else ""))


def check_database(cfg: Config) -> Check:
    try:
        rows = db_get(cfg, "system_config", {"select": "key", "limit": "1"})
    except Exception as e:
        return Check("database", FAIL, f"unreachable: {e.__class__.__name__}: {e}",
                     "check SUPABASE_URL and the service role key")
    return Check("database", OK, f"reachable, {len(cfg.raw)} config key(s) loaded"
                 if rows or cfg.raw else "reachable but empty")


def check_sheets(cfg: Config) -> Check:
    """The service account plane: can it still read the workbook it writes."""
    import requests
    try:
        token = GoogleServiceAccount(cfg).access_token()
    except Exception as e:
        return Check("sheets credential", FAIL,
                     f"could not mint a token: {e.__class__.__name__}: {e}",
                     "the service account key in system_config is wrong or revoked")
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{config_mod.WORKBOOK_ID}",
        headers={"Authorization": "Bearer " + token},
        params={"fields": "properties.title"}, timeout=30)
    if r.status_code == 403:
        return Check("sheets access", FAIL, "the service account is not a writer "
                     "on the workbook any more",
                     "reshare the workbook with the mm-os-gsc service account")
    if r.status_code != 200:
        return Check("sheets access", FAIL, f"read failed ({r.status_code})")
    return Check("sheets access", OK,
                 f"can read {r.json().get('properties', {}).get('title', '?')!r}")


def check_documents(cfg: Config) -> Check:
    """Krish's OAuth plane: the masters are owner only, so a revoked refresh
    token means no package can ever be built again."""
    import requests
    try:
        token = GoogleOAuth(cfg).access_token()
    except Exception as e:
        return Check("documents credential", FAIL,
                     f"could not mint a token: {e.__class__.__name__}: {e}",
                     "run python -m hunter.oauth_grant to re-consent")
    for label, doc_id in (("master CV", config_mod.CV_MASTER_ID),
                          ("cover letter master", config_mod.LETTER_MASTER_ID)):
        r = requests.get(f"https://docs.googleapis.com/v1/documents/{doc_id}",
                         headers={"Authorization": "Bearer " + token},
                         params={"fields": "title"}, timeout=30)
        if r.status_code != 200:
            return Check("documents access", FAIL,
                         f"cannot open the {label} ({r.status_code})",
                         "check the document still exists and canon 9.9 still "
                         "names the right id")
    return Check("documents access", OK, "both masters open")


def check_mail(cfg: Config) -> Check:
    """Can hunter still reach him. A warning, not a failure: the work is worth
    doing even when the telling of it is broken."""
    import requests
    try:
        token = GoogleOAuth(cfg).access_token()
    except Exception as e:
        return Check("mail", WARN, f"no token: {e.__class__.__name__}",
                     "python -m hunter.oauth_grant")
    r = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                     headers={"Authorization": "Bearer " + token}, timeout=30)
    if r.status_code != 200:
        return Check("mail", WARN,
                     f"the mailbox will not answer ({r.status_code}), so no "
                     f"alert can reach you", "python -m hunter.oauth_grant")
    scopes = requests.get("https://oauth2.googleapis.com/tokeninfo",
                          params={"access_token": token}, timeout=30)
    granted = (scopes.json().get("scope", "") if scopes.status_code == 200 else "")
    if "gmail.send" not in granted:
        return Check("mail", WARN, "gmail.send is not granted, so every alert "
                     "falls back to stdout", "python -m hunter.oauth_grant")
    return Check("mail", OK, f"can send as {r.json().get('emailAddress', '?')}")


def check_canon(cfg: Config) -> Check:
    from .canon import load_canon
    from .sheet import HEADERS
    try:
        canon = load_canon(cfg)
    except Exception as e:
        return Check("canon", FAIL, f"{e.__class__.__name__}: {e}",
                     "canon governs every gate; nothing runs without it")
    if list(canon.sheet_headers) != list(HEADERS):
        return Check("canon", FAIL, "canon 9.13 and the code disagree on the "
                     "column contract", "run migrate-columns or amend canon")
    return Check("canon", OK, f"version {canon.version}, {len(canon.gates)} gates, "
                 f"bar {canon.bar}")


def check_sourcing_budget(cfg: Config) -> Check:
    """Sourcing without a token finds nothing and reports success, which is
    the exact shape of the bug that cost a week."""
    token = cfg.optional("hunter_apify_token")
    if not token:
        return Check("sourcing credential", WARN,
                     "no Apify token, so the LinkedIn sweep will find nothing",
                     "set hunter_apify_token in system_config")
    return Check("sourcing credential", OK, "Apify token present")


def run(cfg: Config, *, for_sourcing: bool = False) -> list[Check]:
    checks = [check_database(cfg), check_canon(cfg), check_sheets(cfg),
              check_documents(cfg), check_mail(cfg)]
    if for_sourcing:
        checks.append(check_sourcing_budget(cfg))
    return checks


def gate(checks: list[Check]) -> tuple[bool, list[str]]:
    """(may the run proceed, lines for the summary)."""
    lines = ["preflight:"] + [f"  {c.line()}" for c in checks]
    broken = [c for c in checks if c.state == FAIL]
    if broken:
        lines.append(f"  ABORTING: {len(broken)} credential(s) are dead. "
                     f"Nothing else in this run would have worked.")
    return (not broken), lines
