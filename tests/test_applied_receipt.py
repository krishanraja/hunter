"""What has to be true once an application is actually sent.

Krish replied APPROVE, nothing read it, and when he asked whether Harvey had been
applied for there was no answer anywhere: the Pipeline row still said "Not
applied", the role row's application_state was null, the row was still on the
Pipeline tab, and no message had been sent. Four writes and a receipt, none of
which existed.
"""
from __future__ import annotations

import pytest

import hunter.run as R
from hunter import verdicts
from hunter import sheet as sheet_mod
from hunter.sheet import SheetRow, N_COLS, APPLIED_STATUS


class FakeSheet:
    def __init__(self, rows, archived=()):
        self._rows = rows
        self._archived = list(archived)
        self.applied: dict[int, str] = {}
        self.verdicts_set: dict[int, str] = {}

    def read_pipeline(self, headers):
        return self._rows

    def read_tab_values(self, rng):
        """The Applied tab, as raw cells, in the same 30 column order."""
        grid = []
        for company, role in self._archived:
            cells = [""] * N_COLS
            cells[sheet_mod.COLS["Business"]] = company
            cells[sheet_mod.COLS["Role"]] = role
            grid.append(cells)
        return grid

    def mark_applied(self, row_number, *, when):
        self.applied[row_number] = when

    def set_verdicts(self, mapping):
        self.verdicts_set.update(mapping)
        return len(mapping)


def row(n, company, role):
    return SheetRow(row_number=n, cells=[""] * N_COLS, verdict="Yes",
                    company=company, role=role, jd_url=None)


@pytest.fixture
def wired(monkeypatch):
    seen = {"patch": [], "archive": 0, "mail": []}
    monkeypatch.setattr(R, "db_patch",
                        lambda cfg, table, match, values: seen["patch"].append(
                            (table, match, values)))
    monkeypatch.setattr(R, "cmd_archive",
                        lambda apply=False: seen.__setitem__("archive",
                                                             seen["archive"] + 1))
    monkeypatch.setattr(R, "send_applied_receipt",
                        lambda cfg, **kw: seen["mail"].append(kw))
    return seen


class Canon:
    sheet_headers = []


def test_a_sent_application_updates_the_sheet_the_row_and_krish(wired):
    sheet = FakeSheet([row(71, "Harvey", "Head of GTM Strategy & Operations, AMER")])
    R.record_applied(None, Canon(), sheet, "harvey:head-of-gtm",
                     company="Harvey", role="Head of GTM Strategy & Operations, AMER",
                     screenshot="https://example.com/shot.png")
    assert sheet.applied and list(sheet.applied) == [71]
    assert sheet.verdicts_set == {71: verdicts.APPLIED}
    table, match, values = wired["patch"][0]
    assert table == "hunter_seen_roles"
    assert match == {"job_id": "harvey:head-of-gtm"}
    # "Applied", the one word the sheet's Verdict column and Control Center's
    # Hunt lane both use. It wrote "submitted" while sync_applied_state wrote
    # "Applied" from the same column, and cmd_close_submitted treated only
    # "submitted" as done, so the next full pass relabelled the row and this ran
    # again: a duplicate Submitted receipt for every applied role, every hour.
    assert values["application_state"] == R.APPLIED_STATE == "Applied"
    assert values["applied_at"]
    assert wired["archive"] == 1
    assert wired["mail"] and wired["mail"][0]["company"] == "Harvey"


def test_two_matching_rows_stamp_neither(wired):
    """Harvey alone has six roles across these tabs. Stamping the wrong one
    Applied is worse than stamping none."""
    sheet = FakeSheet([row(71, "Harvey", "Head of GTM"), row(72, "Harvey", "Head of GTM")])
    notes: list[str] = []
    R.record_applied(None, Canon(), sheet, "harvey:x", company="Harvey",
                     role="Head of GTM", summary=notes)
    assert sheet.applied == {}
    assert sheet.verdicts_set == {}
    assert any("do the row by hand" in n for n in notes)
    # And nothing records the role as done, so the next hourly run tries again.
    # The ledger patch used to run either way, and cmd_close_submitted skips any
    # row already carrying an applied state, so one failed match meant the
    # Pipeline row read "Not applied" for ever on an application that was sent.
    assert not wired["patch"], "no ledger write when the sheet was not written"
    assert any("close-submitted will try this role again" in n for n in notes)
    # The receipt still goes out, because the application IS sent and he has to
    # know the sheet needs a hand. Only the "this role is done" write is withheld.
    assert wired["mail"]


