"""iCIMS: the application filled in his own browser, after he gives the
employer his email.

iCIMS opens every application with an email step and then shows the form
inside a frame on the employer's own subdomain, so its questions cannot be
listed in advance. What every iCIMS profile asks is declared here, by the
names iCIMS gives those inputs; the rest is answered in the browser from his
Info Bank by its own wording, and what the extension cannot match it lists.

The posting is read first, so a gone posting is never offered as a form.
"""
from __future__ import annotations

from ..ats import icims
from .model import FormField, FormSpec, unreadable

STANDARD = (
    FormField("FirstName", "First Name", "name", True),
    FormField("LastName", "Last Name", "name", True),
    FormField("Email", "Email", "email", True),
    FormField("Phone", "Phone", "phone", False),
    FormField("AddressStreet1", "Address", "short_text", False),
    FormField("AddressCity", "City", "short_text", False),
    FormField("AddressZip", "Postal Code", "short_text", False),
    FormField("resume", "Resume", "file_resume", True),
)

NOTE = ("iCIMS asks for your email first, then shows the application. The "
        "extension fills it from your Info Bank as each page appears and lists "
        "anything it could not match; you press Next and Submit.")


def fetch_form(slug: str, posting_id: str) -> FormSpec:
    try:
        live, _text, _url = icims.fetch_posting(slug, posting_id)
    except Exception as e:
        return unreadable("icims", f"the iCIMS posting could not be read "
                                   f"({e.__class__.__name__})",
                          slug=icims.host_of(slug), posting_id=posting_id)
    if not live:
        return unreadable("icims", "the posting is no longer on the employer's iCIMS",
                          slug=icims.host_of(slug), posting_id=posting_id)
    return FormSpec(ats="icims", slug=icims.host_of(slug), posting_id=posting_id,
                    title="", fields=STANDARD, readable=True, note=NOTE)
