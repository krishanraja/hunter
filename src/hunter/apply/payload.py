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

# The oldest extension build that can fill everything this payload carries.
#
# Chrome does not update an unpacked extension, so the copy in Krish's folder is
# frozen at whatever he last downloaded while this code moves on. That cost a
# real round: the demographics block shipped here, his extension predated the
# code that reads it, and the form came up with the equal opportunity section
# empty under a green banner saying every field was filled. A version he cannot
# see is a silent failure, so the payload states the floor and the extension
# says out loud when it is below it.
#
# Raise this whenever the extension gains an ability a payload depends on, and
# raise extension/manifest.json to match.
MIN_EXTENSION = "1.7.0"

# Playwright's own pseudo-classes. Real to Playwright, a syntax error to
# document.querySelectorAll, so they are dead weight in a payload a browser has
# to read. The extension finds those controls by their label instead.
PLAYWRIGHT_ONLY = (":text-is(", ":has-text(", ":text(", ">>")


def css_only(selectors: list[str]) -> list[str]:
    return [s for s in selectors if not any(p in s for p in PLAYWRIGHT_ONLY)]


# The questions every EEOC section asks and no board API reports. Krish's answers
# live in his own info bank and he asked for them to be filled; the phrases are
# the wordings those questions actually use, and the extension does the matching
# because only it can see the real labels.
#
# Each entry is (bank field, label, {bank answer: accepted label phrases}). An
# answer with no mapping is never guessed at.
DEMOGRAPHIC_MAP: tuple[tuple[str, str, dict[str, tuple[str, ...]]], ...] = (
    ("Gender", "Gender", {
        "male": ("male",),
        "female": ("female",),
        "decline to self-identify": ("decline to self-identify",),
    }),
    ("Race / ethnicity", "Race or ethnicity", {
        "two or more races": ("two or more races",),
        "asian": ("asian",),
        "white": ("white",),
        "black or african american": ("black or african american",),
        "hispanic or latino": ("hispanic or latino",),
        "decline to self-identify": ("decline to self-identify",),
    }),
    ("Veteran status", "Veteran status", {
        "not a veteran": ("i am not a protected veteran", "not a protected veteran",
                          "i am not a veteran", "not a veteran"),
        "veteran": ("i identify as one or more of the classifications",),
    }),
    ("Disability status", "Disability status", {
        "no disability": ("no, i do not have a disability and have not had one "
                          "in the past",
                          "no, i do not have a disability",
                          "i do not have a disability"),
        "disability": ("yes, i have a disability",),
    }),
)


def demographics(bank) -> list[dict]:
    """His recorded answers, with the label phrasings a form might use.

    Never a guess. A bank row with no value, or a value this does not have a
    mapping for, is simply not sent and the question stays his.
    """
    out: list[dict] = []
    for field_name, label, mapping in DEMOGRAPHIC_MAP:
        entry = bank.get(field_name) if bank else None
        value = (getattr(entry, "value", "") or "").strip()
        if not value or not getattr(entry, "usable", False):
            continue
        phrases = mapping.get(value.lower())
        if not phrases:
            continue
        out.append({"label": label, "answer": value, "phrases": list(phrases)})
    return out


# A Standing row is the writer's material about him, never a form answer.
NOT_ANSWERS = ("standing:",)


def answers(bank) -> list[dict]:
    """His Info Bank as (its wording, his answer), for a form whose questions
    appear only after he signs in (Workday, iCIMS).

    The extension answers a question only when its label reads exactly as one
    of these, once case, spacing, punctuation and a required star are put
    aside: his rows carry the form's own wording, so an exact match is his
    answer to that question and anything looser is a guess. Sensitive rows
    are left out: those are shown to him in the Apply email every time, and
    an answer filled out of his sight would not be."""
    out: list[dict] = []
    for e in getattr(bank, "entries", {}).values() if bank is not None else ():
        name = (e.field_name or "").strip()
        if not name or not e.usable or e.always_flagged:
            continue
        if name.lower().startswith(NOT_ANSWERS):
            continue
        out.append({"label": name, "answer": e.value.strip()})
    return out


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
          names: dict[str, str] | None = None, bank=None) -> dict:
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
    multi = bool(getattr(driver_cls, "multi_step", False))
    doc = {
        "version": 1,
        "needs_extension": MIN_EXTENSION,
        "url": driver.apply_url(),
        "company": plan.company,
        "role": plan.role,
        "fields": fields,
        "files": files,
        "demographics": demographics(bank),
    }
    if multi:
        # The form arrives a step at a time after he signs in, so the
        # extension keeps filling as each step appears, and answers the
        # questions no one could see in advance from his own wording.
        doc["multi_step"] = True
        doc["system"] = plan.ats
        doc["answers"] = answers(bank)
    return doc


def size_of(payload: dict) -> int:
    import json
    return len(json.dumps(payload))
