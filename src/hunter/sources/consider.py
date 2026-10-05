"""Portfolio job boards on the Consider platform, read through their own API.

Krish asked on 2026-10-05 for jobs.sequoiacap.com to be searched alongside
the a16z list. portfolio.py had left Sequoia out on purpose: its board does
not run on Getro, where Accel, General Catalyst and Thrive live. It runs on
Consider, and a Consider board answers two POSTs that the page itself makes:

    /api-boards/search-companies   every portfolio company, with its own
                                   description, markets, offices, investors
    /api-boards/search-jobs        every open role, paged, with the apply
                                   link to the company's own board and, for
                                   most, a structured pay range

Measured on 2026-10-05: 255 companies and 10,298 open roles, read in 21
requests of 500. 547 carry a senior title. The apply link names a Greenhouse,
Ashby or Lever board for 123 of the 160 companies hiring, so this is the same
two things the a16z board is, a company list and an ATS finder, and also the
one thing that board is not: a complete job list, with pay.

Both calls want the CSRF token the page serialises, sent back as the
x-csrf-token header with the session cookie the page set. A board that stops
serialising a token raises, so a dead board is named in the run summary and
never passes for an empty portfolio.
"""
from __future__ import annotations

import re

import requests

from . import RolePosting

UA = {"User-Agent": "Mozilla/5.0 (compatible; hunter/1.0)"}

# firm -> (board host, the board id its own page sends). Verified against the
# live board on 2026-10-05. a16z's board is also Consider underneath but
# serves a different front end with no token on the page, so it keeps its own
# reader (sources/a16z.py).
BOARDS: dict[str, tuple[str, str]] = {
    "Sequoia Capital": ("https://jobs.sequoiacap.com", "sequoia-capital"),
}

TOKEN = re.compile(r'"csrfToken":"([^"]+)"')
PAGE = 500
MAX_PAGES = 60

# The board mixes funding stage and headcount band in one list. Only the
# funding stage is a stage, and only these three say which one; "Growth" is
# a band of rounds rather than a round, so it is kept as the board's word.
FUNDING = {"Seed funded": "seed", "Series A": "series_a", "Public": "public",
           "Growth": "growth"}
SYMBOL = {"USD": "$", "GBP": "£", "EUR": "€"}


class Board:
    """One authenticated session against one firm's board."""

    def __init__(self, firm: str, timeout: int = 60):
        if firm not in BOARDS:
            raise KeyError(f"no Consider board on record for {firm!r}")
        self.firm = firm
        self.host, self.board_id = BOARDS[firm]
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(UA)
        page = self.s.get(f"{self.host}/jobs", timeout=timeout)
        page.raise_for_status()
        m = TOKEN.search(page.text)
        if not m:
            raise RuntimeError(f"{firm} board page serialised no CSRF token")
        self.headers = {"Accept": "application/json", "x-csrf-token": m.group(1)}

    def _post(self, path: str, body: dict) -> dict:
        r = self.s.post(f"{self.host}/api-boards/{path}", json=body,
                        headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _paged(self, path: str, key: str, extra: dict) -> list[dict]:
        out, seq, total = [], None, None
        for _ in range(MAX_PAGES):
            meta = {"size": PAGE}
            if seq:
                meta["sequence"] = seq
            d = self._post(path, {"meta": meta, "query": {},
                                  "board": {"id": self.board_id, "isParent": True},
                                  **extra})
            got = d.get(key) or []
            if total is None:
                total = d.get("total")
            out.extend(got)
            seq = (d.get("meta") or {}).get("sequence")
            if not got or not seq or (total is not None and len(out) >= total):
                break
        return out

    def companies(self) -> list[dict]:
        return self._paged("search-companies", "companies", {})

    def jobs(self) -> list[dict]:
        return self._paged("search-jobs", "jobs", {"grouped": False})


# ---------- companies, in portfolio.py's row shape ----------

def company_row(c: dict, firm: str) -> dict:
    """A board company as the row portfolio.refresh writes. The other
    investors the board lists go into the description, because they are the
    company's backing in the board's own words."""
    stages = c.get("stages") or []
    funding = next((FUNDING[s] for s in stages if s in FUNDING), None)
    size = next((s for s in stages if "employees" in s), "")
    others = [p for p in (c.get("parents") or c.get("investors") or []) if p != firm]
    desc = " ".join((c.get("description") or "").split())
    extra = ((f" Size: {size}." if size else "")
             + (f" Also backed by {', '.join(others[:6])}." if others else ""))
    host, _ = BOARDS[firm]
    return {
        "name": c.get("name"), "slug": c.get("slug"), "domain": c.get("domain"),
        "description": (desc[:1800] + extra).strip(),
        "locations": c.get("officeLocations") or [],
        "stage": funding, "industryTags": c.get("markets") or [],
        "activeJobsCount": c.get("numJobs"),
        "firm": firm, "source_url": f"{host}/companies",
    }


def fetch_companies(firm: str) -> list[dict]:
    return [company_row(c, firm) for c in Board(firm).companies()
            if c.get("name") and c.get("slug")]


# ---------- jobs, as postings ----------

def comp_text(salary: dict | None) -> str | None:
    """A yearly range in the form the pay gate reads, or None. An hourly or
    weekly figure is left out rather than risk reading $70 an hour as a
    $70 salary, and a currency without a symbol hunter reads is written with
    its code only, so it can never pass for dollars."""
    s = salary or {}
    lo, hi = s.get("minValue"), s.get("maxValue")
    period = ((s.get("period") or {}).get("value") or "").lower()
    cur = ((s.get("currency") or {}).get("value") or "").upper()
    if period != "year" or not cur or not isinstance(lo, (int, float)) or lo <= 0:
        return None
    hi = hi if isinstance(hi, (int, float)) and hi >= lo else lo
    sym = SYMBOL.get(cur)
    if sym:
        return f"{sym}{int(lo):,} - {sym}{int(hi):,} {cur} a year"
    return f"{cur} {int(lo):,} - {int(hi):,} a year"


def location_of(job: dict) -> str:
    loc = "; ".join(str(x) for x in (job.get("locations") or []) if x)
    if job.get("remote") and "remote" not in loc.lower():
        loc = f"{loc} (Remote)" if loc else "Remote"
    return loc


def to_posting(job: dict, firm: str) -> RolePosting:
    """A board job as a RolePosting. The ATS key is filled by the caller
    through ats_key(url), as for the a16z board. `url` is the posting on the
    company's own board; `applyUrl` is the same link with a tracking tag."""
    return RolePosting(
        company=job.get("companyName") or "", title=job.get("title") or "",
        url=job.get("url") or job.get("applyUrl") or "",
        source=f"{firm} board", location=location_of(job) or None,
        posted_at=job.get("timeStamp") or None,
        comp_text=comp_text(job.get("salary")),
        raw={"isRemote": bool(job.get("remote")), "board": firm,
             "company_domain": job.get("companyDomain") or "",
             "company_slug": job.get("companySlug") or ""})


def fetch_jobs(firm: str) -> list[dict]:
    return Board(firm).jobs()
