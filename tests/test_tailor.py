"""tailor() validation: the schema guard that makes fabrication structurally
impossible. No model call is made here; validate() is pure."""
from hunter.package.tailor import validate

ELEVEN = [f"Competency {i}" for i in range(11)]


def base_data(**over):
    d = {
        "competency_order": list(reversed(ELEVEN)),
        "letter_bullet_to_cut": 2,
        "hook": "Acme ships agents into production. I took Captify APAC from $0 to $12M ARR at 22% EBITDA.",
        "hiring_lead": "Hiring Team",
    }
    d.update(over)
    return d


def test_valid_passes():
    assert validate(base_data(), ELEVEN) == []


def test_decimal_numbers_do_not_count_as_sentence_breaks():
    d = base_data(hook="Acme is scaling fast. I closed a $1.5M Microsoft partnership and drove 30% of regional revenue.")
    assert validate(d, ELEVEN) == []


def test_three_sentences_fail():
    d = base_data(hook="One thing. Two things. Three things.")
    assert any("two sentences" in f for f in validate(d, ELEVEN))


def test_permutation_enforced():
    d = base_data(competency_order=ELEVEN[:10] + ["Invented Skill"])
    assert any("permutation" in f for f in validate(d, ELEVEN))


def test_bullet_range():
    assert any("1..4" in f for f in validate(base_data(letter_bullet_to_cut=5), ELEVEN))


def test_banned_language():
    d = base_data(hook="We leverage synergy.")
    assert any("banned language" in f for f in validate(d, ELEVEN))


def test_empty_hiring_lead():
    assert any("hiring_lead" in f for f in validate(base_data(hiring_lead="  "), ELEVEN))


def test_placeholder_in_hook():
    d = base_data(hook="Why {{COMPANY}} matters.")
    assert any("placeholder" in f for f in validate(d, ELEVEN))
