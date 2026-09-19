"""Bridge building: for each live target role, the warmest path in. Tiers
per the brief and Krish's 2026-08-31 answers: current employee, ex employee
(needs enriched history), headhunter (same surface, clearly flagged, only
when three or more target roles sit in one firm's coverage), and the
outside-network peer-transition suggestion (his Q5 default) when no
in-network path exists.

Ranking per Q3: path strength, then proximity to the role, then recency.
Drafts are written under the krish-voice rules (direct opening, dropped
pronouns, one specific low-friction ask, nothing invented about the person)
and live in bridge_candidates.draft_ask until Krish edits and sends them
himself. Nothing here sends anything.
"""
from __future__ import annotations

import datetime
import json
import re

from .. import verdicts
from ..config import (ALL_ROWS, Config, db_delete, db_get, db_insert,
                      db_patch)
from ..router import GO_WORDS
from ..sheet import EVIDENCE_NONE, WARM_NONE, Sheet, hyperlink, plain_text
from ..sources import distinctive_tokens, slugify
from . import li_slug
from .strength import EVIDENCE_KEYS  # noqa: F401  (re-export for the guard test)

# newsletter_move sits between ex_employee and current_employee on purpose:
# a person named in the a16z newsletter as having just joined the company is
# timelier than an ex-employee and colder than anyone Krish actually knows.
TIER_BASE = {"current_employee": 40, "newsletter_move": 30, "ex_employee": 25,
             "headhunter": 20, "mindmake_wedge": 18, "cold_target": 15,
             "peer_transition": 10}

# Control Center's graph (contacts + contact_intelligence) scores relationship by
# tier, not by message counts. These numbers are the RANKING WEIGHT: they decide
# which of two people at the same company is offered first.
CC_TIER_STRENGTH = {"1_reciprocated": 70, "2_core_network": 50,
                    "3_known_network": 30, "4_owned_network": 15, "5_cold_lead": 5}

# Eligibility is a separate question from ranking, and until 2026-09-15 one number
# answered both: min_strength=25 was applied to the weights above, so tier 4 at 15
# and tier 5 at 5 were discarded before anything looked at them. Tier 4 is
# 4_owned_network, which is Krish's own LinkedIn connections: 4,693 people, the
# bulk of his graph. Measured on the live pipeline the day it was found: 104
# companies at staging, 21 with a contact at that exact company, 55 such contacts,
# and 8 surviving the cut. 108 of 131 Pipeline rows read "None found" while OpenAI's
# CRO, ElevenLabs' GTM Director and Legora's VP Product sat in the graph.
#
# Krish's ruling 2026-09-15: tiers 1 to 4 are his network, a connection is a real
# path even when weak. Tier 5 is scraped and never met, so it is not a warm path,
# though it can still be a bridge to build.
IN_NETWORK_TIERS = frozenset({"1_reciprocated", "2_core_network",
                              "3_known_network", "4_owned_network"})

# What Path Evidence calls each tier, so a weak connection reads as one and Krish
# can judge it rather than trusting a bare number.
TIER_WORDS = {
    "1_reciprocated": "a two-way relationship on record",
    "2_core_network": "core network",
    "3_known_network": "known network",
    "4_owned_network": "a LinkedIn connection, no recorded contact",
    "5_cold_lead": "scraped, never met",
}


def same_employer(role_tokens: frozenset, contact_tokens: frozenset) -> bool:
    """Are these two company names the same employer?

    The exact slug is matched first by the caller; this is the fallback, and it
    exists for one narrow case: "Google" against "Google (YouTube Partnerships)".
    It used to accept ANY shared distinctive token, and that produced false warm
    paths, which are worse than none because Krish would email a stranger on
    hunter's word. Measured on the live pass: Harpreet Singh, whose company is
    "Accel-Digital Ad Operations", was offered as the inside contact at Notion,
    LangChain AND Render, because three role rows carry a company field of the form
    "Notion - Head of GTM Operations" and "operations" survives
    distinctive_tokens as though it named an employer.

    So the test is containment or a shared lead token, not overlap. One name's
    tokens being a subset of the other's covers the parenthetical case; a shared
    first token covers "Captify" against "Captify APAC". "Accel-Digital Ad
    Operations" and "Notion Head of GTM Operations" satisfy neither.
    """
    if not role_tokens or not contact_tokens:
        return False
    if role_tokens <= contact_tokens or contact_tokens <= role_tokens:
        return True
    return bool(role_tokens & contact_tokens) and (
        min(role_tokens) == min(contact_tokens))


def contact_tier(contact: dict) -> str:
    """The Control Center network tier, whichever graph the contact came through.

    load_cc_graph puts it in `network_tier`. A network_contacts row carries the same
    value inside `strength_evidence.ci_tier`, written by ingest.py from the same
    source. Reading only the first one hid the tier for exactly the people who
    matter: load_cc_graph skips anyone network_contacts already holds, so a
    connection who IS in the export has no `network_tier` and was judged on score
    alone.
    """
    tier = contact.get("network_tier")
    if tier:
        return tier
    ev = contact.get("strength_evidence") or {}
    return (ev.get("ci_tier") or "") if isinstance(ev, dict) else ""


