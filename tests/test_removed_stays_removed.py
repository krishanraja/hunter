"""A row he had removed by name must not come back.

2026-09-24, and this one wasted a whole cycle. Two rows were removed by name
at 17:57 and confirmed gone at 18:12. The 19:15 process run put both back, and
the evening's approvals run then read the form of each one.

reconcile treats a database row with no matching sheet row as a row MISSING
from the sheet and appends it. Its "he deleted this, respect it" branch is
gated on not being decided:

    decided = bool(krish_verdict) or package_status != "none"
    if presented_at and not decided: ...skip

Both rows carried his verdict "Yes" and a built package, so decided was True
and the protection did not apply. Keeping the verdict while removing the row,
which is exactly what he asked for, is the case that branch does not cover.
"""
from __future__ import annotations

import inspect

import hunter.run as R


def test_reconcile_asks_the_database_to_exclude_removed_rows(monkeypatch):
    """The actual query, not the source text.

    A first version of this test grepped run.py for the interpolated string and
    failed, because the file holds an f-string. Grepping source would also have
    passed for a query built and then never used.
    """
    seen = {}

    def fake_db_get(cfg, table, params):
        if table == "hunter_seen_roles" and "presented_at" in params.get("select", ""):
            seen["status"] = params.get("status")
        return []

    class FakeSheet:
        def read_pipeline(self, headers):
            return []

        def read_archive(self):
            return []

    monkeypatch.setattr(R, "db_get", fake_db_get)
    canon = type("C", (), {"sheet_headers": []})()
    try:
        R.reconcile(object(), canon, FakeSheet())
    except Exception:
        # Whatever the rest of reconcile needs, the query happened first.
        pass
    assert seen.get("status") == f"not.in.(duplicate,{R.REMOVED_STATUS})", \
        f"reconcile asked for status={seen.get('status')!r}"


def test_the_pruner_records_the_removal():
    """Deleting the sheet row alone is not enough and never was."""
    src = inspect.getsource(R.cmd_prune_sheet)
    assert "REMOVED_STATUS" in src
    i = src.index("delete_rows")
    assert "REMOVED_STATUS" in src[i:], \
        "the removal is recorded before the rows are deleted, or not at all"


def test_the_pruner_does_not_touch_his_verdict():
    """His decision, recorded 2026-09-24: leave the verdicts, remove only the
    rows. The status says where the row is, not what he thought of the role."""
    src = inspect.getsource(R.cmd_prune_sheet)
    i = src.index("REMOVED_STATUS")
    patch = src[i - 300:i + 200]
    assert "krish_verdict" not in patch, \
        "the removal is rewriting his verdict"


def test_a_failure_to_record_it_is_reported_not_swallowed():
    """If the marker cannot be written, the rows WILL come back, and saying
    nothing would mean claiming a removal that does not hold."""
    src = inspect.getsource(R.cmd_prune_sheet)
    i = src.index("REMOVED_STATUS")
    assert "could NOT be recorded" in src[i:], \
        "a failed marker write is silent"


def test_removed_is_distinct_from_dropped():
    """dropped means a gate refused the role. removed means his row is off the
    sheet and says nothing about the role at all. Collapsing them would teach
    the learning loop that he rejected something he did not."""
    assert R.REMOVED_STATUS != "dropped"
    assert R.REMOVED_STATUS != "duplicate"
