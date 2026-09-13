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
