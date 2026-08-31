"""Assertions over a finished document, never over the plan. A package that
fails a hard check is not recorded as built.

Merged from the two verify() versions in the brief:
  - the uploaded docbuild variant contributed the placeholder-run filter on the
    lost-bold check (replaced placeholder runs legitimately change text);
  - the brief section 3 variant contributed removed-bold accounting, bullet
    counts, heading checks, and the locked-line checks.

The 250-word rule is deliberately NOT an assertion. The body word count is
reported for Krish to judge; his master runs longer than 250 by his own choice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CV_HEADINGS = [
    "PROFESSIONAL SUMMARY",
    "CAREER HIGHLIGHTS",
    "CORE COMPETENCIES",
    "PROFESSIONAL EXPERIENCE",
    "EDUCATION AND RECOGNITION",
]
CV_HEADLINE = "AI-Native Commercial Strategy Leader"
LETTER_LOCKED_LINE = "nine figures rarely ship AI into production"


@dataclass
class VerifyReport:
    ok: bool
    failures: list[str] = field(default_factory=list)
    body_word_count: int = 0


def verify(db, doc_id, *, master_bold: list[str], kind: str,
           cut_bullet_text: str | None = None,
           expect_bullets: int | None = None) -> VerifyReport:
    """Return a VerifyReport. db is a DocBuild; doc_id the finished copy."""
    doc = db.get(doc_id)
    paras = db.paragraphs(doc)
    full = "".join(p["text"] for p in paras)
    fails: list[str] = []

    # Contiguous bold segments, in document order. Docs (and the emulator) may
    # merge or split adjacent runs of equal style, so the loss check matches
    # each expected bold string against contiguous bold text, never against
    # exact run boundaries.
    bold_segments: list[str] = []
    for p in paras:
        current = ""
        last_end = None
        for r in p["runs"]:
            if r["bold"]:
                if last_end is not None and r["start"] != last_end and current:
                    bold_segments.append(current)
                    current = ""
                current += r["text"]
                last_end = r["end"]
            else:
                if current:
                    bold_segments.append(current)
                    current = ""
                last_end = None
        if current:
            bold_segments.append(current)

    if "{{" in full:
        leftovers = re.findall(r"\{\{[^}]*\}\}", full)[:3]
        fails.append(f"A1 placeholder left: {leftovers}")
    if "DELETE THIS BLOCK" in full:
        fails.append("A2 instruction block still present")
    n_bg = sum(1 for p in paras for r in p["runs"] if r["bg"])
    if n_bg:
        fails.append(f"A3 highlighting not cleared on {n_bg} run(s)")
    if "\u2014" in full:
        fails.append("A4 em dash present")

    # The one that actually matters: no styling was lost.
    expected = [b for b in master_bold if "{{" not in b]
    if cut_bullet_text:
        expected = [b for b in expected if b not in cut_bullet_text]
    lost = [b for b in expected
            if not any(b in seg for seg in bold_segments)]
    if lost:
        fails.append(f"A5 LOST {len(lost)} bold run(s): {lost[:4]}")

    if expect_bullets is not None:
        n_b = sum(1 for p in paras if p["bullet"])
        if n_b != expect_bullets:
            fails.append(f"A6 expected {expect_bullets} bullets, found {n_b}")

    if kind == "letter":
        if LETTER_LOCKED_LINE not in full:
            fails.append("A7 locked nine-figures line missing")
    elif kind == "cv":
        if CV_HEADLINE not in full:
            fails.append("A7 headline missing or altered")
        for h in CV_HEADINGS:
            if h not in full:
                fails.append(f"A7 heading missing: {h}")
    else:
        fails.append(f"A0 unknown kind {kind!r}")

    return VerifyReport(ok=not fails, failures=fails,
                        body_word_count=len(full.split()))
