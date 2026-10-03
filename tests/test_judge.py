"""The judge, offline: what it is given, what it refuses, and what its answers
mean for staging. No socket is opened; responses are built here.
"""
import json
import types

import pytest

from hunter import judge, judgedata

POSTING = (
    "KBRA is a credit ratings agency. The Head of Data Management will define and run "
    "the enterprise data management strategy, reporting to the CTO, leading Data "
    "Operations, governance, quality and taxonomy. Salary range $225,000 - $300,000. "
    "New York, NY.")


def answer(**over):
    a = {
        "business": {"what_it_sells": "credit ratings", "to_whom": "issuers",
                     "category": "outside", "employer_type": "bank_insurer_asset_manager",
                     "rulings_that_bear": ["Citi, declined business_uninteresting"],
                     "quote": "KBRA is a credit ratings agency"},
        "function": {"family": "none", "signals": ["data governance"],
                     "quote": "leading Data Operations, governance, quality and taxonomy"},
        "level_scope": {"reports_to": "CTO", "team": "Data Operations", "mandate": "run data",
                        "quote": "reporting to the CTO"},
        "requirements_he_lacks": {"items": [], "quote": ""},
        "logistics": {"location": "New York", "location_ok": "yes", "pay": "$225K to $300K",
                      "pay_meets_floor": "yes", "quote": "Salary range $225,000 - $300,000"},
        "verdict": "reject", "fit": 1, "confidence": "high",
        "likely_decline_code": "business_uninteresting",
        "most_likely_reason_he_says_no": "a ratings agency data seat",
        "red_flags": ["ratings agency", "data governance function"],
        "mandate": "Run enterprise data management under the CTO at a ratings agency.",
        "fit_text": "It does not fit: the seat is data governance, not one of his five "
                    "families, inside a regulated financial incumbent.",
        "risk": "Financial services employer and a technology reporting line.",
        "archetype": "none",
        "snippet": "KBRA rates credit; this seat runs enterprise data governance under the CTO.",
    }
    a.update(over)
    return a


def message(payload, *, stop="end_turn", model="claude-opus-5-5"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)], stop_reason=stop,
        model=model, usage=types.SimpleNamespace(input_tokens=1800, output_tokens=1500,
                                                 cache_read_input_tokens=21767,
                                                 cache_creation_input_tokens=0))


ROLE = judge.Role(job_id="kbra:hdm", company="KBRA", title="Head of Data Management",
                  posting=POSTING)


def test_a_sound_answer_becomes_a_judgement_with_its_cost():
    _, j = judge.interpret(ROLE, message(answer()), model="claude-opus-5-5")
    assert j.verdict == "reject" and j.fit == 1 and not [
        p for p in j.problems if not p.startswith("sheet text")]
    assert j.usd > 0 and j.usage["cache_read_input_tokens"] == 21767
    assert judge.disposition(j) == "blocked"


def test_a_quote_that_is_not_in_the_posting_voids_the_judgement():
    bad = answer(business={**answer()["business"], "quote": "KBRA is a fintech unicorn"})
    _, j = judge.interpret(ROLE, message(bad), model="claude-opus-5-5")
    assert j.verdict == "pending"
    assert any("not in the posting" in p for p in j.problems)
    assert judge.disposition(j) == "judge_pending"


def test_quotes_survive_typography_but_not_paraphrase():
    curly = answer(level_scope={**answer()["level_scope"],
                                "quote": "reporting  to the CTO"})
    _, j = judge.interpret(ROLE, message(curly), model="claude-opus-5-5")
    assert j.verdict == "reject"
    para = answer(level_scope={**answer()["level_scope"], "quote": "reports into the CTO"})
    _, j2 = judge.interpret(ROLE, message(para), model="claude-opus-5-5")
    assert j2.verdict == "pending"


def test_business_and_function_must_be_quoted():
    unquoted = answer(function={**answer()["function"], "quote": ""})
    _, j = judge.interpret(ROLE, message(unquoted), model="claude-opus-5-5")
    assert j.verdict == "pending"


