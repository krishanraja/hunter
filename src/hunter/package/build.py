"""Package builder: copy, replace, clear, delete, verify, export.

Order of operations matters. Deletions run last because every edit shifts
indices and delete_paragraph re-reads the document to resolve its anchor.
Master facts are read fresh every run; Krish edits the masters in place and
the live document always wins over any cached description of it.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

from .. import config
from ..docbuild import DocBuild
from .assertions import VerifyReport, verify
from .tailor import TailorResult, assemble_hook

COMPETENCY_DOT = "·"  # the middle dot; surrounding spacing is measured
DELETE_BLOCK_ANCHOR = "DELETE THIS BLOCK"


class BuildError(RuntimeError):
    pass


@dataclass
class MasterFacts:
    cv_bold: list[str]
    cv_bullet_count: int
    cv_competencies: list[str]
    competency_sep: str        # the master's exact separator, spacing included
    cv_summary_p1: str         # first summary paragraph, verified unstyled
    cv_headings_present: bool
    letter_bold: list[str]
    letter_bullets: list[str]  # full text of the 4 proof bullets, in order
    placeholder_counts: dict[str, int]


@dataclass
class PackageResult:
    cv_doc_id: str = ""
    cv_pdf_id: str = ""
    letter_doc_id: str = ""
    letter_pdf_id: str = ""
    cv_url: str = ""
    cv_pdf_url: str = ""
    letter_url: str = ""
    letter_pdf_url: str = ""
    cv_report: VerifyReport | None = None
    letter_report: VerifyReport | None = None
    notes: list[str] = field(default_factory=list)


EXPECTED_PLACEHOLDERS = {
    "{{DATE}}": 1,
    "{{HIRING_LEAD}}": 2,
    "{{COMPANY}}": 3,
    "{{ROLE}}": 2,
    "{{COMPANY_SPECIFIC_HOOK}}": 1,
}


def doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def pdf_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def read_master_facts(db: DocBuild) -> MasterFacts:
    cv = db.get(config.CV_MASTER_ID)
    letter = db.get(config.LETTER_MASTER_ID)
    cv_paras = db.paragraphs(cv)
    letter_paras = db.paragraphs(letter)
    cv_text = "".join(p["text"] for p in cv_paras)
    letter_text = "".join(p["text"] for p in letter_paras)

    comp_para = next((p for p in cv_paras
                      if p["text"].count(COMPETENCY_DOT) >= 8), None)
    if comp_para is None:
        raise BuildError("CV master: CORE COMPETENCIES paragraph not found")
    sep_match = re.search("[ \u00a0]*\u00b7[ \u00a0]*", comp_para["text"])
    sep = sep_match.group(0) if sep_match else f" {COMPETENCY_DOT} "
    competencies = [c.strip(" \u00a0\n") for c in comp_para["text"].split(COMPETENCY_DOT)]
    competencies = [c for c in competencies if c]
    if len(competencies) != 11:
        raise BuildError(
            f"CV master competencies count is {len(competencies)}, expected 11; "
            f"the master changed, refusing to build")
    styled = [r for r in comp_para["runs"] if r["bold"] and r["text"].strip()]
    if styled:
        raise BuildError(
            "CV master competencies paragraph gained bold runs; rewriting it would "
            "lose them, refusing to build")

    from .assertions import CV_HEADINGS
    headings_ok = all(h in cv_text for h in CV_HEADINGS)
    if not headings_ok:
        raise BuildError("CV master is missing an expected section heading, refusing to build")

    # First paragraph of PROFESSIONAL SUMMARY: the block layer replaces it per
    # role family. It must carry no bold runs, or the swap would lose them and
    # the build refuses (Krish may bold something there later; refusing beats
    # losing his formatting).
    summary_idx = next((i for i, p in enumerate(cv_paras)
                        if p["text"].strip() == "PROFESSIONAL SUMMARY"), None)
    if summary_idx is None:
        raise BuildError("CV master: PROFESSIONAL SUMMARY heading paragraph not found")
    p1 = next((p for p in cv_paras[summary_idx + 1:] if p["text"].strip()), None)
    if p1 is None:
        raise BuildError("CV master: summary paragraph 1 not found")
    p1_styled = [r for r in p1["runs"] if r["bold"] and r["text"].strip()]
    if p1_styled:
        raise BuildError(
            "CV master summary paragraph 1 carries bold runs; the block swap would "
            "lose them, refusing to build")

    letter_bullets = [p["text"].strip() for p in letter_paras if p["bullet"]]
    if len(letter_bullets) != 4:
        raise BuildError(
            f"letter master carries {len(letter_bullets)} bullet paragraphs, expected 4")

    counts = {ph: letter_text.count(ph) for ph in EXPECTED_PLACEHOLDERS}
    if counts != EXPECTED_PLACEHOLDERS:
        raise BuildError(f"letter master placeholder census changed: {counts}; refusing to build")

    return MasterFacts(
        cv_bold=db.bold_runs(cv),
        cv_bullet_count=sum(1 for p in cv_paras if p["bullet"]),
        cv_competencies=competencies,
        competency_sep=sep,
        cv_summary_p1=p1["text"].strip(),
        cv_headings_present=headings_ok,
        letter_bold=db.bold_runs(letter),
        letter_bullets=letter_bullets,
        placeholder_counts=counts,
    )


def _unique_title(db: DocBuild, base: str, parent_id: str, role_slug: str) -> str:
    if not db.find_by_name(base, parent_id):
        return base
    with_slug = f"{base}_{role_slug}"
    if not db.find_by_name(with_slug, parent_id):
        return with_slug
    raise BuildError(f"title collision even with role slug: {with_slug!r}; needs human review")


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def build_letter(db: DocBuild, facts: MasterFacts, tr: TailorResult, *,
                 company: str, title: str, letter_blocks: dict,
                 today: datetime.date | None = None) -> tuple[str, VerifyReport]:
    today = today or datetime.date.today()
    date_text = today.strftime("%B %d, %Y").replace(" 0", " ")
    hook = assemble_hook(letter_blocks, tr.block_key, company, tr.jd_mirror)
    doc_title = _unique_title(db, f"KrishRaja_CoverLetter_{company}",
                              config.LETTER_FOLDER_ID, slugify(title))
    doc_id = db.copy_master(config.LETTER_MASTER_ID, doc_title, config.LETTER_FOLDER_ID)
    db.replace_placeholders(doc_id, {
        "{{DATE}}": date_text,
        "{{HIRING_LEAD}}": tr.hiring_lead,
        "{{COMPANY}}": company,
        "{{ROLE}}": title,
        "{{COMPANY_SPECIFIC_HOOK}}": hook,
    })
    db.clear_highlighting(doc_id)
    removed = db.delete_block(doc_id, DELETE_BLOCK_ANCHOR)
    cut_text = facts.letter_bullets[tr.letter_bullet_to_cut - 1]
    removed += db.delete_paragraph(doc_id, cut_text[:40])
    report = verify(db, doc_id, master_bold=facts.letter_bold, kind="letter",
                    cut_bullet_text=cut_text, expect_bullets=3,
                    expect_present=[hook])
    return doc_id, report


def build_cv(db: DocBuild, facts: MasterFacts, tr: TailorResult, *,
             company: str, title: str, cv_blocks: dict) -> tuple[str, VerifyReport]:
    summary_text = cv_blocks[tr.block_key]["text"]
    doc_title = _unique_title(db, f"KrishRaja_CV_{company}",
                              config.CV_FOLDER_ID, slugify(title))
    doc_id = db.copy_master(config.CV_MASTER_ID, doc_title, config.CV_FOLDER_ID)
    db.set_unstyled_paragraph(doc_id, facts.cv_summary_p1[:60], summary_text)
    anchor = facts.competency_sep.join(facts.cv_competencies[:2])
    db.set_unstyled_paragraph(doc_id, anchor,
                              facts.competency_sep.join(tr.competency_order))
    report = verify(db, doc_id, master_bold=facts.cv_bold, kind="cv",
                    expect_bullets=facts.cv_bullet_count,
                    expect_present=[summary_text])
    return doc_id, report


def build_package(db: DocBuild, tr: TailorResult, *, company: str, title: str,
                  letter_blocks: dict, cv_blocks: dict,
                  facts: MasterFacts | None = None,
                  export_pdfs: bool = True) -> PackageResult:
    facts = facts or read_master_facts(db)
    result = PackageResult()
    result.notes.extend(tr.flags)

    letter_id, letter_report = build_letter(db, facts, tr, company=company,
                                            title=title, letter_blocks=letter_blocks)
    result.letter_doc_id, result.letter_report = letter_id, letter_report
    result.letter_url = doc_url(letter_id)

    cv_id, cv_report = build_cv(db, facts, tr, company=company, title=title,
                                cv_blocks=cv_blocks)
    result.cv_doc_id, result.cv_report = cv_id, cv_report
    result.cv_url = doc_url(cv_id)

    if not (letter_report.ok and cv_report.ok):
        # Leave the docs in place for inspection; the caller records blocked.
        result.notes.append("verification failed; documents left in place for review")
        return result

    if export_pdfs:
        cv_name = f"KrishRaja_CV_{company}.pdf"
        letter_name = f"KrishRaja_CoverLetter_{company}.pdf"
        result.cv_pdf_id = db.upload_pdf(cv_name, config.CV_FOLDER_ID, db.export_pdf(cv_id))
        result.letter_pdf_id = db.upload_pdf(letter_name, config.LETTER_FOLDER_ID,
                                             db.export_pdf(letter_id))
        result.cv_pdf_url = pdf_url(result.cv_pdf_id)
        result.letter_pdf_url = pdf_url(result.letter_pdf_id)
    return result
