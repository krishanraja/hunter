"""iCIMS: one posting's page, read for liveness and its text.

iCIMS hosts each employer's postings on its own subdomain,
careers-<company>.icims.com/jobs/<id>/<slug>/job, and frames the posting
inside the page (in_iframe=1 serves the frame's own document). There is no
public posting API, so the frame's HTML is the source, the same way Lever's
apply page is read. A 404, or a page that says the job is no longer
available, is a dead posting; anything else that carries a description is
live. An answer that is neither is not a verdict: it raises, and the caller
records it unverified.
"""
from __future__ import annotations

import html as html_mod
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (hunter)"}
GONE = ("no longer available", "position has been filled", "job you are looking for",
        "this job is closed", "no longer accepting")


def host_of(slug: str) -> str:
    return slug if slug.endswith(".icims.com") else f"{slug}.icims.com"


def posting_url(slug: str, posting_id: str) -> str:
    return f"https://{host_of(slug)}/jobs/{posting_id}/job"


def apply_url(slug: str, posting_id: str) -> str:
    # The posting's own login step: iCIMS asks for an email first, then shows
    # the form in a frame on the same host.
    return f"https://{host_of(slug)}/jobs/{posting_id}/login"


def _text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def fetch_posting(slug: str, posting_id: str) -> tuple[bool, str, str]:
    url = posting_url(slug, posting_id)
    r = requests.get(url, params={"in_iframe": "1"}, headers=UA, timeout=30)
    if r.status_code in (404, 410):
        return False, "", url
    if r.status_code >= 400:
        raise RuntimeError(f"iCIMS answered {r.status_code}")
    text = _text(r.text)
    low = text.lower()
    if any(g in low for g in GONE) and len(text) < 3000:
        return False, "", url
    if len(text) < 200:
        raise RuntimeError("iCIMS page carried no description")
    return True, text, url
