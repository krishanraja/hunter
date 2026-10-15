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

DOWNLOAD = "https://github.com/krishanraja/hunter/releases/download/extension/hunter-extension.zip"


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


def check_his_view_column(cfg, *, get=None) -> Check:
    """The record of which packages carry his reasons (migration cc6). Missing
    is a warning, not a fault: every build still succeeds and says it could
    not record it, but rebuild-packages refuses to pay until it is there."""
    from .package.his_view import FIELD
    if get is None:
        from .config import db_get as get
    try:
        get(cfg, "hunter_seen_roles", {"select": f"job_id,{FIELD}", "limit": "1"})
    except Exception as e:
        return Check("his reasons on the record", WARN,
                     f"hunter_seen_roles.{FIELD} could not be read "
                     f"({e.__class__.__name__}); rebuild-packages will not rebuild",
                     "apply migration cc6 (add the column)")
    return Check("his reasons on the record", OK,
                 f"hunter_seen_roles.{FIELD} is there; packages record which of "
                 f"his reasons they carry")


def check_private_recipients(cfg) -> Check:
    """His personal address, read from system_config because the repository
    is public (notify.PRIVATE_RECIPIENTS_KEY). Missing or malformed, a reply
    he sends from it is refused as not his, and the drain marks it handled.
    Counts only: the addresses are the point of keeping it out of the log."""
    from .notify import PRIVATE_RECIPIENTS_KEY, private_recipients
    raw = (getattr(cfg, "raw", None) or {}).get(PRIVATE_RECIPIENTS_KEY)
    fix = (f"write system_config {PRIVATE_RECIPIENTS_KEY} as a JSON list of his "
           f"personal addresses")
    if not raw:
        return Check("his personal address", FAIL,
                     f"system_config {PRIVATE_RECIPIENTS_KEY} is not set; a reply from "
                     f"his personal address is refused", fix)
    n = len(private_recipients(cfg))
    if not n:
        return Check("his personal address", FAIL,
                     f"system_config {PRIVATE_RECIPIENTS_KEY} does not read as a list of "
                     f"addresses, so none counts", fix)
    return Check("his personal address", OK, f"{n} address(es) read from system_config")


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


def check_model_keys(cfg) -> Check:
    """Each provider's key, asked directly, because the fallback hides a dead one.

    Krish, 2026-09-24: "need to split all usage of Anthropic API out for
    better monitoring". With OpenAI behind Anthropic, a dead Anthropic key
    costs nothing visible: every call quietly routes to OpenAI, every run
    goes green, and the split he wants stops being true without a single
    error anywhere. Nothing checked this at all.

    Never prints a key. It names which provider answered.
    """
    from . import llm
    order = [p.strip() for p in
             cfg.optional("hunter_model_order", llm.DEFAULT_ORDER).split(",")
             if p.strip() in llm.PROVIDERS]
    if not order:
        return Check("model keys", FAIL, "hunter_model_order names no provider")
    results = []
    for name in order:
        ok, detail = llm.probe(cfg, name)
        results.append((name, ok, detail))
    good = [n for n, ok, _ in results if ok]
    bad = [(n, d) for n, ok, d in results if not ok]
    if not good:
        return Check("model keys", FAIL,
                     "no provider answered: "
                     + "; ".join(f"{n} ({d})" for n, d in bad),
                     "check hunter_anthropic_api_key and hunter_openai_api_key")
    if bad:
        # The run will still work, on the other provider, which is exactly
        # the failure that is worth saying out loud rather than tolerating.
        return Check("model keys", WARN,
                     f"{', '.join(good)} answered; "
                     + "; ".join(f"{n} did NOT ({d})" for n, d in bad),
                     "the fallback is carrying the run, so usage is on the "
                     "wrong provider")
    return Check("model keys", OK, f"{', '.join(good)} answered")


def check_sheet_header(cfg, *, read_header=None, read_archive_grid=None,
                       canon_headers=None) -> Check:
    """The Pipeline header he sees, sheet.HEADERS and canon 9.13 name the same
    columns in the same order, and the Applied header is a layout its
    readers and writers know. When he inserted two columns on 2026-10-11
    every hourly run failed on the header for two days, and nothing here
    looked at it."""
    from .sheet import (APPLIED_STOPPED, HEADERS, archive_header_diffs,
                        archive_layout, describe_header_change, header_diffs,
                        header_shift)
    try:
        if canon_headers is None:
            from .canon import load_canon
            canon_headers = load_canon(cfg).sheet_headers
    except Exception as e:
        return Check("sheet header", FAIL, f"canon would not load: {str(e)[:160]}",
                     "amend canon 9.13 to name the Pipeline columns")
    try:
        if read_header is None or read_archive_grid is None:
            from .config import GoogleServiceAccount
            from .sheet import Sheet
            sheet = Sheet(GoogleServiceAccount(cfg).access_token)
            read_header = read_header or sheet.read_header
            read_archive_grid = read_archive_grid or sheet.read_archive_grid
        live = read_header()
        applied = read_archive_grid()
    except Exception as e:
        return Check("sheet header", FAIL,
                     f"could not read the Pipeline and Applied headers: {str(e)[:90]}")
    problems = []
    if list(canon_headers) != list(HEADERS):
        problems.append(f"canon 9.13 and sheet.HEADERS disagree at "
                        f"{header_diffs(list(canon_headers), HEADERS)[:3]}")
    diffs = header_diffs(live, HEADERS)
    if diffs:
        problems.append(describe_header_change(diffs, shift=header_shift(live, HEADERS)))
    layout = archive_layout(applied)
    if not layout:
        problems.append(describe_header_change(archive_header_diffs(applied),
                                               lead="Applied columns changed: ",
                                               consequence=APPLIED_STOPPED))
    if problems:
        return Check("sheet header", FAIL, " ".join(problems),
                     "bring sheet.HEADERS and canon 9.13 to the live header together")
    applied_says = {"current": "Applied is on the same layout",
                    "previous": "Applied is on the previous layout and gains his "
                                "two columns on the next settle",
                    "empty": "Applied is empty"}[layout]
    return Check("sheet header", OK, f"the Pipeline header, the code and canon 9.13 "
                                     f"name the same {len(HEADERS)} columns; "
                                     f"{applied_says}")


def run(cfg, run_source: str, *, offline: bool = False) -> list[Check]:
    checks = [check_branch_reaches_main(), check_extension_on_main(),
              check_scheduled_commands(run_source)]
    if not offline:
        checks += [
            check_endpoint(PAYLOAD_URL, method="GET", expect=404,
                           name="payload endpoint"),
            check_endpoint(SUBMITTED_URL, method="POST", expect=400,
                           name="submitted endpoint"),
            check_table_columns(cfg), check_his_view_column(cfg), check_live_payloads(cfg),
            check_private_recipients(cfg),
            check_sheet_header(cfg), check_model_keys(cfg), check_browser(),
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