def in_network(contact: dict, min_strength: int) -> bool:
    """Is this person someone Krish can actually ask?

    Three ways to qualify, because a connection with no recorded interaction is
    still a connection. strength.py scores such a person around 3: there are no
    messages, no endorsements and no recommendation to score, and that is a measure
    of INTERACTION, not of whether the path exists. Adrian Parlow at Legora scored
    3, Michael Costa at ElevenLabs 3, Tyrone Millard at OpenAI 3, and all three are
    in his LinkedIn connections.

      - a Control Center tier inside IN_NETWORK_TIERS, from either graph
      - a connected_on date, which is LinkedIn's own record that they connected
      - failing both, the computed strength_score clearing min_strength, which is
        the only test available for a contact the export never saw
    """
    tier = contact_tier(contact)
    if tier:
        return tier in IN_NETWORK_TIERS
    if (contact.get("connected_on") or "").strip():
        return True
    return (contact.get("strength_score") or 0) >= min_strength


def tier_words(contact: dict) -> str:
    return TIER_WORDS.get(contact_tier(contact), "")
# The tiers build_bridges derives from the graph on every run, so a proposed row in
# one of them that this run did not derive is stale. cold_target is not here: the
# cold_targets() command writes it on its own schedule and this pass must not eat it.
DERIVED_TIERS = ("current_employee", "ex_employee", "newsletter_move",
                 "headhunter", "peer_transition", "mindmake_wedge")

# The mindmake wedge, Krish's idea 2026-09-15: "a CEO is likely to be warm to my
# mindmake services if they have that role open. It could be a win either to get a
# new customer, or a really clever way in to the role at the highest level with a
# peer to peer outreach."
#
# The trigger is right and the framing matters more than the trigger. Three things
# shape how this is built, all of them constraints rather than features:
#
#   1. It opens on the OBSERVATION, never the offer. A company hiring a commercial
#      leader has a salary allocated, not advisory budget, so "saw you are hiring,
#      want consulting instead" lands badly and cheapens a practice whose canon says
#      the price is private and the primary action is Start here. The opener is the
#      diagnostic he would give anyway.
#   2. It is a CHOICE, not a parallel track. Applying through the ATS and pitching
#      the CEO the same week reads as "he will take anything" if the two ever
#      compare notes, so the wedge is offered only where he has not already applied,
#      and the draft says plainly that the role is the other road.
#   3. The outcome is instrumented, because the honest expectation is that this
#      converts to the ROLE more often than to a client, and six weeks of recorded
#      outcomes will say so better than either of us can guess now.
#
# Only a leader: the wedge is peer to peer or it is nothing, and a Programmatic
# Director is not the person who decides either a hire or an engagement.
WEDGE_LEADER = re.compile(
    r"\b(chief executive|ceo|founder|co.founder|president|managing partner|"
    r"chief revenue|cro\b|chief commercial|cco\b|chief operating|coo\b|"
    r"general manager|managing director)\b", re.I)

# And only where the open seat is the one his practice speaks to. A company hiring a
# Head of Engineering has no GTM question for him to open on.
WEDGE_SEAT = re.compile(
    r"\b(gtm|go.to.market|revenue|commercial|sales|growth|marketing|"
    r"partnerships?|strategy|chief of staff|general manager)\b", re.I)
COLD_KEY = "hunter_cold_targets_max_per_run"
NEWSLETTER_WINDOW_DAYS = 120
PRIORITY_BONUS = {"A": 15, "B": 8, "C": 3}

# A role falls inside a firm's coverage when they share a search family;
# raw token overlap misses "Chief Revenue Officer" against "CRO".
COVERAGE_FAMILIES = {
    "commercial": re.compile(
        r"\bcro\b|\bcco\b|revenue|sales|commercial|gtm|go.to.market", re.I),
    "partnerships": re.compile(r"partnerships?|alliances|\bpartner\b", re.I),
    "strategy": re.compile(r"strategy|corp\s*dev|corporate development", re.I),
    "chief_of_staff": re.compile(r"chief of staff", re.I),
    "gm": re.compile(r"general manager|country director|managing director", re.I),
}


def coverage_families(text: str) -> set[str]:
    return {fam for fam, rx in COVERAGE_FAMILIES.items() if rx.search(text or "")}

