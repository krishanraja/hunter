"""Lazy enrichment, per Krish's Q1 ruling: only contacts whose current
company matches a target (canon universe or a live pipeline company) get a
profile pull, through the one sanctioned Apify path, on the dedicated
enrichment token, under its own spend caps. Never the whole graph.
"""
from __future__ import annotations

import datetime

from ..config import Config, db_get, db_patch
from ..sources import slugify
from ..sources.apify_linkedin import SpendTracker, run_actor

ENRICHMENT_ACTOR = "2SyF0bVxmgGr8IVCZ"  # dev_fusion/Linkedin-Profile-Scraper
REENRICH_DAYS = 90


def eligible(cfg: Config, target_slugs: set[str], cap: int) -> list[dict]:
    rows = db_get(cfg, "network_contacts", {
        "select": "contact_key,linkedin_url,current_company,strength_score,enriched_at",
        "order": "strength_score.desc",
        "limit": "5000"})
    cutoff = (datetime.date.today() - datetime.timedelta(days=REENRICH_DAYS)).isoformat()
    out = []
    for r in rows:
        company = r.get("current_company") or ""
        if not company or slugify(company) not in target_slugs:
            continue
        if (r.get("enriched_at") or "") >= cutoff:
            continue
        out.append(r)
        if len(out) >= cap:
            break
    return out


def enrich(cfg: Config, target_slugs: set[str]) -> dict:
    cap = int(cfg.optional("hunter_enrich_max_profiles_per_run", "25"))
    max_usd = float(cfg.optional("hunter_enrich_max_usd_per_run", "2.00"))
    todo = eligible(cfg, target_slugs, cap)
    if not todo:
        return {"eligible": 0, "enriched": 0, "spend_usd": 0.0}
    spend = SpendTracker(cap_usd=max_usd)
    items = run_actor(
        cfg, ENRICHMENT_ACTOR,
        {"profileUrls": [r["linkedin_url"] for r in todo]},
        max_charge_usd=max_usd, spend=spend,
        token_key="hunter_apify_enrichment_token")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    by_key = {}
    for item in items:
        from . import li_slug
        slug = li_slug(item.get("linkedinUrl") or item.get("url")
                       or item.get("profileUrl") or item.get("inputUrl"))
        if slug:
            by_key[slug] = item
    enriched = 0
    for r in todo:
        item = by_key.get(r["contact_key"])
        patch = {"enriched_at": now}
        if item:
            history = (item.get("experiences") or item.get("experience")
                       or item.get("positions") or [])
            patch["employment_history"] = _professional_only(history)
            patch["enrichment_status"] = "done"
            if item.get("companyName"):
                patch["current_company"] = item["companyName"]
            if item.get("jobTitle") or item.get("headline"):
                patch["current_title"] = item.get("jobTitle") or item.get("headline")
            enriched += 1
        else:
            patch["enrichment_status"] = "no_result"
        db_patch(cfg, "network_contacts", {"contact_key": r["contact_key"]}, patch)
    return {"eligible": len(todo), "enriched": enriched,
            "spend_usd": round(spend.spent, 2)}


def _professional_only(history: list) -> list[dict]:
    """Employment fields only; drop anything else the actor returns."""
    keep = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        keep.append({k: h.get(k) for k in
                     ("companyName", "company", "title", "jobTitle",
                      "startDate", "endDate", "duration", "location")
                     if h.get(k) is not None})
    return keep
