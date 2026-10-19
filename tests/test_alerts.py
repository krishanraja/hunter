"""When hunter is allowed to interrupt him. The failure this module exists to
prevent is silence; the failure it must not cause is noise, so most of these
tests are about not sending."""
import datetime

import pytest

from hunter import alerts
from hunter.sheet import AI_WHY, COLS, PKG_DEAD, SheetRow, make_row

EM_DASH = chr(0x2014)


def row(n, verdict="New", score=7, **over):
    cells = make_row(company=f"Co{n}", role="VP Strategy",
                     jd_url="https://job-boards.greenhouse.io/c/jobs/1",
                     score=score, source="ats_sweep", jd_snippet="Does a thing.")
    cells[COLS["Verdict"]] = verdict
    for k, v in over.items():
        cells[COLS[k]] = v
    return SheetRow(row_number=n, cells=cells, verdict=verdict,
                    company=f"Co{n}", role="VP Strategy", jd_url=None)


def ago(days):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return t.isoformat()


# ---------- what the sheet holds ----------

def test_the_state_counts_what_he_would_count():
    rows = [row(3), row(4), row(5, "Yes"), row(6, "Applied"),
            row(7, "Yes", **{"Package Status": PKG_DEAD})]
    st = alerts.batch_state(rows)
    # skipped_read False: no record of the blanks he scored past was given,
    # and the count must not pass for one that left them out.
    assert st == {"to_review": 2, "approved": 2, "applied": 1,
                  "dead_approved": 1, "total": 5, "skipped_read": False}


def test_a_declined_row_is_neither_waiting_nor_approved():
    st = alerts.batch_state([row(3, "Declined - comp below bar")])
    assert st["to_review"] == 0 and st["approved"] == 0


# ---------- the email ----------

def test_the_subject_says_how_many_and_how_many_are_waiting():
    rows = [row(3), row(4), row(5, "Yes")]
    subject, html, text = alerts.review_email(rows, staged=2)
    assert subject == "2 new roles to review, 2 waiting in total"
    assert "Score them on the pipeline" in html
    assert alerts.pipeline_url() in html and alerts.pipeline_url() in text


def test_one_role_reads_as_one_role():
    subject, _, _ = alerts.review_email([row(3)], staged=1)
    assert subject.startswith("1 new role to review")


def test_the_strongest_roles_lead():
    rows = [row(3, score=5), row(4, score=9), row(5, score=7)]
    _, html, _ = alerts.review_email(rows, staged=3)
    assert html.index("Co4") < html.index("Co5") < html.index("Co3")


def test_a_dead_approval_is_called_out_in_red():
    rows = [row(3), row(4, "Yes", **{"Package Status": PKG_DEAD})]
    _, html, _ = alerts.review_email(rows, staged=1)
    assert "have closed since" in html


def test_the_email_carries_no_em_dash():
    subject, html, text = alerts.review_email([row(3)], staged=1)
    assert EM_DASH not in subject + html + text


def test_html_from_a_posting_cannot_break_the_email():
    rows = [row(3, **{AI_WHY: "<script>alert(1)</script> and & more"})]
    _, html, _ = alerts.review_email(rows, staged=1)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_the_email_says_what_happens_next():
    """Only what hunter does: a score builds a few an hour, a Yes builds in
    the batch run or from the button and never hourly, and a lowered score
    cancels only what the score built (a Yes package keeps its application
    whatever K says)."""
    _, html, _ = alerts.review_email([row(3)], staged=1)
    assert "Score a role 7 or more and hunter builds the CV and the cover letter, a few an hour." in html
    assert ("A Yes in column A builds at the next batch run, or when you press "
            "Build packages in Control Center.") in html
    assert "Lower the score below 7 on a role your score built" in html
    assert "within a few hours" not in html
    assert "Lower a score below 7 and anything waiting" not in html


# ---------- not sending ----------

class FakeDb:
    def __init__(self, sent=(), tables=None):
        self.sent = list(sent)
        self.tables = tables or {}
        self.inserted = []

    def get(self, cfg, table, params):
        if table == alerts.TABLE:
            fp, kind = params.get("fingerprint", ""), params.get("kind", "")
            hit = [s for s in self.sent
                   if f"eq.{s[0]}" == kind and f"eq.{s[1]}" == fp]
            return [{"id": "x"}] if hit else []
        return self.tables.get(table, [])

    def insert(self, cfg, table, rows, **kw):
        self.inserted.append((table, rows))


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(alerts, "db_get", fake.get)
    monkeypatch.setattr(alerts, "db_insert", fake.insert)
    return fake


def test_a_run_that_staged_nothing_sends_nothing(db, monkeypatch):
    monkeypatch.setattr(alerts.notify, "send_email",
                        lambda *a, **k: pytest.fail("should not have sent"))
    out = alerts.send_review_ready(None, [row(3)], staged=0)
    assert out["sent"] is False and "nothing new" in out["reason"]