DRAFTS = {
    "current_employee": (
        "{company} has the {role} role open and I am going after it this "
        "week. You have the inside view, could I borrow 15 minutes for a "
        "steer before I apply?"),
    "ex_employee": (
        "Going after the {role} role at {company} and your time there gives "
        "you exactly the read I need. Open to a 15 minute call this week "
        "before the application goes in?"),
    "headhunter": (
        "Several of the roles I am tracking sit inside {firm}'s coverage, "
        "including {role} at {company}. Worth 15 minutes on whether they are "
        "yours and how my profile lands?"),
    "newsletter_move": (
        "Saw in the a16z jobs newsletter that you have just joined {company}. "
        "I am going after the {role} role there and would value 15 minutes on "
        "what you are seeing from the inside before I apply."),
    "peer_transition": (
        "TEMPLATE, find the person first: [[NAME]] made the same move I am "
        "making, into {company}'s world. Ask: would you take 15 minutes to "
        "tell me what you wish you had known before you moved?"),
    "cold_target": (
        "I am going after the {role} role at {company} and you are the person "
        "it reports into or sits beside. Rather than go in through the form, "
        "could I have 15 minutes to hear what the role has to solve first?"),
    # Opens on the observation and never on the offer. No price, no programme
    # name, no "I help companies like yours": a leader who has just opened a
    # commercial seat is being told what the seat usually means, by someone who
    # has designed the model twice. It converts to an engagement or to the role,
    # and the last line makes the second road explicit rather than coy, because
    # finding out later that he also applied is the outcome worth avoiding.
    "mindmake_wedge": (
        "You have {role} open. In my experience that seat opens when the "
        "go-to-market model is being rebuilt rather than when a chair is empty, "
        "and the rebuild usually turns on one of four things: product, price, "
        "positioning or people. I have designed that model from both sides, as "
        "the operator carrying the number and as the advisor brought in to fix "
        "it. Happy to tell you which of the four I would look at first at "
        "{company}, in 15 minutes, with nothing to sell at the end of it. If "
        "the answer is that you want someone in the seat rather than beside it, "
        "say so and I will put my name in properly."),
}


ROLE_FIELDS = ("job_id,company,title,score,status,krish_verdict,"
               "warm_path_person,application_state")


def target_roles(cfg: Config, limit: int = 60) -> list[dict]:
    """Roles worth a warm path: ones he said go to, then the best of what is
    still on his sheet awaiting a verdict.

    presented_at is the test of "on his sheet". The retired incumbent left
    fifteen rows at status staging that it never wrote to the sheet, among
    them ElevenLabs GM seats in Brazil, Mexico and Saudi Arabia, and bridges
    were being built into roles he had never been shown.

    EVERY approved role is a target, with no cap. The cap used to apply to
    both halves, so with 163 rows on the sheet a Yes that scored below the
    top sixty fell out of the window, retire_stale read that as the role
    being gone, and its warm path was deleted and rebuilt on alternate runs.
    A role he has approved is the one thing in this system that must never
    churn.
    """
    gos = db_get(cfg, "hunter_seen_roles", {
        "select": ROLE_FIELDS,
        "krish_verdict": "not.is.null", "status": "neq.duplicate",
        "limit": ALL_ROWS})
    approved = [g for g in gos
                if (g.get("krish_verdict") or "").strip().lower() in GO_WORDS]
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": ROLE_FIELDS,
        "status": "in.(staging,presented)",
        "presented_at": "not.is.null",
        "order": "score.desc.nullslast",
        "limit": str(limit)})
    # status stays at presented after a row is archived, so the second half
    # was carrying roles he had already declined or applied to. Only a row
    # still awaiting his verdict earns a bridge it did not already have.
    waiting = [r for r in rows
               if verdicts.parse(r.get("krish_verdict") or "")[0] == "none"]
    seen, out = set(), []
    for r in approved + waiting:
        if r["job_id"] not in seen:
            seen.add(r["job_id"])
            out.append(r)
    return out


def purge_orphan_bridges(cfg: Config) -> dict:
    """Drop proposed bridges that point at nothing he can act on.

    Three kinds, all of which the Hunt lane was showing as live people to
    contact about a live role:

      a job_id that is not in hunter_seen_roles at all, left behind when the
      role row was deleted rather than archived;

      a role he has since declined or applied to, where the reason to reach
      out has gone;

      a posting recorded dead.

    Only hunter's own untouched proposals go. Anything he marked reached out,
    snoozed or not a path is his history and stays, whatever the role did.
    """
    proposed = db_get(cfg, "bridge_candidates", {
        "select": "bridge_id,job_id", "state": "eq.proposed", "limit": ALL_ROWS})
    ids = {p.get("job_id") for p in proposed if p.get("job_id")}
    if not ids:
        return {"checked": 0, "orphaned": 0, "decided": 0, "dead": 0, "deleted": 0}
    known = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,krish_verdict,status", "limit": ALL_ROWS})
    by_id = {r["job_id"]: r for r in known}

    orphan, decided, dead = set(), set(), set()
    for job_id in ids:
        role = by_id.get(job_id)
        if role is None:
            orphan.add(job_id)
            continue
        kind = verdicts.parse(role.get("krish_verdict") or "")[0]
        if kind in ("applied", "rejection"):
            decided.add(job_id)
        elif (role.get("status") or "") == "dead":
            dead.add(job_id)
    gone = orphan | decided | dead
    stale = [p["bridge_id"] for p in proposed
             if p.get("job_id") in gone and p.get("bridge_id")]
    for i in range(0, len(stale), 100):
        db_delete(cfg, "bridge_candidates", {
            "bridge_id": "in.(" + ",".join(stale[i:i + 100]) + ")",
            "state": "eq.proposed"})
    return {"checked": len(ids), "orphaned": len(orphan), "decided": len(decided),
            "dead": len(dead), "deleted": len(stale)}


