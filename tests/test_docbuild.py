"""Bold-run retention: the tests that actually matter. A tailoring pass over
the captured live letter master must lose zero bold runs, and the assertion
layer must catch a deliberately induced loss (proving A5 has teeth)."""
import pytest

from hunter import config
from hunter.package.build import build_package, read_master_facts
from hunter.package.tailor import TailorResult
from hunter.package.assertions import verify

from conftest import COMPETENCIES_11, make_synthetic_cv
from fake_docs import FakeDocBuild


def make_db(letter_fixture):
    return FakeDocBuild({
        config.CV_MASTER_ID: make_synthetic_cv(),
        config.LETTER_MASTER_ID: letter_fixture,
    })


def sample_tailor(facts):
    order = list(facts.cv_competencies)
    order = order[3:] + order[:3]
    return TailorResult(
        competency_order=order,
        letter_bullet_to_cut=3,
        hook=(
            "Acme AI is turning enterprise search into a commercial product and "
            "needs someone who has done the zero-to-one twice. I built Captify "
            "APAC from $0 to $12M ARR and ship AI into production weekly."
        ),
        hiring_lead="Hiring Team",
    )


def test_full_package_zero_bold_loss(letter_fixture):
    db = make_db(letter_fixture)
    facts = read_master_facts(db)
    tr = sample_tailor(facts)
    result = build_package(db, tr, company="Acme AI", title="VP Strategy")
    assert result.letter_report is not None and result.letter_report.ok, \
        result.letter_report.failures
    assert result.cv_report is not None and result.cv_report.ok, \
        result.cv_report.failures
    assert result.letter_report.body_word_count > 0
    assert len(db.uploaded_pdfs) == 2


def test_every_letter_bullet_choice_survives(letter_fixture):
    for cut in (1, 2, 3, 4):
        db = make_db(letter_fixture)
        facts = read_master_facts(db)
        tr = sample_tailor(facts)
        tr.letter_bullet_to_cut = cut
        result = build_package(db, tr, company=f"Cut{cut} Co", title="Head of Strategy",
                               export_pdfs=False)
        assert result.letter_report.ok, (cut, result.letter_report.failures)


def test_verify_catches_induced_bold_loss(letter_fixture):
    """Adversarial: rewrite a bolded region through raw emulator ops and
    prove A5 flags the loss. Without this the happy path proves nothing."""
    db = make_db(letter_fixture)
    facts = read_master_facts(db)
    tr = sample_tailor(facts)
    result = build_package(db, tr, company="LossCo", title="VP Strategy",
                           export_pdfs=False)
    assert result.letter_report.ok
    doc = db.get(result.letter_doc_id)
    target = next(r for p in db.paragraphs(doc) for r in p["runs"]
                  if r["bold"] and "nine figures" not in r["text"] and len(r["text"].strip()) > 5)
    db.batch(result.letter_doc_id, [
        {"deleteContentRange": {"range": {"startIndex": target["start"],
                                          "endIndex": target["end"]}}},
        {"insertText": {"location": {"index": target["start"]},
                        "text": "plain replacement text"}},
    ])
    report = verify(db, result.letter_doc_id, master_bold=facts.letter_bold,
                    kind="letter",
                    cut_bullet_text=facts.letter_bullets[tr.letter_bullet_to_cut - 1],
                    expect_bullets=3)
    assert not report.ok
    assert any(f.startswith("A5 LOST") for f in report.failures), report.failures


def test_set_unstyled_paragraph_refuses_bold(letter_fixture):
    db = make_db(letter_fixture)
    copy_id = db.copy_master(config.CV_MASTER_ID, "refusal-test")
    with pytest.raises(RuntimeError, match="refusing"):
        db.set_unstyled_paragraph(copy_id, "$0 to $12M ARR", "flattened")


def test_replace_preserves_bold_and_highlight_until_cleared(letter_fixture):
    db = make_db(letter_fixture)
    copy_id = db.copy_master(config.LETTER_MASTER_ID, "style-check")
    db.replace_placeholders(copy_id, {"{{COMPANY}}": "StyleCo"})
    doc = db.get(copy_id)
    styled = [r for p in db.paragraphs(doc) for r in p["runs"]
              if "StyleCo" in r["text"]]
    assert styled, "replacement text not found"
    assert all(r["bold"] for r in styled), "replaceAllText dropped bold"
    assert all(r["bg"] for r in styled), "replaceAllText dropped highlight"
    n = db.clear_highlighting(copy_id)
    assert n > 0
    doc = db.get(copy_id)
    assert not any(r["bg"] for p in db.paragraphs(doc) for r in p["runs"])


def test_master_facts_shape(letter_fixture):
    db = make_db(letter_fixture)
    facts = read_master_facts(db)
    assert len(facts.cv_competencies) == 11
    assert facts.cv_competencies == COMPETENCIES_11
    assert len(facts.letter_bullets) == 4
    assert facts.cv_bullet_count == 9  # 7 highlights + 1 experience + 1 education
    assert facts.placeholder_counts == {
        "{{DATE}}": 1, "{{HIRING_LEAD}}": 2, "{{COMPANY}}": 3,
        "{{ROLE}}": 2, "{{COMPANY_SPECIFIC_HOOK}}": 1,
    }
