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
                             contact_tier, in_network, tier_words)
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


# Words that say what kind of entity a name is, not which one. "Higgsfield AI"
# and "Higgsfield Ai", "Reddit" and "Reddit, Inc." differ only in these.
_GENERIC = frozenset({"ai", "inc", "incorporated", "ltd", "limited", "llc", "plc",
                      "gmbh", "co", "corp", "corporation", "company", "the", "hq",
                      "lab", "labs"})
_WORD = re.compile(r"[a-z0-9]+")


def _name_words(name: str) -> frozenset:
    words = _WORD.findall(_PAREN.sub(" ", (name or "").lower()))
    return frozenset(w for w in words if w not in _GENERIC)


def same_company(ours: str, theirs: str) -> bool:
    """Is a contact's employer string this company?

    Stricter than bridges.same_employer, because here a false match names a
    stranger as his way in. Two rounds of measurement on the live graph:

    2026-10-08, morning: containment matched "Harvey Norman (GP Advertising)" to
    Harvey, "Sphere Digital Recruitment" to Sphere, "ARTISAN - Creative and
    Digital Recruitment" to Artisan and "Nexus Adex" to Nexus.

    2026-10-08, afternoon: four cards he had to delete. A shared word was
    enough ("Kana Intelligence" for Physical Intelligence, "Entertainment
    Thinking" for Series Entertainment, "Data-Direct" for Fragment Data
    Technologies), and a short word was invisible ("Together - NZ" for
    Together AI, because the token filter drops anything under four letters).

    So the names must be the same words, every word kept, once parentheses and
    the words in _GENERIC are set aside. "Captify APAC" no longer counts as
    Captify: a missed door waits for a better route, a wrong one sends him to a
    stranger. Two companies that share a whole name ("Braintrust", "ADA")
    still cannot be told apart here, and the page says so rather than guessing.
    """
    from .universe import key_of
    if key_of(ours) and key_of(ours) == key_of(theirs):
        return True  # "higgsfieldai" is Higgsfield AI, written as one word
    a, b = _name_words(ours), _name_words(theirs)
    return bool(a) and a == b


_TITLE_EMPLOYER = re.compile(r"(?:@|\bat\b)\s*([^|\u2022\u00b7,;(&\n]+)", re.I)


def employer_of(c: dict) -> str:
    """Where this person works now, as best the record says.

    The company field goes stale and the headline does not: measured on his
    graph, 2026-10-08, a "Mercor" contact titled "CEO @ Sepal AI", an "OpenAI"
    contact titled "Startups SA @ AWS" and an "Anthropic" contact titled
    "Advisory Solutions Architect @ MongoDB". Each was counted as an insider
    at a company they had left, and the Mercor one as its CEO.

    So a title that names an employer ("@ X", "at X") wins when it is not the
    company field. A title naming no employer leaves the field alone.
    """
    field = (c.get("current_company") or "").strip()
    m = _TITLE_EMPLOYER.search(c.get("current_title") or "")
    if not m:
        return field
    named = re.split(r"\s+-\s+", m.group(1).strip())[0].strip()
    if not named or not _toks(named) or (field and same_company(field, named)):
        return field
    return named


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
        company=employer_of(c),
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
            co = employer_of(c)
            if not co:
                continue
            self.by_slug.setdefault(slugify(co), []).append(c)

    def at(self, company: str) -> list[dict]:
        """Everyone whose current employer is this company: the exact slug,
        by same_company, which wants the same words in both names."""
        out = []
        for k, rows in self.by_slug.items():
            if same_company(company, employer_of(rows[0])):
                out.extend(rows)
        return out

    def alumni_of(self, company: str) -> list[dict]:
        out = []
        for c in self.contacts:
            if same_company(company, employer_of(c)):
                continue
            for h in c.get("employment_history") or []:
                name = (h.get("companyName") or h.get("company") or "") if isinstance(h, dict) else ""
                if name and same_company(company, name):
                    out.append(c)
                    break
        return out


# WEDGE_LEADER says "president", which also matches inside "Vice President".
# Measured 2026-10-08: VPs of marketing, business development and regional
# sales were counted as the person who decides. A vice president is not.
_VICE = re.compile(r"\bvice[\s-]+president\b", re.I)


