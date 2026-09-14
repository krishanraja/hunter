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
    # Added 2026-09-14 for generated summaries and highlight reordering. Defaulted
    # so every existing constructor call keeps working.
    cv_summary_count: int = 1  # how many paragraphs PROFESSIONAL SUMMARY spans
    cv_highlights: list[str] = field(default_factory=list)
    cv_master_text: str = ""   # the haystack voicegate traces generated prose against
    # Bold phrases from the SUMMARY SECTION ONLY. cv_bold is the whole document, and
    # carrying that over bolded "Captify" and "18" inside the generated summary
    # because those phrases are bold elsewhere in the CV. Replacing the summary only
    # licenses preserving the summary's own convention.
    cv_summary_bold: list[str] = field(default_factory=list)


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

    # How far the summary actually runs: every non-empty paragraph between the
    # heading and the next ALL CAPS heading. Krish's master carries three. The
    # generated summary replaces the whole span, which is what stops the CV
    # shipping in two registers; replacing only p1 is what caused that.
    next_heading = next(
        (i for i, pp in enumerate(cv_paras[summary_idx + 1:], summary_idx + 1)
         if pp["text"].strip() and pp["text"].strip().isupper()), None)
    summary_span = [pp for pp in cv_paras[summary_idx + 1:next_heading]
                    if pp["text"].strip()]
    summary_count = len(summary_span) or 1
    summary_bold = [r["text"].strip() for pp in summary_span for r in pp["runs"]
                    if r["bold"] and r["text"].strip()]

    # CAREER HIGHLIGHTS, in the master's order. Canon 9.12 says reorder them per
    # role and never add one, so the text is captured and only the order moves.
    hl_idx = next((i for i, pp in enumerate(cv_paras)
                   if pp["text"].strip() == "CAREER HIGHLIGHTS"), None)
    highlights: list[str] = []
    if hl_idx is not None:
        hl_next = next(
            (i for i, pp in enumerate(cv_paras[hl_idx + 1:], hl_idx + 1)
             if pp["text"].strip() and pp["text"].strip().isupper()), None)
        highlights = [pp["text"].strip() for pp in cv_paras[hl_idx + 1:hl_next]
                      if pp["text"].strip()]

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
        cv_summary_count=summary_count,
        cv_summary_bold=summary_bold,
        cv_highlights=highlights,
        cv_master_text=cv_text,
        cv_headings_present=headings_ok,
        letter_bold=db.bold_runs(letter),
        letter_bullets=letter_bullets,
        placeholder_counts=counts,
    )


def _unique_title(db: DocBuild, base: str, parent_id: str, role_slug: str,
                  today: datetime.date | None = None) -> str:
    """The per-role copy name, never a version number.

    Company first; then the role slug when the company already has a copy
    (two Cohere roles); then the build date when a failed earlier pass left
    the role-slug copy behind (Cohere Head of Corporate Development,
    2026-09-07: the documents were built, the package gate then blocked, and
    the next build had nowhere to land). Drive files are never deleted here.
    """
    if not db.find_by_name(base, parent_id):
        return base
    with_slug = f"{base}_{role_slug}"
    if not db.find_by_name(with_slug, parent_id):
        return with_slug
    stamp = (today or datetime.date.today()).strftime("%Y%m%d")
    dated = f"{with_slug}_{stamp}"
    if not db.find_by_name(dated, parent_id):
        return dated
    # A SECOND failure on the same day used to be fatal. build_package is not
    # atomic: it builds the letter, then the CV, so anything that raises in the CV
    # leaves the letter behind, and the retry has nowhere to land. That happened
    # three times in a row on the first live Harvey build. The docstring above
    # already recorded the same shape for Cohere on 2026-09-07, patched one level
    # deep; this is the next level.
    #
    # The suffix disambiguates same-day rebuilds. It is NOT a CV version number,
    # which canon 9.12 forbids: the master is versioned, per-role copies are not,
    # and this counts attempts at one role on one day.
    for attempt in range(2, 21):
        candidate = f"{dated}_{attempt:02d}"
        if not db.find_by_name(candidate, parent_id):
            return candidate
    raise BuildError(
        f"twenty copies of {dated!r} already exist; something is retrying in a "
        f"loop, so refusing to add another. Needs human review.")


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def build_letter(db: DocBuild, facts: MasterFacts, tr: TailorResult, *,
                 company: str, title: str, letter_blocks: dict,
                 today: datetime.date | None = None) -> tuple[str, VerifyReport]:
    today = today or datetime.date.today()
    date_text = today.strftime("%B %d, %Y").replace(" 0", " ")
    # The generated hook wins. assemble_hook stays the fallback for when the
    # voice gate rejected it, which is why the block layer is not removed.
    hook = (tr.hook or "").strip() or assemble_hook(
        letter_blocks, tr.block_key, company, tr.jd_mirror)
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


