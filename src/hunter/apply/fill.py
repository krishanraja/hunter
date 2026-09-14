"""The FillPlan: one posting's complete, resolved answer set.

This is the artifact everything else agrees on. approval.render shows it to Krish,
approval.plan_hash pins it so an approved plan cannot change underneath him, and
submit.py types it into the real form. Before it existed, the field lines were
assembled ad hoc at each call site, which meant the email and the submission could
have disagreed about what was being sent.

build_payload is pure: no network, no clock, no model call. Everything uncertain
has already happened by the time it runs (the form was fetched, the answers
resolved, the prose generated and gated), so the plan is fully testable offline
and identical inputs always produce an identical hash.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .approval import FieldLine
from .infobank import AnswerBank
from .model import FormSpec
from .resolve import Answer, Resolver, Unanswered


@dataclass(frozen=True)
class FilledField:
    key: str            # what the vendor's form calls it
    label: str          # what a human reads
    kind: str
    required: bool
    value: str = ""
    source: str = ""    # why the value says what it says
    flagged: bool = False
    unresolved: bool = False
    reason: str = ""    # why there is no value

    @property
    def blocking(self) -> bool:
        """Required, and we have nothing to put in it."""
        return self.required and self.unresolved


@dataclass
class FillPlan:
    ats: str
    slug: str
    posting_id: str
    company: str
    role: str
    jd_url: str = ""
    fields: tuple[FilledField, ...] = ()
    essays: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    hook: str = ""
    attachment_style: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> tuple[FilledField, ...]:
        return tuple(f for f in self.fields if f.blocking)

    @property
    def flagged(self) -> tuple[FilledField, ...]:
        return tuple(f for f in self.fields if f.flagged and not f.unresolved)

    @property
    def ready(self) -> bool:
        """Every required field has a value. Being ready is not permission to
        send; that still needs Krish's approval token."""
        return not self.blocking

    def as_dict(self) -> dict:
        """The canonical form for hashing. Only what actually gets submitted goes
        in here: a change to a source string or a note must not void an approval,
        but a change to any value must.
        """
        return {
            "ats": self.ats,
            "posting_id": self.posting_id,
            "attachment_style": self.attachment_style,
            "fields": {f.key: f.value for f in self.fields},
            "essays": dict(sorted(self.essays.items())),
            "summary": self.summary,
            "hook": self.hook,
        }

    def field_lines(self) -> list[FieldLine]:
        """What the approval email renders."""
        return [FieldLine(label=f.label, value=f.value, required=f.required,
                          source=f.source or f.reason, flagged=f.flagged,
                          unresolved=f.unresolved)
                for f in self.fields]

    def submit_values(self) -> dict[str, str]:
        """key to value, for the browser driver. Files are handled separately."""
        return {f.key: f.value for f in self.fields
                if f.value and f.kind not in ("file_resume", "file_cover")}


def build_payload(spec: FormSpec, bank: AnswerBank, *, company: str, role: str,
                  jd_url: str = "", role_location: str = "",
                  summary: str = "", hook: str = "",
                  essays: dict[str, str] | None = None,
                  attachment_style: str = "") -> FillPlan:
    """Resolve every field on the form into a plan. Pure function."""
    essays = dict(essays or {})
    resolver = Resolver(bank, role_location=role_location)
    notes: list[str] = []
    if not spec.readable:
        notes.append(spec.note or "the form could not be read")
        return FillPlan(ats=spec.ats, slug=spec.slug, posting_id=spec.posting_id,
                        company=company, role=role, jd_url=jd_url,
                        summary=summary, hook=hook, essays=essays,
                        attachment_style=attachment_style, notes=notes)

    filled: list[FilledField] = []
    for f in spec.fields:
        label = (f.label or "").strip()
        if f.kind in ("file_resume", "file_cover"):
            filled.append(FilledField(
                key=f.key, label=label or f.kind, kind=f.kind,
                required=f.required,
                value="the built PDF", source="attached from the package"))
            continue
        # An essay the caller already drafted wins over re-resolving it.
        if label in essays and essays[label].strip():
            filled.append(FilledField(
                key=f.key, label=label, kind=f.kind, required=f.required,
                value=essays[label].strip(), source="drafted for this role",
                flagged=True))
            continue
        result = resolver.resolve(f)
        if isinstance(result, Answer):
            filled.append(FilledField(
                key=f.key, label=label, kind=f.kind, required=f.required,
                value=result.value, source=result.source,
                flagged=result.flagged))
        else:
            filled.append(FilledField(
                key=f.key, label=label, kind=f.kind, required=f.required,
                unresolved=True,
                reason=(f"{result.reason}: {result.note}" if result.note
                        else result.reason)))
    return FillPlan(ats=spec.ats, slug=spec.slug, posting_id=spec.posting_id,
                    company=company, role=role, jd_url=jd_url,
                    fields=tuple(filled), essays=essays, summary=summary,
                    hook=hook, attachment_style=attachment_style, notes=notes)
