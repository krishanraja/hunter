"""Workday CXS client. The hosted search endpoint ignores most filters, so
filtering is client-side by design; never trust its facets."""
from __future__ import annotations

import re

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


# The data centre number in a tenant's host. It was hard-coded to wd3, so a
# tenant on wd1 or wd5 (Sonos is on wd1) answered 404 and its live posting was
# read as dead. The host is taken from the link wherever there is one; with
# only a tenant, each data centre is asked in turn and the first that knows
# the tenant answers.
DATA_CENTRES = ("wd1", "wd3", "wd5", "wd2", "wd12", "wd10", "wd103", "wd108", "wd501")
HOST = re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", re.I)


def host_of(url: str) -> str:
    """The tenant's own host in a Workday link, or ""."""
    m = HOST.search(url or "")
    return f"{m.group(1).lower()}.{m.group(2).lower()}.myworkdayjobs.com" if m else ""


def clean_path(path: str) -> str:
    """site/job/... without a query, a fragment or a trailing /apply."""
    path = re.split(r"[?#]", path or "", maxsplit=1)[0].strip("/")
    return re.sub(r"/apply(?:/.*)?$", "", path)


def fetch_posting(tenant: str, path: str, *, host: str = "") -> tuple[bool, str, str]:
    """(live, jd_text, jd_url) for one Workday posting.

    Workday splits a posting URL into tenant (in the host) and site plus
    external path. The CXS endpoint answers with the description; a 404 or an
    empty body is a dead posting, same rule as every other client here.
    `tenant` may carry its data centre ("sonos.wd1"), or `host` may name it.
    """
    path = clean_path(path)
    site, _, rest = path.partition("/job/")
    if not host and "." in tenant:
        name, _, dc = tenant.partition(".")
        host = f"{name}.{dc.split('.')[0]}.myworkdayjobs.com"
    tenant = tenant.split(".")[0]
    hosts = [host] if host else [f"{tenant}.{dc}.myworkdayjobs.com" for dc in DATA_CENTRES]
    first_url = ""
    failure: Exception | None = None
    answered = False
    for h in hosts:
        url = f"https://{h}/en-US/{site}/job/{rest}"
        first_url = first_url or url
        api = f"https://{h}/wday/cxs/{tenant}/{site}/job/{rest}"
        try:
            r = requests.get(api, headers=UA, timeout=30)
        except requests.RequestException as e:
            failure = e
            continue
        answered = True
        if r.status_code >= 400:
            continue
        try:
            info = (r.json() or {}).get("jobPostingInfo") or {}
        except ValueError:
            continue
        if not info or info.get("jobPostingId") is None:
            continue
        text = re.sub(r"<[^>]+>", " ", info.get("jobDescription", ""))
        return True, re.sub(r"\s+", " ", text).strip(), url
    # No host answered at all: that is not knowing, never a dead posting.
    # fetch_with_retry tries once more and then the caller records it
    # unverified.
    if not answered and failure is not None:
        raise failure
    return False, "", first_url