_MONEY_PHRASE = re.compile(
    r"\$\s?\d[\d,.]*\s*[KkMmBb]?(?:\s+(?:to|and)\s+\$?\s?\d[\d,.]*\s*[KkMmBb]?)?"
    r"(?:\s+ARR)?|\d[\d,.]*\s*%(?:\s+EBITDA)?")


def _summary_bold_targets(text: str) -> list[str]:
    """The money and percentage phrases in a generated summary, which is what the
    master bolds. Derived from the text so nothing is bolded that is not there."""
    out, seen = [], set()
    for m in _MONEY_PHRASE.finditer(text or ""):
        frag = m.group(0).strip()
        if frag and frag not in seen:
            seen.add(frag)
            out.append(frag)
    return out


def build_cv(db: DocBuild, facts: MasterFacts, tr: TailorResult, *,
             company: str, title: str, cv_blocks: dict) -> tuple[str, VerifyReport]:
    """The generated summary replaces the WHOLE summary span. The approved block
    is the fallback, used only when the voice gate rejected the generated prose,
    and in that case it replaces just paragraph 1 as it always did."""
    generated = (tr.summary or "").strip()
    summary_text = generated or cv_blocks[tr.block_key]["text"]
    doc_title = _unique_title(db, f"KrishRaja_CV_{company}",
                              config.CV_FOLDER_ID, slugify(title))
    doc_id = db.copy_master(config.CV_MASTER_ID, doc_title, config.CV_FOLDER_ID)
    if generated and facts.cv_summary_count > 1:
        # Canon 9.12: numbers are bold inside the prose, keep the bolding when
        # tailoring. The master bolds a phrase in summary paragraph 2, so the
        # replacement is explicitly allowed and the convention is reapplied to the
        # numbers in the new text rather than lost with the old words.
        # The money phrases in the new prose, plus any phrase the master bolded in
        # ITS OWN SUMMARY that survives verbatim into the new text. The master bolds
        # "14-agent autonomous AI operating system" there and the generated summary
        # still says it, so dropping that bold is a real loss, which the package
        # verifier caught on the first live build. Scoped to the summary because the
        # whole-document list also bolds employer names like "Captify".
        bold = _summary_bold_targets(generated)
        for frag in facts.cv_summary_bold:
            frag = frag.strip()
            if frag and frag in generated and frag not in bold:
                bold.append(frag)
        bold = sorted(set(bold), key=lambda x: generated.index(x))
        db.replace_paragraph_block(doc_id, facts.cv_summary_p1[:60],
                                   facts.cv_summary_count, generated,
                                   allow_styled=True, bold_substrings=bold)
    else:
        db.set_unstyled_paragraph(doc_id, facts.cv_summary_p1[:60], summary_text)
    anchor = facts.competency_sep.join(facts.cv_competencies[:2])
    db.set_unstyled_paragraph(doc_id, anchor,
                              facts.competency_sep.join(tr.competency_order))
    # Canon 9.12 requires highlights reordered per role. A reorder that cannot
    # prove it kept the bold runs raises, and the CV keeps the master's order
    # rather than shipping with the bold in the wrong place.
    if tr.highlight_order and facts.cv_highlights \
            and sorted(tr.highlight_order) == list(range(len(facts.cv_highlights))) \
            and tr.highlight_order != list(range(len(facts.cv_highlights))):
        anchors = [h[:60] for h in facts.cv_highlights]
        db.reorder_paragraphs(doc_id, anchors, tr.highlight_order)
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
