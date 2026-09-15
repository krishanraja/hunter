"""The gate that makes generated CV prose acceptable rather than risky.

Canon 9.12 locked the CV to lossless tailoring. Krish lifted that on 2026-09-14
for the summary and the hook only. The rule that earns the lift is factual: a
generated claim that cannot be traced to recorded evidence is a hard failure, so
the model can reframe his evidence but cannot invent a metric into his CV.
"""
from __future__ import annotations

import pytest

from hunter.package import voicegate as vg
from hunter.package.voicegate import VoiceGateError

EVIDENCE = vg.build_evidence(
    "Built Captify APAC region for a marketing data/ML business from $0 to $12M "
    "ARR in three years at 22% EBITDA, as first hire scaling to an 18-person team.",
    "Grew broadcaster data and automation revenue from $9M to $61M in three "
    "years; launched 70+ commercial products and built a $55M automated "
    "marketplace from scratch.",
    "Scaled a telco martech acquisition in APAC from $4M to $38M across 12 "
    "fragmented markets through two M&As.",
    "$1.5M Microsoft partnership. 30% of regional revenue from partners.",
    "14-agent autonomous AI operating system. 4000 leaders. $254K POC at AdFixus.",
    "Nine Entertainment. SingTel. Nexxen. Mindmake. Meliora. BBC.",
)

BANNED = ("at the intersection of", "uniquely positioned to", "leverage",
          "synergy", "cutting-edge", "I am passionate about")


# ---------------- the factual gate ----------------

def test_his_real_numbers_pass():
    text = ("Sixteen years commercializing data and tech. I built Captify APAC "
            "from $0 to $12M ARR at 22% EBITDA as the first person on the ground.")
    assert vg.check(text, evidence=EVIDENCE, banned_phrases=BANNED).ok


def test_an_inflated_number_is_a_hard_failure():
    """$61M is real. $85M is not, and this is the failure that would otherwise
    reach a real employer."""
    text = "I grew broadcaster revenue from $9M to $85M at Nine Entertainment."
    v = vg.check(text, evidence=EVIDENCE)
    assert not v.ok
    assert any("$85m" in f for f in v.failures)


def test_an_invented_employer_is_a_hard_failure():
    v = vg.check("I closed the $1.5M Salesforce partnership.", evidence=EVIDENCE)
    assert not v.ok and any("Salesforce" in f for f in v.failures)


def test_a_multi_word_name_is_traced_token_by_token():
    """"Captify APAC" is traceable when the evidence carries both words, so
    ordinary rephrasing is not rejected."""
    assert vg.check("I ran Captify APAC.", evidence=EVIDENCE).ok


def test_small_written_numbers_are_not_treated_as_claims():
    assert vg.check("I have done both, four times, across three continents.",
                    evidence=EVIDENCE).ok


def test_a_percentage_must_also_be_traceable():
    assert vg.check("Partners reached 30% of regional revenue.",
                    evidence=EVIDENCE).ok
    v = vg.check("Partners reached 65% of regional revenue.", evidence=EVIDENCE)
    assert not v.ok and any("65%" in f for f in v.failures)


def test_comma_and_space_formatting_does_not_defeat_the_match():
    """"$1,500,000" style and spacing must not let a real number read as invented
    or an invented one read as real."""
    assert vg.check("A $1.5M Microsoft partnership.", evidence=EVIDENCE).ok
    assert vg.check("A $ 1.5M Microsoft partnership.", evidence=EVIDENCE).ok


# ---------------- the style rules ----------------

def test_an_em_dash_is_rejected():
    v = vg.check("I ship AI \u2014 in production.", evidence=EVIDENCE)
    assert not v.ok and any("em dash" in f for f in v.failures)


def test_every_banned_phrase_from_profile_is_rejected():
    for phrase in BANNED:
        v = vg.check(f"I work {phrase} things.", evidence=EVIDENCE,
                     banned_phrases=BANNED)
        assert not v.ok, phrase
        assert any(phrase in f for f in v.failures)


