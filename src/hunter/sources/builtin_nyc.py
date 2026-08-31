"""BuiltInNYC listing sweep. Discovery only: every hit must still resolve to
a live ATS JD before it is recorded anywhere. The five configured filters
live in the workbook Role Targeting tab and are passed in by the caller."""
from __future__ import annotations

import html as html_mod
import re

import requests

from ..sources import RolePosting

UA = {"User-Agent": "Mozilla/5.0 (hunter)"}

DEFAULT_FILTERS = [
    "https://www.builtinnyc.com/jobs?type=full-time&level=executive,senior-level"
    "&job-categories=operations-and-strategy,sales,marketing&search=strategy",
    "https://www.builtinnyc.com/jobs?search=VP+Strategy",
    "https://www.builtinnyc.com/jobs?search=Chief+Commercial+Officer",
    "https://www.builtinnyc.com/jobs?search=Chief+Strategy+Officer",
    "https://www.builtinnyc.com/jobs?search=Corporate+Development",
]

JOB_LINK = re.compile(
    r'href="(/job/[^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{4,120})<', re.S)


def sweep(filters: list[str] | None = None) -> list[RolePosting]:
    out: list[RolePosting] = []
    seen: set[str] = set()
    for url in (filters or DEFAULT_FILTERS):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
        except Exception:
            continue  # a single filter failing must not sink the sweep; reported upstream by count
        for path, title in JOB_LINK.findall(r.text):
            link = "https://www.builtinnyc.com" + path.split("?")[0]
            if link in seen:
                continue
            seen.add(link)
            out.append(RolePosting(
                company="", title=html_mod.unescape(title).strip(),
                url=link, source="builtin_nyc", raw={"filter": url}))
    return out