def test_the_same_batch_is_never_announced_twice(db, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts.notify, "send_email",
                        lambda *a, **k: sent.append(a) or {"sent": True})
    rows = [row(3), row(4)]
    first = alerts.send_review_ready(None, rows, staged=2)
    assert first["sent"] and len(sent) == 1
    db.sent = [(alerts.REVIEW_READY, db.inserted[0][1][0]["fingerprint"])]
    second = alerts.send_review_ready(None, rows, staged=2)
    assert second["sent"] is False and len(sent) == 1


def test_a_batch_that_grew_is_announced_again(db, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts.notify, "send_email",
                        lambda *a, **k: sent.append(a) or {"sent": True})
    alerts.send_review_ready(None, [row(3)], staged=1)
    db.sent = [(alerts.REVIEW_READY, db.inserted[0][1][0]["fingerprint"])]
    alerts.send_review_ready(None, [row(3), row(4)], staged=1)
    assert len(sent) == 2


# ---------- the watchdog ----------

def _runs(**kw):
    base = {"run_at": ago(1), "status": "success", "outcome": {},
            "workflow_name": "hunter", "error_message": None}
    base.update(kw)
    return base


def test_a_turning_loop_reports_nothing(monkeypatch):
    fake = FakeDb(tables={"workflow_runs": [_runs()],
                          "hunter_application_approvals": []})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    assert alerts.trouble_checks(None) == []


def test_a_stalled_schedule_is_reported(monkeypatch):
    fake = FakeDb(tables={"workflow_runs": [_runs(run_at=ago(20))],
                          "hunter_application_approvals": []})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    problems = alerts.trouble_checks(None)
    assert any(p["kind"] == "the schedule has stopped" for p in problems)


def test_two_failures_in_a_row_are_reported(monkeypatch):
    fake = FakeDb(tables={
        "workflow_runs": [_runs(status="failed", error_message="boom"),
                          _runs(status="failed"), _runs()],
        "hunter_application_approvals": []})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    problems = alerts.trouble_checks(None)
    kinds = [p["kind"] for p in problems]
    assert "runs are failing" in kinds
    assert any("boom" in p["detail"] for p in problems)


def test_an_application_he_has_not_answered_is_reported(monkeypatch):
    fake = FakeDb(tables={
        "workflow_runs": [_runs()],
        "hunter_application_approvals": [
            {"token": "t", "company": "Acme", "role": "VP",
             "created_at": ago(5)}]})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    problems = alerts.trouble_checks(None)
    assert any(p["kind"] == "applications waiting on you" for p in problems)


def test_a_fresh_application_is_not_nagged_about(monkeypatch):
    fake = FakeDb(tables={
        "workflow_runs": [_runs()],
        "hunter_application_approvals": [
            {"token": "t", "company": "Acme", "role": "VP",
             "created_at": ago(0.2)}]})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    assert alerts.trouble_checks(None) == []


def test_sourcing_that_reads_postings_and_stages_none_is_reported(monkeypatch):
    empty = _runs(outcome={"discovered": 900, "staged": 0})
    fake = FakeDb(tables={"workflow_runs": [empty, empty, _runs()],
                          "hunter_application_approvals": []})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    problems = alerts.trouble_checks(None)
    assert any(p["kind"] == "sourcing is finding nothing" for p in problems)


def test_a_condition_already_reported_is_not_reported_again(monkeypatch):
    fake = FakeDb(tables={"workflow_runs": [_runs(run_at=ago(20))],
                          "hunter_application_approvals": []})
    fake.sent = [(alerts.TROUBLE, "stalled:20d")]
    monkeypatch.setattr(alerts, "db_get", fake.get)
    monkeypatch.setattr(alerts, "db_insert", fake.insert)
    monkeypatch.setattr(alerts.notify, "send_email",
                        lambda *a, **k: pytest.fail("should not have sent"))
    out = alerts.send_trouble(None)
    assert out["sent"] is False and "already been reported" in out["reason"]


def test_the_trouble_email_names_every_problem():
    problems = [{"kind": "the schedule has stopped", "detail": "20 days",
                 "fingerprint": "a"},
                {"kind": "applications waiting on you", "detail": "3 of them",
                 "fingerprint": "b"}]
    subject, html, text = alerts.trouble_email(problems)
    assert "2 things" in subject
    for p in problems:
        assert p["kind"] in html and p["kind"] in text
    assert EM_DASH not in subject + html + text


# ---------- every path that stages roles tells him ----------

def test_the_source_path_emails_him_too():
    """Only cmd_run sent the review email, so the Control Center "Find roles"
    button sourced roles and said nothing. The one human step in the loop had
    nothing telling him it was his turn."""
    import inspect
    from hunter import run
    src = inspect.getsource(run.run_command)
    assert "send_review_ready" in src


def test_every_staging_path_is_covered():
    import inspect
    from hunter import run
    for fn in (run.run_command, run.cmd_run):
        assert "send_review_ready" in inspect.getsource(fn), fn.__name__