def load_headhunters(sheet: Sheet) -> list[dict]:
    grid = sheet.read_tab_values("Headhunters!A1:M60")
    header_i = next((i for i, row in enumerate(grid)
                     if row[:1] == ["Priority"]), None)
    if header_i is None:
        return []
    headers = grid[header_i]
    out = []
    for row in grid[header_i + 1:]:
        if not row or not (row[0] or "").strip():
            continue
        d = {headers[j]: (row[j] if j < len(row) else "") for j in range(len(headers))}
        if d.get("Priority") in PRIORITY_BONUS:
            out.append(d)
    return out


def _employment_companies(history) -> set[str]:
    out = set()
    for h in history or []:
        name = h.get("companyName") or h.get("company")
        if name:
            out.add(slugify(str(name)))
    return out


def _days_since(published: str) -> int | None:
    """Days since an RFC 822 or ISO date, or None if it cannot be read."""
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(published).date()
    except Exception:
        try:
            d = datetime.date.fromisoformat(published[:10])
        except Exception:
            return None
    return (datetime.date.today() - d).days


def _recency_bonus(evidence: dict) -> float:
    last = (evidence or {}).get("last_message_at") or ""
    try:
        months = (datetime.date.today()
                  - datetime.date.fromisoformat(last[:10])).days / 30.44
    except ValueError:
        return 0.0
    return 5.0 if months <= 6 else 0.0


def retire_stale(cfg: Config, roles: list[dict]) -> int:
    """Drop proposed bridges into roles that are no longer targets.

    The upsert only ever adds, so a bridge into a posting that has since
    died or been archived stayed on the Bridges tab saying the role was
    open. Only hunter's own untouched proposals go; anything Krish marked
    reached out, snoozed or not a path is his history and stays.
    """
    live = {r["job_id"] for r in roles}
    proposed = db_get(cfg, "bridge_candidates", {
        "select": "bridge_id,job_id", "state": "eq.proposed", "limit": ALL_ROWS})
    stale = [p["bridge_id"] for p in proposed
             if p.get("bridge_id") and p.get("job_id") not in live]
    for i in range(0, len(stale), 100):
        db_delete(cfg, "bridge_candidates", {
            "bridge_id": "in.(" + ",".join(stale[i:i + 100]) + ")",
            "state": "eq.proposed"})
    return len(stale)


def load_cc_graph(cfg: Config, known_keys: set[str]) -> list[dict]:
    """Control Center's people graph (contacts joined to contact_intelligence)
    in network_contacts shape, minus anyone network_contacts already holds.
    Only people with a company are useful here, and a row without a name is
    not a person."""
    try:
        rows = db_get(cfg, "contacts", {
            "select": "id,full_name,company,title,linkedin_url,heat_score,"
                      "contact_intelligence(network_tier,warmth)",
            "company": "not.is.null", "limit": "20000"})
    except Exception:
        return []
    out = []
    for r in rows:
        if not (r.get("full_name") or "").strip() or not (r.get("company") or "").strip():
            continue
        slug = li_slug(r.get("linkedin_url"))
        key = slug or f"contact:{r.get('id')}"
        if key in known_keys:
            continue
        intel = r.get("contact_intelligence") or {}
        if isinstance(intel, list):
            intel = intel[0] if intel else {}
        tier = (intel or {}).get("network_tier") or ""
        strength = CC_TIER_STRENGTH.get(tier)
        if strength is None:
            heat = r.get("heat_score")
            strength = int(heat) if isinstance(heat, (int, float)) else 20
        out.append({"contact_key": key, "full_name": r["full_name"],
                    "current_company": r["company"], "current_title": r.get("title") or "",
                    "strength_score": strength,
                    # The tier travels with the contact rather than being collapsed
                    # into the score, so eligibility and ranking can ask different
                    # questions of it. See in_network().
                    "network_tier": tier,
                    "strength_evidence": {"cc_tier": tier} if tier else {},
                    "employment_history": [], "linkedin_url": r.get("linkedin_url"),
                    "graph": "contacts"})
    return out


