"""VC portfolio job boards, as a source of company FACTS.

a16z was wired first (sources/a16z.py) and is a good company list. This is
the same idea generalised, and it earns its place for a different reason.

The component the company scorer most often cannot establish is backing: a
homepage does not say who led the Series B, so hundreds of companies score
6.7 when they would score 8.3 if hunter simply knew who was behind them. A
firm's own portfolio board answers that by definition, and the boards on the
Getro platform, which several firms use, serialise far more than the name:

    {"name": "1Password", "slug": "1password", "domain": "1password.com",
     "description": "1Password is a secure password manager that ...",
     "headCount": 5, "locations": [...], "stage": "series_c",
     "industryTags": [...], "activeJobsCount": 62}

That is a description, a headcount, a location set and a stage, for every
company in the portfolio, in one request, cited to a URL. It fills four of
the five scoring components for companies whose own sites refuse hunter.

Only firms verified by hand go in FIRMS. A page that stops serialising its
companies yields nothing and says so, rather than being guessed at.
"""
from __future__ import annotations

import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; hunter/1.0)"}

# Verified against the live boards on 2026-09-20. Each one was checked to
# answer with its whole portfolio; firms whose boards do not run on this
# platform (Sequoia, Lightspeed, Bessemer, Kleiner Perkins) are deliberately
# absent rather than listed and silently returning nothing.
FIRMS: dict[str, str] = {
    "Accel": "https://jobs.accel.com/companies",
    "General Catalyst": "https://jobs.generalcatalyst.com/companies",
    "Thrive Capital": "https://jobs.thrivecap.com/companies",
}

# The page itself serialises only its first screen of companies, which is 13
# of Accel's 579. The board's own API answers with all of them, and the
# collection id it wants is the network id embedded in the page.
API = "https://api.getro.com/api/v2/collections/{cid}/search/companies"
NETWORK_ID = re.compile(r'"network"\s*:\s*\{\s*"id"\s*:\s*"?(\d+)"?')
# The board answers with a fixed page of twelve whatever per_page asks for,
# and reports the real total separately. Treating a short page as the last
# page stopped the whole sweep after the first twelve of Accel's 579.
PER_PAGE = 100
MAX_PAGES = 80

# The shape the platform serialises. Anchored on slug because it is the one
# field always present and always lowercase, so it cannot match prose.
ANCHOR = re.compile(r'"slug":"[a-z0-9][a-z0-9-]{1,48}"')
# headCount is deliberately absent. The platform reports it as a BAND INDEX,
# not a number of people: 1Password, which has well over a thousand
# employees, comes back as 5. Reading it as a headcount would have put a
# confident wrong fact on his sheet, which is worse than the unknown it
# replaced.
WANTED = ("name", "slug", "domain", "description", "locations",
          "stage", "industryTags", "activeJobsCount")


def _object_around(text: str, index: int) -> dict | None:
    """The JSON object containing this position, by matching braces.

    A regex cannot do this: descriptions contain braces and the payload is
    one long line. Scanning outward from a field that is definitely inside
    the object is both simpler and harder to fool.
    """
    start = text.rfind('{"id":', 0, index)
    if start < 0:
        return None
    depth, j, in_string, escaped = 0, start, False, False
    while j < len(text):
        ch = text[j]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        j += 1
    if depth != 0:
        return None
    try:
        return json.loads(text[start:j + 1])
    except json.JSONDecodeError:
        return None


def parse(body: str) -> list[dict]:
    """Every portfolio company serialised in the page."""
    out, seen = [], set()
    for m in ANCHOR.finditer(body):
        obj = _object_around(body, m.start())
        if not obj or not obj.get("slug") or not obj.get("name"):
            continue
        slug = obj["slug"]
        if slug in seen:
            continue
        seen.add(slug)
        out.append({k: obj.get(k) for k in WANTED})
    return out


def network_id(body: str) -> str:
    m = NETWORK_ID.search(body)
    return m.group(1) if m else ""


def fetch(firm: str, timeout: int = 25) -> list[dict]:
    """Every company in a firm's portfolio, with its facts.

    Falls back to whatever the page itself serialised if the API stops
    answering, because 13 companies with facts is worth more than an
    exception, and the count is reported either way so a fallback cannot
    pass for the whole portfolio.
    """
    url = FIRMS.get(firm)
    if not url:
        raise KeyError(f"no portfolio board on record for {firm!r}")
    page = requests.get(url, headers=UA, timeout=timeout)
    page.raise_for_status()
    cid = network_id(page.text)
    rows: list[dict] = []
    if cid:
        headers = dict(UA)
        headers.update({"Content-Type": "application/json",
                        "Accept": "application/json"})
        seen = set()
        total = None
        for n in range(MAX_PAGES):
            r = requests.post(API.format(cid=cid), headers=headers,
                              timeout=timeout,
                              json={"page": n, "per_page": PER_PAGE})
            if not r.ok:
                break
            results = r.json().get("results") or {}
            if total is None:
                total = results.get("count")
            got = results.get("companies") or []
            if not got:
                break
            for c in got:
                slug = c.get("slug")
                if not slug or slug in seen or not c.get("name"):
                    continue
                seen.add(slug)
                rows.append({
                    "name": c.get("name"), "slug": slug,
                    "domain": c.get("domain"),
                    "description": c.get("description"),
                    "locations": c.get("locations"),
                    "stage": c.get("stage"),
                    "industryTags": c.get("industry_tags"),
                    "activeJobsCount": c.get("active_jobs_count"),
                })
            if total is not None and len(seen) >= total:
                break
    if not rows:
        rows = parse(page.text)
    for row in rows:
        row["firm"] = firm
        row["source_url"] = url
    return rows


