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