def test_an_opened_application_never_reported_is_asked_about(monkeypatch):
    """openrouter, 25 September: opened through the extension, sent, and the
    report never arrived. The role sat at Yes with nothing saying why."""
    fake = FakeDb(tables={
        "workflow_runs": [_runs()],
        "hunter_application_approvals": [
            {"token": "or1", "company": "openrouter", "role": "Director, Channel Partnerships",
             "created_at": ago(9), "opened_at": ago(8)}]})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    problems = alerts.trouble_checks(None)
    asked = [p for p in problems if p["kind"] == "did you send these?"]
    assert asked and "openrouter" in asked[0]["detail"]
    assert "column A" in asked[0]["detail"]
    # and it is not ALSO nagged as an unanswered approval
    assert not any(p["kind"] == "applications waiting on you" for p in problems)


def test_a_batch_the_schedule_gave_up_on_is_reported(monkeypatch):
    import datetime as dt
    from hunter import schedule
    slot = schedule.latest_slot(dt.datetime.now(dt.timezone.utc))
    after = (slot + dt.timedelta(minutes=13)).isoformat()
    fake = FakeDb(tables={
        "workflow_runs": [_runs(run_at=(slot - dt.timedelta(days=1)).isoformat())],
        "hunter_application_approvals": [],
        "hunter_commands": [{"state": "failed", "requested_at": after},
                            {"state": "failed", "requested_at": after}]})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    problems = alerts.trouble_checks(None)
    assert any(p["kind"] == "a batch could not run" for p in problems)


def test_with_control_center_the_button_opens_the_sheet_and_prepare_stays_a_link():
    """He rules on the sheet in K and M since 2026-10-13, so the button opens
    Pipeline; Control Center is where he prepares an application."""
    url = "https://cc.example/#/people?lane=bridges"
    _, html, text = alerts.review_email([row(3)], staged=1, act_url=url)
    assert html.index(alerts.pipeline_url()) < html.index(url)
    assert "Score them on the pipeline" in html and "Rule on them" not in html
    assert url in text and alerts.pipeline_url() in text


def test_the_email_never_promises_a_pack_nothing_sends():
    _, html, _ = alerts.review_email([row(3)], staged=1)
    assert "emails you the pack" not in html and "Prepare" in html


# ---------- the letter writer ----------

def _letter(status, requested, drafted=None, ref="r1"):
    return {"ref": ref, "status": status, "requested_at": requested, "drafted_at": drafted}


def _writer_db(monkeypatch, drafts):
    fake = FakeDb(tables={"workflow_runs": [_runs()], "hunter_application_approvals": [],
                          "hunter_drafts": drafts})
    monkeypatch.setattr(alerts, "db_get", fake.get)
    return [p for p in alerts.trouble_checks(None) if "letter" in p["kind"]]


def test_a_writer_that_has_stopped_is_reported(monkeypatch):
    got = _writer_db(monkeypatch, [_letter("queued", ago(2)), _letter("queued", ago(3), ref="r2"),
                                   _letter("used", ago(6), drafted=ago(5), ref="r3")])
    assert [p["kind"] for p in got] == ["letters waiting on the writer"]
    assert got[0]["detail"].startswith("2 letter(s) have waited more than 36 hours")


def test_a_writer_working_through_a_backlog_is_left_alone(monkeypatch):
    assert _writer_db(monkeypatch, [_letter("queued", ago(2)),
                                    _letter("drafted", ago(2), drafted=ago(0.2), ref="r2")]) == []
    assert _writer_db(monkeypatch, [_letter("queued", ago(0.5))]) == [], "asked today"


def test_a_role_whose_letters_keep_failing_is_reported_once_per_set(monkeypatch):
    rows = [_letter("failed", ago(1), drafted=ago(1)) for _ in range(3)] + \
        [_letter("failed", ago(1), drafted=ago(1), ref="r2")]
    got = _writer_db(monkeypatch, rows)
    assert [(p["kind"], p["fingerprint"]) for p in got] == \
        [("letters the writer cannot get right", "letter-fails:r1")]
    assert "r1" not in got[0]["detail"], "counts only"


def test_the_waiting_reminders_go_once_a_week_whatever_the_count(monkeypatch):
    """19 October: the key held the count, so each approval made or cancelled
    mailed him again, six times in eight hours."""
    def problems_for(n_old, n_opened):
        rows = [{"token": f"a{i}", "company": "Acme", "role": "VP",
                 "created_at": ago(5)} for i in range(n_old)]
        rows += [{"token": f"o{i}", "company": "Acme", "role": "VP",
                  "created_at": ago(5), "opened_at": ago(3)} for i in range(n_opened)]
        fake = FakeDb(tables={"workflow_runs": [_runs()],
                              "hunter_application_approvals": rows})
        monkeypatch.setattr(alerts, "db_get", fake.get)
        return {p["kind"]: p["fingerprint"] for p in alerts.trouble_checks(None)}
    a, b = problems_for(2, 1), problems_for(25, 9)
    for kind in ("applications waiting on you", "did you send these?"):
        assert a[kind] == b[kind]
    import datetime as dt
    monkeypatch.setattr(alerts, "_now",
                        lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7))
    assert problems_for(2, 1)["applications waiting on you"] != a["applications waiting on you"]
