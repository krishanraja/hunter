"""Merge the letter and CV PDFs into one file.

Krish's brief 2026-09-14: two links normally, but where the form has a single
upload slot, one attachment carrying both documents. Which one a posting gets is
already known from the form contract: audit.Audit.attachment_style is CV+CL when
the form declares both file fields and CV only when it declares one.

Concatenating finished PDFs is deliberate. Building a combined Google Doc would
put the formatting back at risk, and formatting fidelity is the part of the brief
with the least tolerance. Once exported, the layout is frozen; appending pages
cannot disturb it.

Letter first, CV second. A reader who opens one file should meet the argument
before the evidence.
"""
from __future__ import annotations

import io


class MergeError(RuntimeError):
    pass


def merged_name(company: str) -> str:
    """Per canon 9.12 naming: no version number on a per-role artifact."""
    safe = "".join(c for c in (company or "") if c.isalnum() or c in " -_").strip()
    safe = safe.replace(" ", "")
    if not safe:
        raise MergeError("company name produced an empty filename")
    return f"KrishRaja_Application_{safe}.pdf"


def merge_pdfs(letter_pdf: bytes, cv_pdf: bytes) -> bytes:
    """One PDF, letter pages then CV pages. Raises rather than returning a
    partial file, so a caller can never attach half an application."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise MergeError(
            "pypdf is not installed; it is declared in pyproject dependencies"
        ) from e

    if not letter_pdf or not cv_pdf:
        raise MergeError("both the letter and the CV PDF are required")

    writer = PdfWriter()
    pages = 0
    for label, blob in (("letter", letter_pdf), ("cv", cv_pdf)):
        try:
            reader = PdfReader(io.BytesIO(blob))
        except Exception as e:
            raise MergeError(f"the {label} PDF could not be read: "
                             f"{e.__class__.__name__}") from e
        if not reader.pages:
            raise MergeError(f"the {label} PDF has no pages")
        for page in reader.pages:
            writer.add_page(page)
            pages += 1

    out = io.BytesIO()
    writer.write(out)
    blob = out.getvalue()

    # Prove the output before handing it to an employer's form.
    check = PdfReader(io.BytesIO(blob))
    if len(check.pages) != pages:
        raise MergeError(f"merged PDF has {len(check.pages)} pages, expected "
                         f"{pages}")
    return blob


def page_count(pdf: bytes) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)
