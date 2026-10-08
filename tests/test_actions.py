"""Buttons pressed in Control Center reach column A as the words he would have
typed there, and every press is closed with what really happened (actions.py)."""
from hunter import actions


class Row:
    def __init__(self, n, company, role, url):
        self.row_number, self.company, self.role, self.jd_url = n, company, role, url


class Sheet:
    def __init__(self, rows):
        self.rows, self.written = rows, []

    def read_pipeline(self, headers):
        return self.rows

    def set_verdicts(self, mapping):
        self.written.append(mapping)
        return len(mapping)


ROLE = {"job_id": "acme:vp-revenue", "company": "Acme", "title": "VP Revenue",
        "url": "https://jobs.acme/1", "job_url": None, "application_state": None}


def _wire(monkeypatch, queued, role=ROLE):
    closed, patched = [], []

    def get(cfg, table, params):
        if table == actions.TABLE:
            return queued
        if params.get("select") == "application_state":
            return [{"application_state": patched[-1][1]["application_state"]}] if patched else []
        return [role] if role else []

    def patch(cfg, table, match, body):
        (closed if table == actions.TABLE else patched).append((match, body))
    monkeypatch.setattr(actions, "db_get", get)
    monkeypatch.setattr(actions, "db_patch", patch)
    return closed, patched


def test_each_press_becomes_exactly_what_he_would_type():
    assert actions.column_a({"verdict": "yes"}) == "Yes"
    assert actions.column_a({"verdict": "applied"}) == "Applied"
    assert actions.column_a({"verdict": "declined", "reason": "comp below bar"}) == "Declined - comp below bar"
    assert actions.column_a({"verdict": "declined", "reason": "made up"}) == ""
    assert actions.column_a({"verdict": "maybe"}) == ""


def test_a_decline_lands_on_his_row_found_by_the_posting_link(monkeypatch):
    closed, _ = _wire(monkeypatch, [{"id": 1, "kind": "verdict", "job_id": ROLE["job_id"],
                                     "payload": {"verdict": "declined", "reason": "stage wrong"}}])
    sheet = Sheet([Row(7, "Other", "VP Revenue", "https://x"), Row(9, "Acme", "VP Revenue", "https://jobs.acme/1")])
    actions.apply_queued(None, sheet, ["h"], apply=True)
    assert sheet.written == [{9: "Declined - stage wrong"}]
    assert closed[-1][1]["status"] == "done"


def test_a_role_no_longer_on_pipeline_fails_with_the_reason(monkeypatch):
    closed, _ = _wire(monkeypatch, [{"id": 1, "kind": "verdict", "job_id": ROLE["job_id"],
                                     "payload": {"verdict": "yes"}}])
    sheet = Sheet([])
    actions.apply_queued(None, sheet, ["h"], apply=True)
    assert sheet.written == [] and closed[-1][1]["status"] == "failed"
    assert "Pipeline" in closed[-1][1]["result"]


def test_prepare_reports_what_the_approval_run_returned(monkeypatch):
    closed, _ = _wire(monkeypatch, [{"id": 1, "kind": "prepare", "job_id": ROLE["job_id"], "payload": {}}])
    actions.apply_queued(None, Sheet([]), ["h"], apply=True, prepare=lambda j: (False, "not built yet"))
    assert closed[-1][1] == {**closed[-1][1], "status": "failed", "result": "not built yet"}


def test_an_outcome_is_recorded_and_read_back(monkeypatch):
    closed, patched = _wire(monkeypatch, [{"id": 1, "kind": "outcome", "job_id": ROLE["job_id"],
                                           "payload": {"outcome": "interview"}}])
    actions.apply_queued(None, Sheet([]), ["h"], apply=True)
    assert patched[-1][1] == {"application_state": "Interviewing"}
    assert closed[-1][1]["status"] == "done"


def test_a_dry_run_writes_nothing(monkeypatch):
    closed, patched = _wire(monkeypatch, [{"id": 1, "kind": "verdict", "job_id": ROLE["job_id"],
                                           "payload": {"verdict": "yes"}}])
    sheet = Sheet([Row(9, "Acme", "VP Revenue", "https://jobs.acme/1")])
    lines = actions.apply_queued(None, sheet, ["h"], apply=False)
    assert sheet.written == [] and closed == [] and patched == []
    assert "would write 'Yes' to row 9" in lines[1]


def test_nothing_here_can_press_submit():
    import inspect
    src = inspect.getsource(actions)
    assert "cmd_submit" not in src and "--confirm" not in src and "confirm=True" not in src
