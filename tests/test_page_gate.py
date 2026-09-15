"""Canon 9.12 says the cover letter is one page. Until 2026-09-15 nothing
measured it and the first live package went out at two, with page two carrying
only the contact footer. These tests hold the measurement and the shrink ladder
that acts on it.
"""
import pytest

from hunter import config
from hunter.apply.merge import MergeError, page_count
from hunter.package.build import build_letter, hook_ladder, read_master_facts, trim_hook
from hunter.package.tailor import TailorResult

from conftest import TEST_LETTER_BLOCKS, make_synthetic_cv
from fake_docs import FakeDocBuild

# 84 words at 477 characters: the hook the first live Harvey build generated
# under the old 650-character budget, which is what spilled the page.
LONG_HOOK = (
    "Harvey is scaling from a legal research tool into the system of record for "
    "how firms actually work, and that transition breaks the go-to-market model "
    "that got it here. The pricing was set for seats and the value is now landing "
    "on matters. I have run that exact repricing once before, at Captify APAC, "
    "where the commercial model went from $0 to $12M ARR on a pricing and "
    "forecasting architecture I designed from nothing."
)


def _db(letter_fixture):
    return FakeDocBuild({
        config.CV_MASTER_ID: make_synthetic_cv(),
        config.LETTER_MASTER_ID: letter_fixture,
    })


def _tr(hook=""):
    return TailorResult(
        competency_order=[], letter_bullet_to_cut=2,
        block_key="commercial_strategy", jd_mirror="", hiring_lead="Hiring Team",
        hook=hook)


def _build(db, facts, tr, notes):
    return build_letter(db, facts, tr, company="Harvey",
                        title="Head of GTM Strategy and Operations",
                        letter_blocks=TEST_LETTER_BLOCKS, notes=notes)


# ---------- the measurement ----------

def test_page_count_reads_a_real_pdf():
    from pypdf import PdfWriter
    import io
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    w.write(out)
    assert page_count(out.getvalue()) == 2


def test_page_count_refuses_a_blob_that_is_not_a_pdf():
    with pytest.raises(MergeError):
        page_count(b"not a pdf at all")


def test_page_count_refuses_nothing():
    with pytest.raises(MergeError):
        page_count(b"")


# ---------- the ladder ----------

def test_trim_hook_keeps_whole_sentences():
    trimmed = trim_hook(LONG_HOOK, 300)
    assert len(trimmed) <= 300
    assert trimmed.endswith(".")
    assert LONG_HOOK.startswith(trimmed)


def test_trim_hook_returns_empty_when_one_sentence_does_not_fit():
    assert trim_hook("A single sentence far too long to fit in the budget.", 10) == ""


def test_hook_ladder_steps_one_sentence_at_a_time_then_the_block():
    rungs = hook_ladder(_tr(LONG_HOOK), TEST_LETTER_BLOCKS, "Harvey")
    assert rungs[0] == (LONG_HOOK, "generated hook")
    # One rung per whole-sentence prefix, strictly shorter each time, block last.
    lengths = [len(t) for t, _ in rungs[:-1]]
    assert lengths == sorted(lengths, reverse=True)
    assert len(set(lengths)) == len(lengths)
    assert "approved block" in rungs[-1][1]
    assert "Harvey is" in rungs[-1][0]
    assert len(rungs) <= 6


def test_the_ladder_has_a_rung_between_a_long_hook_and_one_sentence():
    """The flaw in the first version: rungs at 380 then 300 then a block hook of
    303, so nothing sat between 300 characters and a single sentence, and a real
    build failed A9 on all three rungs."""
    rungs = hook_ladder(_tr(LONG_HOOK), TEST_LETTER_BLOCKS, "Harvey")
    lengths = [len(t) for t, _ in rungs[:-1]]
    assert len(lengths) >= 3, lengths


def test_hook_ladder_is_just_the_block_when_nothing_was_generated():
    rungs = hook_ladder(_tr(""), TEST_LETTER_BLOCKS, "Harvey")
    assert len(rungs) == 1


# ---------- the two together ----------

def test_a_one_page_letter_keeps_the_generated_hook(letter_fixture):
    db = _db(letter_fixture)
    facts = read_master_facts(db)
    notes = []
    tr = _tr(LONG_HOOK)
    doc_id, report, pdf = _build(db, facts, tr, notes)
    assert report.ok, report.failures
    assert report.page_count == 1
    assert notes == []
    assert len(db.exports) == 1
    assert LONG_HOOK in "".join(p["text"] for p in db.paragraphs(db.get(doc_id)))
    assert pdf is not None


def test_a_two_page_letter_steps_down_a_rung_and_says_so(letter_fixture):
    db = _db(letter_fixture)
    db.export_pages = [2, 1]  # spills on the generated hook, fits once trimmed
    facts = read_master_facts(db)
    notes = []
    doc_id, report, _pdf = _build(db, facts, _tr(LONG_HOOK), notes)
    assert report.ok, report.failures
    assert report.page_count == 1
    assert len(notes) == 1 and "cut to" in notes[0]
    full = "".join(p["text"] for p in db.paragraphs(db.get(doc_id)))
    assert LONG_HOOK not in full
    # The next rung down is the whole hook minus its last sentence.
    from hunter.package.build import sentences
    parts = sentences(LONG_HOOK)
    assert " ".join(parts[:-1]) in full


def test_a_letter_that_never_fits_fails_a9_and_ships_nothing(letter_fixture):
    db = _db(letter_fixture)
    db.export_pages = 2  # every render spills, including the approved block
    facts = read_master_facts(db)
    notes = []
    _doc_id, report, _pdf = _build(db, facts, _tr(LONG_HOOK), notes)
    assert not report.ok
    assert any(f.startswith("A9") for f in report.failures), report.failures
    assert report.page_count == 2
    # It walked the whole ladder before giving up, rather than failing on rung one.
    assert len(db.exports) == len(hook_ladder(_tr(LONG_HOOK), TEST_LETTER_BLOCKS,
                                              "Harvey"))


def test_measure_pages_off_reports_zero_and_never_renders(letter_fixture):
    db = _db(letter_fixture)
    db.export_pages = 2
    facts = read_master_facts(db)
    _doc_id, report, pdf = build_letter(
        db, facts, _tr(LONG_HOOK), company="Harvey", title="Head of GTM",
        letter_blocks=TEST_LETTER_BLOCKS, measure_pages=False)
    assert pdf is None
    assert report.page_count == 0
    assert db.exports == []
    # A page count of zero must never read as "fits on one page".
    assert report.ok, report.failures
