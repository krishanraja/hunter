"""Where the sourcing is actually looking, and what that costs.

Run 126 sourced 567 roles and G6 deleted 253 of them, 42 percent of the
LinkedIn leg and 52 percent of the boards. The obvious reading was that
hunter was sweeping foreign postings, so the first fix proposed was a
location filter at fetch time using gates.names_foreign_geo.

Measured against all 567 of those locations, that filter drops SIX: three
Singapore, two Dubai, one Munich. Every other role G6 deleted was in the
United States, in a city that is neither New York nor remote. San Francisco
alone was 35 of them, Santa Clara 10, and the New Jersey and Connecticut
commuter belt around 30.

The aggressive version of that filter, refusing any location that does not
satisfy GEO_ALLOW on its own, would have saved 253 fetches and dropped a San
Francisco Bay Area role that REACHED HIM, because G6 reads the job
description alongside the location and the description rescued it. A bare
"United States" is not evidence of being out of geography: 23 of the 68
roles carrying it passed G6 on the strength of their own text. Blocking on
that is blocking on no evidence, so neither filter is here.

What IS true is that nothing shows him where his searches are pointed. The
searches are free text in the Role Targeting tab, passed verbatim to Apify,
and there is no geography handling in code at all. This module reads them
back and says which ones name a location, and it counts what the gate threw
away by location, so the two can be read side by side.

Nothing here filters anything. It reports.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# The parameters LinkedIn uses to scope a job search by place. geoId is the
# canonical one its own UI writes; location is the human readable form that
# survives a copied URL.
GEO_PARAMS = ("geoId", "location")
# Not geography, but it decides whether an out-of-town posting is reachable
# at all, so it belongs in the same glance. 1 on-site, 2 remote, 3 hybrid.
WORK_TYPE = "f_WT"
WORK_TYPE_NAMES = {"1": "on-site", "2": "remote", "3": "hybrid"}


@dataclass(frozen=True)
class SearchGeo:
    """One search URL, and whether it says where."""
    url: str
    geo: str          # the location it names, or "" for none
    param: str        # which parameter carried it
    work_type: str    # the f_WT values it asks for, human readable
    keywords: str

    @property
    def scoped(self) -> bool:
        return bool(self.geo)


def read_search(url: str) -> SearchGeo:
    """What one LinkedIn search asks for. Pure, and never raises: a URL he
    typed by hand is still a URL hunter has to run."""
    try:
        q = parse_qs(urlparse(url).query)
    except Exception:
        q = {}

    def one(name: str) -> str:
        vals = [v.strip() for v in q.get(name, []) if v and v.strip()]
        return vals[0] if vals else ""

    geo, param = "", ""
    for name in GEO_PARAMS:
        val = one(name)
        if val:
            # A geoId is an opaque number. It is reported verbatim rather
            # than translated, because a lookup table written from memory
            # would put a confident wrong city next to his search.
            geo, param = val, name
            break
    wt = ", ".join(WORK_TYPE_NAMES.get(v, v)
                   for v in (one(WORK_TYPE).split(",") if one(WORK_TYPE) else []))
    return SearchGeo(url=url, geo=geo, param=param, work_type=wt,
                     keywords=one("keywords"))


def search_lines(urls: list[str]) -> list[str]:
    """The searches, and which of them say where to look."""
    if not urls:
        return ["no LinkedIn searches found on the Role Targeting tab"]
    reads = [read_search(u) for u in urls]
    loose = [r for r in reads if not r.scoped]
    out = [f"{len(reads)} LinkedIn search(es); "
           f"{len(reads) - len(loose)} name a location, {len(loose)} do not:"]
    for r in reads:
        where = f"{r.param}={r.geo}" if r.scoped else "NO LOCATION"
        extra = f", {r.work_type}" if r.work_type else ""
        out.append(f"  {where:34} {(r.keywords or '(no keywords)')[:44]}{extra}")
    if loose:
        out.append(f"  a search with no location returns whatever LinkedIn "
                   f"ranks highest, and hunter pays to fetch and score all of "
                   f"it before G6 deletes what is out of geography.")
    return out


def deleted_by_location(rows, *, leg_of, top: int = 12) -> list[tuple[str, str, int, int]]:
    """(leg, location, deleted at G6, seen) for the places costing the most.

    Sorted by what was thrown away, because that is the number an edit to a
    search can recover. A location hunter saw once and kept is not news.
    """
    seen: collections.Counter = collections.Counter()
    gone: collections.Counter = collections.Counter()
    for r in rows:
        loc = (r.get("location") or "").strip()
        if not loc:
            continue
        key = (leg_of(r.get("source") or ""), loc)
        seen[key] += 1
        if _died_at_geography(r.get("rejection_reason") or ""):
            gone[key] += 1
    ranked = sorted(gone.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(leg, loc, n, seen[(leg, loc)]) for (leg, loc), n in ranked[:top]]


# G6 is named in the reason hunter records, and the gate's own wording is the
# fallback for rows written before the code numbered it.
_GEO_REASONS = ("g6", "london, uk-remote")


def _died_at_geography(reason: str) -> bool:
    low = reason.lower()
    return any(tok in low for tok in _GEO_REASONS)


def deleted_lines(rows, *, leg_of, top: int = 12) -> list[str]:
    """What geography cost, by place, in the roles hunter already paid for."""
    ranked = deleted_by_location(rows, leg_of=leg_of, top=top)
    if not ranked:
        return ["no roles were deleted for geography in this window"]
    total = sum(1 for r in rows
                if _died_at_geography(r.get("rejection_reason") or ""))
    out = [f"{total} role(s) fetched, scored and then deleted for geography. "
           f"The places that cost the most:"]
    for leg, loc, n, of in ranked:
        out.append(f"  {n:>4} of {of:<4} {leg:16} {loc[:44]}")
    return out
