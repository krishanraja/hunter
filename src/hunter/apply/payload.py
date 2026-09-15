"""What a browser extension needs to fill one application, and nothing more.

Krish is on Windows with no Python and no terminal, and everything built so far
asked him for one. A page may not read his disk, but it MAY put bytes it already
has into a file input, so a content script can do the whole job: fill the fields,
attach the CV, and stop. Verified against the live Harvey form before this
module existed.

The selectors come from the drivers rather than from a second list. There is one
idea of where a field lives on an Ashby form, and it is AshbyDriver's; a copy
here would drift the first time a vendor moved something.
"""
from __future__ import annotations

import base64
import secrets

from .fill import FillPlan
from .submit import CHOICE_KINDS, TYPEAHEAD_KINDS, driver_for

# What the content script knows how to do. A field of any other kind is sent
# anyway, marked, so the extension can say "do this one yourself" rather than
# quietly leaving it blank.
FILLABLE = ("text", "choice", "typeahead", "file")

# Playwright's own pseudo-classes. Real to Playwright, a syntax error to
# document.querySelectorAll, so they are dead weight in a payload a browser has
# to read. The extension finds those controls by their label instead.
PLAYWRIGHT_ONLY = (":text-is(", ":has-text(", ":text(", ">>")


def css_only(selectors: list[str]) -> list[str]:
    return [s for s in selectors if not any(p in s for p in PLAYWRIGHT_ONLY)]


def new_key() -> str:
    """The half of the capability that is not the token.

    The link lives in his email and the payload carries his CV and his answers,
    so knowing a job id must not be enough to fetch it.
    """
    return secrets.token_hex(16)


def kind_for(field) -> str:
    if field.kind in ("file_resume", "file_cover"):
        return "file"
    if field.kind in TYPEAHEAD_KINDS:
        return "typeahead"
    if field.kind in CHOICE_KINDS:
        return "choice"
    return "text"


def build(plan: FillPlan, *, attachments: dict[str, bytes] | None = None,
          names: dict[str, str] | None = None) -> dict:
    """One JSON document: where the form is, what goes in it, and the documents."""
    attachments, names = attachments or {}, names or {}
    driver_cls = driver_for(plan)
    driver = driver_cls(None, plan)   # selectors only; no page is touched
    fields, files = [], []
    for f in plan.fields:
        kind = kind_for(f)
        if kind == "file":
            key = "file_resume" if plan.attachment_style == "CV" else f.kind
            blob = attachments.get(key) or attachments.get(f.kind)
            if not blob:
                continue
            files.append({
                "label": f.label,
                "selectors": css_only(driver.file_selectors(f)),
                "name": names.get(key) or names.get(f.kind) or "KrishRaja_CV.pdf",
                "b64": base64.b64encode(blob).decode(),
            })
            continue
        if not f.value:
            continue
        selectors = css_only(driver.choice_selectors(f)
                             if kind in ("choice", "typeahead")
                             else driver.text_selectors(f))
        fields.append({"label": f.label, "kind": kind, "value": f.value,
                       "selectors": selectors, "required": bool(f.required)})
    return {
        "version": 1,
        "url": driver.apply_url(),
        "company": plan.company,
        "role": plan.role,
        "fields": fields,
        "files": files,
    }


def size_of(payload: dict) -> int:
    import json
    return len(json.dumps(payload))
