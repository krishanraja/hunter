"""Retiring a role Krish approved, and the evidence that has to exist first.

cmd_decline refuses any row not reading exactly "New", so nothing hunter
decides can overwrite a judgement of his, and that rule stays. This is the
other case: on 2026-09-24 he had marked Databricks and Ladders "Yes", verify
found both postings gone, and he said "drop them".

"He named it" is authority to retire a DEAD role. It is not a licence to
delete a live one, so the death is checked here rather than taken on trust.
"""
from __future__ import annotations

import pytest

import hunter.run as R


ROWS = [
    {"job_id": "databricks:dir", "company": "Databricks", "title": "Director",
     "url": "https://boards.greenhouse.io/databricks/jobs/8735590002",
     "status": "staging", "package_status": "blocked", "krish_verdict": "Yes"},
    {"job_id": "profound:vp", "company": "Profound", "title": "VP",
     # A REAL Ashby posting URL. An earlier version of this fixture used
     # ".../Profound/abc", which ats_key cannot parse (Ashby ids are 36 char
     # UUIDs), so the row fell through to the no-link branch and this file
     # proved the liveness check while never once reaching it. The mutation
     # that deletes that check survived because of it.
     "url": "https://jobs.ashbyhq.com/Profound/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
     "status": "staging", "package_status": "built", "krish_verdict": "Yes"},
    {"job_id": "boi:dir", "company": "BOI", "title": "AI Director",
     "url": "", "status": "dead", "package_status": "blocked",
     "krish_verdict": "Yes"},
    {"job_id": "vista:svp", "company": "Vista", "title": "SVP",
     "url": "", "status": "unresolved", "package_status": "none",
     "krish_verdict": "Yes"},
]


@pytest.fixture
def wired(monkeypatch):
    seen = {"retired": [], "live": True, "raise": None}
    monkeypatch.setattr(R, "build_context",
                        lambda: (object(), type("C", (), {"sheet_headers": []})()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: object())
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: list(ROWS))
    monkeypatch.setattr(R, "retire_dead_posting",
                        lambda cfg, canon, sheet, job_id, **kw:
                        seen["retired"].append(job_id))

    def fake_fetch(fn, slug, pid):
        if seen["raise"]:
            raise seen["raise"]
        return seen["live"], "", ""

    monkeypatch.setattr(R, "fetch_with_retry", fake_fetch)
    return seen


def test_a_dead_posting_he_named_is_retired(wired):
    wired["live"] = False
    assert R.cmd_retire("databricks:dir", apply=True) == 0
    assert wired["retired"] == ["databricks:dir"]


def test_a_live_posting_is_refused_however_it_was_named(wired):
    """The whole reason the check is here. Retiring a live role would write
    'Declined - dead posting' onto his sheet about a job he can still apply
    for, which is a claim hunter has no evidence for."""
    wired["live"] = True
    assert R.cmd_retire("profound:vp", apply=True) == 1
    assert wired["retired"] == []


def test_a_board_it_cannot_reach_is_refused_not_assumed_dead(wired):
    """Unreadable is not dead and must never be reported as such."""
    wired["raise"] = RuntimeError("connection reset")
    assert R.cmd_retire("databricks:dir", apply=True) == 1
    assert wired["retired"] == []


def test_a_role_already_recorded_dead_with_no_ats_link_is_allowed(wired):
    """BOI has no board hunter can read, so there is nothing to re-check. Its
    own row already says dead, which is the evidence."""
    assert R.cmd_retire("boi:dir", apply=True) == 0
    assert wired["retired"] == ["boi:dir"]


def test_no_link_and_not_recorded_dead_is_refused(wired):
    """Vista is UNVERIFIABLE, which is the third answer verify exists to give.
    Neither live nor dead is not a licence to retire."""
    assert R.cmd_retire("vista:svp", apply=True) == 1
    assert wired["retired"] == []


def test_a_job_id_that_matches_nothing_fails_loudly(wired):
    """A typo must not leave a role he asked to be rid of sitting there while
    the run reports success."""
    wired["live"] = False
    assert R.cmd_retire("databricks:typo", apply=True) == 1
    assert wired["retired"] == []


def test_one_bad_id_retires_none_of_the_batch(wired):
    wired["live"] = False
    assert R.cmd_retire("databricks:dir,nonsense", apply=True) == 1
    assert wired["retired"] == []


def test_the_dry_run_writes_nothing(wired):
    wired["live"] = False
    assert R.cmd_retire("databricks:dir") == 0
    assert wired["retired"] == []


def test_naming_nothing_is_a_usage_error_not_a_no_op(wired):
    assert R.cmd_retire("", apply=True) == 2
    assert wired["retired"] == []
