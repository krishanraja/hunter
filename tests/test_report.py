"""Run reporting: the contract that makes hunter visible in Control Center.
One row per run, alerts that hunter itself can clear, and a reporting layer
that can never take a completed run down with it."""
import datetime

import pytest

import hunter.report as report_mod
from hunter.report import WORKFLOW_ID, report_run

STARTED = datetime.datetime(2026, 9, 1, 8, 27, tzinfo=datetime.timezone.utc)


class FakeDb:
    def __init__(self, open_alerts=None):
        self.inserted = []
        self.patched = []
        self.open_alerts = open_alerts or []

    def install(self, monkeypatch):
        monkeypatch.setattr(report_mod, "db_insert",
                            lambda cfg, table, rows, **kw: self.inserted.append((table, rows)))
        monkeypatch.setattr(report_mod, "db_patch",
                            lambda cfg, table, match, values: self.patched.append((table, match, values)))
        monkeypatch.setattr(report_mod, "db_get",
                            lambda cfg, table, params: list(self.open_alerts))
        return self

    def table(self, name):
        return [rows for t, rows in self.inserted if t == name]


def good_counts(**over):
    c = {"discovered": 1875, "senior": 148, "fresh": 140, "recorded": 140,
         "staged": 32, "unresolved": 0, "built": 2, "spend_usd": 1.0}
    c.update(over)
    return c


def test_healthy_run_writes_one_row_with_the_fields_the_ui_reads(monkeypatch):
    db = FakeDb().install(monkeypatch)
    report_run(None, started_at=STARTED, ok=True, counts=good_counts(),
               spend_usd=1.0, summary_line="140 roles recorded, 32 staged, 2 packages built")
    runs = db.table("workflow_runs")
    assert len(runs) == 1 and len(runs[0]) == 1
    row = runs[0][0]
    assert row["agent_id"] == "hunter"          # the slug every join uses
    assert row["status"] == "success"
    assert row["cost_usd"] == 1.0
    assert row["outcome_count"] == 34           # staged + built
    assert row["error_message"] is None
    assert row["metadata"]["staged"] == 32
    assert row["duration_ms"] > 0
    # the roster stamp so the agent card reads "Last run ..."
    assert ("agents", {"id": "hunter"}) == db.patched[-1][:2]


def test_failed_run_raises_a_tier_three_alert(monkeypatch):
    db = FakeDb().install(monkeypatch)
    report_run(None, started_at=STARTED, ok=False, counts={}, spend_usd=0.0,
               summary_line="run failed", error="ReadTimeout: boards-api timed out")
    alerts = db.table("silent_failures")
    assert len(alerts) == 1
    a = alerts[0][0]
    assert a["tier"] == 3 and a["workflow_id"] == WORKFLOW_ID
    assert a["failure_type"] == "run_error"
    assert "ReadTimeout" in a["detail"]
    assert a["run_count"] == 1


def test_run_that_records_nothing_is_a_failure_even_without_an_exception(monkeypatch):
    db = FakeDb().install(monkeypatch)
    report_run(None, started_at=STARTED, ok=True, counts=good_counts(recorded=0),
               spend_usd=0.0, summary_line="0 roles recorded, 0 staged, 0 packages built")
    a = db.table("silent_failures")[0][0]
    assert a["failure_type"] == "empty_run"
    assert db.table("workflow_runs")[0][0]["status"] == "success"


def test_repeat_failure_increments_instead_of_stacking_rows(monkeypatch):
    db = FakeDb(open_alerts=[{"id": "a-1", "run_count": 2,
                              "detected_at": "2026-08-28T08:27:00Z"}]).install(monkeypatch)
    report_run(None, started_at=STARTED, ok=False, counts={}, spend_usd=0.0,
               summary_line="run failed", error="boom")
    assert db.table("silent_failures") == []
    patch = [p for p in db.patched if p[0] == "silent_failures"][0]
    assert patch[1] == {"id": "a-1"} and patch[2]["run_count"] == 3


def test_healthy_run_clears_hunters_own_open_alerts(monkeypatch):
    """Nothing in the Control Center UI ever writes resolved_at, so an alert
    hunter raises and never clears would sit on the banner forever."""
    db = FakeDb(open_alerts=[{"id": "a-1", "run_count": 1,
                              "detected_at": "2026-08-28T08:27:00Z"}]).install(monkeypatch)
    report_run(None, started_at=STARTED, ok=True, counts=good_counts(),
               spend_usd=0.5, summary_line="fine")
    resolved = [p for p in db.patched if p[0] == "silent_failures"][0]
    assert resolved[1] == {"id": "a-1"}
    assert resolved[2]["resolved_at"] and resolved[2]["resolution_note"]


def test_reporting_never_raises_into_a_completed_run(monkeypatch, capsys):
    def boom(*a, **kw):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(report_mod, "db_insert", boom)
    monkeypatch.setattr(report_mod, "db_get", boom)
    monkeypatch.setattr(report_mod, "db_patch", boom)
    report_run(None, started_at=STARTED, ok=True, counts=good_counts(),
               spend_usd=0.0, summary_line="fine")   # must not raise
    assert "run reporting failed" in capsys.readouterr().out
