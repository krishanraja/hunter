"""Lever postings API client."""
from __future__ import annotations

import requests

from ..sources import RolePosting

API = "https://api.lever.co/v0/postings"


def board(slug: str) -> list[RolePosting]:
    r = requests.get(f"{API}/{slug}", params={"mode": "json"}, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json():
        out.append(RolePosting(
            company=slug, title=j.get("text", ""),
            url=j.get("hostedUrl", ""),
            source=f"lever:{slug}",
            location=(j.get("categories") or {}).get("location"),
            ats="lever", ats_slug=slug, ats_posting_id=j.get("id"), raw={}))
    return out


def fetch_posting(slug: str, posting_id: str) -> tuple[bool, str, str]:
    url = f"{API}/{slug}/{posting_id}"
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return False, "", url
    r.raise_for_status()
    j = r.json()
    text = j.get("descriptionPlain", "") + "\n" + "\n".join(
        item.get("text", "") + " " + item.get("content", "")
        for item in j.get("lists", []))
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    return True, re.sub(r"\s+", " ", text).strip(), j.get("hostedUrl", url)