def is_leader(c: dict) -> bool:
    title = c.get("current_title") or ""
    return bool(WEDGE_LEADER.search(_VICE.sub(" ", title)))


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
    named = set()
    for c in leaders:  # one person can sit in both graphs under two keys
        p = _person(c)
        if p.name.lower() not in named:
            named.add(p.name.lower())
            cov.leaders.append(p)

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


_PLACEHOLDER = re.compile(r"\b(stealth|unnamed|undisclosed|confidential)\b", re.I)


def is_placeholder(radar_row: dict) -> bool:
    """A Radar row that names no real company. Measured 2026-10-08: a top row
    called "Stealth Vertical AI Startup", with no source and another company's
    description, matched a contact whose LinkedIn employer said the same, and
    became a card. A company is only a company when something named it."""
    return bool(_PLACEHOLDER.search(radar_row.get("name") or "")) or not radar_row.get("sources")


def coverage(radar_rows: list[dict], contacts: list[dict], roles: list[dict],
             posts: list[dict]) -> list[Coverage]:
    """One Coverage per top company. radar_rows are hunter_company_radar rows;
    only top=true ones are read, because the Radar already folds in his list,
    his Target Companies tiers and his Yes companies."""
    graph = Graph(contacts)
    seen, out = set(), []
    for r in radar_rows:
        if not r.get("top") or is_placeholder(r):
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
        # description, why and lookalike_why are the company's own words, the
        # only evidence the opening line may use. The first live run selected
        # without them and every card came back "no recorded words".
        "select": "key,name,sources,top,lookalike,area,why,description,lookalike_why",
        "top": "eq.true", "limit": ALL_ROWS})
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


# ---------- phase 2: the leader card ----------
#
# One card per company that is a door: the leader, the reason now, the warm
# route, and the one observation he would open on. It lands in Control
# Center's pilot_deals as `listed`, so there is one pipeline and not two.
# Nothing here sends. A card is a draft he reads, edits and sends himself.

NOTES_TAG = "hunter door-in:"
MAX_OBSERVATIONS = 20
OBSERVATION_MAX_CHARS = 280

# Mindmake's canon keeps these out of public words, and the price is private
# (docs/DOOR_IN.md section 4). They go to the model as banned phrases AND are
# checked on everything a card carries, because a word the model was told to
# avoid still turns up.
FORBIDDEN = ("chief of staff", "fractional", "sprint", "pilot", "proof",
             "retainer", "consulting", "my services", "i help companies",
             "$", "£", "per month", "day rate")

OBSERVATION_SYSTEM = """You write one sentence for Krish Raja to open a conversation with a company's leader.

It is the observation he would make anyway: what the company's own words suggest its go-to-market is about to have to solve, put as a diagnosis, never as an offer. He has run go-to-market as the operator carrying the number and as the advisor brought in to fix it.

Rules:
- One sentence, under 280 characters, no greeting, no sign-off, no question to buy anything.
- Use only facts in the evidence. Do not invent a number, a customer, a product or a person.
- Only the text labelled "The company's own description" is the company's words. Krish's notes are his view, not theirs: never call them "your own words", "your positioning" or "the company's language".
- Never mention a price, a programme, consulting, services, a sprint, a pilot, a proof, chief of staff or fractional.
- No em dashes.
Return only the sentence."""


@dataclass
class Card:
    company: str
    key: str
    sources: list
    leader: Person
    route: Route
    trigger: Trigger | None
    ask_kind: str            # buyer | intro, the values pilot_deals accepts
    door: str                # brain | gtm, a suggestion he overrides
    why_top: str = ""
    observation: str = ""
    observation_note: str = ""


def _door_of(cov: Coverage, trigger: Trigger | None) -> str:
    """Which Mindmake door the conversation most likely fits. A rule, shown as
    a suggestion: an open commercial seat is a company problem (GTM); with no
    seat open, the leader buying for themselves (Brain) is the likelier door."""
    return "gtm" if trigger is not None and trigger.kind == "open_seat" else "brain"