def test_no_matching_row_still_tells_him(wired):
    sheet = FakeSheet([row(71, "Someone Else", "Another Role")])
    notes: list[str] = []
    R.record_applied(None, Canon(), sheet, "harvey:x", company="Harvey",
                     role="Head of GTM", summary=notes)
    assert any("0 Pipeline rows match" in n for n in notes)
    assert wired["mail"]


def test_a_bookkeeping_failure_never_swallows_the_send(wired):
    """A sent application must not look unsent because a write failed."""
    class Breaking(FakeSheet):
        def mark_applied(self, row_number, *, when):
            raise RuntimeError("sheets is down")

    sheet = Breaking([row(71, "Harvey", "Head of GTM")])
    notes: list[str] = []
    R.record_applied(None, Canon(), sheet, "harvey:x", company="Harvey",
                     role="Head of GTM", summary=notes)
    assert any("FAILED: sheets is down" in n for n in notes)
    assert sheet.verdicts_set == {71: verdicts.APPLIED}
    assert wired["mail"]


def test_the_applied_date_is_written_as_text_not_a_date_value():
    """The first application hunter ever sent recorded its Applied Date as 46280.

    Sheets parses "2026-09-15" under USER_ENTERED into a date value, and a cell
    with no date format displays the serial number. The date has to survive as
    the text it is.
    """
    from hunter.sheet import Sheet

    seen = {}

    class Spy(Sheet):
        def __init__(self): pass
        def _write(self, blocks, *, raw=False):
            seen["raw"] = raw
            seen["blocks"] = blocks
        def _values(self, rng, formulas=True):
            if "Application Status" in str(seen.get("last", "")):
                pass
            return [["x"]]

    spy = Spy()
    # read-back is exercised by the live path; here only the write mode matters
    try:
        spy.mark_applied(71, when="2026-09-15")
    except Exception:
        pass
    assert seen["raw"] is True
    assert any("2026-09-15" in str(v) for _, v in seen["blocks"])


# ---------- the queue notices when it stops ----------

import datetime as _dt
from hunter.apply import approval as _ap


def _iso(hours_ago):
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def ledger(monkeypatch):
    rows = {"data": []}
    mail = []
    monkeypatch.setattr(R, "db_get",
                        lambda cfg, table, params: [
                            r for r in rows["data"]
                            if r["state"] == params["state"].split("eq.")[-1]])
    import hunter.notify as N
    monkeypatch.setattr(N, "send_email",
                        lambda cfg, subject, html, **kw: mail.append(subject))
    monkeypatch.setattr(N, "mailbox", lambda cfg: "krish@example.com")
    return rows, mail


def test_an_approval_nobody_acted_on_is_named(ledger):
    """He replied APPROVE, nothing read it, and nothing noticed that nothing had
    happened. A queue with no alarm on it stops quietly."""
    rows, mail = ledger
    rows["data"] = [{"token": "harvey:x", "company": "Harvey", "role": "Head of GTM",
                     "state": _ap.APPROVED, "decided_at": _iso(5), "sent_at": _iso(6)}]
    stuck = R.report_stalled_approvals(None, apply=True)
    assert len(stuck) == 1 and "Harvey" in stuck[0]
    assert mail and mail[0].startswith("Stalled: 1")


def test_a_fresh_approval_is_not_an_alarm(ledger):
    rows, mail = ledger
    rows["data"] = [{"token": "harvey:x", "company": "Harvey", "role": "Head of GTM",
                     "state": _ap.APPROVED, "decided_at": _iso(0), "sent_at": _iso(1)}]
    assert R.report_stalled_approvals(None, apply=True) == []
    assert mail == []


def test_waiting_on_krish_is_not_a_fault(ledger):
    """An AWAITING row is waiting on him, and he takes as long as he takes."""
    rows, mail = ledger
    rows["data"] = [{"token": "harvey:x", "company": "Harvey", "role": "Head of GTM",
                     "state": _ap.AWAITING, "decided_at": None, "sent_at": _iso(72)}]
    assert R.report_stalled_approvals(None, apply=True) == []
    assert mail == []


