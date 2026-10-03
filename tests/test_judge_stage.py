"""The judge in the sourcing path: shadow changes nothing that reaches him, gate
decides, and neither can leak a role onto his sheet by the back door."""
import types

import pytest

from hunter import judge, judge_stage


class Cfg:
    def __init__(self, **o):
        self.o = o

    def optional(self, k, d=""):
        return self.o.get(k, d)


def cand(jid, rank=0.0):
    role = types.SimpleNamespace(job_id=jid, company=jid.upper(), title="VP", location="NYC",
                                 comp="", url="u", jd_url="u", jd_text="posting", source="s")
    return judge_stage.Candidate(role=role, result=types.SimpleNamespace(score=5),
                                 row={"job_id": jid, "status": "staging"}, rank=rank)


def verdict_for(table):
    def fake(cfg, system, role, client=None):
        v, fit = table[role.job_id]
        return judge.Judgement(job_id=role.job_id, verdict=v, fit=fit, confidence="high",
                               answers={"most_likely_reason_he_says_no": "x"},
                               red_flags=["flag"], likely_decline_code="none",
                               why_it_fits="why" if v == "present" else "", snippet="s",
                               model="claude-opus-5-5", served_model="claude-opus-5-5",
                               usage={"input_tokens": 10, "cache_read_input_tokens": 100},
                               usd=0.05)
    return fake


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(judge_stage, "db_insert", lambda *a, **k: None)


def run(monkeypatch, table, mode, **cfg):
    monkeypatch.setattr(judge, "judge_role", verdict_for(table))
    cands = [cand(j, rank=i) for i, j in enumerate(table)]
    out = judge_stage.run(Cfg(**cfg), None, None, cands, mode=mode, system="ctx",
                          client=object())
    return cands, out


TABLE = {"a": ("present", 9), "b": ("present", 7), "c": ("hold", 8),
         "d": ("reject", 2), "e": ("present", 8)}


def test_shadow_records_the_judgement_and_changes_no_status(monkeypatch, quiet):
    cands, out = run(monkeypatch, TABLE, "shadow")
    assert all(c.row["status"] == "staging" for c in cands)
    assert all(c.row["judge_verdict"] for c in cands)
    assert [c.role.job_id for c in out.present] == ["a", "e"]


def test_gate_presents_fit_8_or_more_and_holds_the_rest(monkeypatch, quiet):
    cands, out = run(monkeypatch, TABLE, "gate", hunter_judge_audit_per_run="0")
    judge_stage.apply_gate(out, cands)
    status = {c.role.job_id: c.row["status"] for c in cands}
    assert status == {"a": "staging", "b": "held", "c": "held", "d": "blocked", "e": "staging"}
    assert cands[3].row["rejection_reason"].startswith("JUDGE reject")


def test_a_held_row_is_never_left_at_staging(monkeypatch, quiet):
    """Reconcile appends any staging row the sheet lacks, so a held role left at
    'staging' would reach his sheet the next day."""
    table = {f"r{i}": ("present", 9) for i in range(14)}
    cands, out = run(monkeypatch, table, "gate", hunter_judge_max_present="10",
                     hunter_judge_audit_per_run="0")
    judge_stage.apply_gate(out, cands)
    staged = [c for c in cands if c.row["status"] == "staging"]
    assert len(staged) == 10
    assert all(c.row["status"] == "held" for c in cands if c not in staged)


def test_the_audit_sample_is_labelled_and_counted(monkeypatch, quiet):
    cands, out = run(monkeypatch, TABLE, "gate", hunter_judge_audit_per_run="1")
    judge_stage.apply_gate(out, cands)
    audited = [c for c in cands if c.row.get("audit_sample")]
    assert len(audited) == 1 and audited[0].row["status"] == "staging"
    assert judge_stage.audit_why(audited[0].judgement).startswith(judge_stage.AUDIT_PREFIX)


def test_over_budget_is_pending_and_never_presented(monkeypatch, quiet):
    table = {f"r{i}": ("present", 9) for i in range(5)}
    cands, out = run(monkeypatch, table, "gate", hunter_judge_max_per_run="2",
                     hunter_judge_audit_per_run="0")
    judge_stage.apply_gate(out, cands)
    pending = [c for c in cands if c.row["status"] == "judge_pending"]
    assert len(pending) == 3
    assert all("budget" in c.row["rejection_reason"] for c in pending)


def test_the_dollar_ceiling_stops_new_calls(monkeypatch, quiet):
    table = {f"r{i}": ("present", 9) for i in range(30)}
    cands, out = run(monkeypatch, table, "gate", hunter_judge_max_usd_per_run="0.2",
                     hunter_judge_workers="2")
    assert out.usd <= 0.2 + 2 * 0.05
    assert sum(1 for c in cands if c.judgement.verdict == "pending") >= 20


def test_off_judges_nothing(monkeypatch, quiet):
    monkeypatch.setattr(judge, "judge_role", lambda *a, **k: pytest.fail("judged in off mode"))
    out = judge_stage.run(Cfg(), None, None, [cand("a")], mode="off", system="x")
    assert out.judged == 0


def test_only_clear_cut_gates_stop_a_role_before_the_judge():
    def report(*gates):
        fails = [types.SimpleNamespace(gate=g, reason="r") for g in gates]
        return types.SimpleNamespace(failures=lambda: fails)
    for g in ("G0", "G1", "G2", "G6"):
        assert judge_stage.hard_failure(report(g))
    # the judgement gates are the judge's now
    for g in ("G3", "G4", "G5", "G7", "G11", "G12", "G13"):
        assert judge_stage.hard_failure(report(g)) is None


def test_a_pending_role_is_seen_again():
    import inspect
    from hunter import run as R
    assert '"judge_pending"' in inspect.getsource(R.seen_identity_keys)


def test_a_judge_that_did_not_run_stages_nothing_in_gate_mode():
    import inspect
    from hunter import run as R
    src = inspect.getsource(R.stage_postings)
    assert 'row["status"] = "judge_pending"' in src
