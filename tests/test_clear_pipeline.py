"""Clearing Pipeline after he has applied to a batch.

His words, 2026-10-20: "ok, all jobs have been applied, clear the pipeline".
A row with a package is recorded applied on his word; every other row leaves
with no verdict; nothing is mailed; Pipeline ends empty.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as R  # noqa: E402
from hunter import notify, sheet as S  # noqa: E402
from hunter.apply import approval  # noqa: E402
from test_score_builds import srow  # noqa: E402


def _row(n, company, status):
    r = srow("New", "", "", n=n, company=company, role="VP",
             url=f"https://job-boards.greenhouse.io/{company.lower()}/jobs/{n}")
    r.cells[S.COLS["Package Status"]] = status
    return r


class Tabs:
    def __init__(self, rows, refuse=()):
        self.rows, self.refuse = list(rows), set(refuse)
        self.applied_marks, self.verdicts, self.archived = [], {}, []

    def read_pipeline(self, headers):
        return list(self.rows)

    def mark_applied(self, rn, *, when):
        self.applied_marks.append(rn)

    def set_verdicts(self, mapping):
        self.verdicts.update(mapping)

    def archive_rows(self, rows, *, archive_tab, archive_sheet_id, headers, report=None):
        go = [r for r in rows if r.row_number not in self.refuse]
        self.archived += go
        self.rows = [r for r in self.rows if r not in go]
        if report is not None:
            report["moved"] = [r.row_number for r in go]
        return len(go)


def _world(monkeypatch, tabs):
    roles = [{"job_id": f"{r.company.lower()}:vp", "company": r.company, "title": "VP",
              "url": r.jd_url, "job_url": r.jd_url, "status": "staging",
              "application_state": None} for r in tabs.rows]
    approvals = [{"token": "t1", "state": approval.AWAITING, "job_id": "built:vp"}]
    patched, states = [], []
    monkeypatch.setattr(R, "build_context", lambda: (object(), type("C", (), {"sheet_headers": []})()))
    monkeypatch.setattr(R, "GoogleServiceAccount", lambda cfg: type("G", (), {"access_token": "x"})())
    monkeypatch.setattr(R, "Sheet", lambda token: tabs)

    def get(cfg, table, q):
        if table == approval.TABLE:
            return [a for a in approvals if q.get("job_id") == f"eq.{a['job_id']}"]
        return roles
    monkeypatch.setattr(R, "db_get", get)
    monkeypatch.setattr(R, "db_patch", lambda cfg, t, k, v: patched.append((k["job_id"], v)))
    monkeypatch.setattr(approval, "set_state", lambda cfg, tok, st, **kw: states.append((tok, st, kw)))
    monkeypatch.setattr(notify, "send_email", lambda *a, **k: (_ for _ in ()).throw(AssertionError("mailed")))
    return patched, states


def test_applied_rows_are_recorded_on_his_word_and_the_rest_leave_without_a_verdict(monkeypatch):
    tabs = Tabs([_row(3, "Built", S.PKG_BUILT_DIRECT), _row(4, "Fresh", "Not started"),
                 _row(5, "Dead", S.PKG_DEAD)])
    patched, states = _world(monkeypatch, tabs)
    assert R.cmd_clear_pipeline(apply=True) == 0
    assert tabs.applied_marks == [3] and tabs.verdicts == {3: R.verdicts.APPLIED}
    assert states == [("t1", approval.SUBMITTED, {"submitted_at": states[0][2]["submitted_at"],
                                                  "failure_reason": R.SAID_SO})]
    assert ("built:vp", {"application_state": R.APPLIED_STATE,
                         "applied_at": patched[0][1]["applied_at"]}) in patched
    assert ("fresh:vp", {"status": "set_aside"}) in patched
    assert ("dead:vp", {"status": "set_aside"}) in patched
    assert not any(j == "fresh:vp" and "application_state" in v for j, v in patched)
    assert tabs.rows == []


def test_a_refused_row_stays_and_the_command_says_so(monkeypatch):
    tabs = Tabs([_row(3, "Built", S.PKG_BUILT_DIRECT), _row(4, "Fresh", "Not started")],
                refuse={4})
    patched, _ = _world(monkeypatch, tabs)
    assert R.cmd_clear_pipeline(apply=True) == 1
    assert [r.row_number for r in tabs.rows] == [4]
    assert ("fresh:vp", {"status": "set_aside"}) not in patched


def test_a_dry_run_moves_nothing(monkeypatch):
    tabs = Tabs([_row(3, "Built", S.PKG_BUILT_DIRECT)])
    _world(monkeypatch, tabs)
    assert R.cmd_clear_pipeline(apply=False) == 0
    assert len(tabs.rows) == 1 and not tabs.applied_marks