def test_the_watcher_opens_one_form_at_a_time(monkeypatch):
    """Ten approvals answered in one sitting would arrive as ten tabs at once,
    which is not a review. The next one is a minute away anyway."""
    monkeypatch.setattr(R, "build_context", lambda: (None, None))
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [
        {"token": "t1"}, {"token": "t2"}, {"token": "t3"}])
    opened = []
    monkeypatch.setattr(R, "cmd_apply_local",
                        lambda token=None, port=0, profile_dir="":
                        opened.append(token) or 0)
    seen = set()
    assert R._open_approved(seen, port=9222, profile_dir="") == 1
    assert opened == ["t1"]
    assert R._open_approved(seen, port=9222, profile_dir="") == 1
    assert opened == ["t1", "t2"]


def test_a_dead_posting_is_retired_not_just_skipped(monkeypatch):
    """Krish: the listing has been taken down, so the role should be purged.
    A skipped row is still a built package and comes back on the next run."""
    from hunter import sheet as sheet_mod
    patched = []
    monkeypatch.setattr(R, "db_patch",
                        lambda cfg, table, match, values: patched.append(
                            (table, match, values)))
    sheet = FakeSheet([row(71, "Slingshot AI", "Chief of Staff, GTM")])
    sheet.package_status = {}
    sheet.update_package_status = lambda rn, st: sheet.package_status.__setitem__(rn, st)
    R.retire_dead_posting(None, Canon(), sheet, "slingshotai:cos-gtm",
                          company="Slingshot AI", role="Chief of Staff, GTM",
                          why="Ashby returned no posting; the posting is dead")
    table, match, values = patched[0]
    assert table == "hunter_seen_roles"
    assert values["package_status"] == "dead" and values["status"] == "dead"
    assert sheet.package_status == {71: sheet_mod.PKG_DEAD}
    assert "dead posting" in sheet.verdicts_set[71]


def test_a_role_already_written_is_not_written_again(monkeypatch):
    """The duplicate receipt bug, from the other end.

    cmd_close_submitted decides a submitted approval still needs sheet work by
    reading hunter_seen_roles.application_state. Both spellings have to count as
    written, or every role applied for before this fix would be re-recorded and
    re-emailed on the next hourly run.
    """
    seen = {
        "a": {"job_id": "a", "application_state": "Applied"},
        "b": {"job_id": "b", "application_state": "submitted"},
        "c": {"job_id": "c", "application_state": None},
    }
    approvals = [{"token": k, "job_id": k, "company": k.upper(), "role": "r",
                  "submitted_at": "2026-09-15T20:00:00Z", "failure_reason": ""}
                 for k in ("a", "b", "c")]

    def fake_db_get(cfg, table, params):
        return approvals if table.endswith("approvals") else list(seen.values())

    recorded: list[str] = []
    monkeypatch.setattr(R, "db_get", fake_db_get)
    monkeypatch.setattr(R, "build_context", lambda: (None, Canon()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: None)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "record_applied",
                        lambda *a, **k: recorded.append(a[3]))
    assert R.cmd_close_submitted(apply=True) == 0
    assert recorded == ["c"], "only the role with nothing written should be written"


def test_a_role_already_on_the_applied_tab_closes_instead_of_retrying(wired):
    """Mutiny, 2026-09-24. record_applied ends by archiving every decided row,
    and it ran once per role in the batch. A role written earlier in the same
    batch, or reached under a second job id for the same posting, then found no
    Pipeline row, was reported as "0 Pipeline rows match", and had its ledger
    entry left open on purpose so the next run would retry. The row it wanted
    was on the Applied tab and never coming back, so it retried for ever."""
    sheet = FakeSheet([row(71, "Clay", "Head of GTM Strategy & Ops")],
                      archived=[("Mutiny", "Head of GTM (Path to COO)")])
    wrote = R.record_applied(None, Canon(), sheet, "mutiny:head-of-gtm",
                             company="Mutiny", role="Head of GTM (Path to COO)")
    assert wrote is True, "an archived role is finished, not a failure"
    # Nothing was written to Pipeline, because there is nothing there to write.
    assert sheet.applied == {} and sheet.verdicts_set == {}
    # And the ledger closes, which is what stops the retry.
    table, match, values = wired["patch"][0]
    assert match == {"job_id": "mutiny:head-of-gtm"}
    assert values["application_state"] == R.APPLIED_STATE
    notes = wired["mail"][0]["notes"]
    assert any("already on the Applied tab" in n for n in notes)
    assert not any("NOT marked done" in n for n in notes)


