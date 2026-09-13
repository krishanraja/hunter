"""Derives the six sheet columns that have been dead fields since the 30 column
layout landed: Application Format, Attachment Style, Additional Questions, Form
Complexity, Autonomy Score, Form Audit Date. They were declared in
sheet.HEADERS, given defaults, asserted in tests, and never written by anything.

Every vocabulary below is closed. A new value is a canon 9.13 amendment filed to
workflow_proposals, the same rule sheet.py already carries for Package Status.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from .infobank import AnswerBank
from .model import FormSpec
from .resolve import NEEDS_ESSAY, Answer, Resolver, Unanswered

# Application Format
FMT_ASHBY = "Ashby"
FMT_GREENHOUSE = "Greenhouse"
FMT_LEVER = "Lever"
FMT_WORKDAY = "Workday"
FMT_GOOGLE = "Google Careers"
FMT_LINKEDIN = "LinkedIn"
FMT_UNKNOWN = "Unknown"
FORMATS = (FMT_ASHBY, FMT_GREENHOUSE, FMT_LEVER, FMT_WORKDAY, FMT_GOOGLE,
           FMT_LINKEDIN, FMT_UNKNOWN)

# Attachment Style
ATT_BOTH = "CV+CL"
ATT_CV = "CV only"
ATT_NONE = "No attachment"
ATTACHMENTS = (ATT_BOTH, ATT_CV, ATT_NONE)

# Form Complexity
CPX_LOW = "Low"
CPX_MED = "Med"
CPX_HIGH = "High"
CPX_UNKNOWN = "Unknown"
COMPLEXITIES = (CPX_LOW, CPX_MED, CPX_HIGH, CPX_UNKNOWN)

# Autonomy Score
AUT_FULL = "Full"
AUT_ANSWERS = "Partial (answers)"
AUT_ESSAY = "Partial (essay)"
AUT_ACCOUNT = "Partial (account)"
AUT_MANUAL = "Manual"
AUT_DEAD = "Posting dead"
AUTONOMIES = (AUT_FULL, AUT_ANSWERS, AUT_ESSAY, AUT_ACCOUNT, AUT_MANUAL, AUT_DEAD)

ATS_FORMATS = {"ashby": FMT_ASHBY, "greenhouse": FMT_GREENHOUSE,
               "lever": FMT_LEVER, "workday": FMT_WORKDAY,
               "google": FMT_GOOGLE, "linkedin": FMT_LINKEDIN}


@dataclass(frozen=True)
class Audit:
    """Exactly the six cells, plus the detail the approval email needs."""
    application_format: str
    attachment_style: str
    additional_questions: str
    form_complexity: str
    autonomy_score: str
    form_audit_date: str
    unresolved: tuple[str, ...] = ()
    flagged: tuple[str, ...] = ()

    def cells(self) -> dict[str, str]:
        return {
            "Application Format": self.application_format,
            "Attachment Style": self.attachment_style,
            "Additional Questions": self.additional_questions,
            "Form Complexity": self.form_complexity,
            "Autonomy Score": self.autonomy_score,
            "Form Audit Date": self.form_audit_date,
        }


def _attachment_style(spec: FormSpec) -> str:
    kinds = {f.kind for f in spec.fields}
    if "file_cover" in kinds and "file_resume" in kinds:
        return ATT_BOTH
    if "file_resume" in kinds:
        return ATT_CV
    return ATT_NONE


def _complexity(spec: FormSpec) -> str:
    if not spec.readable:
        return CPX_UNKNOWN
    required = len(spec.required_fields)
    required_essays = sum(1 for f in spec.essay_fields if f.required)
    if required_essays or required >= 10:
        return CPX_HIGH
    if required >= 6:
        return CPX_MED
    return CPX_LOW


def _additional_questions(spec: FormSpec) -> str:
    if not spec.readable:
        return "not readable"
    custom = [f for f in spec.fields
              if f.kind not in ("name", "email", "phone", "file_resume",
                                "file_cover")]
    essays = len(spec.essay_fields)
    parts = [f"{len(custom)} custom"]
    if essays:
        parts.append(f"essay:{essays}")
    consents = len(spec.flagged_fields)
    if consents:
        parts.append(f"flagged:{consents}")
    return ", ".join(parts)


def audit(spec: FormSpec, bank: AnswerBank, *,
          today: datetime.date | None = None,
          account_required: bool = False,
          role_location: str = "") -> Audit:
    day = (today or datetime.date.today()).isoformat()
    fmt = ATS_FORMATS.get(spec.ats, FMT_UNKNOWN)

    if not spec.readable:
        dead = "dead" in (spec.note or "").lower()
        return Audit(
            application_format=fmt,
            attachment_style=ATT_BOTH,
            additional_questions=_additional_questions(spec),
            form_complexity=CPX_UNKNOWN,
            autonomy_score=AUT_DEAD if dead else (
                AUT_ACCOUNT if account_required else AUT_MANUAL),
            form_audit_date=day,
            unresolved=(spec.note,) if spec.note else ())

    resolver = Resolver(bank, role_location=role_location)
    unresolved: list[str] = []
    flagged: list[str] = []
    essay_blocked = False
    for f in spec.fields:
        if f.kind in ("file_resume", "file_cover"):
            continue
        result = resolver.resolve(f)
        if isinstance(result, Answer):
            if result.flagged:
                flagged.append(f"{f.label}: {result.value}")
            continue
        if result.reason == NEEDS_ESSAY:
            if f.required:
                essay_blocked = True
            continue
        if f.kind in ("consent", "demographic"):
            flagged.append(f"{f.label} ({result.reason})")
            continue
        if f.required:
            unresolved.append(f"{f.label} ({result.reason}"
                              + (f": {result.note}" if result.note else "") + ")")

    if account_required:
        autonomy = AUT_ACCOUNT
    elif unresolved:
        autonomy = AUT_ANSWERS
    elif essay_blocked:
        autonomy = AUT_ESSAY
    else:
        autonomy = AUT_FULL

    return Audit(application_format=fmt,
                 attachment_style=_attachment_style(spec),
                 additional_questions=_additional_questions(spec),
                 form_complexity=_complexity(spec),
                 autonomy_score=autonomy,
                 form_audit_date=day,
                 unresolved=tuple(unresolved),
                 flagged=tuple(flagged))