def test_an_unreplaced_placeholder_is_rejected():
    v = vg.check("{{COMPANY}} is scaling.", evidence=EVIDENCE)
    assert not v.ok and any("placeholder" in f for f in v.failures)


def test_an_unfilled_template_slot_is_rejected():
    v = vg.check("[[COMPANY]] is scaling fast.", evidence=EVIDENCE)
    assert not v.ok and any("template slot" in f for f in v.failures)


def test_length_bounds_keep_the_cv_to_one_page():
    long_text = "I built markets. " * 200
    v = vg.check(long_text, evidence=EVIDENCE, max_chars=1200)
    assert not v.ok and any("too long" in f for f in v.failures)
    v = vg.check("Short.", evidence=EVIDENCE, min_chars=100)
    assert not v.ok and any("too short" in f for f in v.failures)


def test_empty_output_is_rejected():
    assert not vg.check("", evidence=EVIDENCE).ok
    assert not vg.check("   ", evidence=EVIDENCE).ok


# ---------------- every failure is reported at once ----------------

def test_all_failures_come_back_together_for_one_regeneration_pass():
    text = "I leverage $99M synergy \u2014 at Salesforce."
    v = vg.check(text, evidence=EVIDENCE, banned_phrases=BANNED)
    assert not v.ok
    joined = " ".join(v.failures)
    for expected in ("em dash", "leverage", "synergy", "$99m", "Salesforce"):
        assert expected in joined, expected


def test_raise_if_bad_names_what_failed():
    v = vg.check("I grew to $85M.", evidence=EVIDENCE)
    with pytest.raises(VoiceGateError, match="summary failed the voice gate"):
        v.raise_if_bad("summary")


def test_raise_if_bad_is_silent_on_a_pass():
    vg.check("I built Captify APAC.", evidence=EVIDENCE).raise_if_bad("summary")


def test_allow_names_lets_the_target_company_through():
    """The JD's own company is legitimately new text, so the caller passes it in
    rather than the gate guessing."""
    v = vg.check("Higgsfield is scaling entertainment GTM.", evidence=EVIDENCE)
    assert not v.ok
    assert vg.check("Higgsfield is scaling entertainment GTM.",
                    evidence=EVIDENCE,
                    allow_names=frozenset({"Higgsfield"})).ok


# ---------- sentence-initial capitals ----------

def test_an_ordinary_word_opening_a_sentence_is_not_a_company():
    """Two real packages lost their tailored prose to this: a summary rejected for
    "Underneath" and a hook for "Designing". A rejection drops back to a generic
    block, so the cost was customisation, for no factual reason."""
    hay = vg.build_evidence("captify apac went from $0 to $12m arr")
    for text in ("Underneath that sits one operating model.",
                 "Designing those models is my practice.",
                 "Scaling it was the easy part. Winning the first deal was not."):
        verdict = vg.check(text, evidence=hay)
        assert verdict.ok, (text, verdict.failures)


def test_a_fabricated_employer_still_fails_wherever_it_sits():
    hay = vg.build_evidence("captify apac went from $0 to $12m arr")
    for text in ("Salesforce is where I built it.",
                 "I built it at Salesforce.",
                 "Underneath Salesforce sat a partner engine."):
        verdict = vg.check(text, evidence=hay)
        assert not verdict.ok, text
        assert any("Salesforce" in f for f in verdict.failures), verdict.failures


def test_the_exemption_is_one_token_only():
    """A multi-word capitalised run is not sentence case, so the employer in it
    still has to trace."""
    hay = vg.build_evidence("captify apac")
    verdict = vg.check("Underneath Captify sat a partner engine.", evidence=hay)
    assert verdict.ok, verdict.failures
    verdict = vg.check("Underneath Nine Entertainment sat a data business.",
                              evidence=hay)
    assert not verdict.ok
    assert any("Nine" in f or "Entertainment" in f for f in verdict.failures)