def fetch_all(timeout: int = 25) -> tuple[list[dict], list[str]]:
    """(companies, notes). A firm that fails is named, never silently absent."""
    out, notes = [], []
    for firm in FIRMS:
        try:
            rows = fetch(firm, timeout=timeout)
        except Exception as e:
            notes.append(f"{firm} portfolio unavailable: {e.__class__.__name__}")
            continue
        if not rows:
            notes.append(f"{firm} portfolio page serialised no companies")
            continue
        out.extend(rows)
        notes.append(f"{firm}: {len(rows)} portfolio companies")
    return out, notes


# ---------- turning a portfolio row into the scorer's facts ----------

# The platform's own stage vocabulary, in the words company.py already reads.
STAGES = {
    "pre_seed": "Pre-seed", "seed": "Seed", "series_a": "Series A",
    "series_b": "Series B", "series_c": "Series C", "series_d": "Series D",
    "series_e": "Series E", "series_f": "Series F",
    "public": "publicly traded", "acquired": "acquired",
}


def to_facts(row: dict):
    """Facts for company.py, every one cited to the board it was read on."""
    from ..company import Fact

    src = row.get("source_url") or "portfolio board"
    firm = row.get("firm") or "a venture firm"
    out: dict = {}
    desc = (row.get("description") or "").strip()
    if len(desc) >= 25:
        tags = ", ".join(row.get("industryTags") or [])
        out["what_it_does"] = Fact((desc + (f" Tags: {tags}" if tags else ""))[:600], src)
    out["investors"] = Fact(f"in the {firm} portfolio", src)
    stage = STAGES.get((row.get("stage") or "").strip())
    if stage:
        out["stage"] = Fact(stage, src)
    locs = row.get("locations") or []
    if locs:
        out["locations"] = Fact("offices in " + "; ".join(str(x) for x in locs[:8])[:300], src)
    return out


# ---------- keeping it, so a run pays for the sweep once ----------

TABLE = "hunter_portfolio_companies"


def refresh(cfg) -> tuple[int, list[str]]:
    """Sweep every firm and write what came back. (count, notes)."""
    from ..config import db_insert
    from . import company_key

    rows, notes = fetch_all()
    out, seen = [], set()
    for r in rows:
        key = company_key(r.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "key": key, "name": r["name"], "firm": r.get("firm"),
            "slug": r.get("slug"), "domain": r.get("domain"),
            "description": (r.get("description") or "")[:2000],
            "locations": r.get("locations") or [],
            "stage": r.get("stage"), "industry_tags": r.get("industryTags") or [],
            "active_jobs_count": r.get("activeJobsCount"),
            "source_url": r.get("source_url"),
        })
    if out:
        db_insert(cfg, TABLE, out, on_conflict="key", merge=True)
    return len(out), notes


# A portfolio changes slowly, and a firm's board is one request per page.
# Weekly keeps the facts current without spending a Sunday morning on it.
REFRESH_DAYS = 7


def refresh_if_stale(cfg, *, days: int = REFRESH_DAYS) -> list[str]:
    """Sweep the boards only when what hunter holds has aged out.

    Returns lines for the run summary, empty when nothing was needed. Never
    raises: a firm being down is a reason to use last week's facts, not a
    reason to lose the sourcing run.
    """
    from datetime import datetime, timedelta, timezone
    from ..config import ALL_ROWS, db_get
    try:
        rows = db_get(cfg, TABLE, {"select": "last_seen", "limit": ALL_ROWS})
    except Exception as e:
        return [f"portfolio cache unreadable: {e.__class__.__name__}"]
    newest = max((r.get("last_seen") or "" for r in rows), default="")
    if newest:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        if newest > cutoff:
            return []
    try:
        n, notes = refresh(cfg)
    except Exception as e:
        return [f"portfolio refresh failed, using what hunter already holds: "
                f"{e.__class__.__name__}"]
    return notes + [f"portfolio facts refreshed: {n} companies"]


def load(cfg) -> dict[str, dict]:
    """key -> portfolio row, for the scorer to read before it fetches anything."""
    from ..config import ALL_ROWS, db_get
    try:
        rows = db_get(cfg, TABLE,
                      {"select": "key,name,firm,slug,domain,description,"
                                 "locations,stage,industry_tags,active_jobs_count,"
                                 "source_url", "limit": ALL_ROWS})
    except Exception:
        return {}
    return {r["key"]: r for r in rows}


def row_to_facts(row: dict):
    """A stored row in the shape to_facts expects."""
    return to_facts({
        "name": row.get("name"), "firm": row.get("firm"),
        "description": row.get("description"),
        "locations": row.get("locations") or [],
        "stage": row.get("stage"),
        "industryTags": row.get("industry_tags") or [],
        "source_url": row.get("source_url"),
    })
