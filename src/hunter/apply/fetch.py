"""Picks the right form adapter for a posting URL, and records honestly when a
form cannot be enumerated without a signed in session.

Ground truth from the 2026-09-13 sweep of the 28 approved rows: the ATS key path
(run.ats_key) already resolves liveness correctly for Ashby and Greenhouse,
including the five dead postings. The gap was the non ATS URLs, where a 200
response was read as "liveness unverified" for two postings that are in fact
gone. linkedin.py closes that; Google Careers stays genuinely unverifiable
without a session and is reported as such rather than guessed.
"""
from __future__ import annotations

from . import ashby_form, greenhouse_form, lever_form
from .model import FormSpec, unreadable

GOOGLE_HOST = "google.com/about/careers"
LINKEDIN_HOST = "linkedin.com/jobs"


def form_for(url: str, ats_key) -> FormSpec:
    """ats_key is run.ats_key, injected to avoid importing run (which imports
    most of the repo) from inside the apply layer."""
    key = ats_key(url)
    if key:
        ats, slug, posting_id = key
        if ats == "ashby":
            return ashby_form.fetch_form(slug, posting_id)
        if ats == "greenhouse":
            return greenhouse_form.fetch_form(slug, posting_id)
        if ats == "lever":
            return lever_form.fetch_form(slug, posting_id)
        return unreadable(
            ats, f"no form adapter for {ats} yet; the posting is readable but "
                 f"its form is not", slug=slug, posting_id=posting_id)
    if GOOGLE_HOST in url:
        return unreadable(
            "google",
            "Google Careers needs a signed in Google account and a Careers "
            "profile; the form cannot be enumerated anonymously")
    if LINKEDIN_HOST in url:
        # Two different postings wear the same URL. An OFF-SITE LinkedIn
        # posting sends the candidate to the employer's own ATS, which hunter
        # can then read like any other; an ON-SITE one is Easy Apply, which
        # happens inside LinkedIn on Krish's own session and is not something
        # this repository will ever drive (canon 9).
        #
        # Measured 2026-09-24 on the three LinkedIn roles then on his sheet,
        # BOI, Confidential and Strativ Group: all three are onsite, and their
        # only external hosts are LinkedIn's own CDNs. He believed they clicked
        # through to a real application page; for those three they do not.
        return unreadable(
            "linkedin",
            "LinkedIn Easy Apply: the application is completed inside "
            "LinkedIn on your own session, so there is no form to read")
    return unreadable("unknown", "no adapter matches this URL")


def account_required(spec: FormSpec) -> bool:
    return spec.ats in ("google", "linkedin")
