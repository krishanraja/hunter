"""Ashby application form reader.

The form is NOT in the posting page's window.__appData (ats/ashby.py reads that
for liveness and the JD; it carries no applicationForm). It comes from the
public non-user GraphQL endpoint.

Two discovered facts about that endpoint, both load bearing:
  1. `field` is a JSON scalar. It must be requested BARE. Asking for a
     subselection on it fails validation.
  2. Introspection is disabled, so QUERY below is a captured contract, not
     something a future reader can rediscover from the schema. Change it only
     against a live response.
A dead posting answers with data.jobPosting = null, which is the same shape
ats/ashby.py already treats as dead.
"""
from __future__ import annotations

import requests

from .model import FormField, FormSpec, Option

ENDPOINT = "https://jobs.ashbyhq.com/api/non-user-graphql"
UA = {"User-Agent": "Mozilla/5.0 (hunter)", "Content-Type": "application/json"}

QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, "
    "$jobPostingId: String!) { jobPosting("
    "organizationHostedJobsPageName: $organizationHostedJobsPageName, "
    "jobPostingId: $jobPostingId) { title applicationForm { sections { "
    "title fieldEntries { isRequired field } } } } }"
)

# Ashby's own field type -> our kind. A type absent here is a real change on
# their side and must fail loudly rather than be guessed into short_text.
TYPE_KINDS = {
    "String": "short_text",
    "LongText": "long_text",
    "Email": "email",
    "Phone": "phone",
    "File": "file_resume",   # refined below by path and label
    "Boolean": "boolean",
    "Location": "location",
    "ValueSelect": "single_select",
    "MultiValueSelect": "multi_select",
    "Url": "url",
    "Number": "number",
    "Date": "date",
}

# Ashby system paths carry stable meaning regardless of the label a company types.
SYSTEM_KINDS = {
    "_systemfield_name": "name",
    "_systemfield_email": "email",
    "_systemfield_location": "location",
    "_systemfield_resume": "file_resume",
    "_systemfield_phone": "phone",
}

CONSENT_HINTS = ("privacy polic", "arbitration", "acknowledg", "i hereby certify",
                 "consent", "terms and conditions")
DEMOGRAPHIC_HINTS = ("gender", "race", "ethnic", "veteran", "disability",
                     "self-identif", "self identif")
COVER_HINTS = ("cover letter",)


def _kind(path: str, label: str, vendor_type: str) -> str:
    low = (label or "").strip().lower()
    if vendor_type == "File":
        return "file_cover" if any(h in low for h in COVER_HINTS) else "file_resume"
    if path in SYSTEM_KINDS:
        return SYSTEM_KINDS[path]
    base = TYPE_KINDS.get(vendor_type)
    if base is None:
        raise ValueError(
            f"unmapped Ashby field type {vendor_type!r} on {path!r}; add it to "
            f"TYPE_KINDS rather than letting it fall through")
    if any(h in low for h in DEMOGRAPHIC_HINTS):
        return "demographic"
    # A consent is a boolean or an acknowledgement select whose label is a policy.
    if base in ("boolean", "single_select", "multi_select") and any(
            h in low for h in CONSENT_HINTS):
        return "consent"
    return base


def parse(payload: dict, *, slug: str, posting_id: str) -> FormSpec:
    """Parse a captured or live ApiJobPosting response into a FormSpec."""
    posting = (payload.get("data") or {}).get("jobPosting")
    if not posting:
        return FormSpec(ats="ashby", slug=slug, posting_id=posting_id, title="",
                        fields=(), readable=False,
                        note="Ashby returned no posting; the posting is dead")
    fields: list[FormField] = []
    form = posting.get("applicationForm") or {}
    for section in form.get("sections") or []:
        for entry in section.get("fieldEntries") or []:
            raw = entry.get("field") or {}
            path = raw.get("path") or raw.get("id") or ""
            label = (raw.get("title") or "").strip()
            vendor_type = raw.get("type") or ""
            options = tuple(
                Option(label=str(o.get("label", "")), value=str(o.get("value", "")))
                for o in (raw.get("selectableValues") or [])
                if not o.get("isArchived"))
            fields.append(FormField(
                key=path, label=label,
                kind=_kind(path, label, vendor_type),
                required=bool(entry.get("isRequired")),
                options=options, vendor_type=vendor_type))
    return FormSpec(ats="ashby", slug=slug, posting_id=posting_id,
                    title=posting.get("title") or "", fields=tuple(fields))


def fetch_form(slug: str, posting_id: str, *, timeout: int = 30) -> FormSpec:
    r = requests.post(ENDPOINT, headers=UA, timeout=timeout, json={
        "operationName": "ApiJobPosting",
        "variables": {"organizationHostedJobsPageName": slug,
                      "jobPostingId": posting_id},
        "query": QUERY})
    r.raise_for_status()
    return parse(r.json(), slug=slug, posting_id=posting_id)