def build_bridges(cfg: Config, sheet: Sheet, min_strength: int = 25) -> dict:
    roles = target_roles(cfg)
    retired = retire_stale(cfg, roles)
    contacts = db_get(cfg, "network_contacts", {
        # connected_on is selected because in_network() treats it as proof of a
        # first-degree connection, which it could not do when it was not fetched.
        "select": "contact_key,full_name,current_company,current_title,"
                  "strength_score,strength_evidence,employment_history,connected_on",
        "order": "strength_score.desc", "limit": ALL_ROWS})
    contacts = list(contacts) + load_cc_graph(
        cfg, {c.get("contact_key") for c in contacts})
    by_company: dict[str, list[dict]] = {}
    company_tokens: dict[str, set[str]] = {}
    for c in contacts:
        if c.get("current_company"):
            key = slugify(c["current_company"])
            by_company.setdefault(key, []).append(c)
            company_tokens[key] = distinctive_tokens(c["current_company"])

    headhunters = load_headhunters(sheet)
    hh_role_hits: dict[str, list[dict]] = {}
    for hh in headhunters:
        firm_fams = coverage_families(
            f"{hh.get('Title', '')} {hh.get('Why-relevant', '')}")
        hits = [r for r in roles if firm_fams & coverage_families(r["title"])]
        if len(hits) >= 3:  # Q4: surfaced only with real coverage overlap
            hh_role_hits[hh.get("Firm", "")] = hits

    upserts, warm_patches = [], {}
    # Counted because the two defects this pass shipped with were both silent
    # discards: a tier floor that dropped 47 of 55 candidates and said nothing, and
    # a company match that found nobody and read the same as a company with nobody
    # to find. A pass that throws work away reports how much.
    considered = dropped = no_contact = wedge = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for role in roles:
        cslug = slugify(role["company"])
        found_in_network = False

        # exact slug first, then any company sharing a distinctive token
        # ("Google" for "Google (YouTube Partnerships)"), strongest first
        rtoks = distinctive_tokens(role["company"])
        pool = list(by_company.get(cslug, []))
        for key, toks in company_tokens.items():
            if key != cslug and same_employer(rtoks, toks):
                pool.extend(by_company.get(key, []))
        pool.sort(key=lambda c: -(c.get("strength_score") or 0))
        if not pool:
            no_contact += 1
        for c in pool[:3]:
            considered += 1
            if not in_network(c, min_strength):
                dropped += 1
                continue
            found_in_network = True
            score = (TIER_BASE["current_employee"] + c["strength_score"] * 0.5
                     + _recency_bonus(c.get("strength_evidence") or {}))
            words = tier_words(c)
            upserts.append(_candidate(
                role, c["contact_key"], "current_employee",
                f"{c['full_name']} is {c.get('current_title') or 'at'} "
                f"{role['company']} now; strength {c['strength_score']}"
                + (f", {words}" if words else ""),
                "works there now", score,
                DRAFTS["current_employee"].format(company=role["company"],
                                                  role=role["title"]), now))
            if score > warm_patches.get(role["job_id"], (0, None, ""))[0]:
                warm_patches[role["job_id"]] = (score, c, "current_employee")

        for c in contacts:
            if slugify(c.get("current_company") or "") == cslug:
                continue
            if not in_network(c, min_strength):
                continue
            if cslug in _employment_companies(c.get("employment_history")):
                found_in_network = True
                score = (TIER_BASE["ex_employee"] + c["strength_score"] * 0.5
                         + _recency_bonus(c.get("strength_evidence") or {}))
                words = tier_words(c)
                upserts.append(_candidate(
                    role, c["contact_key"], "ex_employee",
                    f"{c['full_name']} previously worked at {role['company']} "
                    f"per profile history; strength {c['strength_score']}"
                    + (f", {words}" if words else ""),
                    "worked there, knows the terrain", score,
                    DRAFTS["ex_employee"].format(company=role["company"],
                                                 role=role["title"]), now))
                if score > warm_patches.get(role["job_id"], (0, None, ""))[0]:
                    warm_patches[role["job_id"]] = (score, c, "ex_employee")

        # People the a16z newsletter says have just joined this company.
        for c in by_company.get(cslug, []):
            ev = c.get("strength_evidence") or {}
            if not ev.get("newsletter_post"):
                continue
            days = _days_since(ev.get("newsletter_date") or "")
            if days is None or days > NEWSLETTER_WINDOW_DAYS:
                continue
            found_in_network = True
            score = TIER_BASE["newsletter_move"] + max(0.0, 10.0 - days / 12.0)
            upserts.append(_candidate(
                role, c["contact_key"], "newsletter_move",
                f"Named in the a16z jobs newsletter {days} day(s) ago as joining "
                f"{role['company']}: {ev.get('quote', '')[:180]} "
                f"({ev.get('newsletter_post')})",
                "just joined, named publicly", score,
                DRAFTS["newsletter_move"].format(company=role["company"],
                                                 role=role["title"]), now))
            if score > warm_patches.get(role["job_id"], (0, None, ""))[0]:
                warm_patches[role["job_id"]] = (score, c, "newsletter_move")

        for firm, hits in hh_role_hits.items():
            if role in hits:
                hh = next(h for h in headhunters if h.get("Firm") == firm)
                score = (TIER_BASE["headhunter"]
                         + PRIORITY_BONUS.get(hh.get("Priority", ""), 0)
                         + float(hh.get("Fit") or 0))
                upserts.append(_candidate(
                    role, f"headhunter:{slugify(firm)}", "headhunter",
                    f"HEADHUNTER PATH: {hh.get('Partner')} at {firm} "
                    f"(priority {hh.get('Priority')}, fit {hh.get('Fit')}); "
                    f"{len(hits)} tracked roles in coverage: "
                    f"{hh.get('Why-relevant', '')[:80]}",
                    "retained search coverage, not an insider", score,
                    DRAFTS["headhunter"].format(firm=firm, company=role["company"],
                                                role=role["title"]), now))

        # The mindmake wedge. Offered where the open seat is one his practice
        # speaks to, a leader at that company is reachable, and he has NOT already
        # applied: doing both to one company in one week is the failure mode, not
        # the feature. See WEDGE_LEADER above for why only a leader qualifies.
        if WEDGE_SEAT.search(role["title"] or "") \
                and not (role.get("application_state") or "").strip():
            for c in pool[:6]:
                if not WEDGE_LEADER.search(c.get("current_title") or ""):
                    continue
                wedge += 1
                score = TIER_BASE["mindmake_wedge"] + (c.get("strength_score") or 0) * 0.3
                words = tier_words(c)
                upserts.append(_candidate(
                    role, c["contact_key"], "mindmake_wedge",
                    f"{c['full_name']} leads {role['company']} as "
                    f"{c.get('current_title') or 'a leader there'} and the open "
                    f"{role['title']} seat is the opening"
                    + (f"; {words}" if words else "")
                    + ". Peer to peer, about their GTM model, not about the role.",
                    "the person who decides both", score,
                    DRAFTS["mindmake_wedge"].format(company=role["company"],
                                                    role=role["title"]), now))
                break  # one leader per role: a company gets one approach, not three

        covered_by_hh = any(role in hits for hits in hh_role_hits.values())
        if not found_in_network and not covered_by_hh:
            # a NULL contact_key would dodge the unique constraint and stack
            # a copy per run, so the placeholder key is explicit
            upserts.append(_candidate(
                role, "peer:unidentified", "peer_transition",
                "No in-network path found. Q5 default: find a peer who made "
                "the same transition; highest response rate",
                "outside network", TIER_BASE["peer_transition"],
                DRAFTS["peer_transition"].format(company=role["company"]), now))

    for i in range(0, len(upserts), 100):
        db_insert(cfg, "bridge_candidates", upserts[i:i + 100],
                  on_conflict="job_id,contact_key,path_tier", merge=True)

    # A bridge that stopped qualifying has to go, not just a bridge into a role that
    # died. This pass only ever upserted, so a candidate derived by an older and
    # looser rule survived forever: tightening the employer match dropped four false
    # paths from the current run and all four would have stayed on the board,
    # naming a stranger as the way into a company.
    #
    # Only rows hunter derives from the graph every run, only state=proposed, and
    # only for roles still targeted. cold_target is excluded because a different
    # command writes it, and anything Krish touched is his history.
    derived = {(u["job_id"], u["contact_key"], u["path_tier"]) for u in upserts}
    superseded = 0
    target_ids = [r["job_id"] for r in roles]
    for i in range(0, len(target_ids), 100):
        chunk = target_ids[i:i + 100]
        existing = db_get(cfg, "bridge_candidates", {
            "select": "bridge_id,job_id,contact_key,path_tier",
            "job_id": "in.(" + ",".join(f'"{j}"' for j in chunk) + ")",
            "path_tier": "in.(" + ",".join(DERIVED_TIERS) + ")",
            "state": "eq.proposed", "limit": ALL_ROWS})
        stale = [str(r["bridge_id"]) for r in existing
                 if (r["job_id"], r["contact_key"], r["path_tier"]) not in derived]
        for j in range(0, len(stale), 100):
            db_delete(cfg, "bridge_candidates", {
                "bridge_id": "in.(" + ",".join(stale[j:j + 100]) + ")",
                "state": "eq.proposed"})
        superseded += len(stale)

    for job_id, (score, c, tier) in warm_patches.items():
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id}, {
            "warm_path_person": c["full_name"],
            "warm_path_tier": tier,
            "warm_path_evidence": f"strength {c['strength_score']}, "
                                  f"bridge score {round(score, 1)}"})
    return {"roles": len(roles), "bridges": len(upserts), "retired": retired,
            "warm_paths_set": len(warm_patches),
            "headhunter_firms_surfaced": len(hh_role_hits),
            "considered": considered, "dropped_out_of_network": dropped,
            "roles_with_no_contact_at_company": no_contact,
            "superseded": superseded, "mindmake_wedges": wedge}


