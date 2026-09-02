"""Column J must read the same on every pass, and must never invent a
figure the job description does not contain."""
import pytest

from hunter.package.rationale import (MAX_CHARS, assemble, deterministic,
                                      digits_grounded, validate)

JD = ("Higgsfield AI is hiring a Head of Entertainment GTM. We are at $500M ARR "
      "with 25M users and 390 Fortune 500 customers. You will own the "
      "entertainment vertical end to end and build the partnerships motion.")


def good_parts(**over):
    p = {"mandate": "Own the entertainment vertical end to end at $500M ARR.",
         "fit": "Maps to the Engine-Builder archetype: he has built a category "
                "motion from zero and carried the P&L for it.",
         "risk": "The remit leans partnerships, so the commercial scope needs "
                 "checking before he commits.",
         "archetype": "partnerships_alliances"}
    p.update(over)
    return p


def test_a_grounded_rationale_passes():
    assert validate(good_parts(), JD) == []


def test_a_figure_absent_from_the_jd_is_rejected():
    """The whole point of the guard: a plausible invented number is worse
    than no rationale, because Krish would act on it."""
    fails = validate(good_parts(mandate="Own the vertical at $900M ARR."), JD)
    assert any("figure" in f for f in fails)


def test_figures_that_are_in_the_jd_are_allowed():
    assert digits_grounded("390 Fortune 500 customers and 25M users", JD)


def test_banned_language_is_rejected():
    fails = validate(good_parts(fit="A world-class chance to leverage synergy."), JD)
    assert any("banned" in f for f in fails)


def test_em_dash_is_rejected():
    fails = validate(good_parts(risk="Scope is unclear \u2014 check it."), JD)
    assert any("banned" in f for f in fails)


def test_thin_and_bloated_rationales_are_both_rejected():
    assert any("thin" in f for f in validate(
        {"mandate": "Short.", "fit": "Fits.", "risk": "None.",
         "archetype": "commercial_strategy"}, JD))
    assert any("too long" in f for f in validate(
        good_parts(fit="x" * (MAX_CHARS + 50)), JD))


def test_empty_field_is_rejected():
    assert any("risk is empty" in f for f in validate(good_parts(risk="  "), JD))


def test_assembled_shape_is_identical_every_time():
    text = assemble(good_parts())
    assert text.count("FIT:") == 1 and text.count("RISK:") == 1
    assert text.startswith("Own the entertainment vertical")


def test_the_fallback_admits_what_it_does_not_know():
    """An honest fallback beats a fabricated rationale. It must say so."""
    text = deterministic("Acme", "VP Strategy", 8, "Engine-Builder signals 4")
    assert "no grounded rationale" in text
    assert "8 of 10" in text
    assert "\u2014" not in text


def test_thin_jd_never_calls_the_model(monkeypatch):
    from hunter.package import rationale as mod

    def explode(*a, **kw):
        raise AssertionError("model called for a JD too thin to ground anything")

    monkeypatch.setattr(mod, "validate", explode)
    text, flags = mod.write_rationale(cfg=None, canon=None, company="Acme",
                                      title="VP Strategy", jd="too short",
                                      score=8, score_reason="signals 4")
    assert flags == ["thin JD"] and "no grounded rationale" in text
