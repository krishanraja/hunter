"""An employer's receipt closes an application still marked awaiting, but only
on strong evidence.

He applies through the extension link without replying APPROVE, so his
approvals sit at 'awaiting'. The receipt reader looked only at approved and
submitted rows, so the commonest case was never read at all.
"""
import pytest

from hunter import run as R
from hunter.apply import approval, confirmations


class Canon:
    sheet_headers = []


@pytest.fixture
def wired(monkeypatch):
    rows_by_state: dict[str, list] = {approval.AWAITING: [], approval.APPROVED: [],
                                      approval.SUBMITTED: []}
    closed, recorded = [], []
    monkeypatch.setattr(R, "build_context", lambda: (None, Canon()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: None)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "db_get", lambda cfg, t, p: list(
        rows_by_state[p["state"].split(".", 1)[1]]))
    monkeypatch.setattr(confirmations, "mailbox",
                        lambda cfg: {"address": "x", "total": 1})
    monkeypatch.setattr(approval, "set_state",
                        lambda cfg, token, state, **kw: closed.append(token))
    monkeypatch.setattr(R, "record_applied", lambda *a, **k: recorded.append(a[3]))
    return rows_by_state, closed, monkeypatch


def receipt(company, date):
    return {"message_id": "m1", "subject": f"Thank you for applying to {company}",
            "sender": f"no-reply@{company.lower()}.com", "date": date,
            "snippet": "We have received your application"}


def awaiting(token, company, sent_at):
    return {"token": token, "company": company, "role": "Director",
            "state": approval.AWAITING, "job_id": token, "submitted_at": None,
            "sent_at": sent_at}


def test_a_receipt_after_the_send_closes_the_only_open_role_there(wired):
    rows, closed, mp = wired
    rows[approval.AWAITING] = [awaiting("or1", "OpenRouter", "2026-09-24T19:26:00+00:00")]
    mp.setattr(confirmations, "fetch",
               lambda cfg: [receipt("OpenRouter", "Thu, 25 Sep 2026 05:02:11 +0000")])
    R.cmd_confirmations(apply=True)
    assert closed == ["or1"]


def test_a_receipt_from_before_the_send_closes_nothing(wired):
    rows, closed, mp = wired
    rows[approval.AWAITING] = [awaiting("or1", "OpenRouter", "2026-09-24T19:26:00+00:00")]
    mp.setattr(confirmations, "fetch",
               lambda cfg: [receipt("OpenRouter", "Mon, 01 Sep 2026 10:00:00 +0000")])
    R.cmd_confirmations(apply=True)
    assert closed == []


def test_two_open_roles_at_one_company_close_neither_on_one_receipt(wired):
    rows, closed, mp = wired
    rows[approval.AWAITING] = [awaiting("h1", "Harvey", "2026-09-20T10:00:00+00:00"),
                               awaiting("h2", "Harvey", "2026-09-20T10:00:00+00:00")]
    mp.setattr(confirmations, "fetch",
               lambda cfg: [receipt("Harvey", "Tue, 22 Sep 2026 10:00:00 +0000")])
    R.cmd_confirmations(apply=True)
    assert closed == []
