"""Greenhouse board API client. The public boards API returns full JDs with
content=true; liveness is the job id being present on the board."""
from __future__ import annotations

import html as html_mod
import re

import requests

from ..sources import RolePosting

API = "https://boards-api.greenhouse.io/v1/boards"


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html_mod.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def board(slug: str) -> list[RolePosting]:
    r = requests.get(f"{API}/{slug}/jobs", params={"content": "true"}, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append(RolePosting(
            company=slug, title=j.get("title", ""),
            url=j.get("absolute_url", ""),
            source=f"greenhouse:{slug}",
            location=(j.get("location") or {}).get("name"),
            posted_at=j.get("updated_at"),
            ats="greenhouse", ats_slug=slug,
            ats_posting_id=str(j.get("id")), raw={"id": j.get("id")}))
    return out


def fetch_posting(slug: str, job_id: str) -> tuple[bool, str, str]:
    url = f"{API}/{slug}/jobs/{job_id}"
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return False, "", url
    r.raise_for_status()
    j = r.json()
    return True, _strip_html(j.get("content", "")), j.get("absolute_url", url)
