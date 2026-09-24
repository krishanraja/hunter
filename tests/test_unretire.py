"""Putting back a row that should never have been retired.

On 2026-09-24 hunter retired Versapay and Rembrand, two live Lever postings
Krish had marked Yes, because the approvals refusal branch read "no form
adapter for lever yet; the posting is readable but its form is not" as death.
The rule is fixed. This is the repair, and the next wrong retirement will
need it too.
"""
from __future__ import annotations

import pytest

import hunter.run as R
from hunter.sheet import SheetRow, N_COLS


LEVER = "https://jobs.lever.co/rembrand/ea63ab95-1d16-4f01-abf9-1c114bff7eee"

ROWS = [
    {"job_id": "rembrand:head", "company": "Rembrand", "title": "Head of Content",
     "url": LEVER, "status": "dead", "package_status": "dead",
     "krish_verdict": "Yes", "package_cv_url": "http://cv", 
     "package_letter_url": "http://letter"},
    {"job_id": "nopkg:role", "company": "NoPkg", "title": "GM",
     "url": LEVER, "status": "dead", "package_status": "dead",
     "krish_verdict": "Yes", "package_cv_url": None, "package_letter_url": None},
]


def srow(n, company, role, verdict):
    return SheetRow(row_number=n, cells=[""] * N_COLS, verdict=verdict,
                    company=company, role=role, jd_url=None)


@pytest.fixture
def wired(monkeypatch):
    seen = {"verdicts": {}, "pkg": {}, "patch": [], "gone": False,
            "sheet": [srow(7, "Rembrand", "Head of Content", "Declined - dead posting"),
                      srow(9, "NoPkg", "GM", "Declined - dead posting")]}

    class FakeSheet:
        def __init__(self, *a, **k):
            pass

        def read_pipeline(self, headers):
            return list(seen["sheet"])

        def set_verdicts(self, mapping):
            seen["verdicts"].update(mapping)
            # the sheet really changes, so the read-back can see it
            for rn, v in mapping.items():
                for i, r in enumerate(seen["sheet"]):
                    if r.row_number == rn:
                        seen["sheet"][i] = srow(rn, r.company, r.role, v)

        def update_package_status(self, rn, status):
            seen["pkg"][rn] = status

    monkeypatch.setattr(R, "Sheet", FakeSheet)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "build_context",
                        lambda: (object(), type("C", (), {"sheet_headers": []})()))
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: list(ROWS))
    monkeypatch.setattr(R, "db_patch",
                        lambda cfg, t, m, v: seen["patch"].append((m["job_id"], v)))
    monkeypatch.setattr(R, "posting_is_gone", lambda row: seen["gone"])
    return seen


def test_a_live_role_is_put_back_with_his_own_verdict(wired):
    assert R.cmd_unretire("rembrand:head", apply=True) == 0
    assert wired["verdicts"] == {7: "Yes"}
    jid, patch = wired["patch"][0]
    assert jid == "rembrand:head"
    assert patch["status"] == "staging"
    assert patch["package_status"] == "built"
    assert patch["rejection_reason"] is None


def test_a_role_that_really_is_gone_is_refused(wired):
    """The same evidence check retire makes, pointing the other way. Putting a
    genuinely dead posting back hands him a row he cannot act on."""
    wired["gone"] = True
    assert R.cmd_unretire("rembrand:head", apply=True) == 1
    assert wired["verdicts"] == {}
    assert wired["patch"] == []


def test_a_role_with_no_package_comes_back_unbuilt_not_falsely_built(wired):
    """package_status "built" with no CV and no letter is a claim that two
    documents exist. It has to be earned, not assumed by the repair."""
    assert R.cmd_unretire("nopkg:role", apply=True) == 0
    _, patch = wired["patch"][0]
    assert patch["package_status"] == "none"
    assert 9 not in wired["pkg"], "wrote a built Package Status with no documents"


def test_the_dry_run_writes_nothing(wired):
    assert R.cmd_unretire("rembrand:head") == 0
    assert wired["verdicts"] == {} and wired["patch"] == []


def test_an_ambiguous_sheet_match_needs_a_person(wired):
    wired["sheet"].append(srow(11, "Rembrand", "Head of Content", "New"))
    assert R.cmd_unretire("rembrand:head", apply=True) == 1
    assert wired["patch"] == []


def test_a_job_id_that_matches_nothing_fails_loudly(wired):
    assert R.cmd_unretire("rembrand:typo", apply=True) == 1
    assert wired["patch"] == []


def test_it_reads_the_sheet_back_rather_than_trusting_the_write(monkeypatch, wired):
    """Every other write path in this repo reads its result back."""
    class LyingSheet:
        def __init__(self, *a, **k):
            pass

        def read_pipeline(self, headers):
            return [srow(7, "Rembrand", "Head of Content", "Declined - dead posting")]

        def set_verdicts(self, mapping):
            pass          # silently does nothing

        def update_package_status(self, rn, status):
            pass

    monkeypatch.setattr(R, "Sheet", LyingSheet)
    assert R.cmd_unretire("rembrand:head", apply=True) == 1
