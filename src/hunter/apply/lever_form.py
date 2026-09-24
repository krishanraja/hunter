"""Lever application form reader.

Lever publishes no question schema through its API: api.lever.co returns the
posting text and an applyUrl and nothing about the form. So the form is read
from the apply page, and the apply page is better than it sounds. Lever embeds
the whole definition of every custom card and every EEO survey as JSON in a
hidden `baseTemplate` input, with the employer's own wording, its own required
flags and its own option ids. Those are declared facts, not labels scraped out
of markup, and this adapter prefers them everywhere they exist.

Measured against 19 postings on 7 boards (versapay, rembrand, cfgi, aircall,
shieldai, sandboxvr, crosscountry-consulting) on 2026-09-24. Five card field
types appear and seven core inputs, both listed below.

Two things that a guess would have got wrong, and which is why the required
flag is read per posting and never assumed:

  versapay requires phone AND location. rembrand requires neither.

The resume file input is marked optional by Lever on every posting sampled,
because Lever lets a candidate type their history instead of uploading. The
vendor's flag is taken as it stands, per FormField's contract.
"""
from __future__ import annotations

import html as html_mod
import json
import re

import requests

from .model import FormField, FormSpec, Option

APPLY = "https://jobs.lever.co/{slug}/{posting_id}/apply"

# Lever's card and survey field types, all five seen in the sample. Unmapped
# raises, following greenhouse_form: a question hunter cannot type is a
# question it must not claim to have answered.
CARD_KINDS = {
    "multiple-choice": "single_select",
    "multiple-select": "multi_select",
    "dropdown": "single_select",
    "text": "short_text",
    "textarea": "long_text",
}

# The core inputs Lever puts on every application, by its own field name.
CORE_KINDS = {
    "name": "name",
    "email": "email",
    "phone": "phone",
    "location": "location",
    "org": "short_text",
    "resume": "file_resume",
    # Self-identification, so it is his to give or withhold like any other.
    # It reached 3 of the 19 postings sampled.
    "pronouns": "demographic",
}

CONSENT_HINTS = ("privacy polic", "privacy notice", "arbitration", "acknowledg",
                 "i hereby certify", "consent", "terms and conditions",
                 "gdpr", "data protection")
DEMOGRAPHIC_HINTS = ("gender", "race", "ethnic", "veteran", "disability",
                     "self-identif", "self identif", "hispanic", "lgbtq",
                     "sexual orientation", "pronoun", "neurodiver")

_INPUT = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.I)
_ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
# The hidden input carrying a card's or a survey's own JSON definition.
_TEMPLATE = re.compile(
    r'value="(\{&quot;createdAt&quot;.*?)"[\s>]', re.S)


def _attrs(blob: str) -> dict:
    return {k.lower(): v for k, v in _ATTR.findall(blob)}


# Lever's required marker, rendered inside the label text. It is decoration:
# the required flag comes from the input's own attribute, and leaving the glyph
# in means the approval email shows Krish "Full name /" instead of the
# question the employer asked.
REQUIRED_GLYPHS = "\u2731\u2217*"


def _label_before(page: str, at: int) -> str:
    """The question as Lever renders it, from the nearest application-label."""
    window = page[max(0, at - 700):at]
    hits = re.findall(r'class="application-label[^"]*">(?:\s*<div[^>]*>)?(.*?)</div>',
                      window, re.S)
    if not hits:
        return ""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                                     html_mod.unescape(hits[-1]))).strip()
    return text.rstrip(REQUIRED_GLYPHS + " ").strip()


def _kind_for_card(vendor_type: str, label: str, *, survey: bool) -> str:
    base = CARD_KINDS.get(vendor_type)
    if base is None:
        raise ValueError(
            f"unmapped Lever card field type {vendor_type!r} on {label[:60]!r}; "
            f"add it to CARD_KINDS rather than letting it fall through")
    low = (label or "").lower()
    # A survey is Lever's EEO block. Every question in one is his to answer.
    if survey or any(h in low for h in DEMOGRAPHIC_HINTS):
        return "demographic"
    if base in ("single_select", "multi_select") and any(
            h in low for h in CONSENT_HINTS):
        return "consent"
    return base


def _templates(page: str) -> list[tuple[str, dict]]:
    """(container_name, definition) for every card and survey on the page."""
    out = []
    for m in _TEMPLATE.finditer(page):
        raw = html_mod.unescape(m.group(1))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # The name attribute sits after the value on these inputs.
        tail = page[m.end():m.end() + 400]
        nm = re.search(r'name="((?:cards|surveysResponses)\[[^\]]+\])\[baseTemplate\]"',
                       tail)
        if not nm:
            continue
        out.append((nm.group(1), data))
    return out


def parse(page: str, *, slug: str, posting_id: str, title: str = "") -> FormSpec:
    fields: list[FormField] = []

    # 1. The core inputs, with Lever's own required attribute.
    for m in _INPUT.finditer(page):
        a = _attrs(m.group(2))
        name, vtype = a.get("name", ""), a.get("type", "")
        if not name or vtype == "hidden" or "[" in name:
            continue
        if name in ("urls",):
            continue
        kind = CORE_KINDS.get(name)
        if kind is None:
            continue
        label = _label_before(page, m.start()) or name.replace("_", " ").title()
        fields.append(FormField(
            key=name, label=label, kind=kind,
            required="required" in m.group(2).lower(),
            vendor_type=vtype or m.group(1).lower()))

    # 2. The link boxes, which Lever names urls[LinkedIn] and so on.
    for m in _INPUT.finditer(page):
        a = _attrs(m.group(2))
        name = a.get("name", "")
        if not name.startswith("urls["):
            continue
        label = _label_before(page, m.start()) or name[5:-1]
        fields.append(FormField(
            key=name, label=label, kind="url",
            required="required" in m.group(2).lower(), vendor_type="url"))

    # 3. Cards and surveys, from their own declarations.
    for container, data in _templates(page):
        survey = container.startswith("surveysResponses")
        for i, f in enumerate(data.get("fields") or []):
            label = re.sub(r"\s+", " ", str(f.get("text") or "")).strip()
            options = tuple(
                Option(label=str(o.get("text", "")),
                       value=str(o.get("optionId") or o.get("text", "")))
                for o in (f.get("options") or []))
            key = (f"{container}[responses][field{i}]" if survey
                   else f"{container}[field{i}]")
            fields.append(FormField(
                key=key, label=label,
                kind=_kind_for_card(f.get("type") or "", label, survey=survey),
                required=bool(f.get("required")), options=options,
                vendor_type=f.get("type") or ""))

    if not fields:
        return FormSpec(ats="lever", slug=slug, posting_id=posting_id,
                        title=title, fields=(), readable=False,
                        note="Lever served the apply page but no form fields "
                             "could be read from it")
    return FormSpec(ats="lever", slug=slug, posting_id=posting_id,
                    title=title, fields=tuple(fields))


def fetch_form(slug: str, posting_id: str, *, timeout: int = 30) -> FormSpec:
    url = APPLY.format(slug=slug, posting_id=posting_id)
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0 (hunter)"})
    if r.status_code == 404:
        return FormSpec(ats="lever", slug=slug, posting_id=posting_id, title="",
                        fields=(), readable=False,
                        note="Lever apply page 404; the posting is dead")
    r.raise_for_status()
    return parse(r.text, slug=slug, posting_id=posting_id)
