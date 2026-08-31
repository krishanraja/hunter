"""Ashby client. THE RULE, from the 2026-08-11 incident (three false
negatives): liveness and the JD come ONLY from the direct posting page's
window.__appData. The posting-api job-board list may be used for discovery,
never for liveness. If __appData is absent entirely, the posting is dead.
"""
from __future__ import annotations

import html as html_mod
import json
import re

import requests

from ..sources import RolePosting

UA = {"User-Agent": "Mozilla/5.0 (hunter)"}


def _strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html_mod.unescape(s)).strip()


def extract_app_data(page: str) -> dict | None:
    """Brace-balanced, string-aware extraction of window.__appData."""
    m = re.search(r"window\.__appData\s*=\s*\{", page)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(page)):
        c = page[i]
        if esc:
            esc = False
        elif in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(page[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def fetch_posting(slug: str, posting_id: str) -> tuple[bool, str, str]:
    """(live, jd_text, jd_url) from the direct posting page only."""
    url = f"https://jobs.ashbyhq.com/{slug}/{posting_id}"
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code >= 400:
        return False, "", url
    data = extract_app_data(r.text)
    if data is None:
        return False, "", url  # a JS shell with no appData is a dead posting
    posting = data.get("posting") or {}
    if not posting or posting.get("isListed") is False:
        return False, "", url
    return True, _strip_html(posting.get("descriptionHtml", "")), url


def board(slug: str) -> list[RolePosting]:
    """Discovery only. Never a liveness source; see the module rule."""
    r = requests.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        params={"includeCompensation": "true"}, headers=UA, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append(RolePosting(
            company=slug, title=j.get("title", ""),
            url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}",
            source=f"ashby:{slug}", location=j.get("location"),
            comp_text=(j.get("compensation") or {}).get("compensationTierSummary")
            if isinstance(j.get("compensation"), dict) else None,
            ats="ashby", ats_slug=slug, ats_posting_id=j.get("id"), raw=j))
    return out
