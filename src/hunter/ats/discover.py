"""Find a company's job board when canon does not name it.

Ten Pipeline rows carried a LinkedIn or careers-page URL and none of their
companies was in ATS_MAP, so hunter could not tell whether the role was live.
Unverifiable looked exactly like live on the sheet, which is how a role Krish
said go to sat there after the posting had gone.

The three boards answer an unauthenticated GET, so the cheapest honest fix is
to ask them. Slug candidates come from the company name; "higgsfieldai" and
"thetradedesk" are why the variants exist rather than just slugify.

A discovery is cached in system_config so the probing happens once per
company, and a company that answers nowhere stays unverifiable rather than
being guessed at.
"""
from __future__ import annotations

import json

import requests

from ..config import Config, db_insert
from ..sources import slugify

CACHE_KEY = "hunter_discovered_ats"
UA = {"User-Agent": "Mozilla/5.0 (compatible; hunter/1.0)"}

BOARDS = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", "jobs"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{slug}", "jobs"),
    ("lever", "https://api.lever.co/v0/postings/{slug}?mode=json", None),
]


def slug_candidates(company: str) -> list[str]:
    """Slugs worth trying, most likely first.

    Real misses drove each variant: Higgsfield AI boards as "higgsfieldai",
    The Trade Desk as "thetradedesk".
    """
    base = slugify(company)
    if not base:
        return []
    parts = [p for p in base.split("-") if p]
    out = [base, base.replace("-", "")]
    if parts and parts[-1] in ("ai", "labs", "inc", "io"):
        trimmed = "-".join(parts[:-1])
        out += [trimmed, trimmed.replace("-", "")]
    if parts and parts[0] == "the":
        out.append("-".join(parts[1:]))
    if len(parts) > 1 and parts[0] not in ("the", "a"):
        out.append(parts[0])
    seen, uniq = set(), []
    for c in out:
        # "the" is not a company. A candidate that short would match some
        # unrelated board and report a role live that hunter never saw.
        if c and len(c) >= 4 and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def probe(slug: str, timeout: int = 12) -> tuple[str, int] | None:
    """(ats, job count) for the first board that answers with postings."""
    for ats, url, key in BOARDS:
        try:
            r = requests.get(url.format(slug=slug), headers=UA, timeout=timeout)
            if not r.ok:
                continue
            body = r.json()
            jobs = body if key is None else (body or {}).get(key) or []
            if isinstance(jobs, list) and jobs:
                return ats, len(jobs)
        except Exception:
            continue
    return None


def load_cache(cfg: Config) -> dict:
    raw = cfg.optional(CACHE_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cfg: Config, cache: dict) -> None:
    db_insert(cfg, "system_config",
              [{"key": CACHE_KEY, "value": json.dumps(cache, indent=2, sort_keys=True)}],
              on_conflict="key", merge=True)


def discover(cfg: Config, company: str, cache: dict | None = None
             ) -> tuple[str, str] | None:
    """(ats, slug) for a company, from cache or by probing. None means the
    company boards somewhere hunter cannot read, which is not the same as
    the role being dead and is never reported as such."""
    key = slugify(company)
    if cache is not None and key in cache:
        hit = cache[key]
        return (hit["ats"], hit["slug"]) if hit else None
    found = None
    for slug in slug_candidates(company):
        hit = probe(slug)
        if hit:
            found = (hit[0], slug)
            break
    if cache is not None:
        cache[key] = {"ats": found[0], "slug": found[1]} if found else None
    return found