def test_a_refusal_a_cutoff_and_bad_json_are_pending_never_a_default():
    for msg in (message(answer(), stop="refusal"), message(answer(), stop="max_tokens"),
                message("not json at all")):
        _, j = judge.interpret(ROLE, msg, model="claude-opus-5-5")
        assert j.verdict == "pending" and j.problems


def test_an_answer_from_another_model_is_held_never_presented():
    good = answer(verdict="present", fit=9)
    _, j = judge.interpret(ROLE, message(good, model="claude-opus-4-8"), model="claude-opus-5-5")
    assert j.verdict == "hold"
    assert judge.disposition(j) == "held"


def test_a_figure_from_nowhere_drops_the_sheet_text_but_keeps_the_verdict():
    made_up = answer(fit_text="He grew a business from $9M to $999M, which fits.")
    _, j = judge.interpret(ROLE, message(made_up), model="claude-opus-5-5")
    assert j.verdict == "reject" and j.why_it_fits == ""
    assert any(p.startswith("sheet text not used") for p in j.problems)
    # the same figure is fine when it is in his recorded evidence
    _, j2 = judge.interpret(ROLE, message(made_up), model="claude-opus-5-5",
                            evidence="Captify APAC: $9M to $999M")
    assert j2.why_it_fits


@pytest.mark.parametrize("verdict,fit,want", [
    ("present", 9, "staging"), ("present", 8, "staging"), ("present", 7, "held"),
    ("hold", 9, "held"), ("reject", 9, "blocked")])
def test_what_a_verdict_means_for_staging(verdict, fit, want):
    _, j = judge.interpret(ROLE, message(answer(verdict=verdict, fit=fit)), model="claude-opus-5-5")
    assert judge.disposition(j, min_fit=8) == want


def test_the_context_is_byte_identical_for_the_same_inputs():
    """A cache that misses every call costs ten times as much. Same inputs, same
    bytes, so the fixed context is cached across a run."""
    kw = dict(canon_sections={"5": "families", "2": "history"},
              tabs={"Profile": "p", "Interview Answers": "i"}, targets_text="t",
              rulings=["[2026-09-10] YES | Clay | Head of GTM | his words: Yes"])
    a, b = judge.build_system(**kw), judge.build_system(**kw)
    assert a == b
    assert a.index("canon 2") < a.index("canon 5")
    assert "Never present a role <9/10" in a, "the stale line is named as stale"


def test_the_request_caches_the_context_and_asks_for_the_schema():
    p = judge.request_params("CONTEXT", ROLE, model="claude-opus-5-5", effort="high")
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert p["output_config"]["effort"] == "high"
    assert p["output_config"]["format"]["schema"] is judge.SCHEMA
    assert "thinking" not in p, "Opus 5.5 cannot turn thinking off; effort controls depth"
    assert POSTING in p["messages"][0]["content"]


def test_the_schema_closes_every_object():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(judge.SCHEMA)


@pytest.mark.parametrize("text,label", [
    ("Yes", "yes"), ("Applied", "yes"), ("go", "yes"),
    ("Declined - domain expertise", "no"),
    ("Declined - dead posting", None), ("Declined - duplicate row", None),
    ("Declined - sourced before the bar was fixed", None), ("", None)])
def test_only_his_own_rulings_are_ground_truth(text, label):
    assert judgedata.label_of(text)[0] == label


def test_the_split_never_shows_the_judge_a_ruling_from_after_the_cutoff():
    import datetime
    from hunter import judge_eval

    def ruled(day, label):
        return judgedata.Ruled(job_id=f"j{day}", company="c", title="t", label=label,
                               words="Yes", code=None, comp="", location="", url="",
                               presented_at=f"2026-09-{day:02d}T10:00:00Z", jd_text="",
                               snippet="", why="", source="", archived=True)
    rows = [ruled(10, "yes"), ruled(16, "no"), ruled(17, "yes"), ruled(24, "no")]
    before, after, undated = judgedata.split(rows, judge_eval.CUTOFF)
    assert [r.job_id for r in before] == ["j10", "j16"]
    assert [r.job_id for r in after] == ["j17", "j24"]
    assert all(r.when < judge_eval.CUTOFF for r in before)