def _candidate(role, contact_key, tier, evidence, proximity, score, draft, now):
    return {"job_id": role["job_id"], "contact_key": contact_key,
            "path_tier": tier, "path_evidence": evidence,
            "proximity": proximity, "bridge_score": round(score, 2),
            "draft_ask": draft, "state": "proposed", "surfaced_at": now}


def top_bridges(cfg: Config, n: int = 5) -> list[dict]:
    return db_get(cfg, "bridge_candidates", {
        "select": "job_id,contact_key,path_tier,path_evidence,proximity,"
                  "bridge_score,draft_ask",
        "state": "eq.proposed",
        "order": "bridge_score.desc",
        "limit": str(n)})


def clear_junk_warm_paths(cfg: Config) -> int:
    """Null the placeholder sentences the retired incumbent wrote into
    warm_path_person ("None identified with...", "None. No connections").
    A placeholder read as a person put "bridge first" on rows with nobody
    to bridge through."""
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,warm_path_person",
        "warm_path_person": "ilike.none*", "limit": ALL_ROWS})
    for r in rows:
        db_patch(cfg, "hunter_seen_roles", {"job_id": r["job_id"]},
                 {"warm_path_person": None, "warm_path_tier": None,
                  "warm_path_evidence": None})
    return len(rows)


