"""His house style, and the pause until a package can carry it (package/style.py).

His ruling, 2026-10-15: no new packages and no Apply emails until every letter
passes his new rules. These tests run against the real pause: every other test
in the suite runs as if the composer were live (conftest._house_style_live).
Every role, company and word here is invented.
"""
import pytest

import hunter.run as run_mod
from hunter import actions
from hunter.package import style
from test_his_view import (L, Plan, REACTION, approvals_world, rebuild_world, rec)
from test_score_builds import Cfg, FakeCanon, _stub_build

pytestmark = pytest.mark.house_style_paused


def test_only_the_current_style_is_stamped():
    assert not style.stamped(None)
    assert not style.stamped({})
    assert not style.stamped({style.FIELD: None})
    assert not style.stamped({style.FIELD: ""})
    assert not style.stamped({style.FIELD: "house-2020-01-01"})
    assert style.stamped({style.FIELD: style.HOUSE_STYLE})


def test_production_builds_wait_until_a_composer_writes_his_style():
    """The composer that writes his style does not exist yet: this goes red
    the day it lands, which is when the tests here change with it."""
    assert style.COMPOSER_LIVE is False
    assert style.builds_paused()


# ---------- builds ----------

def test_build_one_waits_before_anything_is_fetched_paid_or_written(monkeypatch):
    rec_ = _stub_build(monkeypatch)
    summary = []
    row_ = {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
            "title": "Chief of Staff", "url": "https://job-boards.greenhouse.io/o/jobs/7",
            "status": "staging", "build_trigger": "column A Yes"}
    sheet_reads = []
    sheet = type("S", (), {"read_pipeline": lambda self, h: sheet_reads.append(1) or []})()
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), sheet,
                             row_, summary, cache={}) is False
    assert rec_["resolved"] == 0 and rec_["tailored"] == 0
    assert rec_["patches"] == [], "nothing is written, so the role is chosen again"
    assert sheet_reads == []
    assert summary == [f"SKIPPED ostravia:chief-of-staff: {style.PAUSED}; nothing "
                       f"fetched or built"]


def test_a_rebuild_waits_too(monkeypatch):
    rec_ = _stub_build(monkeypatch)
    summary = []
    assert run_mod.build_one(Cfg({}), FakeCanon(), None, {"job_id": "a:b"}, summary,
                             cache={}, rebuild=True) is False
    assert rec_["tailored"] == 0 and rec_["patches"] == []


def test_score_builds_wait_without_reading_the_sheet(monkeypatch, capsys):
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg({"hunter_max_score_builds_per_run": "3"}),
                                                           FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: pytest.fail("no sheet read"))
    assert run_mod.cmd_score_builds(apply=True) == 0
    assert f"score builds: waiting, {style.PAUSED}" in capsys.readouterr().out


def test_rebuild_packages_counts_its_targets_and_builds_none(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent", m=REACTION)], [rec("ardent")])
    from test_his_view import _commands
    _commands(monkeypatch, s)
    monkeypatch.setattr(run_mod, "build_one", lambda *a, **kw: pytest.fail("built"))
    assert run_mod.cmd_rebuild_packages(apply=True) == 0
    out = capsys.readouterr().out
    assert "1 package(s) built without his" in out
    assert f"rebuild: waiting, {style.PAUSED}; nothing built" in out


def test_the_process_line_says_how_many_wait():
    line = run_mod._process_line({"built": 0, "waiting_style": 4})
    assert "4 waiting for his house style" in line
    assert "waiting" not in run_mod._process_line({"built": 2})


# ---------- every send path ----------

def _with_not_in(monkeypatch, db):
    """The fake database reads not.in.(...), as cmd_approvals asks it to."""
    def get(cfg, table, params):
        params = dict(params)
        cond = params.get("status", "")
        if cond.startswith("not.in.("):
            params.pop("status")
            out = db.get(cfg, table, params)
            return [r for r in out if str(r.get("status")) not in cond[8:-1].split(",")]
        return db.get(cfg, table, params)
    monkeypatch.setattr(run_mod, "db_get", get)


def test_a_package_built_before_his_style_is_never_prepared_or_sent(monkeypatch, capsys):
    db, mailed, reached = approvals_world(monkeypatch, [L("ardent", k="9")],
                                          [rec("ardent")], Plan())
    tally = {}
    assert run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")],
                                 tally=tally) == 0
    assert tally == {style.OLD_STYLE: 1}
    assert reached == [], "no fill plan, so no essay is drafted or paid for"
    assert mailed == {"by_hand": [], "approval": []}
    assert db.tables.get("hunter_alerts", []) == [], \
        "never marked as dealt with, and never against his answer bank"
    out = capsys.readouterr().out
    assert "Ardent" not in out and "ardent" not in out


def test_one_built_in_his_style_still_goes(monkeypatch):
    stamped = rec("ardent", **{style.FIELD: style.HOUSE_STYLE})
    db, mailed, reached = approvals_world(monkeypatch, [L("ardent", k="9")], [stamped], Plan())
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[stamped], tally=tally)
    assert tally == {"sent": 1} and reached == ["ardent:vp"] and mailed["approval"] == [1]


def test_an_unfillable_form_is_not_emailed_by_hand_either(monkeypatch):
    db, mailed, reached = approvals_world(monkeypatch, [L("ardent", k="9")],
                                          [rec("ardent")], Plan(readable=False))
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {style.OLD_STYLE: 1} and mailed["by_hand"] == []