def make_cards(rows: list[Coverage], radar_rows: list[dict] | None = None,
               skip: set[str] | frozenset = frozenset()) -> tuple[list[Card], list[dict]]:
    """(cards, held back with the reason). A company with a live application is
    held back: one road per company, the wedge's rule. So is any company he
    named in `skip`, which is how a same-name collision the code cannot see
    (two companies called Braintrust) is kept off his pipeline."""
    why = {r.get("key"): (r.get("lookalike_why") or r.get("why") or "") for r in radar_rows or []}
    cards, held = [], []
    for c in rows:
        if not c.door:
            continue
        if c.key in skip or slugify(c.name) in skip:
            held.append({"company": c.name, "reason": "skipped by name: check it is the same company"})
            continue
        if c.applied:
            held.append({"company": c.name, "reason": "an application there is live; one road per company"})
            continue
        leader = next((p for p in c.leaders if p.in_network), c.leaders[0])
        route = c.routes[0]
        trigger = next((t for t in c.triggers if t.kind == "open_seat"),
                       c.triggers[0] if c.triggers else None)
        cards.append(Card(
            company=c.name, key=c.key, sources=c.sources, leader=leader, route=route,
            trigger=trigger, ask_kind="buyer" if route.kind == "leader_direct" else "intro",
            door=_door_of(c, trigger), why_top="; ".join(c.sources) + (
                f". {why[c.key]}" if why.get(c.key) else "")))
    # Leaders he knows first, then the warmest route, then a reason to talk now.
    cards.sort(key=lambda k: (k.ask_kind != "buyer", k.trigger is None, k.company.lower()))
    return cards, held


def forbidden_in(text: str) -> list[str]:
    """Banned words as whole words ("Copilot" is not "pilot"), and any
    currency sign at all."""
    low = (text or "").lower()
    hits = []
    for w in FORBIDDEN:
        if w in ("$", "\u00a3"):
            if w in low:
                hits.append(w)
        elif re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
            hits.append(w)
    return hits


def observe(cfg, card: Card, evidence: str) -> Card:
    """The opening observation, from one model call, checked by the voice gate.
    A sentence that fails is retried once with the reasons; a second failure
    leaves the card with no observation and says why. Never a default line."""
    from . import llm
    from .package import voicegate
    haystack = voicegate.build_evidence(evidence, card.company, card.leader.name,
                                        card.trigger.what if card.trigger else "")
    prompt = (f"Company: {card.company}\nLeader: {card.leader.title}\n"
              f"Reason to talk now: {card.trigger.what if card.trigger else 'none recorded'}\n\n"
              f"Evidence:\n{evidence[:4000]}")
    problem = ""
    for _ in range(2):
        text, notes = llm.complete(cfg, prompt + (f"\n\nYour last answer failed: {problem}. Fix it." if problem else ""),
                                   max_tokens=300, system=OBSERVATION_SYSTEM,
                                   purpose="door_observation", cache=True)
        text = " ".join((text or "").split()).strip().strip('"')
        if not text:
            card.observation_note = "no model answered: " + "; ".join(notes)[:200]
            return card
        verdict = voicegate.check(text, evidence=haystack, banned_phrases=FORBIDDEN,
                                  max_chars=OBSERVATION_MAX_CHARS,
                                  allow_names=frozenset({card.company, card.leader.name}))
        if verdict.ok:
            card.observation, card.observation_note = text, ""
            return card
        problem = "; ".join(verdict.failures[:3])
    card.observation_note = "the voice gate rejected both drafts: " + problem
    return card


def deal_row(card: Card, now: str) -> dict:
    """The pilot_deals row for a card. sourced_by must be 'krish' or 'os' (the
    table's check constraint), so hunter's rows say 'os' and carry NOTES_TAG,
    which is also how land() knows a row is its own."""
    t = card.trigger
    notes = (f"{NOTES_TAG} {card.company}. Route: {card.route.evidence}. "
             f"Door (suggested): {'Build your AI GTM' if card.door == 'gtm' else 'Build your AI brain'}."
             + (f" Observation not drafted: {card.observation_note}" if card.observation_note else ""))
    return {"contact_id": card.leader.contact_id, "state": "listed", "listed_at": now,
            "sourced_by": "os", "ask_kind": card.ask_kind,
            "why_face": f"{card.leader.title} at {card.company}. Top company: {card.why_top}"[:500],
            "trigger_signal": t.what if t else None,
            "trigger_source_url": (t.url or None) if t else None,
            "trigger_found_at": (t.when or None) if t else None,
            "draft_body": card.observation or None,
            "notes": notes[:1000]}


