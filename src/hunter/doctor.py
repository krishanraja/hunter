"""One command that proves the chain is live and matches itself.

Three silent drifts cost most of one day, and all three were invisible from
inside a test run:

  the fix was on a branch and `main` was behind it, so the extension Krish
  downloads could not do the thing the emails assumed it could;

  the scheduled workflow reads `.github/workflows/` from `main`, so a command
  that existed only on a branch never ran and his APPROVE sat unread;

  the control-center endpoint the extension POSTs to was written, committed
  locally and never pushed, so the sheet kept saying "Not applied".

Every one of those passed `pytest`. Tests check this repository against itself;
these checks ask whether what is DEPLOYED agrees with what is built. Read only,
no writes, safe to run at any time.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

OK, WARN, FAIL = "ok", "warn", "fail"

PAYLOAD_URL = "https://controlcenter.krishraja.com/api/hunter/payload"
SUBMITTED_URL = "https://controlcenter.krishraja.com/api/hunter/submitted"

DOWNLOAD = "https://github.com/krishanraja/hunter/archive/refs/heads/main.zip"


@dataclass
class Check:
    name: str
    state: str
    detail: str
    fix: str = ""


def _git(*args: str) -> str:
    try:
        return subprocess.run(("git",) + args, capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except Exception:
        return ""


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in str(v or "0").split("."))


def check_branch_reaches_main() -> Check:
    """Work he can use is work that is on main."""
    _git("fetch", "origin", "main", "--quiet")
    head = _git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    ahead = _git("rev-list", "--count", "origin/main..HEAD")
    dirty = _git("status", "--porcelain")
    n = int(ahead) if ahead.isdigit() else -1
    if dirty:
        return Check("branch reaches main", FAIL,
                     f"{len(dirty.splitlines())} uncommitted change(s) on {head}",
                     "commit them, then merge to main")
    if n > 0:
        return Check("branch reaches main", FAIL,
                     f"{head} is {n} commit(s) ahead of main",
                     "merge to main. Scheduled runs and his ZIP both come from main")
    if n < 0:
        return Check("branch reaches main", WARN, "could not compare against main")
    return Check("branch reaches main", OK, f"{head} is level with main")


def check_extension_on_main() -> Check:
    """The version he can download must satisfy the floor hunter now sends.

    Below it, every application he opens shows the red out of date banner, which
    is correct behaviour and a broken experience.
    """
    from .apply.payload import MIN_EXTENSION
    raw = _git("show", "origin/main:extension/manifest.json")
    if not raw:
        return Check("extension on main", WARN, "could not read the manifest on main")
    try:
        shipped = json.loads(raw).get("version", "0")
    except Exception:
        return Check("extension on main", FAIL, "the manifest on main is not JSON")
    if _version_tuple(shipped) < _version_tuple(MIN_EXTENSION):
        return Check("extension on main", FAIL,
                     f"main ships {shipped}, payloads require {MIN_EXTENSION}",
                     "merge the extension to main before telling him to download")
    return Check("extension on main", OK,
                 f"main ships {shipped}, floor is {MIN_EXTENSION}")


def check_scheduled_commands(run_source: str) -> Check:
    """Every command the workflow on MAIN invokes must exist in this CLI.

    `approvals-drain` was added to the workflow on a branch. Actions reads the
    workflow from main, so his APPROVE was never read for hours.
    """
    yml = _git("show", "origin/main:.github/workflows/hunter.yml")
    if not yml:
        return Check("scheduled commands", WARN, "no workflow on main to read")
    wanted = sorted(set(re.findall(r"hunter\.run\s+([a-z][a-z-]*)", yml)))
    missing = [c for c in wanted if f'cmd == "{c}"' not in run_source
               and f'cmd in ("{c}"' not in run_source
               and f'"{c}")' not in run_source]
    if missing:
        return Check("scheduled commands", FAIL,
                     f"the workflow on main calls {', '.join(missing)}, "
                     f"which this CLI does not handle",
                     "add the command, or fix the workflow")
    return Check("scheduled commands", OK,
                 f"{len(wanted)} command(s) on main all exist: {', '.join(wanted)}")


def check_endpoint(url: str, *, method: str, expect: int, name: str) -> Check:
    """Is the route deployed at all.

    A missing Vercel route answers 404 with HTML. A deployed one answers with
    JSON, so the body distinguishes "refused me" from "is not there".
    """
    import requests
    try:
        if method == "POST":
            r = requests.post(url, json={}, timeout=30)
        else:
            r = requests.get(url, params={"token": "doctor-probe", "key": "0"},
                             timeout=30)
    except Exception as e:
        return Check(name, FAIL, f"could not reach it: {str(e)[:80]}")
    body = (r.text or "")[:200]
    looks_json = body.lstrip().startswith("{")
    if r.status_code == expect and looks_json:
        return Check(name, OK, f"deployed, answered {r.status_code} as it should")
    if not looks_json:
        return Check(name, FAIL,
                     f"answered {r.status_code} with HTML, so the route is not "
                     f"deployed", "push and merge the control-center half")
    return Check(name, WARN,
                 f"answered {r.status_code}, expected {expect}: {body[:90]}")


def check_table_columns(cfg) -> Check:
    """The columns the browser half writes have to exist."""
    from .config import db_get
    need = {"state", "submitted_at", "decided_at", "failure_reason", "open_key",
            "fill_payload", "plan_hash"}
    try:
        rows = db_get(cfg, "hunter_application_approvals",
                      {"select": "*", "limit": "1"})
    except Exception as e:
        return Check("approvals table", FAIL, f"could not read it: {str(e)[:80]}")
    if not rows:
        return Check("approvals table", WARN, "no rows to read the shape from")
    missing = sorted(need - set(rows[0].keys()))
    if missing:
        return Check("approvals table", FAIL,
                     f"missing column(s): {', '.join(missing)}")
    return Check("approvals table", OK, f"all {len(need)} columns present")


def check_live_payloads(cfg) -> Check:
    """Applications sitting in his inbox right now, against the current floor."""
    from .apply.payload import MIN_EXTENSION
    from .config import db_get
    try:
        rows = db_get(cfg, "hunter_application_approvals",
                      {"select": "token,fill_payload", "state": "eq.awaiting",
                       "limit": "200"})
    except Exception as e:
        return Check("live applications", FAIL, f"could not read: {str(e)[:80]}")
    if not rows:
        return Check("live applications", OK, "none awaiting")
    stale = []
    for r in rows:
        pay = r.get("fill_payload") or {}
        floor = pay.get("needs_extension") or "0"
        if _version_tuple(floor) > _version_tuple(MIN_EXTENSION):
            stale.append(r["token"])
    if stale:
        return Check("live applications", FAIL,
                     f"{len(stale)} require an extension newer than {MIN_EXTENSION}")
    return Check("live applications", OK,
                 f"{len(rows)} awaiting, all fillable by {MIN_EXTENSION}")


def check_browser() -> Check:
    """A launch that fails takes the screenshot and the empty and required list
    out of every approval email, and the pass carries on without either."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return Check("browser", WARN, "playwright is not installed",
                     "pip install -e '.[browser]'")
    from .apply.submit import _launch
    try:
        with sync_playwright() as pw:
            b = _launch(pw)
            p = b.new_page()
            p.set_content("<h1>ok</h1>")
            got = p.inner_text("h1")
            b.close()
        if got != "ok":
            return Check("browser", FAIL, "launched but rendered nothing")
    except Exception as e:
        return Check("browser", FAIL, f"will not launch: {str(e)[:90]}",
                     "set HUNTER_CHROMIUM_PATH, or install a chromium")
    return Check("browser", OK, "launches and renders")


def run(cfg, run_source: str, *, offline: bool = False) -> list[Check]:
    checks = [check_branch_reaches_main(), check_extension_on_main(),
              check_scheduled_commands(run_source)]
    if not offline:
        checks += [
            check_endpoint(PAYLOAD_URL, method="GET", expect=404,
                           name="payload endpoint"),
            check_endpoint(SUBMITTED_URL, method="POST", expect=400,
                           name="submitted endpoint"),
            check_table_columns(cfg), check_live_payloads(cfg), check_browser(),
        ]
    return checks


MARK = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}


def report(checks: list[Check]) -> int:
    for c in checks:
        print(f"{MARK[c.state]}  {c.name}: {c.detail}")
        if c.fix and c.state != OK:
            print(f"        fix: {c.fix}")
    bad = [c for c in checks if c.state == FAIL]
    warn = [c for c in checks if c.state == WARN]
    print(f"\n{len(checks) - len(bad) - len(warn)} ok, {len(warn)} warning(s), "
          f"{len(bad)} broken")
    if bad:
        print("\nWhat Krish sees while these are broken is a form that does not "
              "fill, or a sheet that says Not applied on a role he applied to.")
    return 1 if bad else 0