def test_prepare_on_one_is_refused_with_its_own_note(monkeypatch):
    db, mailed, reached = approvals_world(
        monkeypatch, [L("ardent", k="9", verdict="Yes")],
        [rec("ardent", package_trigger="column A Yes")], Plan())
    _with_not_in(monkeypatch, db)
    tally = {}
    code = run_mod.cmd_approvals(apply=True, prefill=False, job_id="ardent:vp", tally=tally)
    assert code == 1 and tally == {style.OLD_STYLE: 1} and mailed["approval"] == []
    ok, note = actions.prepare_note(code, tally, [])
    assert ok is False
    assert note.startswith("built before your house style, so nothing was sent")


def test_the_bulk_run_skips_them_and_stays_green(monkeypatch):
    db, mailed, reached = approvals_world(
        monkeypatch, [L("ardent", k="9", verdict="Yes"), L("brisk", k="9", verdict="Yes")],
        [rec("ardent", package_trigger="column A Yes"),
         rec("brisk", package_trigger="column A Yes")], Plan())
    _with_not_in(monkeypatch, db)
    tally = {}
    assert run_mod.cmd_approvals(apply=True, prefill=False, tally=tally) == 0
    assert tally == {style.OLD_STYLE: 2} and reached == []


def test_approvals_wanted_never_chooses_one(monkeypatch):
    s, db = rebuild_world(monkeypatch, [L("ardent", k="9"), L("brisk", k="9")],
                          [rec("ardent"), rec("brisk", **{style.FIELD: style.HOUSE_STYLE})])
    monkeypatch.setattr(run_mod, "live_postings", lambda cfg: {})
    monkeypatch.setattr(run_mod, "prepared_marks", lambda cfg: {})
    rep = {}
    got = run_mod.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep)
    assert [d["job_id"] for d in got] == ["brisk:vp"]
    assert rep["skips"].get(style.OLD_STYLE) == 1


# ---------- an AMEND while builds wait ----------

def _drain_world(monkeypatch, *, edited=True):
    from hunter.apply import approval, inbox
    s, db = rebuild_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")],
                          edited=("ardent:vp",) if edited else ())
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(inbox, "fetch_replies", lambda cfg: [
        {"token": "tok-invented", "message_id": "m1"}])
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: {"job_id": "ardent:vp"})
    monkeypatch.setattr(inbox, "classify",
                        lambda r, row, cfg=None: ("amend", "Invented words: lead on the partners."))
    monkeypatch.setattr(run_mod, "report_stalled_approvals", lambda cfg, apply=False: [])
    order = []
    monkeypatch.setattr(approval, "supersede", lambda cfg, token: order.append("supersede"))
    monkeypatch.setattr(approval, "mark_processed", lambda *a, **kw: None)
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    recorded = []
    monkeypatch.setattr(run_mod.amend, "record",
                        lambda cfg, pairs: order.append("record") or recorded.extend(pairs))
    monkeypatch.setattr(run_mod, "build_one", lambda *a, **kw: pytest.fail("built"))
    monkeypatch.setattr(run_mod, "cmd_approvals", lambda **kw: pytest.fail("sent"))
    return order, recorded


def test_an_amend_keeps_his_edits_first_and_rebuilds_nothing(monkeypatch, capsys):
    order, recorded = _drain_world(monkeypatch)
    assert run_mod.cmd_approvals_drain(apply=True) == 0
    assert order == ["record", "supersede"], "his edit is kept before the token dies"
    assert [p["field"] for p in recorded] == ["cover letter"]
    assert "A paragraph he wrote himself." in recorded[0]["after_text"]
    assert recorded[0]["before_text"] == "Letter hunter wrote for ardent:vp."
    out = capsys.readouterr().out
    assert "his edits to 1 document(s) kept" in out
    assert "SUPERSEDED; his words are kept" in out
    for leak in ("paragraph he wrote", "Invented words", "tok-invented", "Ardent"):
        assert leak not in out, leak


def test_an_amend_over_unedited_documents_records_no_pair(monkeypatch):
    order, recorded = _drain_world(monkeypatch, edited=False)
    assert run_mod.cmd_approvals_drain(apply=True) == 0
    assert recorded == [] and "supersede" in order


def test_edit_pairs_never_read_an_unknown_as_an_edit():
    from hunter import amend
    docs = {"letter_text": "L", "cv_text": "C"}
    read = {"L": "", "C": "CV now"}.get
    assert amend.edit_pairs(read, docs, {"letter_text": "was", "cv_text": "CV now"},
                            job_id="j") == []
    assert amend.edit_pairs(read, docs, None, job_id="j") == []
    got = amend.edit_pairs(read, docs, {"cv_text": "CV then"}, job_id="j")
    assert [(p["field"], p["before_text"], p["after_text"]) for p in got] == [
        ("cv", "CV then", "CV now")]


def test_a_package_whose_rebuild_did_not_land_can_be_chosen_again(monkeypatch):
    """Blocked so that nothing sends it, and still a rebuild target: once a
    composer writes his style it is rebuilt like any other."""
    s, _ = rebuild_world(monkeypatch, [L("ardent", m=REACTION), L("brisk", m=REACTION)], [
        rec("ardent", package_status="blocked",
            rejection_reason=run_mod.REBUILD_REFUSED + "package verification failed: x"),
        rec("brisk", package_status="blocked", rejection_reason="G0: never apply")])
    rep = {}
    got = run_mod.rebuild_targets(Cfg(), s, FakeCanon(), report=rep)
    assert [d["job_id"] for d in got] == ["ardent:vp"]
    assert rep["skips"].get("not built yet") == 1
