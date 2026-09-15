"""The AI-native GTM evidence key, and the truncation that would have defeated it.

Krish's feedback on the first real package: it said nothing about the AI-native
GTM work he is most experienced in. The cause was that none of it was in the
evidence haystack, so package/voicegate.py would have rejected every sentence
making the claim. These tests hold the content rules the source repository imposes
on that material, and the one structural trap: evidence the gate can trace but the
model never sees.
"""
from hunter.apply.gtmseed import CONFIG_KEY, EVIDENCE
from hunter.package import voicegate
from hunter.package.tailor import EVIDENCE_MAX_CHARS, _prompt

# From 04_PROOF_RECORDS.md's own public-use rules. Each of these is retired, and
# anything in this key is a number the voice gate will then permit in a CV.
RETIRED_NUMBERS = [
    "22%", "22 percent",   # R-02's revenue lift
    "40%", "75%",          # R-02's production and setup reductions
    "$2K", "$8K", "$3K",   # R-04's prices; no prices from those records go public
]


def test_the_retired_numbers_are_not_in_the_evidence():
    for token in RETIRED_NUMBERS:
        assert token not in EVIDENCE, (
            f"{token!r} is retired from public use in 04_PROOF_RECORDS.md and "
            f"must not become a number the voice gate permits")


def test_only_the_two_clients_the_master_cv_already_names_are_named():
    # The rest of the engagements stay at role and sector per 04_PROOF.md.
    assert "AdFixus" in EVIDENCE
    assert "Meliora" in EVIDENCE
    for anonymous in ["Vox Media", "Steph Darmanin", "Dipti Divekar",
                      "Legacy Ascend"]:
        assert anonymous not in EVIDENCE


def test_attendance_brands_are_labelled_as_attendance_not_clients():
    # 04_PROOF.md guardrail: never describe an attendee organisation as a client.
    i = EVIDENCE.index("BBC")
    tail = EVIDENCE[i:]
    assert "attendance proof" in tail
    assert "never described as clients" in tail


def test_the_claims_krish_asked_for_are_now_traceable():
    """The point of the whole key: these are the phrases the gate could not find."""
    hay = voicegate.build_evidence(EVIDENCE)
    for phrase in ["build your ai brain", "build your ai gtm", "ai-native gtm",
                   "product, price, positioning"]:
        assert phrase in hay, phrase


def test_a_sentence_making_the_claim_now_passes_the_gate():
    hay = voicegate.build_evidence(EVIDENCE)
    claim = ("I design AI-native GTM models through Build your AI GTM, across "
             "product, price, positioning and people, including a $254K POC "
             "contracted at AdFixus.")
    verdict = voicegate.check(claim, evidence=hay, allow_names=frozenset({"Harvey"}))
    assert verdict.ok, verdict.failures


def test_the_gate_still_refuses_an_invented_client():
    hay = voicegate.build_evidence(EVIDENCE)
    verdict = voicegate.check("I built the AI GTM model at Salesforce.",
                              evidence=hay)
    assert not verdict.ok
    assert any("Salesforce" in f for f in verdict.failures)


def test_the_key_name_is_the_one_run_py_reads():
    assert CONFIG_KEY == "hunter_ai_gtm_evidence"


def test_the_prompt_carries_the_evidence_whole():
    """The trap. The prompt capped evidence at 12000 characters while the real
    haystack ran to 30743, so the gate would trace claims the model never saw.
    """
    evidence = "UNIQUE_TAIL_MARKER".rjust(40000, "x")
    assert len(evidence) > 12000
    prompt = _prompt(None, "Harvey", "Head of GTM", " " * 300, ["a"] * 11,
                     ["commercial_strategy"],
                     {"commercial_strategy": {"text": "x"}},
                     highlights=["h"], evidence_view=evidence, voice_rules="- none")
    assert "UNIQUE_TAIL_MARKER" in prompt
    assert evidence in prompt


def test_the_real_evidence_is_well_inside_the_ceiling():
    # Nothing is truncated today, and the flag exists for the day that changes.
    assert len(EVIDENCE) < EVIDENCE_MAX_CHARS


# ---------- the naming law ----------

def test_the_banned_variants_do_not_ban_the_correct_name():
    """voicegate lowercases both the phrase and the body, so "MindMake" in this list
    would ban "Mindmake" too. The first package using this evidence wrote
    "Build your AI GTM at Mind/Make", which is what the list is for."""
    from hunter.apply.gtmseed import NAME_VARIANTS_BANNED
    good = "I run Build your AI GTM at Mindmake, and the site is mindmake.co."
    hay = voicegate.build_evidence(EVIDENCE)
    verdict = voicegate.check(good, evidence=hay,
                              banned_phrases=NAME_VARIANTS_BANNED)
    assert verdict.ok, verdict.failures


def test_each_variant_is_caught():
    from hunter.apply.gtmseed import NAME_VARIANTS_BANNED
    hay = voicegate.build_evidence(EVIDENCE)
    for bad in ("Build your AI GTM at Mind/Make.", "I founded Mindmaker.",
                "The Mindmaker programs.", "Delivered through Mindmake AI."):
        verdict = voicegate.check(bad, evidence=hay,
                                  banned_phrases=NAME_VARIANTS_BANNED)
        assert not verdict.ok, bad
        assert any("banned phrase" in f for f in verdict.failures)


def test_the_law_is_in_the_evidence_so_the_model_reads_it():
    assert "naming law" in EVIDENCE
    assert "Never Mind/Make" in EVIDENCE