def _person_lookup(cfg: Config, keys: list[str]) -> dict[str, dict]:
    """contact_key -> {name, title, company, linkedin_url} across both graphs."""
    out: dict[str, dict] = {}
    keys = [k for k in keys if k and not k.startswith(("peer:", "headhunter:"))]
    if not keys:
        return out
    plain = [k for k in keys if not k.startswith("contact:")]
    for i in range(0, len(plain), 100):
        chunk = plain[i:i + 100]
        rows = db_get(cfg, "network_contacts", {
            "select": "contact_key,full_name,current_title,current_company,linkedin_url",
            "contact_key": "in.(" + ",".join(f'"{k}"' for k in chunk) + ")",
            "limit": "500"})
        for r in rows:
            # No stored URL means no URL. This used to synthesise
            # https://www.linkedin.com/in/<contact_key>, which put eight dead links
            # into Krish's sheet: a cold target's key is "cold:<name>", and the
            # li_slug guard accepted it. warm_path_cells already renders a plain
            # name when there is no URL, so a missing link costs nothing and an
            # invented one costs trust.
            out[r["contact_key"]] = {
                "name": r.get("full_name") or "", "title": r.get("current_title") or "",
                "company": r.get("current_company") or "",
                "linkedin_url": r.get("linkedin_url") or ""}
    missing = [k for k in keys if k not in out]
    ids = [k.split(":", 1)[1] for k in missing if k.startswith("contact:")]
    slugs = [k for k in missing if not k.startswith("contact:")]
    try:
        if ids:
            rows = db_get(cfg, "contacts", {
                "select": "id,full_name,title,company,linkedin_url",
                "id": "in.(" + ",".join(ids) + ")", "limit": "500"})
            for r in rows:
                out[f"contact:{r['id']}"] = {
                    "name": r.get("full_name") or "", "title": r.get("title") or "",
                    "company": r.get("company") or "",
                    "linkedin_url": r.get("linkedin_url") or ""}
        for slug in slugs:
            rows = db_get(cfg, "contacts", {
                "select": "id,full_name,title,company,linkedin_url",
                "linkedin_url_norm": f"ilike.*/in/{slug}*", "limit": "1"})
            if rows:
                r = rows[0]
                # The slug came OUT of a real LinkedIn URL here (it is what the
                # ilike matched on), so rebuilding it is sound. li_slug now refuses
                # a slug carrying a colon, so a contact_key cannot reach this path.
                out[slug] = {"name": r.get("full_name") or "",
                             "title": r.get("title") or "",
                             "company": r.get("company") or "",
                             "linkedin_url": r.get("linkedin_url") or
                             f"https://www.linkedin.com/in/{slug}"}
    except Exception:
        pass
    return out


def warm_path_cells(cfg: Config, job_ids: list[str]) -> dict[str, tuple[str, str]]:
    """job_id -> (Warm Path cell, Path Evidence cell) for the sheet.

    The best proposed bridge per role that names a person. Warm Path is a
    HYPERLINK to the person's LinkedIn profile labelled name, title at
    company; evidence carries the tier, the evidence line and the draft
    ask. A role with nobody gets the canon defaults, never a blank.
    """
    out: dict[str, tuple[str, str]] = {}
    if not job_ids:
        return out
    cands: list[dict] = []
    for i in range(0, len(job_ids), 100):
        chunk = job_ids[i:i + 100]
        cands.extend(db_get(cfg, "bridge_candidates", {
            "select": "job_id,contact_key,path_tier,path_evidence,bridge_score,draft_ask,state",
            "job_id": "in.(" + ",".join(f'"{j}"' for j in chunk) + ")",
            "state": "in.(proposed,reached_out)",
            "order": "bridge_score.desc", "limit": ALL_ROWS}))
    best: dict[str, dict] = {}
    for c in cands:
        if (c.get("contact_key") or "").startswith("peer:"):
            continue
        if c["job_id"] not in best:
            best[c["job_id"]] = c
    people = _person_lookup(cfg, [c["contact_key"] for c in best.values()])
    # a person the DB already names (an earlier run, or the incumbent) is a
    # better answer than None found when no bridge row exists
    named: dict[str, dict] = {}
    missing = [j for j in job_ids if j not in best]
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        for r in db_get(cfg, "hunter_seen_roles", {
                "select": "job_id,warm_path_person,warm_path_tier,warm_path_evidence",
                "job_id": "in.(" + ",".join(f'"{j}"' for j in chunk) + ")",
                "warm_path_person": "not.is.null", "limit": "500"}):
            person = (r.get("warm_path_person") or "").strip()
            if person and not re.match(r"^\s*(none|n/a|nobody|unknown)\b", person, re.I):
                named[r["job_id"]] = r
    for jid in job_ids:
        c = best.get(jid)
        if not c and jid in named:
            r = named[jid]
            out[jid] = (plain_text(r["warm_path_person"]),
                        plain_text(f"{r.get('warm_path_tier') or 'known'}: "
                                   f"{r.get('warm_path_evidence') or 'named on the role record'}")[:500])
            continue
        if not c:
            out[jid] = (WARM_NONE, EVIDENCE_NONE)
            continue
        key = c["contact_key"]
        if key.startswith("headhunter:"):
            firm = key.split(":", 1)[1].replace("-", " ").title()
            m = re.search(r"HEADHUNTER PATH: (.+?) at (.+?) \(", c.get("path_evidence") or "")
            warm = (f"{m.group(1)} at {m.group(2)} (headhunter)" if m
                    else f"{firm} (headhunter)")
        else:
            p = people.get(key) or {}
            name = p.get("name") or key
            bits = [name]
            if p.get("title"):
                bits.append(p["title"])
            label = ", ".join(bits) + (f" at {p['company']}" if p.get("company") else "")
            url = p.get("linkedin_url") or ""
            warm = hyperlink(url, label) if url.startswith("http") else label
        evidence = (f"{c['path_tier']}: {plain_text(c.get('path_evidence') or '')}. "
                    f"Ask: {plain_text(c.get('draft_ask') or '')}")
        out[jid] = (warm, evidence[:500])
    return out