def land(cfg, cards: list[Card], apply: bool = False, refresh_only: bool = False) -> dict:
    """Put the cards in pilot_deals as `listed`, or say what would be written.

    Only a leader with a Control Center contacts row can land (the table is
    keyed by contact_id). A contact who already has a pipeline row is left
    alone unless the row is hunter's own and still `listed`, so nothing he has
    drafted, sent or ruled on is ever overwritten. Every write is read back.

    refresh_only writes no new rows: it only refreshes hunter's own `listed`
    rows, for a pass whose job is to add the opening line to cards he has
    already approved without listing new leaders he has not seen."""
    from .config import db_get, db_insert, db_patch
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out = {"write": [], "update": [], "not_in_control_center": [], "theirs": [],
           "blocked_words": [], "read_back": None}
    ids = [c.leader.contact_id for c in cards if c.leader.contact_id]
    existing = {}
    if ids:
        for r in db_get(cfg, "pilot_deals", {
                "select": "contact_id,state,notes",
                "contact_id": "in.(" + ",".join(ids) + ")"}):
            existing[str(r["contact_id"])] = r
    for c in cards:
        if not c.leader.contact_id:
            out["not_in_control_center"].append(c.company)
            continue
        row = deal_row(c, now)
        # What he might send is the draft; the other fields are his pipeline's
        # own notes, where a company's funding figure is not a Mindmake price.
        bad = forbidden_in(row.get("draft_body") or "")
        if bad:
            out["blocked_words"].append({"company": c.company, "words": bad})
            continue
        prior = existing.get(c.leader.contact_id)
        if prior is None:
            if refresh_only:
                out.setdefault("new_not_written", []).append(c.company)
                continue
            out["write"].append(row)
        elif (prior.get("notes") or "").startswith(NOTES_TAG) and prior.get("state") == "listed":
            out["update"].append(row)
        else:
            out["theirs"].append({"company": c.company, "state": prior.get("state")})
    if apply:
        if out["write"]:
            db_insert(cfg, "pilot_deals", out["write"])
        for row in out["update"]:
            db_patch(cfg, "pilot_deals", {"contact_id": row["contact_id"]},
                     {k: v for k, v in row.items() if k != "contact_id"})
        done = [r["contact_id"] for r in out["write"] + out["update"]]
        if done:
            back = db_get(cfg, "pilot_deals", {
                "select": "contact_id,state,sourced_by,notes",
                "contact_id": "in.(" + ",".join(done) + ")"})
            ok = [r for r in back if r.get("state") == "listed"
                  and (r.get("notes") or "").startswith(NOTES_TAG)]
            out["read_back"] = {"expected": len(done), "found": len(ok)}
    return out


def evidence_for(radar_row: dict) -> str:
    """What the opening line may draw on, labelled by whose words it is.

    The first drafted lines said "Beehiiv's own words" about a note Krish wrote
    on his Target Companies tab. A line that attributes his words to the
    company is a claim the company never made, so the two are kept apart."""
    parts = []
    if (radar_row.get("description") or "").strip():
        parts.append("The company's own description: " + radar_row["description"].strip())
    note = " ".join(x.strip() for x in (radar_row.get("why"), radar_row.get("lookalike_why"))
                    if (x or "").strip())
    if note:
        parts.append("Krish's note on why it matters to him (his words, not the company's): " + note)
    return "\n".join(parts)


def run_cards(cfg, data: dict, *, skip=frozenset(), observe_with=None,
              apply: bool = False, refresh_only: bool = False) -> dict:
    """The whole phase 2 pass: coverage, cards, observations (capped), land."""
    rows = coverage(data["radar"], data["contacts"], data["roles"], data["posts"])
    cards, held = make_cards(rows, data["radar"], skip=set(skip))
    by_key = {r.get("key"): r for r in data["radar"]}
    if observe_with is not None:
        # Only the cards that will be written are worth a model call. Measured
        # 2026-10-08: drafting for every door, landable or not, made 69 calls
        # across three runs where 14 would have done.
        targets = cards
        if cfg is not None:
            plan = land(cfg, cards, apply=False, refresh_only=refresh_only)
            going = {r["contact_id"] for r in plan["write"] + plan["update"]}
            targets = [c for c in cards if c.leader.contact_id in going]
        for c in targets[:MAX_OBSERVATIONS]:
            r = by_key.get(c.key) or {}
            evidence = evidence_for(r)
            if not evidence:
                c.observation_note = "no recorded words from the company to observe from"
                continue
            observe_with(cfg, c, evidence)
    landed = land(cfg, cards, apply=apply, refresh_only=refresh_only) if cfg is not None else None
    return {"cards": cards, "held": held, "landed": landed}
