"""Column A acts within the hour, in an order that loses nothing.

On 3 October Krish typed Applied against openrouter and BOI. Nothing hourly
read column A, so both would have sat on Pipeline as live work until the next
full run, and a full run could be days away. Worse, openrouter already read
"Yes" in the database, and reconcile only ever recorded a row's first verdict,
so the database would never have heard he applied at all.
"""
import pytest

from hunter import run as R
from hunter import sheet as sheet_mod
from hunter.sheet import N_COLS, SheetRow


class Canon:
    sheet_headers = sheet_mod.HEADERS


def srow(n, company, role, verdict, status="Not applied", url=None):
    cells = [""] * N_COLS
    cells[sheet_mod.COLS["Application Status"]] = status
    return SheetRow(row_number=n, cells=cells, verdict=verdict,
                    company=company, role=role, jd_url=url)


class FakeSheet:
    def __init__(self, rows):
        self.rows = list(rows)
        self.applied = {}
        self.calls = []

    def read_pipeline(self, headers):
        self.calls.append("read")
        return list(self.rows)

    def mark_applied(self, row_number, *, when):
        self.calls.append(f"mark_applied:{row_number}")
        self.applied[row_number] = when


@pytest.fixture
def wired(monkeypatch):
    seen = {"patch": [], "insert": [], "events": [], "state": [], "order": []}
    db = {"hunter_seen_roles": [], "approvals": []}

    def get(cfg, table, params):
        if table == "hunter_application_approvals":
            return list(db["approvals"])
        return list(db["hunter_seen_roles"])

    monkeypatch.setattr(R, "db_get", get)
    monkeypatch.setattr(R, "db_patch", lambda cfg, t, k, v: seen["patch"].append((t, k, v)))
    monkeypatch.setattr(R, "db_insert", lambda cfg, t, rows, **kw: seen["insert"].append((t, rows)))
    monkeypatch.setattr(R, "record_verdict_event",
                        lambda cfg, d, text, kind, code: seen["events"].append((d["job_id"], text)))
    monkeypatch.setattr(R.amend, "load_state", lambda cfg: {"x": {}})
    monkeypatch.setattr(R.amend, "detect",
                        lambda rows, paired, state: seen["order"].append("amend") or [])
    monkeypatch.setattr(R.amend, "record", lambda cfg, found: len(found))

    from hunter.apply import approval
    monkeypatch.setattr(approval, "set_state",
                        lambda cfg, token, state, **kw: seen["state"].append((token, state, kw)))

    def archive(sheet, canon):
        seen["order"].append("archive")
        moved = [r for r in sheet.rows
                 if R.verdicts.parse(r.verdict)[0] in ("applied", "rejection")]
        sheet.rows = [r for r in sheet.rows if r not in moved]
        return len(moved), []

    monkeypatch.setattr(R, "archive_decided", archive)
    return seen, db


def test_applied_rows_are_marked_recorded_closed_and_moved(wired):
    seen, db = wired
    db["hunter_seen_roles"] = [
        {"job_id": "ashby:openrouter:dcp", "company": "openrouter",
         "title": "Director, Channel Partnerships", "krish_verdict": "Yes",
         "job_url": "https://jobs.ashbyhq.com/openrouter/abc", "url": ""},
    ]
    db["approvals"] = [{"token": "tok-or", "job_id": "ashby:openrouter:dcp",
                        "state": "awaiting"}]
    sheet = FakeSheet([
        srow(6, "openrouter", "Director, Channel Partnerships", "Applied"),
        srow(19, "BOI (Board of Innovation)", "AI Transformation Director", "Applied"),
        srow(7, "Rogo", "Director, Content and Data Partnerships",
             "Declined - domain expertise"),
        srow(9, "versapay", "VP of Strategic Partnerships & Ecosystems", "Yes"),
    ])
    summary = []
    out = R.settle_step(object(), Canon(), sheet, summary, apply=True)

    # both Applied rows are marked on the sheet, BOI included though it has no
    # approval and no database pairing
    assert set(sheet.applied) == {6, 19}
    # openrouter's Yes in the database becomes Applied: the change is recorded
    assert ("ashby:openrouter:dcp", "Applied") in seen["events"]
    assert any(v.get("application_state") == "Applied" for _, _, v in seen["patch"])
    # its open approval is closed on his word, and says so
    assert seen["state"] and seen["state"][0][0] == "tok-or"
    assert seen["state"][0][2]["failure_reason"].startswith(R.SAID_SO)
    # the unpaired Rogo decline still becomes a learning event
    assert any(t == "hunter_verdict_events" for t, _ in seen["insert"])
    # Yes stays: it is work in flight, not a decision
    assert [r.company for r in sheet.rows] == ["versapay"]
    assert out["archived"] == 3 and out["approvals_closed"] == 1


def test_edits_are_read_before_the_rows_leave(wired):
    seen, db = wired
    sheet = FakeSheet([srow(7, "Rogo", "Director", "Declined - domain expertise")])
    R.settle_step(object(), Canon(), sheet, [], apply=True)
    assert seen["order"] == ["amend", "archive"]


def test_the_sheet_is_written_before_the_database(wired):
    seen, db = wired
    db["hunter_seen_roles"] = [{"job_id": "j1", "company": "Acme", "title": "VP",
                                "krish_verdict": "Applied"}]
    sheet = FakeSheet([srow(3, "Acme", "VP", "Applied")])

    def refuse(row_number, *, when):
        raise sheet_mod.SheetError("read back as ''")

    sheet.mark_applied = refuse
    summary = []
    R.settle_step(object(), Canon(), sheet, summary, apply=True)
    assert not any(v.get("application_state") for _, _, v in seen["patch"])
    assert any("not written" in line for line in summary)


def test_a_dry_run_writes_nothing(wired):
    seen, db = wired
    db["approvals"] = [{"token": "t", "job_id": "j1", "state": "awaiting"}]
    db["hunter_seen_roles"] = [{"job_id": "j1", "company": "Acme", "title": "VP",
                                "krish_verdict": "Yes"}]
    sheet = FakeSheet([srow(3, "Acme", "VP", "Applied")])
    out = R.settle_step(object(), Canon(), sheet, [], apply=False)
    assert seen["patch"] == [] and seen["insert"] == [] and seen["state"] == []
    assert sheet.applied == {} and len(sheet.rows) == 1
    assert out["decided"] == 1 and out["approvals_closed"] == 1


def test_nothing_decided_touches_nothing(wired):
    seen, db = wired
    sheet = FakeSheet([srow(3, "Acme", "VP", "Yes"), srow(4, "Beta", "GM", "New")])
    out = R.settle_step(object(), Canon(), sheet, [], apply=True)
    assert out["decided"] == 0 and seen["order"] == []


def test_reconcile_records_a_verdict_that_moved_on():
    """The full run's reconcile had the same blind spot: only a row's first
    verdict was recorded."""
    import inspect
    src = inspect.getsource(R.reconcile)
    assert "moved_on" in src and "learn.classify(stored)[0] != verdict_kind" in src


def test_the_apply_by_hand_email_names_a_step_that_exists():
    """It told him to press an Applied button. There was no such button."""
    import inspect
    src = inspect.getsource(R)
    assert "Applied button" not in src
    assert "type <b>Applied</b> in column A" in src
