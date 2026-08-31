"""Failure matrix for verify(): each induced defect trips exactly the intended
assertion. Uses the captured live letter master via the emulator."""
from hunter import config
from hunter.package.assertions import verify
from hunter.package.build import build_letter, read_master_facts
from hunter.package.tailor import TailorResult

from conftest import make_synthetic_cv
from fake_docs import FakeDocBuild


def build_ok_letter(letter_fixture):
    db = FakeDocBuild({
        config.CV_MASTER_ID: make_synthetic_cv(),
        config.LETTER_MASTER_ID: letter_fixture,
    })
    facts = read_master_facts(db)
    tr = TailorResult(
        competency_order=list(facts.cv_competencies),
        letter_bullet_to_cut=2,
        hook="Sierra is scaling agents into the enterprise. I took Captify APAC from $0 to $12M ARR.",
        hiring_lead="Hiring Team",
    )
    doc_id, report = build_letter(db, facts, tr, company="Sierra", title="Head of GTM")
    assert report.ok, report.failures
    cut = facts.letter_bullets[tr.letter_bullet_to_cut - 1]
    return db, facts, doc_id, cut


def run_verify(db, facts, doc_id, cut):
    return verify(db, doc_id, master_bold=facts.letter_bold, kind="letter",
                  cut_bullet_text=cut, expect_bullets=3)


def find_paragraph(db, doc_id, needle):
    doc = db.get(doc_id)
    return next(p for p in db.paragraphs(doc) if needle in p["text"])


def test_a1_placeholder_left(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    p = find_paragraph(db, doc_id, "Dear ")
    db.batch(doc_id, [{"insertText": {"location": {"index": p["start"]},
                                      "text": "{{HIRING_LEAD}} "}}])
    report = run_verify(db, facts, doc_id, cut)
    assert any(f.startswith("A1") for f in report.failures), report.failures


def test_a2_delete_block_left(letter_fixture):
    db = FakeDocBuild({
        config.CV_MASTER_ID: make_synthetic_cv(),
        config.LETTER_MASTER_ID: letter_fixture,
    })
    facts = read_master_facts(db)
    copy_id = db.copy_master(config.LETTER_MASTER_ID, "a2")
    db.replace_placeholders(copy_id, {
        "{{DATE}}": "x", "{{HIRING_LEAD}}": "x", "{{COMPANY}}": "x",
        "{{ROLE}}": "x", "{{COMPANY_SPECIFIC_HOOK}}": "x"})
    db.clear_highlighting(copy_id)
    report = verify(db, copy_id, master_bold=facts.letter_bold, kind="letter")
    assert any(f.startswith("A2") for f in report.failures), report.failures


def test_a3_highlight_left(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    p = find_paragraph(db, doc_id, "Dear ")
    db.batch(doc_id, [{"updateTextStyle": {
        "range": {"startIndex": p["start"], "endIndex": p["start"] + 4},
        "textStyle": {"backgroundColor": {"color": {"rgbColor": {"red": 1, "green": 1}}}},
        "fields": "backgroundColor"}}])
    report = run_verify(db, facts, doc_id, cut)
    assert any(f.startswith("A3") for f in report.failures), report.failures


def test_a4_em_dash(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    p = find_paragraph(db, doc_id, "Dear ")
    db.batch(doc_id, [{"insertText": {"location": {"index": p["start"]},
                                      "text": "\u2014"}}])
    report = run_verify(db, facts, doc_id, cut)
    assert any(f.startswith("A4") for f in report.failures), report.failures


def test_a5_cut_bullet_is_excused_but_other_loss_is_not(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    ok = run_verify(db, facts, doc_id, cut)
    assert ok.ok, ok.failures
    # Without the cut-bullet filter the same doc must fail: proves the filter
    # is doing real work rather than masking everything.
    strict = verify(db, doc_id, master_bold=facts.letter_bold, kind="letter",
                    cut_bullet_text=None, expect_bullets=3)
    assert any(f.startswith("A5") for f in strict.failures), strict.failures


def test_a6_bullet_count(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    remaining = [b for b in facts.letter_bullets if b != cut]
    db.delete_paragraph(doc_id, remaining[0][:40])
    report = run_verify(db, facts, doc_id, cut)
    assert any(f.startswith("A6") for f in report.failures), report.failures


def test_a7_locked_line(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    db.delete_paragraph(doc_id, "nine figures rarely ship AI into production")
    report = run_verify(db, facts, doc_id, cut)
    assert any(f.startswith("A7") for f in report.failures), report.failures


def test_word_count_reported_not_asserted(letter_fixture):
    db, facts, doc_id, cut = build_ok_letter(letter_fixture)
    report = run_verify(db, facts, doc_id, cut)
    assert report.ok
    assert report.body_word_count > 250, (
        "the letter body legitimately runs past 250 words; if this fails the "
        "master changed materially and the run summary should say so")
