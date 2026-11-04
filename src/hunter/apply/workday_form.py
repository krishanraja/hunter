"""Workday: the application every Workday tenant asks first, filled in his own
browser after he signs in.

Workday serves the application only to a signed-in candidate, one account per
employer, so no anonymous read can list a posting's questions the way
Greenhouse's API or Lever's apply page can. What CAN be known in advance is
the step every Workday tenant opens with, My Information, whose controls carry
Workday's own stable data-automation-id and id attributes. That is the form
declared here. The questions a tenant adds after it (application questions,
voluntary disclosures) are answered in the browser from his Info Bank by their
own wording, and whatever the extension cannot match it lists for him: the
banner says what it filled on each step and what it left.

The posting itself is read before anything is declared, so a dead posting is
never offered as a form (CLAUDE.md section 1, the FillPlan.ready lesson).
"""
from __future__ import annotations

from ..ats import workday
from .model import FormField, FormSpec, unreadable

# The My Information step. Required as Workday marks it on every tenant seen:
# the name, the email (the account's own, filled already), the phone and the
# CV. The address lines vary by tenant, so they are offered, never required.
STANDARD = (
    FormField("legalName--firstName", "First Name", "name", True),
    FormField("legalName--lastName", "Last Name", "name", True),
    FormField("emailAddress--emailAddress", "Email Address", "email", True),
    FormField("phoneNumber--phoneNumber", "Phone Number", "phone", True),
    FormField("address--addressLine1", "Address Line 1", "short_text", False),
    FormField("address--city", "City", "short_text", False),
    FormField("address--postalCode", "Postal Code", "short_text", False),
    FormField("linkedin", "LinkedIn", "url", False),
    FormField("resume", "Resume/CV", "file_resume", True),
)

NOTE = ("Workday shows the application only after you sign in to this employer's "
        "Workday account. The extension fills each step as it appears, from your "
        "Info Bank, and lists anything it could not match; you press Next and "
        "Submit.")


def fetch_form(url: str) -> FormSpec:
    """The standard Workday application for the posting at `url`, or
    unreadable when the posting is gone or the link names no posting."""
    host = workday.host_of(url)
    m = workday.HOST.search(url or "")
    if not host or not m:
        return unreadable("workday", "a Workday link with no tenant in it")
    tail = (url or "").split(".myworkdayjobs.com/", 1)[-1]
    # Drop the locale ("en-US/") and keep site/job/...
    parts = tail.split("/")
    if parts and "-" in parts[0] and len(parts[0]) <= 6 and "/job/" in "/".join(parts[1:]) + "/":
        parts = parts[1:]
    path = workday.clean_path("/".join(parts))
    if "/job/" not in path:
        return unreadable("workday", "this Workday link names no posting",
                          slug=host)
    live, _text, _url = workday.fetch_posting(m.group(1).lower(), path, host=host)
    if not live:
        return unreadable("workday", "the posting is no longer on the employer's Workday",
                          slug=host, posting_id=path)
    return FormSpec(ats="workday", slug=host, posting_id=path, title="",
                    fields=STANDARD, readable=True, note=NOTE)


def apply_url(host: str, path: str) -> str:
    return f"https://{host}/en-US/{path}/apply"