COLD_PROMPT = """Krish Raja is applying for the role "{title}" at {company}{loc}.
Find the one person at {company} most likely to own or sit beside this hire:
for a company under about 200 people the CEO or a co-founder; otherwise the
executive this role reports into (CRO, COO, Chief of Staff to the CEO, Head of
Talent) or the leader of the function named in the title. Use web search.

Answer with ONE JSON object and nothing else:
{{"name": "...", "title": "...", "linkedin_url": "https://www.linkedin.com/in/...",
  "source_url": "the page that shows this person in that role", "why": "one sentence"}}
If you cannot find a named person with a source, answer {{"name": null}}.
Never invent a LinkedIn URL: leave it null unless a page showed it."""


def cold_targets(cfg: Config, roles: list[dict], cap: int | None = None) -> dict:
    """A named person at each Yes company with no in-network path.

    One Claude call with web search per role, capped per run. The person
    lands in network_contacts (source hunter cold target) and a cold_target
    bridge, so the Warm Path cell can link to them. Nothing is sent.
    """
    if cap is None:
        cap = int(cfg.optional(COLD_KEY, "30")) if cfg is not None else 30
    stats = {"eligible": 0, "searched": 0, "found": 0, "skipped": []}
    if not roles or cap <= 0:
        return stats
    jids = [r["job_id"] for r in roles]
    have: dict[str, set[str]] = {}
    for i in range(0, len(jids), 100):
        chunk = jids[i:i + 100]
        for c in db_get(cfg, "bridge_candidates", {
                "select": "job_id,contact_key,path_tier",
                "job_id": "in.(" + ",".join(f'"{j}"' for j in chunk) + ")",
                "limit": ALL_ROWS}):
            if not (c.get("contact_key") or "").startswith("peer:"):
                have.setdefault(c["job_id"], set()).add(c["path_tier"])
    todo = [r for r in roles if not have.get(r["job_id"])]
    stats["eligible"] = len(todo)
    if not todo:
        return stats
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.require("hunter_anthropic_api_key"))
    model = cfg.optional("hunter_anthropic_model", "claude-opus-5")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for r in todo[:cap]:
        stats["searched"] += 1
        loc = f" ({r['location']})" if r.get("location") else ""
        try:
            resp = client.messages.create(
                model=model, max_tokens=4000,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}],
                messages=[{"role": "user", "content": COLD_PROMPT.format(
                    title=r["title"], company=r["company"], loc=loc)}])
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            data = json.loads(m.group(0)) if m else {}
        except Exception as e:
            stats["skipped"].append(f"{r['job_id']}: {e.__class__.__name__}")
            continue
        name = (data.get("name") or "").strip() if isinstance(data, dict) else ""
        if not name:
            stats["skipped"].append(f"{r['job_id']}: nobody found with a source")
            continue
        url = (data.get("linkedin_url") or "").strip()
        slug = li_slug(url) if url else None
        key = slug or f"cold:{slugify(name)}"
        evidence = (f"COLD TARGET, found by web search: {name}, {data.get('title') or 'title unknown'} "
                    f"at {r['company']}. {data.get('why') or ''} "
                    f"Source: {data.get('source_url') or 'not given'}")
        db_insert(cfg, "network_contacts", [{
            "contact_key": key, "linkedin_url": url or None, "full_name": name,
            "current_company": r["company"], "current_title": data.get("title") or None,
            "strength_score": 0, "strength_evidence": {"cold_target": True},
            "source": "hunter cold target", "updated_at": now}],
            on_conflict="contact_key", merge=True)
        db_insert(cfg, "bridge_candidates", [_candidate(
            r, key, "cold_target", plain_text(evidence)[:900], "outside network, named",
            TIER_BASE["cold_target"],
            DRAFTS["cold_target"].format(company=r["company"], role=r["title"]), now)],
            on_conflict="job_id,contact_key,path_tier", merge=True)
        stats["found"] += 1
    return stats
