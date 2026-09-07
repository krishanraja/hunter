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

from ..config import Config, db_delete, db_get, db_insert, db_patch
from ..router import GO_WORDS
from ..sheet import EVIDENCE_NONE, WARM_NONE, Sheet, hyperlink, plain_text
from ..sources import distinctive_tokens, slugify
from . import li_slug
from .strength import EVIDENCE_KEYS  # noqa: F401  (re-export for the guard test)

# newsletter_move sits between ex_employee and current_employee on purpose:
# a person named in the a16z newsletter as having just joined the company is
# timelier than an ex-employee and colder than anyone Krish actually knows.
TIER_BASE = {"current_employee": 40, "newsletter_move": 30, "ex_employee": 25,
             "headhunter": 20, "cold_target": 15, "peer_transition": 10}

# Control Center's graph (contacts + contact_intelligence) scores relationship
# by tier, not by message counts. Mapped onto the same 0..100 strength scale
# network_contacts uses, so one min_strength cut applies to both.
CC_TIER_STRENGTH = {"1_reciprocated": 70, "2_core_network": 50,
                    "3_known_network": 30, "4_owned_network": 15, "5_cold_lead": 5}
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
                    "strength_evidence": {"cc_tier": tier} if tier else {},
                    "employment_history": [], "linkedin_url": r.get("linkedin_url"),
                    "graph": "contacts"})
    return out


def build_bridges(cfg: Config, sheet: Sheet, min_strength: int = 25) -> dict:
    roles = target_roles(cfg)
    retired = retire_stale(cfg, roles)
    contacts = db_get(cfg, "network_contacts", {
        "select": "contact_key,full_name,current_company,current_title,"
                  "strength_score,strength_evidence,employment_history",
        "order": "strength_score.desc", "limit": "5000"})
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
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for role in roles:
        cslug = slugify(role["company"])
        found_in_network = False

        # exact slug first, then any company sharing a distinctive token
        # ("Google" for "Google (YouTube Partnerships)"), strongest first
        rtoks = distinctive_tokens(role["company"])
        pool = list(by_company.get(cslug, []))
        for key, toks in company_tokens.items():
            if key != cslug and rtoks and toks & rtoks:
                pool.extend(by_company.get(key, []))
        pool.sort(key=lambda c: -(c.get("strength_score") or 0))
        for c in pool[:3]:
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


def clear_junk_warm_paths(cfg: Config) -> int:
    """Null the placeholder sentences the retired incumbent wrote into
    warm_path_person ("None identified with...", "None. No connections").
    A placeholder read as a person put "bridge first" on rows with nobody
    to bridge through."""
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,warm_path_person",
        "warm_path_person": "ilike.none*", "limit": "5000"})
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
            out[r["contact_key"]] = {
                "name": r.get("full_name") or "", "title": r.get("current_title") or "",
                "company": r.get("current_company") or "",
                "linkedin_url": r.get("linkedin_url") or
                (f"https://www.linkedin.com/in/{r['contact_key']}"
                 if li_slug(f"https://www.linkedin.com/in/{r['contact_key']}") else "")}
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
            "order": "bridge_score.desc", "limit": "2000"}))
    best: dict[str, dict] = {}
    for c in cands:
        if (c.get("contact_key") or "").startswith("peer:"):
            continue
        if c["job_id"] not in best:
            best[c["job_id"]] = c
    people = _person_lookup(cfg, [c["contact_key"] for c in best.values()])
    for jid in job_ids:
        c = best.get(jid)
        if not c:
            out[jid] = (WARM_NONE, EVIDENCE_NONE)
            continue
        key = c["contact_key"]
        if key.startswith("headhunter:"):
            label = f"Headhunter: {key.split(':', 1)[1].replace('-', ' ').title()}"
            warm = label
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
                "limit": "2000"}):
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
