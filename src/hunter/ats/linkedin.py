"""LinkedIn liveness only. hunter never applies through LinkedIn and never uses
a LinkedIn session; sourcing goes through Apify (sources/apify_linkedin.py).

Why this exists: a removed LinkedIn posting still answers 200. Two of the 28
approved rows on 2026-09-13 were dead and both read as "liveness unverified",
so packages were built for them. The two signals below are the ones LinkedIn
actually gives an anonymous reader.
"""
from __future__ import annotations

import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}

CLOSED = re.compile(r"no longer accepting applications", re.I)
# A removed posting redirects to a keyword search such as /jobs/<kw>-jobs.
SEARCH_REDIRECT = re.compile(r"/jobs/(?!view/)[^/?]*jobs\b", re.I)


def posting_state(url: str, *, timeout: int = 35) -> tuple[bool | None, str]:
    """(live, why). False means verifiably gone. None means unknown, which the
    caller must keep treating as unverified rather than dead."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        return None, f"LinkedIn fetch failed: {type(e).__name__}"
    if r.status_code in (404, 410):
        return False, f"LinkedIn returned {r.status_code}"
    if r.status_code >= 400:
        return None, f"LinkedIn returned {r.status_code}"
    if "/jobs/view/" not in r.url and SEARCH_REDIRECT.search(r.url):
        return False, "LinkedIn redirected the posting to a jobs search page"
    if CLOSED.search(r.text):
        return False, "LinkedIn says no longer accepting applications"
    return None, "LinkedIn page loads but liveness is not assertable anonymously"


# How the posting is applied to, read off the public page. An OFFSITE posting
# sends the candidate to the employer's own site; an Easy Apply one keeps the
# application inside LinkedIn. Measured 2026-10-21 on his batch of 20 October:
# Google, Publicis, Innovamat and Nscale carry the offsite apply link, and hunter
# had told him all four were Easy Apply.
OFFSITE = "offsite"
EASY = "easy"
_OFFSITE = re.compile(r"apply-link-offsite")
_EASY = re.compile(r"apply-link-onsite|easy apply", re.I)
_JOB_NUMBER = re.compile(r"(\d{8,})")


def apply_mode_of(html: str) -> str | None:
    if _OFFSITE.search(html or ""):
        return OFFSITE
    if _EASY.search(html or ""):
        return EASY
    return None


def apply_mode(url: str, *, timeout: int = 35) -> str | None:
    """OFFSITE, EASY, or None when the page says neither or cannot be read."""
    m = _JOB_NUMBER.findall(url or "")
    if not m:
        return None
    try:
        r = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m[-1]}",
                         headers=UA, timeout=timeout)
    except requests.RequestException:
        return None
    return apply_mode_of(r.text) if r.status_code == 200 else None
