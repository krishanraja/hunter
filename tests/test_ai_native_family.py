"""The sixth block family, and the JD signal that selects it.

Krish's call 2026-09-15. The first real package went to a GTM seat at an AI company
and got the commercial_strategy block, which opens on Captify's pricing and
forecasting model: right for a media business, wrong for this one. The title
"Head of GTM Strategy & Operations" carries no AI word at all, so the signal has to
come from the job description.
"""
from hunter.apply.gtmseed import BLOCK_KEY, CV_BLOCK, LETTER_BLOCK
from hunter.package import voicegate
from hunter.package.tailor import (AI_NATIVE_JD_THRESHOLD, BLOCK_KEYS,
                                  ai_native_signals, is_ai_native_jd,
                                  select_candidates)

# The live Harvey title, verbatim from the pipeline row that exposed this.
HARVEY_TITLE = "Head of GTM Strategy & Operations, AMER"

# Harvey's own boilerplate, verbatim from the live JD. One sentence in 4325
# characters, which is why counting terms across the whole JD failed: the rest of
# it describes the job, not the product.
AI_NATIVE_JD = (
    "Own GTM strategy and operations for the Americas. By combining frontier "
    "agentic AI, an enterprise-grade platform, and deep domain expertise, we are "
    "reshaping how critical knowledge work gets done for decades to come. Partner "
    "with AMER Enterprise Sales Leadership to develop field strategy."
)
# No strong phrase, four weak ones. The other road to the same verdict.
WEAK_SIGNAL_JD = (
    "Our LLM powers generative ai workflows. Inference cost and prompt quality are "
    "first-order commercial constraints, and evals gate every release."
)
# A media business that uses AI. Says "AI" repeatedly and is not an AI company.
MEDIA_JD = (
    "A digital publisher scaling programmatic revenue across display and video. "
    "We use AI in the newsroom and AI in ad operations, and we are investing in AI "
    "tooling for our sales team."
)


def test_the_family_is_in_block_keys():
    assert BLOCK_KEY in BLOCK_KEYS
    assert len(BLOCK_KEYS) == 6


def test_harvey_at_an_ai_company_gets_the_new_family_first():
    candidates, flags = select_candidates(HARVEY_TITLE, AI_NATIVE_JD)
    assert candidates[0] == BLOCK_KEY
    assert "commercial_strategy" in candidates  # still offered, the model chooses
    assert any("AI-native company" in f for f in flags)


def test_the_same_title_at_a_media_company_is_unchanged():
    candidates, flags = select_candidates(HARVEY_TITLE, MEDIA_JD)
    assert candidates == ["commercial_strategy"]
    assert flags == []


def test_one_mention_of_ai_is_not_a_signal():
    candidates, _ = select_candidates(HARVEY_TITLE,
                                     "A logistics business. We are exploring AI.")
    assert BLOCK_KEY not in candidates


def test_one_strong_phrase_is_enough_because_it_appears_once():
    strong, weak = ai_native_signals(AI_NATIVE_JD)
    assert strong
    assert len(weak) < AI_NATIVE_JD_THRESHOLD  # counting alone would have missed it
    assert is_ai_native_jd(AI_NATIVE_JD)[0]


def test_weak_signals_still_count_together():
    strong, weak = ai_native_signals(WEAK_SIGNAL_JD)
    assert not strong
    assert len(weak) >= AI_NATIVE_JD_THRESHOLD
    assert is_ai_native_jd(WEAK_SIGNAL_JD)[0]


def test_a_publisher_that_uses_ai_is_not_an_ai_company():
    strong, weak = ai_native_signals(MEDIA_JD)
    assert not strong
    assert len(weak) < AI_NATIVE_JD_THRESHOLD
    assert not is_ai_native_jd(MEDIA_JD)[0]


def test_the_promotion_says_why_in_the_run_report():
    _candidates, flags = select_candidates(HARVEY_TITLE, AI_NATIVE_JD)
    assert any("frontier agentic" in f or "agentic ai" in f for f in flags)


def test_an_ai_title_needs_no_jd_signal():
    candidates, _ = select_candidates("Head of AI GTM", "")
    assert BLOCK_KEY in candidates


def test_partnerships_and_corp_dev_are_not_promoted():
    """Those seats have their own blocks, which already fit an AI company."""
    for title, expected in (("Head of Partnerships", "partnerships_alliances"),
                            ("VP of Strategy", "corp_dev_strategy")):
        candidates, _ = select_candidates(title, AI_NATIVE_JD)
        assert candidates[0] == expected
        assert BLOCK_KEY not in candidates


def test_no_match_still_falls_back_rather_than_losing_the_package():
    candidates, flags = select_candidates("Head of Facilities", AI_NATIVE_JD)
    assert candidates == ["commercial_strategy"]
    assert any("weak archetype" in f for f in flags)


# ---------- the approved copy ----------

def test_the_letter_block_carries_both_slots_and_a_default_mirror():
    assert "[[COMPANY]]" in LETTER_BLOCK["text"]
    assert "[[JD_MIRROR]]" in LETTER_BLOCK["text"]
    assert LETTER_BLOCK["default_mirror"]


def test_both_blocks_are_in_the_register_of_the_approved_five():
    # Letter blocks run 271 to 330 chars, CV summaries 349 to 399, measured from
    # the five Krish approved. Close enough that the letter still fits one page.
    assert 260 <= len(LETTER_BLOCK["text"]) <= 340
    assert 340 <= len(CV_BLOCK["text"]) <= 420
    # The closing line every approved CV block shares.
    assert CV_BLOCK["text"].endswith(
        "I apply Level 5 agency and accountability to what is in front of me, and "
        "I connect dots in ways others tend to follow.")


def test_every_claim_in_both_blocks_traces_to_the_evidence():
    """The blocks are Krish-approved copy, so the gate does not police them. They
    still have to be true against the same haystack, or the CV and the letter say
    different things."""
    from hunter.apply.gtmseed import EVIDENCE

    # Against the GTM key ALONE, not the whole haystack. The blocks must not depend
    # on the master CV happening to carry a word: "18 months" is in the approved
    # ai_transformation block and in canon, and was in neither the master (which
    # says 1.5yr) nor this key until the check caught it.
    hay = voicegate.build_evidence(EVIDENCE)
    for text in (LETTER_BLOCK["text"].replace("[[COMPANY]] is [[JD_MIRROR]]", "X"),
                 CV_BLOCK["text"]):
        verdict = voicegate.check(text, evidence=hay)
        assert verdict.ok, verdict.failures
