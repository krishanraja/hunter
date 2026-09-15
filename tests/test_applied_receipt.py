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
from hunter.sheet import SheetRow, N_COLS, APPLIED_STATUS


class FakeSheet:
    def __init__(self, rows):
        self._rows = rows
        self.applied: dict[int, str] = {}
        self.verdicts_set: dict[int, str] = {}

    def read_pipeline(self, headers):
        return self._rows

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
    assert values["application_state"] == "submitted"
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
    assert any("do it by hand" in n for n in notes)
    # The ledger and the receipt still happen: the application IS sent.
    assert wired["patch"] and wired["mail"]


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
