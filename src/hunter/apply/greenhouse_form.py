"""Greenhouse application form reader.

The public board API returns the whole question set with questions=true. Note
the board token is the URL segment even on the newer job-boards.greenhouse.io
host, and a per-job 404 there means the JOB is gone, not the board: probe the
board list before calling a posting dead (ats/greenhouse.fetch_posting already
treats the per-job 404 as dead, which is correct for liveness).

Greenhouse nests one or more `fields` under one `questions` entry. A resume
question carries both input_file and textarea; we keep the file and drop the
paste-the-text twin, because the repo always has a built PDF.
"""
from __future__ import annotations

import requests

from .model import FormField, FormSpec, Option

API = "https://boards-api.greenhouse.io/v1/boards"

TYPE_KINDS = {
    "input_text": "short_text",
    "textarea": "long_text",
    "input_file": "file_resume",
    "multi_value_single_select": "single_select",
    "multi_value_multi_select": "multi_select",
    "input_hidden": "short_text",
}

# Greenhouse's field names are stable across every board for the core set.
NAME_KINDS = {
    "first_name": "name",
    "last_name": "name",
    "preferred_name": "name",
    "email": "email",
    "phone": "phone",
    "resume": "file_resume",
    "cover_letter": "file_cover",
}

CONSENT_HINTS = ("privacy polic", "arbitration", "acknowledg", "i hereby certify",
                 "consent", "terms and conditions", "candidate privacy")
DEMOGRAPHIC_HINTS = ("gender", "race", "ethnic", "veteran", "disability",
                     "self-identif", "self identif", "hispanic")
URL_HINTS = ("linkedin", "website", "portfolio", "github", "blog")


def _kind(name: str, label: str, vendor_type: str) -> str:
    if name in NAME_KINDS:
        return NAME_KINDS[name]
    low = (label or "").strip().lower()
    base = TYPE_KINDS.get(vendor_type)
    if base is None:
        raise ValueError(
            f"unmapped Greenhouse field type {vendor_type!r} on {name!r}; add it "
            f"to TYPE_KINDS rather than letting it fall through")
    if any(h in low for h in DEMOGRAPHIC_HINTS):
        return "demographic"
    if base in ("single_select", "multi_select") and any(
            h in low for h in CONSENT_HINTS):
        return "consent"
    if base == "short_text" and any(h in low for h in URL_HINTS):
        return "url"
    return base


# Two required controls the board API never reports. Measured 2026-09-15 against
# three live boards: `country` is on every one of them, `candidate-location` on
# two of the three, and both carry the required asterisk. A plan built from the
# API alone therefore says every required field has an answer while the form has
# two empty ones, which is the exact condition submit.py exists to prevent. They
# are added here rather than in the driver because they are part of what the form
# asks, and the approval email should show Krish both answers before he approves.
DOM_ONLY = (
    ("country", "Country", "location"),
    ("candidate-location", "Location (City)", "location"),
)


def _dom_only(present: set[str]) -> list[FormField]:
    return [FormField(key=key, label=label, kind=kind, required=True,
                      options=(), vendor_type="dom_only")
            for key, label, kind in DOM_ONLY if key not in present]


def parse(payload: dict, *, slug: str, posting_id: str) -> FormSpec:
    questions = payload.get("questions")
    if questions is None:
        return FormSpec(ats="greenhouse", slug=slug, posting_id=posting_id,
                        title=payload.get("title") or "", fields=(), readable=False,
                        note="Greenhouse returned no question set for this job")
    fields: list[FormField] = []
    for q in questions:
        label = (q.get("label") or "").strip()
        required = bool(q.get("required"))
        raw_fields = q.get("fields") or []
        file_names = {f.get("name") for f in raw_fields
                      if f.get("type") == "input_file"}
        for f in raw_fields:
            name = f.get("name") or ""
            vendor_type = f.get("type") or ""
            # Drop the paste-the-text twin when the same question takes a file.
            if vendor_type == "textarea" and name.endswith("_text") \
                    and name[: -len("_text")] in file_names:
                continue
            options = tuple(
                Option(label=str(v.get("label", "")), value=str(v.get("value", "")))
                for v in (f.get("values") or []))
            fields.append(FormField(
                key=name, label=label,
                kind=_kind(name, label, vendor_type),
                required=required, options=options, vendor_type=vendor_type))
    # After the phone, before the files, which is where the live page puts them.
    known = {f.key for f in fields}
    extra = _dom_only(known)
    if extra:
        at = next((i for i, f in enumerate(fields)
                   if f.kind in ("file_resume", "file_cover")), len(fields))
        fields[at:at] = extra
    return FormSpec(ats="greenhouse", slug=slug, posting_id=posting_id,
                    title=payload.get("title") or "", fields=tuple(fields))


def fetch_form(slug: str, job_id: str, *, timeout: int = 30) -> FormSpec:
    r = requests.get(f"{API}/{slug}/jobs/{job_id}",
                     params={"questions": "true"}, timeout=timeout)
    if r.status_code == 404:
        return FormSpec(ats="greenhouse", slug=slug, posting_id=str(job_id),
                        title="", fields=(), readable=False,
                        note="Greenhouse job id not on the board; the posting is dead")
    r.raise_for_status()
    return parse(r.json(), slug=slug, posting_id=str(job_id))
