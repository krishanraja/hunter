"""The door in: for each company he would join, the leader who decides and the
warm way to reach them. docs/DOOR_IN.md is the thesis; this is its first
measurement, and later its leader cards.

His words, 2026-10-07: "my door in to the role I actually want is probably more
so getting to know a leader that runs a company I want to be a part of". Hunter
had only ever asked that question per POSTING (people/bridges.py), so a company
with no open seat had no leader, and a leader with no open seat had no route.

This module asks it per COMPANY, from the same graph bridges.py reads, with the
same employer match and the same test of who he can actually ask. It spends
nothing and writes nothing until land() is called with apply=True.

Two answers are kept apart because they fail differently:

  a LEADER is anyone whose title says they decide (WEDGE_LEADER), at that
  company now, whether or not he knows them. Naming one is not reaching one.

  a ROUTE is a person he knows (in_network): the leader themself, someone at
  the company now, or someone who worked there. A newsletter move is a TRIGGER,
  not a route: the person just joined and has never met him.

Nothing here is cold outbound. A company with a leader and no route is kept and
shown, ranked lower, never routed to a stranger (DOOR_IN.md section 6, rule 1).
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

from .people.bridges import (NEWSLETTER_WINDOW_DAYS, WEDGE_LEADER, WEDGE_SEAT,
                             contact_tier, in_network, same_employer, tier_words)
from .sources import distinctive_tokens, slugify
from . import verdicts

# The same floor bridges.build_bridges uses for a contact with no tier and no
# connection date. One number for one question across both modules.
MIN_STRENGTH = 25

# When the wedge was born (bridges.py, Krish's idea of 2026-09-15). The record
# is counted from here.
WEDGE_SINCE = "2026-09-15"

# Roles that are not live: nothing to apply to, nothing to trigger on.
DEAD_STATUSES = {"dead", "duplicate", "archived"}


_PAREN = re.compile(r"\(.*?\)")


def _toks(name: str) -> frozenset:
    return distinctive_tokens(_PAREN.sub(" ", name or ""))


def same_company(ours: str, theirs: str) -> bool:
    """Is a contact's employer string this company?

    Stricter than bridges.same_employer, because here a false match names a
    stranger as his way in. Measured on the live graph, 2026-10-08: one-word
    names in his top list matched "Harvey Norman (GP Advertising)" to Harvey,
    "Sphere Digital Recruitment" to Sphere, "ARTISAN - Creative and Digital
    Recruitment" to Artisan and "Nexus Adex" to Nexus, all by containment.

    So a one-word company matches only when the contact's employer reduces to
    the same word ("Reddit, Inc.", "Krea.Ai"), parentheses aside. A longer name
    keeps the containment rule bridges.py trusts. Two companies that share a
    whole name ("Braintrust", "ADA") cannot be told apart here at all, and the
    page says so rather than guessing.
    """
    a, b = _toks(ours), _toks(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) == 1 or len(b) == 1:
        return False
    return same_employer(a, b)


@dataclass
class Person:
    key: str
    name: str
    title: str
    company: str
    tier: str = ""
    strength: int = 0
    in_network: bool = False
    contact_id: str = ""
    words: str = ""


@dataclass
class Route:
    kind: str           # leader_direct | inside | alumni
    person: Person
    evidence: str


@dataclass
class Trigger:
    kind: str           # open_seat | newsletter_move | newsletter_hiring
    what: str
    url: str = ""
    when: str = ""


@dataclass
class Coverage:
    key: str
    name: str
    sources: list = field(default_factory=list)
    leaders: list = field(default_factory=list)      # [Person]
    routes: list = field(default_factory=list)       # [Route], best first
    triggers: list = field(default_factory=list)     # [Trigger]
    applied: bool = False
    founder_hint: str = ""

    @property
    def has_leader(self) -> bool:
        return bool(self.leaders)

    @property
    def has_route(self) -> bool:
        return bool(self.routes)

    @property
    def door(self) -> bool:
        """A named leader AND a warm route: the number Phase 1 exists to read."""
        return self.has_leader and self.has_route


def _person(c: dict) -> Person:
    return Person(
        key=c.get("contact_key") or f"contact:{c.get('id') or c.get('contact_id') or ''}",
        name=(c.get("full_name") or "").strip(),
        title=(c.get("current_title") or "").strip(),
        company=(c.get("current_company") or "").strip(),
        tier=contact_tier(c),
        strength=int(c.get("strength_score") or 0),
        in_network=in_network(c, MIN_STRENGTH),
        contact_id=str(c.get("contact_id") or ""),
        words=tier_words(c))


class Graph:
    """Contacts indexed by employer, built once and asked per company."""

    def __init__(self, contacts: list[dict]):
        self.contacts = contacts
        self.by_slug: dict[str, list[dict]] = {}
        for c in contacts:
            co = (c.get("current_company") or "").strip()
            if not co:
                continue
            self.by_slug.setdefault(slugify(co), []).append(c)

    def at(self, company: str) -> list[dict]:
        """Everyone whose current employer is this company: the exact slug,
        then the narrow same_employer fallback bridges.py already trusts."""
        out = []
        for k, rows in self.by_slug.items():
            if same_company(company, rows[0].get("current_company") or ""):
                out.extend(rows)
        return out

    def alumni_of(self, company: str) -> list[dict]:
        out = []
        for c in self.contacts:
            if same_company(company, c.get("current_company") or ""):
                continue
            for h in c.get("employment_history") or []:
                name = (h.get("companyName") or h.get("company") or "") if isinstance(h, dict) else ""
                if name and same_company(company, name):
                    out.append(c)
                    break
        return out


def is_leader(c: dict) -> bool:
    return bool(WEDGE_LEADER.search(c.get("current_title") or ""))


def _days_since(iso: str) -> int | None:
    try:
        return (datetime.date.today() - datetime.date.fromisoformat((iso or "")[:10])).days
    except ValueError:
        return None


def triggers_for(company: str, roles: list[dict], posts: list[dict]) -> tuple[list[Trigger], bool, str]:
    """(triggers, has a live application, founder named in a newsletter).

    A role counts as an application whether hunter recorded the state or he
    wrote "applied" in column A: either way the wedge rule says choose one road.
    """
    def same(name: str) -> bool:
        return bool(name) and same_company(company, name)

    out, applied, founder = [], False, ""
    for r in roles:
        if not same(r.get("company") or ""):
            continue
        if (r.get("application_state") or "").strip() \
                or verdicts.parse(r.get("krish_verdict") or "")[0] == "applied":
            applied = True
        if (r.get("status") or "") in DEAD_STATUSES:
            continue
        if WEDGE_SEAT.search(r.get("title") or ""):
            out.append(Trigger("open_seat", f"{r.get('title')} open",
                               r.get("url") or r.get("job_url") or "",
                               (r.get("scanned_at") or r.get("presented_at") or "")[:10]))
    for p in posts:
        days = _days_since(p.get("published_at") or "")
        if days is None or days > NEWSLETTER_WINDOW_DAYS:
            continue
        sig = p.get("signals") or {}
        for m in sig.get("talent_moves") or []:
            if same(m.get("company") or ""):
                out.append(Trigger("newsletter_move",
                                   f"{m.get('person') or 'someone'} joined as {m.get('title') or 'a hire'}",
                                   p.get("link") or "", (p.get("published_at") or "")[:10]))
        for h in sig.get("hiring") or []:
            if same(h.get("company") or ""):
                if h.get("founder") and not founder:
                    founder = h["founder"]
                out.append(Trigger("newsletter_hiring",
                                   "hiring: " + ", ".join(h.get("roles") or [])[:120],
                                   p.get("link") or "", (p.get("published_at") or "")[:10]))
    return out, applied, founder


def cover(company: dict, graph: Graph, roles: list[dict], posts: list[dict]) -> Coverage:
    name = company.get("name") or company.get("key") or ""
    cov = Coverage(key=company.get("key") or slugify(name), name=name,
                   sources=list(company.get("sources") or []))
    here = graph.at(name)
    leaders = sorted((c for c in here if is_leader(c)),
                     key=lambda c: (not in_network(c, MIN_STRENGTH), -(c.get("strength_score") or 0)))
    cov.leaders = [_person(c) for c in leaders]

    routes: list[Route] = []
    for p in cov.leaders:
        if p.in_network:
            routes.append(Route("leader_direct", p,
                                f"{p.name} is {p.title} and in his network"
                                + (f" ({p.words})" if p.words else "")))
    insiders = sorted((c for c in here if not is_leader(c) and in_network(c, MIN_STRENGTH)),
                      key=lambda c: -(c.get("strength_score") or 0))
    for c in insiders[:3]:
        p = _person(c)
        routes.append(Route("inside", p, f"{p.name} works there now as {p.title or 'staff'}"
                            + (f" ({p.words})" if p.words else "")))
    for c in graph.alumni_of(name):
        if in_network(c, MIN_STRENGTH):
            p = _person(c)
            routes.append(Route("alumni", p, f"{p.name} worked there before, now at {p.company}"
                                + (f" ({p.words})" if p.words else "")))
    cov.routes = routes
    cov.triggers, cov.applied, cov.founder_hint = triggers_for(name, roles, posts)
    return cov


def coverage(radar_rows: list[dict], contacts: list[dict], roles: list[dict],
             posts: list[dict]) -> list[Coverage]:
    """One Coverage per top company. radar_rows are hunter_company_radar rows;
    only top=true ones are read, because the Radar already folds in his list,
    his Target Companies tiers and his Yes companies."""
    graph = Graph(contacts)
    seen, out = set(), []
    for r in radar_rows:
        if not r.get("top"):
            continue
        k = r.get("key") or slugify(r.get("name") or "")
        if k in seen:
            continue
        seen.add(k)
        out.append(cover(r, graph, roles, posts))
    out.sort(key=lambda c: (not c.door, not c.has_route, not c.has_leader, c.name.lower()))
    return out


def wedge_record(bridge_rows: list[dict]) -> dict:
    """What the mindmake_wedge has produced since it was built. Proposals hunter
    stopped deriving were deleted (bridges.py supersede pass), so this sees only
    what is still live or what he touched, and says so."""
    rows = [b for b in bridge_rows if b.get("path_tier") == "mindmake_wedge"
            and (b.get("surfaced_at") or "")[:10] >= WEDGE_SINCE]
    by_state: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    for b in rows:
        by_state[b.get("state") or "unknown"] = by_state.get(b.get("state") or "unknown", 0) + 1
        if b.get("outcome"):
            outcomes[b["outcome"]] = outcomes.get(b["outcome"], 0) + 1
    return {"visible": len(rows), "by_state": by_state, "outcomes": outcomes,
            "sent": sum(n for s, n in by_state.items() if s not in ("proposed", "unknown")),
            "caveat": "untouched proposals hunter stopped deriving were deleted; "
                      "this counts only rows still live or ones he acted on"}


def summary(rows: list[Coverage]) -> dict:
    return {"companies": len(rows),
            "with_leader": sum(c.has_leader for c in rows),
            "with_route": sum(c.has_route for c in rows),
            "door": sum(c.door for c in rows),
            "leader_direct": sum(any(r.kind == "leader_direct" for r in c.routes) for c in rows),
            "with_trigger": sum(bool(c.triggers) for c in rows),
            "applied": sum(c.applied for c in rows)}


def to_json(rows: list[Coverage]) -> list[dict]:
    """Rows for the private page. Names travel here and only here: nothing
    produced by this function is written to the repository."""
    out = []
    for c in rows:
        out.append({
            "company": c.name, "key": c.key, "sources": c.sources,
            "door": c.door, "applied": c.applied, "founder_hint": c.founder_hint,
            "leaders": [{"name": p.name, "title": p.title, "in_network": p.in_network,
                         "tier": p.tier, "contact_id": p.contact_id} for p in c.leaders[:3]],
            "routes": [{"kind": r.kind, "evidence": r.evidence,
                        "contact_id": r.person.contact_id} for r in c.routes[:4]],
            "triggers": [t.__dict__ for t in c.triggers[:4]],
        })
    return out


ROLE_FIELDS = "job_id,company,title,url,job_url,status,scanned_at,presented_at,krish_verdict,application_state"
NC_FIELDS = ("contact_key,full_name,current_company,current_title,connected_on,"
             "strength_score,strength_evidence,employment_history,contact_id")


def load(cfg) -> dict:
    """Every input, read once. Control Center contacts that network_contacts
    does not already hold are added in the same shape, carrying contact_id,
    because a card can only land in pilot_deals against a contacts row."""
    from .config import ALL_ROWS, db_get
    radar = db_get(cfg, "hunter_company_radar", {
        "select": "key,name,sources,top,lookalike", "top": "eq.true", "limit": ALL_ROWS})
    nc = db_get(cfg, "network_contacts", {"select": NC_FIELDS, "limit": ALL_ROWS})
    held = {str(c.get("contact_id")) for c in nc if c.get("contact_id")}
    cc = db_get(cfg, "contacts", {
        "select": "id,full_name,company,title,contact_intelligence(network_tier)",
        "company": "not.is.null", "limit": ALL_ROWS})
    for r in cc:
        if str(r.get("id")) in held or not (r.get("full_name") or "").strip():
            continue
        intel = r.get("contact_intelligence") or {}
        if isinstance(intel, list):
            intel = intel[0] if intel else {}
        nc.append({"contact_key": f"contact:{r['id']}", "full_name": r["full_name"],
                   "current_company": r.get("company"), "current_title": r.get("title") or "",
                   "network_tier": (intel or {}).get("network_tier") or "",
                   "contact_id": r["id"]})
    roles = db_get(cfg, "hunter_seen_roles", {"select": ROLE_FIELDS, "limit": ALL_ROWS})
    posts = db_get(cfg, "hunter_newsletter_posts", {
        "select": "link,published_at,signals", "limit": ALL_ROWS})
    bridges = db_get(cfg, "bridge_candidates", {
        "select": "path_tier,state,outcome,surfaced_at", "path_tier": "eq.mindmake_wedge",
        "limit": ALL_ROWS})
    return {"radar": radar, "contacts": nc, "roles": roles, "posts": posts,
            "bridges": bridges}


def load_dump(path: str) -> dict:
    """The same inputs from a directory of JSON files named by key (radar.json,
    contacts.json, ...), for a run with no database connection."""
    import json
    import os
    out = {}
    for k in ("radar", "contacts", "roles", "posts", "bridges"):
        f = os.path.join(path, f"{k}.json")
        out[k] = json.load(open(f, encoding="utf-8")) if os.path.exists(f) else []
    return out


def report(data: dict) -> dict:
    rows = coverage(data["radar"], data["contacts"], data["roles"], data["posts"])
    return {"summary": summary(rows), "wedge": wedge_record(data["bridges"]),
            "companies": to_json(rows)}