def test_a_role_that_is_simply_missing_still_refuses_to_close(wired):
    """The guard above may not become a blanket pass. A company or role that is
    on neither tab is a real miss and must stay open for the next run."""
    sheet = FakeSheet([row(71, "Clay", "Head of GTM Strategy & Ops")],
                      archived=[("Mutiny", "Head of GTM (Path to COO)")])
    wrote = R.record_applied(None, Canon(), sheet, "nowhere:x",
                             company="Nowhere", role="Some Role")
    assert wrote is False
    assert wired["patch"] == [], "nothing may be marked done in Supabase"
    assert any("NOT marked done" in n for n in wired["mail"][0]["notes"])


def test_the_archive_can_be_held_back_for_a_batch(wired):
    """Archiving between roles moved rows out from under the roles still to be
    written. cmd_close_submitted runs the pass once, itself, at the end."""
    sheet = FakeSheet([row(71, "Clay", "Head of GTM Strategy & Ops")])
    R.record_applied(None, Canon(), sheet, "clay:x", company="Clay",
                     role="Head of GTM Strategy & Ops", archive=False)
    assert wired["archive"] == 0
    R.record_applied(None, Canon(), sheet, "clay:x", company="Clay",
                     role="Head of GTM Strategy & Ops")
    assert wired["archive"] == 1


def test_two_job_ids_for_one_posting_are_written_once_and_both_close(monkeypatch):
    """One posting, however many role rows point at it. Mutiny arrived under
    three job ids; each was a separate ledger row, and each wanted the same
    single Pipeline row."""
    seen = {
        "a": {"job_id": "a", "application_state": None,
              "job_url": "https://jobs.ashbyhq.com/mutiny/123"},
        "b": {"job_id": "b", "application_state": None,
              "job_url": "https://jobs.ashbyhq.com/mutiny/123?src=x"},
        "c": {"job_id": "c", "application_state": None,
              "job_url": "https://jobs.ashbyhq.com/clay/999"},
    }
    approvals = [{"token": k, "job_id": k, "company": "Mutiny" if k != "c" else "Clay",
                  "role": "r", "submitted_at": "2026-09-24T12:00:00Z",
                  "failure_reason": "the form said \"successfully submitted\""}
                 for k in ("a", "b", "c")]

    def fake_db_get(cfg, table, params):
        return approvals if table.endswith("approvals") else list(seen.values())

    recorded: list[str] = []
    patched: list[tuple] = []
    monkeypatch.setattr(R, "db_get", fake_db_get)
    monkeypatch.setattr(R, "db_patch",
                        lambda cfg, table, match, values: patched.append((match, values)))
    monkeypatch.setattr(R, "build_context", lambda: (None, Canon()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: None)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "cmd_archive", lambda apply=False: 0)

    def fake_record(cfg, canon, sheet, job_id, **kw):
        recorded.append(job_id)
        return True

    monkeypatch.setattr(R, "record_applied", fake_record)
    assert R.cmd_close_submitted(apply=True) == 0
    assert recorded == ["a", "c"], "the second id for one posting is not written again"
    assert [m["job_id"] for m, _ in patched] == ["b"], "and it closes with its sibling"
    assert patched[0][1]["application_state"] == R.APPLIED_STATE


def test_a_sibling_stays_open_when_the_write_failed(monkeypatch):
    """A posting whose row was not written is not finished, so neither is the
    second id pointing at it."""
    seen = {
        "a": {"job_id": "a", "application_state": None,
              "job_url": "https://jobs.ashbyhq.com/mutiny/123"},
        "b": {"job_id": "b", "application_state": None,
              "job_url": "https://jobs.ashbyhq.com/mutiny/123"},
    }
    approvals = [{"token": k, "job_id": k, "company": "Mutiny", "role": "r",
                  "submitted_at": "2026-09-24T12:00:00Z", "failure_reason": ""}
                 for k in ("a", "b")]
    patched: list[tuple] = []
    monkeypatch.setattr(R, "db_get",
                        lambda cfg, table, params: approvals
                        if table.endswith("approvals") else list(seen.values()))
    monkeypatch.setattr(R, "db_patch",
                        lambda cfg, table, match, values: patched.append((match, values)))
    monkeypatch.setattr(R, "build_context", lambda: (None, Canon()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: None)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "cmd_archive", lambda apply=False: 0)
    monkeypatch.setattr(R, "record_applied", lambda *a, **k: False)
    assert R.cmd_close_submitted(apply=True) == 0
    assert patched == [], "nothing closes on the back of a write that did not happen"
