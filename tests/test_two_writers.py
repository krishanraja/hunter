"""Rows two systems write. The canon drift proposal must land in a shape the
table accepts and never overwrite his decision; a submit is final."""
from hunter import run


def _wire(monkeypatch, existing):
    calls = {"insert": [], "patch": []}
    monkeypatch.setattr(run, "db_get", lambda cfg, table, params: existing)
    monkeypatch.setattr(run, "db_insert",
                        lambda cfg, table, rows, **kw: calls["insert"].append((table, rows, kw)))
    monkeypatch.setattr(run, "db_patch",
                        lambda cfg, table, match, body: calls["patch"].append((table, match, body)))
    return calls


def test_a_new_proposal_is_filed_as_the_table_requires(monkeypatch):
    calls = _wire(monkeypatch, [])
    run.propose_canon_universe(None, ["A", "B"], ["B"], [])
    (table, rows, kw), = calls["insert"]
    row = rows[0]
    assert table == "workflow_proposals"
    # The check constraint allows these statuses only, and these columns are NOT NULL.
    assert row["status"] in {"proposed", "approved", "rejected", "executing",
                             "completed", "rolled_back"}
    assert row["proposal_type"] == "quality_improve"
    assert row["description"] and "body" not in row
    assert "on_conflict" not in kw


def test_a_waiting_proposal_gets_fresh_text_only(monkeypatch):
    calls = _wire(monkeypatch, [{"id": 7, "status": "proposed"}])
    run.propose_canon_universe(None, ["A"], [], ["C"])
    assert not calls["insert"]
    (_, match, body), = calls["patch"]
    assert match == {"id": 7} and "status" not in body and "C" in body["description"]


def test_his_decision_is_never_touched(monkeypatch):
    for status in ("rejected", "approved", "executing", "completed"):
        calls = _wire(monkeypatch, [{"id": 7, "status": status}])
        run.propose_canon_universe(None, ["A"], [], ["C"])
        assert not calls["insert"] and not calls["patch"], status


def test_nothing_but_a_submit_can_touch_a_submitted_approval(monkeypatch):
    # Control Center's api/hunter/submitted records his press. A cancel from an
    # amend, or a late APPROVE, must not overwrite it.
    from hunter.apply import approval
    seen = []
    monkeypatch.setattr(approval, "db_patch_where",
                        lambda cfg, table, filters, values: seen.append(filters) or 1)
    for state in (approval.CANCELLED, approval.APPROVED, approval.FAILED):
        approval.set_state(None, "t1", state)
        assert seen[-1] == {"token": "eq.t1", "state": "neq.submitted"}, state
    approval.set_state(None, "t1", approval.SUBMITTED)
    assert seen[-1] == {"token": "eq.t1"}


def test_lookalike_is_under_the_months_ceiling(monkeypatch):
    # It was the third dearest job in the ledger and the only one at that
    # volume the ceiling did not cover.
    from hunter import lookalike, spend
    from hunter.universe import Company
    monkeypatch.setattr(spend, "over_budget", lambda cfg: "the month's $150 is spent")

    class Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise AssertionError("a model was called over the ceiling")

    co = Company(key="acme", name="Acme")
    scored, usd, problems = lookalike.score(None, [co], [], [], client=Boom())
    assert (scored, usd) == (0, 0.0) and "did not run" in problems[0]


def test_extraction_goes_to_openai_first_and_his_words_stay_on_anthropic():
    # Krish, 2026-10-09: use the other keys where the Anthropic one is not needed.
    from hunter import llm

    class Cfg:
        def __init__(self, **kv): self.kv = kv
        def optional(self, k, d=""): return self.kv.get(k, d)
    assert llm.provider_order(Cfg(), "newsletter") == ["openai", "anthropic"]
    for purpose in ("tailor", "essay", "door_observation", "rationale"):
        assert llm.provider_order(Cfg(), purpose) == ["anthropic", "openai"], purpose
    # A job that searches the web can only go where there is a search tool.
    assert llm.provider_order(Cfg(), "cold_targets", web_search=True) == ["anthropic"]
    assert llm.provider_order(Cfg(hunter_provider_newsletter="anthropic"), "newsletter") == ["anthropic"]


def test_an_openai_call_is_priced_at_its_own_rate_not_the_dearest():
    from hunter import spend
    assert spend.PRICES["gpt-4.1-mini"]["out"] < spend.PRICES["claude-sonnet-5-5"]["out"]
