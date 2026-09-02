"""Workday CXS client. The hosted search endpoint ignores most filters, so
filtering is client-side by design; never trust its facets."""
from __future__ import annotations

import requests

from ..sources import RolePosting

UA = {"User-Agent": "Mozilla/5.0 (hunter)", "Content-Type": "application/json"}


def search(tenant: str, site: str, host: str, *, search_text: str = "",
           limit: int = 20, offset: int = 0) -> list[RolePosting]:
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    r = requests.post(url, headers=UA, timeout=30, json={
        "limit": limit, "offset": offset, "searchText": search_text,
        "appliedFacets": {}})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobPostings", []):
        path = j.get("externalPath", "")
        out.append(RolePosting(
            company=tenant, title=j.get("title", ""),
            url=f"https://{host}/en-US/{site}{path}",
            source=f"workday:{tenant}",
            location=j.get("locationsText"),
            posted_at=j.get("postedOn"),
            ats="workday", ats_slug=tenant, raw=j))
    return out


def fetch_posting(tenant: str, path: str) -> tuple[bool, str, str]:
    """(live, jd_text, jd_url) for one Workday posting.

    Workday splits a posting URL into tenant (in the host) and site plus
    external path. The CXS endpoint answers with the description; a 404 or an
    empty body is a dead posting, same rule as every other client here.
    """
    import re
    site, _, rest = path.partition("/job/")
    host = f"{tenant}.wd3.myworkdayjobs.com"
    url = f"https://{host}/en-US/{site}/job/{rest}"
    api = f"https://{host}/wday/cxs/{tenant}/{site}/job/{rest}"
    try:
        r = requests.get(api, headers=UA, timeout=30)
    except Exception:
        return False, "", url
    if r.status_code >= 400:
        return False, "", url
    info = (r.json() or {}).get("jobPostingInfo") or {}
    if not info or info.get("jobPostingId") is None:
        return False, "", url
    text = re.sub(r"<[^>]+>", " ", info.get("jobDescription", ""))
    return True, re.sub(r"\s+", " ", text).strip(), url
