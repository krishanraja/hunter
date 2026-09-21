"""One normalised form model that Ashby, Greenhouse and any future adapter map
onto, so the rest of the layer never learns a vendor's field vocabulary.

KIND is the closed vocabulary. A new kind is a deliberate change here plus a
resolver entry, never an ad hoc string invented at a call site.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Closed vocabulary. Resolution and the audit both switch on these.
KINDS = (
    "name", "email", "phone", "location", "file_resume", "file_cover",
    "url", "boolean", "single_select", "multi_select", "short_text",
    "long_text", "number", "date", "consent", "demographic",
    # Ashby's repeating sub-forms. Not single controls, so no flat answer
    # can fill them. ashby_form.py mapped them and nothing downstream was
    # told, so FormField refused to exist and one posting raised through
    # the whole approvals run.
    "education_history", "work_history", "social_links",
)

# Kinds that carry no answer of Krish's and must never be auto-filled from a
# generic default: a consent tick and a demographic disclosure are his to make.
FLAGGED_KINDS = frozenset({"consent", "demographic"})

# Sub-forms he completes in his own browser, with the form in front of him.
#
# These are the ONLY unanswered required fields that do not block the
# approval email. Hunter cannot fill a repeating education or work history
# from a flat answer bank and never will, so blocking on one means that
# application never reaches him at all. He presses submit himself, on the
# real form, so an education section he can see and type into is not the
# silent blank the blocking rule exists to prevent. Every other required
# field with no answer still refuses to send.
HIS_OWN_KINDS = frozenset({"education_history", "work_history", "social_links"})

# Kinds whose answer is prose rather than a lookup.
ESSAY_KINDS = frozenset({"long_text"})

# Some boards type a genuine essay as a plain string ("Why do you want to work
# at Profound?" arrives as Ashby String). Intent decides, not the vendor's type.
ESSAY_LABEL_HINTS = (
    "why do you want", "why are you interested", "why this company",
    "why us", "what makes you excited", "tell us about", "describe a",
    "share an example", "share one example", "how would you", "what metrics",
    "hard problem", "proud of",
)


def looks_like_essay(label: str) -> bool:
    low = (label or "").strip().lower()
    return any(h in low for h in ESSAY_LABEL_HINTS)


@dataclass(frozen=True)
class Option:
    label: str
    value: str


@dataclass(frozen=True)
class FormField:
    """One input on one posting's form.

    key         stable identifier the adapter can post back to (Ashby field
                path, Greenhouse field name)
    label       the question as a human reads it, verbatim from the vendor
    kind        one of KINDS
    required    the vendor's own required flag, never our guess
    options     enumerated choices, empty when free entry
    """
    key: str
    label: str
    kind: str
    required: bool
    options: tuple[Option, ...] = ()
    vendor_type: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown field kind {self.kind!r} for {self.key!r}")


@dataclass(frozen=True)
class FormSpec:
    """A posting's whole form, as the vendor declares it."""
    ats: str
    slug: str
    posting_id: str
    title: str
    fields: tuple[FormField, ...] = ()
    readable: bool = True
    note: str = ""

    @property
    def required_fields(self) -> tuple[FormField, ...]:
        return tuple(f for f in self.fields if f.required)

    @property
    def essay_fields(self) -> tuple[FormField, ...]:
        return tuple(f for f in self.fields
                     if f.kind in ESSAY_KINDS or (
                         f.kind in ("short_text",) and looks_like_essay(f.label)))

    @property
    def flagged_fields(self) -> tuple[FormField, ...]:
        return tuple(f for f in self.fields if f.kind in FLAGGED_KINDS)


def unreadable(ats: str, note: str, *, slug: str = "", posting_id: str = "",
               title: str = "") -> FormSpec:
    """A form we cannot enumerate without a signed-in session. Recorded as a
    fact, not as an empty form, so the audit never reports zero questions for a
    posting whose questions we simply could not see."""
    return FormSpec(ats=ats, slug=slug, posting_id=posting_id, title=title,
                    fields=(), readable=False, note=note)
