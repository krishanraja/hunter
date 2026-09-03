"""jobs.a16z.com, the a16z portfolio job board, as a source.

Krish asked for it on 2026-09-03. Two things it is good for, and one it is
not.

It is a strong COMPANY list: the companies index serialises the whole
portfolio (851 companies on the day this was written) with market, stage,
headcount band and open job count. That maps straight onto canon 9.1's "any
AI-native Series B+ over floor" clause.

It is a strong ATS FINDER: a posting's apply_url points at the company's own
Greenhouse, Ashby, Lever or Workday board, which hunter can already read in
full. So the board seeds the sweep set, and the ATS clients do the exhaustive
listing, newest and all.

It is a poor JOB LIST on its own: a page renders 25 relevance-sorted postings
per query with no paging and no discoverable API. Those 25 are still fed
through the ordinary gates, but nobody should mistake them for coverage.

The page is a Next.js render, so the data hunter wants sits in the HTML as a
JSON string with escaped quotes. The parsers below read that form directly
and are tested against a captured page, never against a guess.
"""
from __future__ import annotations

import json
import re

import requests

from . import RolePosting

BASE = "https://jobs.a16z.com"
UA = {"User-Agent": "Mozilla/5.0 (compatible; hunter/1.0)"}

# One query per canon section 5 family, in the words postings use. Spaces
# must be %20: the site treats a plus literally and returns nothing.
FAMILY_QUERIES = [
    "chief of staff", "general manager", "managing director", "country manager",
    "head of gtm", "go to market strategy", "gtm strategy and operations",
    "corporate development", "head of strategy", "vp strategy",
    "partnerships", "chief commercial officer", "chief revenue officer",
    "head of commercial", "revenue strategy",
]

# Canon section 5 excludes defence outright. The index does not label it
# reliably (Anduril's market is "AI"; its description says "defense
# technology company"), so both the market labels and the description are
# read. Every other exclusion there is judged on the posting by G7.
EXCLUDED_MARKETS = {"defense", "defence"}
# Phrases that name the business, not the bare word: "defense-in-depth" is
# email security and "network defenses" is a cyber product, and a bare
# "military" caught a founder's bio. Anduril, ZeroMark, Chariot Defense,
# Galadyne and Swan all say what they are in one of these forms.
DEFENCE = re.compile(
    r"\bdefen[cs]e[\s-]+(?:technology|tech|contractor|industry|sector|company|"
    r"startup|prime|and industrial|customers?|market)\b|"
    r"\b(?:military|warfighter|missile|munition|firearms?|weapons?|"
    r"national security)\b[\s\w,'\u2019]{0,40}?\b(?:capabilit|customer|operator|"
    r"hardware|logistic|architecture|system|platform|product|market|technolog)|"
    r"\bsystems? (?:america|the (?:us|dod|pentagon)) (?:and its partners )?needs?\b",
    re.I)

_OBJ = re.compile(r'\{\\"id\\":\\"consider:')


def _field(obj: str, key: str) -> str | None:
    m = re.search(r'\\"' + re.escape(key) + r'\\":\\"((?:[^\\]|\\\\.)*?)\\"', obj)
    if not m:
        return None
    return m.group(1).replace("\\\\/", "/").replace("\\\\u0026", "&")


def _bool(obj: str, key: str) -> bool | None:
    m = re.search(r'\\"' + re.escape(key) + r'\\":(true|false)', obj)
    return None if not m else m.group(1) == "true"


def parse_jobs(page_html: str) -> list[dict]:
    """Every serialised job object on a board page, as plain dicts."""
    out: list[dict] = []
    parts = _OBJ.split(page_html)[1:]
    for part in parts:
        obj = part[:4000]
        title = _field(obj, "title")
        if not title:
            continue
        out.append({
            "title": title,
            "apply_url": _field(obj, "apply_url") or "",
            "location": _field(obj, "location") or "",
            "remote": _bool(obj, "remote"),
            "posted_at": _field(obj, "posted_at") or "",
            "company": _field(obj, "company_name") or "",
            "company_slug": _field(obj, "company_slug") or "",
            "stage": _field(obj, "company_stage") or "",
        })
    return out


def parse_companies(index_html: str) -> list[dict]:
    """The portfolio, from the companies index. The index is a JSON array in
    the page; slicing from its start and reading objects one at a time is
    more robust than trusting the surrounding markup."""
    i = index_html.find('\\"companies\\":[')
    if i < 0:
        return []
    raw = index_html[i:]
    out: list[dict] = []
    for m in re.finditer(r'\{\\"id\\":\\"[^\\]+\\",\\"slug\\":\\"([^\\]+)\\"', raw):
        obj = raw[m.start():m.start() + 2500]
        markets_m = re.search(r'\\"markets\\":\[((?:\\"[^\\]*\\",?)*)\]', obj)
        markets = re.findall(r'\\"([^\\]+)\\"', markets_m.group(1)) if markets_m else []
        jc = re.search(r'\\"jobCount\\":(\d+)', obj)
        out.append({
            "slug": m.group(1),
            "name": _field(obj, "name") or m.group(1),
            "domain": _field(obj, "domain"),
            "markets": markets,
            "stage": _field(obj, "stage"),
            "band": _field(obj, "employeeBand"),
            "job_count": int(jc.group(1)) if jc else None,
            "description": _field(obj, "description") or "",
        })
    return out


# A company that puts the word in its own name has settled the question.
DEFENCE_NAME = re.compile(r"\bdefen[cs]e\b", re.I)


def excluded(company: dict) -> bool:
    if any(m.lower() in EXCLUDED_MARKETS for m in company.get("markets") or []):
        return True
    if DEFENCE_NAME.search(company.get("name") or ""):
        return True
    return bool(DEFENCE.search(company.get("description") or ""))


def fetch_companies(timeout: int = 30) -> list[dict]:
    r = requests.get(f"{BASE}/companies", headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse_companies(r.text)


def fetch_family(query: str, timeout: int = 30) -> list[dict]:
    # Built by hand rather than with params=: requests encodes a space as a
    # plus, and the site treats a plus literally and returns nothing.
    r = requests.get(f"{BASE}/jobs?q={query.replace(' ', '%20')}",
                     headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse_jobs(r.text)


def to_posting(job: dict) -> RolePosting:
    """A board job as a RolePosting. The ATS key is filled by the caller
    through ats_key(apply_url), the same way a Greenhouse board posting gets
    its identity, so the ordinary resolve path applies."""
    return RolePosting(
        company=job["company"], title=job["title"], url=job["apply_url"],
        source="a16z board", location=job.get("location") or None,
        posted_at=job.get("posted_at") or None, raw=job)


def family_sweep(queries: list[str] | None = None) -> tuple[list[RolePosting], list[str]]:
    """Postings from every family query, and the queries that failed."""
    postings: list[RolePosting] = []
    failed: list[str] = []
    seen: set[str] = set()
    for q in queries or FAMILY_QUERIES:
        try:
            jobs = fetch_family(q)
        except Exception as e:
            failed.append(f"{q}: {e.__class__.__name__}")
            continue
        for j in jobs:
            key = j["apply_url"] or f"{j['company_slug']}:{j['title']}"
            if key in seen:
                continue
            seen.add(key)
            postings.append(to_posting(j))
    return postings, failed
