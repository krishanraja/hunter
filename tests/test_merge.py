"""One upload slot means one attachment carrying both documents.

Letter first, CV second: a reader who opens one file should meet the argument
before the evidence.
"""
from __future__ import annotations

import io

import pytest

from hunter.apply.merge import MergeError, merge_pdfs, merged_name, page_count


def make_pdf(pages: int, marker: str) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    w.add_metadata({"/Title": marker})
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_two_single_page_pdfs_make_one_two_page_pdf():
    merged = merge_pdfs(make_pdf(1, "letter"), make_pdf(1, "cv"))
    assert page_count(merged) == 2


def test_a_two_page_cv_is_preserved_in_full():
    merged = merge_pdfs(make_pdf(1, "letter"), make_pdf(2, "cv"))
    assert page_count(merged) == 3


def test_the_output_is_a_readable_pdf_not_a_concatenated_blob():
    """Naive byte concatenation produces something that opens in nothing."""
    merged = merge_pdfs(make_pdf(1, "letter"), make_pdf(1, "cv"))
    assert merged.startswith(b"%PDF")
    from pypdf import PdfReader
    PdfReader(io.BytesIO(merged))  # raises if malformed


def test_a_missing_document_is_refused():
    with pytest.raises(MergeError, match="both the letter and the CV"):
        merge_pdfs(b"", make_pdf(1, "cv"))
    with pytest.raises(MergeError, match="both the letter and the CV"):
        merge_pdfs(make_pdf(1, "letter"), b"")


def test_a_corrupt_document_names_which_one_failed():
    with pytest.raises(MergeError, match="the cv PDF could not be read"):
        merge_pdfs(make_pdf(1, "letter"), b"not a pdf at all")
    with pytest.raises(MergeError, match="the letter PDF could not be read"):
        merge_pdfs(b"also not a pdf", make_pdf(1, "cv"))


def test_the_filename_follows_the_canon_naming_rule():
    """Canon 9.12: per-role artifacts never carry a version number."""
    assert merged_name("Harvey") == "KrishRaja_Application_Harvey.pdf"
    assert merged_name("Aptos Labs") == "KrishRaja_Application_AptosLabs.pdf"
    assert merged_name("Setpoint.io") == "KrishRaja_Application_Setpointio.pdf"
    assert "v1" not in merged_name("Harvey")


def test_an_empty_company_name_is_refused_rather_than_producing_a_bare_file():
    with pytest.raises(MergeError, match="empty filename"):
        merged_name("   ")
    with pytest.raises(MergeError, match="empty filename"):
        merged_name("...")
