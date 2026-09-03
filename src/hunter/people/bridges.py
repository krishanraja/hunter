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
import re

from ..config import Config, db_delete, db_get, db_insert, db_patch
from ..router import GO_WORDS
from ..sheet import Sheet
from ..sources import slugify
from .strength import EVIDENCE_KEYS  # noqa: F401  (re-export for the guard test)

# newsletter_move sits between ex_employee and current_employee on purpose:
# a person named in the a16z newsletter as having just joined the company is
# timelier than an ex-employee and colder than anyone Krish actually knows.
TIER_BASE = {"current_employee": 40, "newsletter_move": 30, "ex_employee": 25,
             "headhunter": 20, "peer_transition": 10}
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
}


def target_roles(cfg: Config, limit: int = 60) -> list[dict]:
    """Roles worth a warm path: on Krish's sheet now, or ones he said go to.

    presented_at is the test of "on his sheet". The retired incumbent left
    fifteen rows at status staging that it never wrote to the sheet, among
    them ElevenLabs GM seats in Brazil, Mexico and Saudi Arabia, and bridges
    were being built into roles he had never been shown.
    """
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,score,status,krish_verdict,warm_path_person",
        "status": "in.(staging,presented)",
        "presented_at": "not.is.null",
        "order": "score.desc.nullslast",
        "limit": str(limit)})
    gos = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,score,status,krish_verdict,warm_path_person",
        "krish_verdict": "not.is.null", "status": "neq.duplicate",
        "limit": str(limit)})
    seen, out = set(), []
    for r in rows + [g for g in gos
                     if (g.get("krish_verdict") or "").strip().lower() in GO_WORDS]:
        if r["job_id"] not in seen:
            seen.add(r["job_id"])
            out.append(r)
    return out


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
        "select": "bridge_id,job_id", "state": "eq.proposed", "limit": "5000"})
    stale = [p["bridge_id"] for p in proposed
             if p.get("bridge_id") and p.get("job_id") not in live]
    for i in range(0, len(stale), 100):
        db_delete(cfg, "bridge_candidates", {
            "bridge_id": "in.(" + ",".join(stale[i:i + 100]) + ")",
            "state": "eq.proposed"})
    return len(stale)


def build_bridges(cfg: Config, sheet: Sheet, min_strength: int = 25) -> dict:
    roles = target_roles(cfg)
    retired = retire_stale(cfg, roles)
    contacts = db_get(cfg, "network_contacts", {
        "select": "contact_key,full_name,current_company,current_title,"
                  "strength_score,strength_evidence,employment_history",
        "order": "strength_score.desc", "limit": "5000"})
    by_company: dict[str, list[dict]] = {}
    for c in contacts:
        if c.get("current_company"):
            by_company.setdefault(slugify(c["current_company"]), []).append(c)

    headhunters = load_headhunters(sheet)
    hh_role_hits: dict[str, list[dict]] = {}
    for hh in headhunters:
        firm_fams = coverage_families(
            f"{hh.get('Title', '')} {hh.get('Why-relevant', '')}")
        hits = [r for r in roles if firm_fams & coverage_families(r["title"])]
        if len(hits) >= 3:  # Q4: surfaced only with real coverage overlap
            hh_role_hits[hh.get("Firm", "")] = hits

    upserts, warm_patches = [], {}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for role in roles:
        cslug = slugify(role["company"])
        found_in_network = False

        for c in by_company.get(cslug, [])[:3]:
            if c["strength_score"] < min_strength:
                continue
            found_in_network = True
            score = (TIER_BASE["current_employee"] + c["strength_score"] * 0.5
                     + _recency_bonus(c.get("strength_evidence") or {}))
            upserts.append(_candidate(
                role, c["contact_key"], "current_employee",
                f"{c['full_name']} is {c.get('current_title') or 'at'} "
                f"{role['company']} now; strength {c['strength_score']}",
                "works there now", score,
                DRAFTS["current_employee"].format(company=role["company"],
                                                  role=role["title"]), now))
            if score > warm_patches.get(role["job_id"], (0, None, ""))[0]:
                warm_patches[role["job_id"]] = (score, c, "current_employee")

        for c in contacts:
            if c["strength_score"] < min_strength or slugify(c.get("current_company") or "") == cslug:
                continue
            if cslug in _employment_companies(c.get("employment_history")):
                found_in_network = True
                score = (TIER_BASE["ex_employee"] + c["strength_score"] * 0.5
                         + _recency_bonus(c.get("strength_evidence") or {}))
                upserts.append(_candidate(
                    role, c["contact_key"], "ex_employee",
                    f"{c['full_name']} previously worked at {role['company']} "
                    f"per profile history; strength {c['strength_score']}",
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

    for job_id, (score, c, tier) in warm_patches.items():
        db_patch(cfg, "hunter_seen_roles", {"job_id": job_id}, {
            "warm_path_person": c["full_name"],
            "warm_path_tier": tier,
            "warm_path_evidence": f"strength {c['strength_score']}, "
                                  f"bridge score {round(score, 1)}"})
    return {"roles": len(roles), "bridges": len(upserts), "retired": retired,
            "warm_paths_set": len(warm_patches),
            "headhunter_firms_surfaced": len(hh_role_hits)}


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
