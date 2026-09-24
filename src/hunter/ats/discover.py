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
import re

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


# A careers page almost always links straight at the board, which answers
# the question without guessing a slug at all. His Target Companies tab
# carries a careers URL for 50 of its 53 companies, and every one of those
# was being ignored while hunter guessed.
BOARD_LINK = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/(?!embed\b)([a-z0-9][a-z0-9_-]{2,40})|"
    r"greenhouse\.io/[^\s\"'<>]*[?&]for=([a-z0-9_-]{3,40})|"
    r"https?://jobs\.ashbyhq\.com/([a-z0-9][a-z0-9._-]{2,40})|"
    r"https?://(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9][a-z0-9-]{2,40})",
    re.I)

# Path segments that are part of an embed URL rather than a company's board
# name. Descript's careers page yielded the slug "embed", which probes a real
# and entirely unrelated Greenhouse board.
NOT_A_SLUG = {"embed", "job_board", "js", "jobs", "careers", "board"}

CAREERS_PATHS = ("", "/careers", "/jobs", "/company/careers", "/about/careers")


def from_careers_page(url: str, timeout: int = 15) -> tuple[str, str] | None:
    """(ats, slug) read off a careers page, rather than guessed from a name.

    Deterministic where slug guessing is a lottery: hunter had no mapping for
    32 of the 52 companies Krish named, and every one of them publishes a
    careers page that links at its own board.
    """
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    if not r.ok:
        return None
    for m in BOARD_LINK.finditer(r.text):
        gh, gh_embed, ashby, lever = m.group(1), m.group(2), m.group(3), m.group(4)
        if gh and gh.lower() not in NOT_A_SLUG:
            return "greenhouse", gh.lower()
        if gh_embed and gh_embed.lower() not in NOT_A_SLUG:
            return "greenhouse", gh_embed.lower()
        if ashby and ashby.lower() not in NOT_A_SLUG:
            return "ashby", ashby
        if lever and lever.lower() not in NOT_A_SLUG:
            return "lever", lever.lower()
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


# When a company that could not be read is worth trying again.
#
# A miss was written as null and never retried: no TTL, no staleness check, no
# invalidation anywhere. companyintel has exactly this rule and says why, and
# the board cache had nothing. So a company probed in a quiet week with no open
# jobs, a slug the 2 to 5 candidate spellings missed, or a careers page that
# happened to be down became a PERMANENT exclusion from the sweep. On
# 2026-09-24 fourteen of Krish's own target companies sat in that state,
# including Anysphere (Cursor), Beehiiv, Every and Lindy.
#
# Shorter than companyintel's 14 days because a board is one cheap request and
# a company that starts hiring is exactly what hunter exists to notice.
RETRY_MISS_DAYS = 7


def _miss_is_stale(entry, *, now=None) -> bool:
    """A recorded miss old enough to be worth one more probe."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    # null is the old shape, written before misses carried a date. It has been
    # in the cache for an unknown length of time, so it gets its retry now.
    if not isinstance(entry, dict):
        return True
    when = entry.get("missed_at") or ""
    if not when:
        return True
    return when < (now - timedelta(days=RETRY_MISS_DAYS)).isoformat()


def is_miss(entry) -> bool:
    """A cache entry that records failure rather than a board."""
    return not (isinstance(entry, dict) and entry.get("ats") and entry.get("slug"))


def discover(cfg: Config, company: str, cache: dict | None = None,
             careers_url: str = "") -> tuple[str, str] | None:
    """(ats, slug) for a company, from cache, its careers page, or by probing.

    None means the company boards somewhere hunter cannot read, which is not
    the same as the role being dead and is never reported as such.
    """
    from datetime import datetime, timezone
    key = slugify(company)
    if cache is not None and key in cache:
        hit = cache[key]
        if not is_miss(hit):
            return (hit["ats"], hit["slug"])
        if not _miss_is_stale(hit):
            return None
        # Stale miss: fall through and probe again.
    found = None
    # The careers page first, because it is an answer rather than a guess.
    if careers_url:
        root = "/".join(careers_url.split("/")[:3])
        for path in CAREERS_PATHS:
            found = from_careers_page(careers_url if not path else root + path)
            if found:
                break
    if not found:
        for slug in slug_candidates(company):
            hit = probe(slug)
            if hit:
                found = (hit[0], slug)
                break
    if cache is not None:
        cache[key] = ({"ats": found[0], "slug": found[1]} if found else
                      {"missed_at": datetime.now(timezone.utc).isoformat()})
    return found
