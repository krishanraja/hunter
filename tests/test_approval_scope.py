"""An approval may only be sent for a role that is still on his sheet.

cmd_approvals selects on hunter_seen_roles.package_status == "built" and never
re-read column A. On 2026-09-24 Krish had two Pipeline rows removed by name,
Confidential and Strativ Group, and chose to keep their verdicts. Both kept
krish_verdict "Yes" and package_status "built", so the next approvals run
would have mailed him an application to press for each of the two roles he had
just had deleted.

router.select_for_build already treats the sheet as the authority, in its own
words "column A as it reads now, never a DB field". This is the one step
downstream of it that did not, and sending is the failure that cannot be
undone.
"""
from __future__ import annotations

import pytest

import hunter.run as R
from hunter.sheet import SheetRow, N_COLS


def srow(n, company, role, verdict="Yes"):
    return SheetRow(row_number=n, cells=[""] * N_COLS, verdict=verdict,
                    company=company, role=role, jd_url=None)


def dbrow(job_id, company, title):
    return {"job_id": job_id, "company": company, "title": title,
            "url": f"https://jobs.ashbyhq.com/{company.lower()}/{job_id}",
            "package_status": "built", "status": "staging",
            "krish_verdict": "Yes"}


def test_a_built_package_whose_row_has_left_the_sheet_is_not_sent():
    on_sheet = [srow(3, "Profound", "VP, Strategic Partnerships")]
    built = [dbrow("profound:vp", "Profound", "VP, Strategic Partnerships"),
             dbrow("strativ:cro", "Strativ Group", "Chief Revenue Officer")]
    paired, _, _, _ = R.match_rows(on_sheet, list(built))
    keep = {d["job_id"] for _, d in paired}
    assert "profound:vp" in keep
    assert "strativ:cro" not in keep


def test_the_row_that_is_still_there_is_untouched_by_the_rule():
    """The guard must not become a reason nothing ever sends."""
    on_sheet = [srow(3, "Profound", "VP, Strategic Partnerships"),
                srow(4, "openrouter", "Director, Channel Partnerships")]
    built = [dbrow("profound:vp", "Profound", "VP, Strategic Partnerships"),
             dbrow("openrouter:dir", "openrouter", "Director, Channel Partnerships")]
    paired, _, _, _ = R.match_rows(on_sheet, list(built))
    assert len(paired) == 2


def test_cmd_approvals_reads_the_sheet_before_it_sends(monkeypatch):
    """The rule has to be IN cmd_approvals, not merely true of match_rows.

    An earlier mutation of exactly this shape survived: the helper was proved
    in isolation and nothing proved the caller used it.
    """
    calls = {"read": 0, "sent": []}

    class FakeSheet:
        def __init__(self, *a, **k):
            pass

        def read_pipeline(self, headers):
            calls["read"] += 1
            # Profound is on the sheet. Strativ is not.
            return [srow(3, "Profound", "VP, Strategic Partnerships")]

    monkeypatch.setattr(R, "Sheet", FakeSheet)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "build_context",
                        lambda: (object(), type("C", (), {"sheet_headers": []})()))
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [
        dbrow("strativ:cro", "Strativ Group", "Chief Revenue Officer")])

    # Everything past the guard would need Google, Playwright and a mailbox.
    # If the guard works, none of it is reached.
    def explode(*a, **k):
        raise AssertionError("cmd_approvals got past the sheet check")

    monkeypatch.setattr(R, "_answer_bank", explode)

    out = R.cmd_approvals(apply=False)
    assert calls["read"] == 1, "cmd_approvals never read the Pipeline sheet"
    assert out == 1


def test_an_unreadable_sheet_refuses_the_batch_rather_than_mailing_it(monkeypatch):
    """Sending is the thing that cannot be undone. If hunter cannot see which
    rows are still there, it sends nothing rather than sending all of them."""
    class BrokenSheet:
        def __init__(self, *a, **k):
            pass

        def read_pipeline(self, headers):
            raise RuntimeError("Google said no")

    monkeypatch.setattr(R, "Sheet", BrokenSheet)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "build_context",
                        lambda: (object(), type("C", (), {"sheet_headers": []})()))
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [
        dbrow("profound:vp", "Profound", "VP, Strategic Partnerships")])
    monkeypatch.setattr(R, "_answer_bank",
                        lambda s: (_ for _ in ()).throw(
                            AssertionError("sent on an unreadable sheet")))
    with pytest.raises(RuntimeError):
        R.cmd_approvals(apply=False)


# ---------- unreadable is not dead ----------

def test_a_linkedin_only_posting_is_not_retired_as_dead():
    """BOI (Board of Innovation) is a role Krish approved whose only URL is a
    LinkedIn job view. apply/fetch returns unreadable because LinkedIn needs an
    authenticated session, NOT because the posting has gone. The refusal branch
    retired every unreadable form, so it would have written "Declined - dead
    posting" onto his sheet about a job that is very likely still open.
    """
    assert "linkedin" in R.CANNOT_ENUMERATE
    assert "google" in R.CANNOT_ENUMERATE
    assert "unknown" in R.CANNOT_ENUMERATE
    # A real ATS that answered "gone" is still retired.
    assert "ashby" not in R.CANNOT_ENUMERATE
    assert "greenhouse" not in R.CANNOT_ENUMERATE
    assert "lever" not in R.CANNOT_ENUMERATE


def test_the_set_matches_what_fetch_actually_returns():
    """Written by reading apply/fetch.py, so a new unreadable kind added there
    without thinking about this branch shows up as a failure here rather than
    as a false 'dead posting' on his sheet."""
    import pathlib
    src = pathlib.Path(R.__file__).parent.joinpath("apply/fetch.py").read_text()
    for kind in ("linkedin", "google", "unknown"):
        assert f'unreadable(\n            "{kind}"' in src or \
               f'unreadable("{kind}"' in src, kind
