"""The checks that ask whether what is DEPLOYED agrees with what is built.

pytest cannot ask that on its own: it checks this repository against itself, and
the three drifts that cost a day all passed it clean. These tests check the
checkers, offline.
"""
from __future__ import annotations

import json

import pytest

from hunter import doctor as D


def test_a_version_is_compared_as_numbers_not_text():
    """1.10.0 is newer than 1.9.0. Compared as text it is older, which would tell
    him his current extension is stale and send him to download the one he has."""
    assert D._version_tuple("1.10.0") > D._version_tuple("1.9.0")
    assert D._version_tuple("1.2.0") > D._version_tuple("1.1.9")
    assert D._version_tuple("") == (0,)
    assert D._version_tuple("1.x.0") == (1, 0, 0)


def test_main_behind_the_payload_floor_is_broken(monkeypatch):
    """The failure this whole check exists for. Krish downloads main. When the
    floor hunter sends is above what main ships, every application he opens shows
    the red out of date banner, and the only fix is a merge he cannot do."""
    monkeypatch.setattr(D, "_git", lambda *a: json.dumps({"version": "1.1.0"}))
    monkeypatch.setattr("hunter.apply.payload.MIN_EXTENSION", "1.2.0")
    got = D.check_extension_on_main()
    assert got.state == D.FAIL
    assert "1.1.0" in got.detail and "1.2.0" in got.detail
    assert "merge" in got.fix


def test_main_level_with_the_floor_is_fine(monkeypatch):
    monkeypatch.setattr(D, "_git", lambda *a: json.dumps({"version": "1.2.0"}))
    monkeypatch.setattr("hunter.apply.payload.MIN_EXTENSION", "1.2.0")
    assert D.check_extension_on_main().state == D.OK


def test_a_newer_main_than_the_floor_is_fine(monkeypatch):
    """He may be ahead of what an older email requires. That is not a fault."""
    monkeypatch.setattr(D, "_git", lambda *a: json.dumps({"version": "1.4.0"}))
    monkeypatch.setattr("hunter.apply.payload.MIN_EXTENSION", "1.2.0")
    assert D.check_extension_on_main().state == D.OK


def test_a_scheduled_command_this_cli_lacks_is_broken(monkeypatch):
    """Actions reads the workflow from main. approvals-drain was added to the
    workflow on a branch, so the schedule invoked a command that was not there and
    his APPROVE sat unread for hours."""
    monkeypatch.setattr(D, "_git", lambda *a: (
        "    - run: python -m hunter.run approvals-drain --apply\n"
        "    - run: python -m hunter.run teleport --apply\n"))
    got = D.check_scheduled_commands('if cmd == "approvals-drain":')
    assert got.state == D.FAIL
    assert "teleport" in got.detail


def test_every_scheduled_command_present_is_fine(monkeypatch):
    monkeypatch.setattr(D, "_git", lambda *a: (
        "    - run: python -m hunter.run close-submitted --apply\n"))
    assert D.check_scheduled_commands('if cmd == "close-submitted":').state == D.OK


def test_a_route_that_is_not_deployed_is_named_as_such(monkeypatch):
    """A missing Vercel route answers 404 with HTML. The endpoint that accepts his
    press was written, committed locally and never pushed, so the extension POSTed
    into nothing and the sheet kept saying Not applied. An HTML 404 and a JSON 404
    mean opposite things."""
    class Res:
        status_code = 404
        text = "<!DOCTYPE html><title>404</title>"

    monkeypatch.setattr("requests.post", lambda *a, **k: Res())
    got = D.check_endpoint("http://x/submitted", method="POST", expect=400,
                           name="submitted endpoint")
    assert got.state == D.FAIL
    assert "not" in got.detail and "deployed" in got.detail


def test_a_route_that_refuses_properly_is_deployed(monkeypatch):
    class Res:
        status_code = 400
        text = '{"error":"token and key required"}'

    monkeypatch.setattr("requests.post", lambda *a, **k: Res())
    got = D.check_endpoint("http://x/submitted", method="POST", expect=400,
                           name="submitted endpoint")
    assert got.state == D.OK


def test_an_unreachable_route_is_broken_not_silent(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("requests.post", boom)
    assert D.check_endpoint("http://x/s", method="POST", expect=400,
                            name="s").state == D.FAIL


def test_uncommitted_work_is_broken_before_anything_else_is_judged(monkeypatch):
    """Work he can use is work that is on main, and work in the working tree is
    not even on a branch."""
    def git(*a):
        if a[:1] == ("status",):
            return " M src/hunter/run.py"
        if a[:1] == ("rev-parse",):
            return "claude/x"
        return "0"

    monkeypatch.setattr(D, "_git", git)
    got = D.check_branch_reaches_main()
    assert got.state == D.FAIL
    assert "uncommitted" in got.detail


def test_a_branch_ahead_of_main_is_broken(monkeypatch):
    def git(*a):
        if a[:1] == ("status",):
            return ""
        if a[:1] == ("rev-parse",):
            return "claude/x"
        return "4"

    monkeypatch.setattr(D, "_git", git)
    got = D.check_branch_reaches_main()
    assert got.state == D.FAIL
    assert "4 commit" in got.detail


def test_report_exits_non_zero_only_on_a_real_break(capsys):
    assert D.report([D.Check("a", D.OK, "fine")]) == 0
    assert D.report([D.Check("a", D.WARN, "hm")]) == 0
    assert D.report([D.Check("a", D.FAIL, "no", "do this")]) == 1
    out = capsys.readouterr().out
    assert "fix: do this" in out


def test_the_doctor_never_writes_anything():
    """It is safe to run at any time, including while he is mid application. Any
    write in here would be a change nobody asked for, made by a health check."""
    import inspect
    src = inspect.getsource(D)
    for forbidden in ("db_patch", "db_post", "db_insert", ".update(", "send_email",
                      "requests.put", "requests.patch", "requests.delete"):
        assert forbidden not in src, forbidden
    # POST is allowed, and only to probe the route with an empty body.
    assert src.count("requests.post") == 1
