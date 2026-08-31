"""Block selection, jd_mirror validation, and hook assembly: the layer that
keeps shipped prose inside Krish-approved blocks. No model call is made here;
everything under test is pure."""
import pytest

from hunter.package.tailor import (BLOCK_KEYS, TailorError, assemble_hook,
                                   select_candidates, validate,
                                   validate_jd_mirror)

from conftest import TEST_LETTER_BLOCKS

ELEVEN = [f"Competency {i}" for i in range(11)]

JD = (
    "We are looking for a leader to own our partner ecosystem across EMEA, "
    "building channel revenue from scratch. You will design the partnership "
    "motion, own a $40M target, and report to the CRO. 10+ years experience "
    "required in enterprise software go to market strategy and operations."
)


# ---------- deterministic family selection ----------

@pytest.mark.parametrize("title,expected_first", [
    ("General Manager, EMEA", "gm_market_builder"),
    ("Country Manager, UK", "gm_market_builder"),
    ("VP of Strategy and Corporate Development", "corp_dev_strategy"),
    ("Head of Strategy", "corp_dev_strategy"),
    ("AI Chief of Staff", "ai_transformation"),
    ("Chief of Staff to the CEO", "ai_transformation"),
    ("VP of Partnerships, EMEA", "partnerships_alliances"),
    ("Director, Strategic Alliances", "partnerships_alliances"),
    ("Chief Commercial Officer", "commercial_strategy"),
    ("Head of GTM Incentive Strategy", "commercial_strategy"),
    ("Partner Success Director", "partnerships_alliances"),
])
def test_selection_first_candidate(title, expected_first):
    candidates, flags = select_candidates(title)
    assert candidates[0] == expected_first, (title, candidates)
    assert not flags


def test_no_match_falls_back_with_flag():
    candidates, flags = select_candidates("Head of Underwater Basketweaving")
    assert candidates == ["commercial_strategy"]
    assert any("weak archetype match" in f for f in flags)


def test_hybrid_title_yields_multiple_candidates_in_precedence_order():
    candidates, _ = select_candidates("Managing Director, Partnerships")
    assert candidates[0] == "partnerships_alliances"
    assert "gm_market_builder" in candidates


def test_seniority_prefix_still_matches():
    candidates, flags = select_candidates("Senior Director, Strategic Partnerships")
    assert candidates[0] == "partnerships_alliances" and not flags


# ---------- jd_mirror validation ----------

def test_mirror_from_jd_language_passes():
    assert validate_jd_mirror("own the partner ecosystem across EMEA", JD) == []


def test_empty_mirror_is_valid():
    assert validate_jd_mirror("", JD) == []


def test_mirror_number_must_be_verbatim():
    fails = validate_jd_mirror("own a $45M channel target", JD)
    assert any("not in the JD verbatim" in f for f in fails)


def test_mirror_number_verbatim_passes():
    assert validate_jd_mirror("own a $40M channel target", JD) == []


def test_mirror_over_12_words_fails():
    long = "a very long clause that keeps going and going well past the twelve word limit"
    assert any("exceeds" in f for f in validate_jd_mirror(long, JD))


def test_mirror_off_topic_fails_overlap():
    fails = validate_jd_mirror("delightful culinary adventures await ambitious chefs", JD)
    assert any("mirror the JD" in f for f in fails)


def test_mirror_banned_language_fails():
    fails = validate_jd_mirror("leverage the partner ecosystem", JD)
    assert any("banned language" in f for f in fails)


# ---------- full decision validation ----------

def base_data(**over):
    d = {
        "competency_order": list(reversed(ELEVEN)),
        "letter_bullet_to_cut": 2,
        "block_key": "partnerships_alliances",
        "jd_mirror": "building channel revenue from scratch",
        "hiring_lead": "Hiring Team",
    }
    d.update(over)
    return d


CANDIDATES = ["partnerships_alliances", "commercial_strategy"]


def test_valid_passes():
    assert validate(base_data(), ELEVEN, CANDIDATES, JD) == []


def test_block_outside_candidates_rejected():
    fails = validate(base_data(block_key="gm_market_builder"), ELEVEN, CANDIDATES, JD)
    assert any("candidates" in f for f in fails)


def test_permutation_enforced():
    d = base_data(competency_order=ELEVEN[:10] + ["Invented Skill"])
    assert any("permutation" in f for f in validate(d, ELEVEN, CANDIDATES, JD))


def test_bullet_range():
    fails = validate(base_data(letter_bullet_to_cut=5), ELEVEN, CANDIDATES, JD)
    assert any("1..4" in f for f in fails)


def test_empty_hiring_lead():
    fails = validate(base_data(hiring_lead="  "), ELEVEN, CANDIDATES, JD)
    assert any("hiring_lead" in f for f in fails)


# ---------- assembly ----------

def test_assemble_hook_substitutes_all_slots():
    hook = assemble_hook(TEST_LETTER_BLOCKS, "partnerships_alliances",
                         "Cresta", "building channel revenue from scratch")
    assert "Cresta" in hook
    assert "building channel revenue from scratch" in hook
    assert "[[" not in hook


def test_assemble_hook_empty_mirror_uses_default():
    hook = assemble_hook(TEST_LETTER_BLOCKS, "gm_market_builder", "Acme", "")
    assert TEST_LETTER_BLOCKS["gm_market_builder"]["default_mirror"] in hook
    assert "[[" not in hook


def test_block_keys_stable():
    assert set(TEST_LETTER_BLOCKS) == set(BLOCK_KEYS)


def test_load_blocks_rejects_missing_slot():
    from hunter.config import Config
    import json
    bad_letter = {k: dict(v) for k, v in TEST_LETTER_BLOCKS.items()}
    bad_letter["gm_market_builder"] = {"text": "no slots here", "approved_at": "x"}
    cfg = Config(supabase_url="http://offline.invalid", supabase_key="offline",
                 raw={"hunter_letter_blocks": json.dumps(bad_letter),
                      "hunter_cv_summary_blocks": json.dumps(
                          {k: {"text": "t", "approved_at": "x"} for k in BLOCK_KEYS})})
    from hunter.package.tailor import load_blocks
    with pytest.raises(TailorError, match="COMPANY"):
        load_blocks(cfg)
