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

def test_an_unparseable_form_is_never_by_itself_evidence_of_death():
    """2026-09-24, and this one reached his sheet.

    The approvals refusal branch retired every unreadable form as a dead
    posting. The first fix listed the ats names that mean "cannot enumerate"
    (linkedin, google, unknown) and missed the branch that carries the REAL
    ats name: apply/fetch returns

        unreadable("lever", "no form adapter for lever yet; the posting is
                             readable but its form is not")

    so a live Lever posting was read as death. Versapay and Rembrand, both
    roles Krish had said Yes to, were marked "Declined - dead posting" on his
    sheet. Versapay he had approved an hour earlier.

    The rule is no longer a list of names. Liveness is ASKED of the board.
    """
    import inspect
    src = inspect.getsource(R.cmd_approvals)
    assert "posting_is_gone(row)" in src, \
        "the refusal branch must ask the board, not inspect the ats name"
    # and the retire call has to be gated on that answer being True
    i = src.index("posting_is_gone(row)")
    j = src.index("retire_dead_posting", i)
    assert "if gone:" in src[i:j], "retire is not gated on the board's answer"


def test_a_board_that_cannot_be_reached_is_not_dead():
    """None is a third answer and never collapses into either of the others."""
    assert R.posting_is_gone({"url": ""}) is None
    assert R.posting_is_gone({"url": "https://example.com/careers/123"}) is None


def test_the_liveness_answer_comes_from_the_board(monkeypatch):
    live_row = {"url": "https://jobs.lever.co/rembrand/"
                       "ea63ab95-1d16-4f01-abf9-1c114bff7eee"}
    monkeypatch.setattr(R, "fetch_with_retry", lambda fn, s, p: (True, "", ""))
    assert R.posting_is_gone(live_row) is False
    monkeypatch.setattr(R, "fetch_with_retry", lambda fn, s, p: (False, "", ""))
    assert R.posting_is_gone(live_row) is True

    def boom(fn, s, p):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(R, "fetch_with_retry", boom)
    assert R.posting_is_gone(live_row) is None


def test_retire_and_approvals_share_one_liveness_rule():
    """Two copies of this rule is how the second one drifts. The first fix
    put the check in cmd_retire and left cmd_approvals guessing from names."""
    import inspect
    assert "posting_is_gone(" in inspect.getsource(R.cmd_retire)
    assert "posting_is_gone(" in inspect.getsource(R.cmd_approvals)
