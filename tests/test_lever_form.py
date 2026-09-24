"""The Lever form adapter, against two real saved forms.

Lever was the second most common ATS in his pipeline and had no form adapter,
so on 2026-09-24 five of the six roles in a batch reported "apply by hand" and
only one could arm the extension. Versapay and Rembrand were both Lever.

The fixtures are the real apply pages, saved that day. Lever publishes no
question schema through its API, but it embeds every card and every EEO survey
as JSON in a hidden baseTemplate input, with the employer's own wording,
required flags and option ids. That is what this reads.
"""
from __future__ import annotations

import pathlib

import pytest

from hunter.apply import lever_form as L
from hunter.apply.model import FLAGGED_KINDS

FIX = pathlib.Path(__file__).parent / "fixtures"


def spec(name, slug):
    return L.parse((FIX / f"lever_{name}_apply.html").read_text(),
                   slug=slug, posting_id="pid")


@pytest.fixture
def versapay():
    return spec("versapay", "versapay")


@pytest.fixture
def rembrand():
    return spec("rembrand", "rembrand")


def test_the_core_application_is_read(versapay):
    keys = {f.key for f in versapay.fields}
    for k in ("name", "email", "phone", "location", "org", "resume"):
        assert k in keys, k
    assert versapay.readable and versapay.ats == "lever"


def test_required_is_the_vendors_flag_and_differs_per_posting(versapay, rembrand):
    """The measurement that makes an assumption impossible: versapay requires
    phone AND location, rembrand requires neither. A hardcoded core set would
    have been wrong on one of these two."""
    vreq = {f.key for f in versapay.fields if f.required}
    rreq = {f.key for f in rembrand.fields if f.required}
    assert {"name", "email", "phone", "location"} <= vreq
    assert {"name", "email"} <= rreq
    assert "phone" not in rreq and "location" not in rreq


def test_every_eeo_survey_question_is_demographic(versapay):
    """Lever's survey block is the EEO section. Every question in one is his to
    answer or withhold, so all of them are flagged and none auto-filled."""
    survey = [f for f in versapay.fields if f.key.startswith("surveysResponses")]
    assert len(survey) == 6, [f.label for f in survey]
    for f in survey:
        assert f.kind == "demographic", (f.label, f.kind)
        assert f.kind in FLAGGED_KINDS


def test_a_custom_card_keeps_the_employers_own_wording_and_options(versapay):
    card = [f for f in versapay.fields if f.key.startswith("cards[")]
    assert len(card) == 1
    f = card[0]
    assert f.label == ("Will you now or in the future require any work "
                       "authorization or sponsorship?")
    assert f.kind == "single_select"
    assert {o.label for o in f.options} == {"Yes", "No"}
    # The option VALUE is Lever's own id, which is what a submission posts.
    assert all(o.value for o in f.options)


def test_the_card_key_addresses_the_real_control(versapay):
    """cards[<uuid>][field<N>], which is what the page names the radio group."""
    f = next(x for x in versapay.fields if x.key.startswith("cards["))
    assert f.key.endswith("][field0]")
    raw = (FIX / "lever_versapay_apply.html").read_text()
    assert f'name="{f.key}"' in raw, "the key does not exist on the page"


def test_the_survey_key_addresses_the_real_control(versapay):
    raw = (FIX / "lever_versapay_apply.html").read_text()
    for f in versapay.fields:
        if f.key.startswith("surveysResponses"):
            assert f'name="{f.key}"' in raw, f.key


def test_link_boxes_are_urls_with_the_employers_label(rembrand):
    urls = [f for f in rembrand.fields if f.kind == "url"]
    assert {f.key for f in urls} >= {"urls[LinkedIn]", "urls[GitHub]"}
    # Rembrand renamed the Other box. Verbatim, not normalised away.
    other = next(f for f in urls if f.key == "urls[Other]")
    assert other.label == "Spaceback"


def test_the_required_glyph_is_not_part_of_the_question(versapay):
    """Lever renders its required marker inside the label. Leaving it in shows
    him "Full name /" in the approval email instead of the question asked."""
    for f in versapay.fields:
        assert not any(g in f.label for g in L.REQUIRED_GLYPHS), f.label
    assert next(f for f in versapay.fields if f.key == "name").label == "Full name"


def test_pronouns_is_treated_as_his_to_give(versapay):
    """It appeared on 3 of the 19 postings sampled. Self-identification, so it
    is flagged like any other, never filled from a default."""
    assert L.CORE_KINDS["pronouns"] == "demographic"
    assert "demographic" in FLAGGED_KINDS


def test_an_unmapped_card_type_raises_rather_than_guessing(versapay):
    """A question hunter cannot type must not be claimed as answered. Five card
    types were seen across 19 postings; a sixth is a deliberate change here."""
    with pytest.raises(ValueError, match="unmapped Lever card field type"):
        L._kind_for_card("signature-pad", "Sign here", survey=False)
    assert set(L.CARD_KINDS) == {"multiple-choice", "multiple-select",
                                 "dropdown", "text", "textarea"}


def test_a_page_with_no_form_is_unreadable_not_an_empty_form():
    """Zero fields read as a valid empty form is how an approval went out for a
    posting nobody could apply to."""
    s = L.parse("<html><body>nothing here</body></html>", slug="x",
                posting_id="y")
    assert s.readable is False
    assert s.fields == ()


def test_a_404_is_a_dead_posting_not_an_unreadable_one(monkeypatch):
    class R:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("should not be reached")

    monkeypatch.setattr(L.requests, "get", lambda *a, **k: R())
    s = L.fetch_form("slug", "pid")
    assert s.readable is False
    assert "dead" in s.note


def test_consent_is_told_apart_from_an_ordinary_choice():
    assert L._kind_for_card("multiple-choice", "I accept the privacy policy",
                            survey=False) == "consent"
    assert L._kind_for_card("multiple-choice", "Which region?",
                            survey=False) == "single_select"


def test_a_demographic_question_outside_a_survey_is_still_demographic():
    """Some employers put the EEO questions in an ordinary card."""
    assert L._kind_for_card("multiple-choice", "What gender do you identify as?",
                            survey=False) == "demographic"
