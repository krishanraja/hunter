"""What the model calls cost, and the changes of 2026-10-07 that cut it.

Krish asked for the bill to come down three times. The judge was the only
call with a record: $8.17 of the $21 the 4 October run cost on hunter's key.
These tests hold the parts that make the rest visible and cheaper: one price
table that never prices a call at zero, a ledger row for every call, the job
each call runs as, the cache on text that repeats, and a judge that does not
pay twice for a role it judged an hour ago.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from hunter import judge, judge_stage, llm, spend


@pytest.fixture(autouse=True)
def _clean():
    spend.reset()
    llm.reset()
    yield
    spend.reset()
    llm.reset()


class Cfg:
    def __init__(self, **kw):
        self.raw = {"hunter_anthropic_api_key": "a"}
        self.raw.update(kw)

    def optional(self, key, default=""):
        return self.raw.get(key) or default

    def require(self, key):
        return self.raw[key]


def usage(i=1000, o=1000, cr=0, cw=0):
    return {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw}


# ---------- prices ----------

def test_an_unknown_model_is_never_free():
    """judge.usd_of returned 0.0 for a model missing from its table, so
    pointing hunter_judge_model anywhere else switched the judge's dollar cap
    off. A model with no price is costed at the dearest price there is."""
    assert judge.usd_of("some-new-model", usage()) == spend.usd_of("claude-opus-5", usage())
    assert judge.usd_of("some-new-model", usage()) > 0


def test_the_prices_are_one_table():
    from hunter import lookalike
    assert judge.PRICES is spend.PRICES
    assert not hasattr(lookalike, "PRICES")


def test_published_prices():
    # A million in and a million out, per the published rates.
    m = 1_000_000
    assert spend.usd_of("claude-opus-5-5", usage(m, m)) == 24.0
    assert spend.usd_of("claude-opus-5", usage(m, m)) == 30.0
    assert spend.usd_of("claude-sonnet-5-5", usage(m, m)) == 12.0
    assert spend.usd_of("claude-haiku-4-5-20251001", usage(m, m)) == 6.0
    assert spend.usd_of("claude-opus-5-5", usage(0, 0, cr=m)) == 0.2
    assert spend.usd_of("claude-opus-5-5", usage(m, m), batch=True) == 12.0


def test_a_web_search_is_counted():
    u = dict(usage(0, 0), web_search_requests=3)
    assert spend.usd_of("claude-sonnet-5-5", u) == 0.03


# ---------- the ledger ----------

def test_every_call_is_counted_by_job():
    spend.record(Cfg(), "tailor", "claude-opus-5-5", usage())
    spend.record(Cfg(), "case", "claude-opus-5-5", usage())
    spend.record(Cfg(), "case", "claude-opus-5-5", usage())
    lines = spend.summary()
    assert lines[0].startswith("model spend this run: $0.07 in 3 call(s)")
    assert any(line.strip().startswith("case: $0.05 in 2 call(s)") for line in lines)


def test_the_offline_suite_never_writes_the_ledger(monkeypatch):
    """Rows are written only for a configuration config.load() read."""
    import hunter.config as config
    monkeypatch.setattr(config, "db_insert",
                        lambda *a, **k: pytest.fail("the ledger reached the network"))
    spend.record(Cfg(), "tailor", "claude-opus-5-5", usage())
    assert spend._CFG is None, "nothing is kept for an exit-time write"
    assert "not written" in spend.flush(Cfg())


def test_the_ledger_is_written_for_a_live_configuration(monkeypatch):
    import hunter.config as config
    wrote = []
    monkeypatch.setattr(config, "LOADED", True)
    monkeypatch.setattr(config, "db_insert", lambda cfg, table, rows, **k: wrote.append((table, rows)))
    live = types.SimpleNamespace(supabase_url="https://x", optional=lambda k, d="": d)
    spend.record(live, "newsletter", "claude-sonnet-5-5", usage(), job_id=None)
    assert spend.flush(live) == "spend: 1 call(s) written"
    table, rows = wrote[0]
    assert table == "hunter_judge_calls" and rows[0]["purpose"] == "newsletter"
    assert rows[0]["usd"] == 0.012


def test_the_month_ceiling_stops_calls(monkeypatch):
    monkeypatch.setattr(spend, "month_spent", lambda cfg: 151.0)
    assert "ceiling" in spend.over_budget(Cfg())
    assert spend.over_budget(Cfg(hunter_llm_max_usd_per_month="0")) is None
    monkeypatch.setattr(spend, "month_spent", lambda cfg: None)
    assert spend.over_budget(Cfg()) is None, "an unreadable ledger stops nothing"


# ---------- the shared door: model, effort, cache ----------

def fake_anthropic(monkeypatch, text='{"answer": "x"}'):
    seen = []

    class Block:
        type = "text"
        def __init__(self, t): self.text = t

    class Resp:
        def __init__(self, model):
            self.content = [Block(text)]
            self.stop_reason = "end_turn"
            self.model = model
            self.usage = types.SimpleNamespace(**usage(2000, 500, cr=30000))

    class Msgs:
        def create(self, **kw):
            seen.append(kw)
            return Resp(kw["model"])

    class Client:
        def __init__(self, **kw): self.messages = Msgs()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    mod.APIError = Exception
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return seen


def test_each_job_runs_on_its_own_model_and_effort(monkeypatch):
    """Every job had shared claude-opus-5 at its default high effort."""
    seen = fake_anthropic(monkeypatch)
    cfg = Cfg(hunter_anthropic_model="claude-opus-5")
    llm.complete(cfg, "p", purpose="rationale")
    llm.complete(cfg, "p", purpose="essay")
    assert seen[0]["model"] == "claude-sonnet-5-5"
    assert seen[0]["output_config"]["effort"] == "low"
    assert seen[1]["model"] == "claude-opus-5-5"
    assert seen[1]["output_config"]["effort"] == "medium"
    assert spend.run_total() > 0, "both calls are in the ledger"


def test_one_job_can_be_moved_without_a_deploy(monkeypatch):
    seen = fake_anthropic(monkeypatch)
    llm.complete(Cfg(hunter_model_essay="claude-opus-5", hunter_effort_essay="high"),
                 "p", purpose="essay")
    assert seen[0]["model"] == "claude-opus-5"
    assert seen[0]["output_config"]["effort"] == "high"


def test_a_cached_system_text_is_marked(monkeypatch):
    seen = fake_anthropic(monkeypatch)
    llm.complete(Cfg(), "p", system="evidence", purpose="essay", cache=True)
    llm.complete(Cfg(), "p", system="plain", purpose="rationale")
    assert seen[0]["system"] == [{"type": "text", "text": "evidence",
                                  "cache_control": {"type": "ephemeral"}}]
    assert seen[1]["system"] == "plain"


def test_no_call_over_the_month_ceiling(monkeypatch):
    seen = fake_anthropic(monkeypatch)
    monkeypatch.setattr(spend, "month_spent", lambda cfg: 500.0)
    text, notes = llm.complete(Cfg(), "p", purpose="essay")
    assert text == "" and "ceiling" in notes[0]
    assert seen == []


# ---------- the essays' evidence is cached, and sent once ----------

def test_the_essay_evidence_is_cached_and_not_resent_on_a_retry(monkeypatch):
    from hunter.apply import essays
    from hunter.package import voicegate
    evidence = voicegate.build_evidence(
        "Krish Raja took Nine Entertainment's data and automation line from $9m "
        "to $61m over three years. He ran a 14-agent fleet at Mindmake. "
        "He worked at Microsoft.")
    good = ("I took Nine's data and automation line from $9m to $61m over three "
            "years, and ran a 14-agent fleet at Mindmake after that. ")
    answers = iter([good + "x" * essays.MAX_CHARS, good * 2])
    seen = []

    class Block:
        type = "text"
        def __init__(self, t): self.text = t

    class Msgs:
        def create(self, **kw):
            seen.append(kw)
            return types.SimpleNamespace(content=[Block(json.dumps({"answer": next(answers)}))],
                                         stop_reason="end_turn", model=kw["model"],
                                         usage=types.SimpleNamespace(**usage()))

    mod = types.ModuleType("anthropic")
    mod.Anthropic = lambda **kw: types.SimpleNamespace(messages=Msgs())
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    got, why = essays.draft_one(Cfg(), question="Why us?", company="c", role="r",
                                jd_text="jd", evidence=evidence)
    assert got and not why
    assert len(seen) == 2
    for kw in seen:
        assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "worked at microsoft" in kw["system"][0]["text"].lower()
        assert "microsoft" not in json.dumps(kw["messages"]).lower(), \
            "the evidence goes in the cached system text, not the messages"


# ---------- tailor: the retry reads the cache ----------

def test_the_tailor_retry_reads_the_prompt_from_the_cache(monkeypatch):
    from hunter.package import tailor
    seen = []

    class Block:
        type = "text"
        text = "not json"

    class Msgs:
        def create(self, **kw):
            seen.append(kw)
            return types.SimpleNamespace(content=[Block()], stop_reason="end_turn",
                                         model=kw["model"],
                                         usage=types.SimpleNamespace(**usage()))

    mod = types.ModuleType("anthropic")
    mod.Anthropic = lambda **kw: types.SimpleNamespace(messages=Msgs())
    mod.APIError = RuntimeError
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    blocks = {k: {"text": "block"} for k in tailor.BLOCK_KEYS}
    result = tailor.tailor(Cfg(hunter_anthropic_model="claude-opus-5"), None,
                           company="Acme", title="VP Partnerships", jd_text="jd " * 200,
                           master_competencies=["a"], letter_blocks=blocks,
                           evidence="evidence")
    assert any("fallback" in f for f in result.flags)
    assert len(seen) == 2
    first, second = seen[0]["messages"][0]["content"], seen[1]["messages"][0]["content"]
    assert first[0]["cache_control"] == {"type": "ephemeral"}
    assert second[0] == first[0], "the same cached block, byte for byte"
    assert len(second) == 2 and "failed validation" in second[1]["text"]
    assert seen[0]["model"] == "claude-opus-5-5"
    assert seen[0]["output_config"]["effort"] == "medium"
    assert spend.summary()[1].strip().startswith("tailor:")


# ---------- the judge: a role judged an hour ago is not paid for again ----------

def judgement(jid, verdict="present", fit=8):
    return judge.Judgement(job_id=jid, verdict=verdict, fit=fit, confidence="high",
                           answers={}, red_flags=[], likely_decline_code="none",
                           why_it_fits="why", snippet="s", model="claude-opus-5-5",
                           served_model="claude-opus-5-5",
                           usage=usage(10, 10, cr=100), usd=0.05)


def cand(jid, rank=0):
    role = types.SimpleNamespace(job_id=jid, company=jid.upper(), title="VP", location="NYC",
                                 comp="", url="u", jd_url="u", jd_text="posting", source="s")
    return judge_stage.Candidate(role=role, result=types.SimpleNamespace(score=5),
                                 row={"job_id": jid, "status": "staging"}, rank=rank)


def test_a_judgement_from_the_last_day_is_reused(monkeypatch):
    """4 October: the first scheduled attempt judged 100 roles and failed a
    minute later; the retry judged the same 100 again."""
    called, wrote = [], []
    monkeypatch.setattr(judge, "judge_role",
                        lambda cfg, system, role, client=None: called.append(role.job_id)
                        or judgement(role.job_id))
    monkeypatch.setattr(judge_stage, "earlier_judgements",
                        lambda cfg, ids, model: {"a": judge.Judgement(
                            **judge_stage.stored(judgement("a", fit=9)), usage={}, usd=0.0)})
    monkeypatch.setattr(judge_stage, "db_insert", lambda cfg, t, rows, **k: wrote.extend(rows))
    cands = [cand("a", 0), cand("b", 1)]
    out = judge_stage.run(Cfg(), None, None, cands, mode="gate", system="ctx", client=object())
    assert called == ["b"], "only the role with no recent judgement is paid for"
    assert out.judged == 2 and cands[0].judgement.fit == 9
    assert [r["job_id"] for r in wrote] == ["b"], "a reused judgement is not a new cost"
    assert wrote[0]["judgement"]["verdict"] == "present"
    assert wrote[0]["prompt_version"] == judge.PROMPT_VERSION
    assert "1 reused" in out.lines[0]


def test_a_stored_judgement_comes_back_whole():
    j = judgement("x")
    back = judge.Judgement(**judge_stage.stored(j), usage={}, usd=0.0)
    assert back.row_patch().keys() == j.row_patch().keys()
    assert (back.verdict, back.fit, back.why_it_fits) == ("present", 8, "why")


def test_the_judge_does_not_run_over_the_month_ceiling(monkeypatch):
    monkeypatch.setattr(spend, "month_spent", lambda cfg: 999.0)
    monkeypatch.setattr(judge, "judge_role", lambda *a, **k: pytest.fail("judged over the ceiling"))
    monkeypatch.setattr(judge_stage, "db_insert", lambda *a, **k: None)
    cands = [cand("a")]
    out = judge_stage.run(Cfg(), None, None, cands, mode="gate", system="ctx", client=object())
    judge_stage.apply_gate(out, cands)
    assert cands[0].row["status"] == "judge_pending"
    assert "ceiling" in cands[0].row["rejection_reason"]


def test_the_case_writes_the_cache_before_the_rest_read_it(monkeypatch):
    import threading
    order, lock = [], threading.Lock()
    first_done = threading.Event()

    def fake(cfg, system, role, client=None):
        with lock:
            order.append((role.job_id, first_done.is_set()))
        if role.job_id == "r0":
            # Slow enough that a call started alongside it would see it unfinished.
            import time
            time.sleep(0.05)
            first_done.set()
        return "case. FIT: f. RISK: r.", "snip", [], 0.01
    monkeypatch.setattr(judge, "make_the_case", fake)
    roles = [cand(f"r{i}").role for i in range(5)]
    cases, _ = judge_stage.cases_for(Cfg(), "ctx", roles, client=object())
    assert len(cases) == 5
    assert order[0] == ("r0", False)
    assert all(done for jid, done in order[1:]), "the rest start after the first ends"


def test_the_case_is_written_at_medium_effort():
    assert judge.CASE_EFFORT == "medium"
    assert judge.DEFAULT_EFFORT == "low", "the judge stays where it was measured"
