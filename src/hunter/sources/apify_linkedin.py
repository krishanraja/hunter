"""The only Apify path in the repo. Every call goes through run_actor, which
carries three non-negotiables:

1. The forbidden actors hard-fail before any HTTP. BHzefUZlZRKWxkTck returns
   cached global data regardless of filters; pZezG04IIqOdtiwu7 is a rented
   actor Krish does not have. Calling either is a run-level failure.
2. Every actor input carries maxTotalChargeUsd.
3. A run-scoped SpendTracker soft-stops all further sourcing at the per-run
   cap; the stop is reported, never silent.

Dedupe against hunter_seen_roles.job_id happens BEFORE any paid call, in the
orchestrator, never after.
"""
from __future__ import annotations

import time

import requests

from ..sources import RolePosting

APIFY = "https://api.apify.com/v2"

PRIMARY_LINKEDIN = "hKByXkMQaC5Qt9UMN"
SECONDARY_WORKDAY = "FJKQ5hqMjjwEVXdHG"     # filtering broken; filter client-side
BACKUP_CAREER_SITE = "s3dtSTZSZWFtAVLn5"    # $0.012/job; budget-gated, sparingly
FORBIDDEN_ACTORS = frozenset({"BHzefUZlZRKWxkTck", "pZezG04IIqOdtiwu7"})


class ForbiddenActorError(RuntimeError):
    """Hard-fails the entire run; never catch this to continue sourcing."""


class SpendTracker:
    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self.spent = 0.0
        self.stopped = False

    def can_spend(self, usd: float) -> bool:
        if self.spent + usd > self.cap:
            self.stopped = True
            return False
        return True

    def add(self, usd: float) -> None:
        self.spent += usd


def run_actor(cfg, actor_id: str, input_obj: dict, *, max_charge_usd: float,
              spend: SpendTracker | None = None,
              poll_seconds: int = 10, timeout_seconds: int = 900,
              token_key: str = "hunter_apify_token") -> list[dict]:
    if actor_id in FORBIDDEN_ACTORS:
        raise ForbiddenActorError(
            f"actor {actor_id} is forbidden by the brief; the run must stop")
    if spend is not None and not spend.can_spend(max_charge_usd):
        raise RuntimeError(
            f"per-run Apify budget exhausted (cap ${spend.cap:.2f}); sourcing "
            f"soft-stopped, report it")
    token = cfg.require(token_key)
    body = dict(input_obj)
    body["maxTotalChargeUsd"] = max_charge_usd

    r = requests.post(f"{APIFY}/acts/{actor_id}/runs",
                      params={"token": token}, json=body, timeout=60)
    r.raise_for_status()
    run = r.json()["data"]
    run_id = run["id"]

    deadline = time.time() + timeout_seconds
    status = run.get("status", "RUNNING")
    while status in ("READY", "RUNNING") and time.time() < deadline:
        time.sleep(poll_seconds)
        rr = requests.get(f"{APIFY}/actor-runs/{run_id}",
                          params={"token": token}, timeout=30)
        rr.raise_for_status()
        run = rr.json()["data"]
        status = run.get("status")
    if spend is not None:
        charged = (run.get("usage") or {}).get("TOTAL_USD") or run.get(
            "usageTotalUsd") or max_charge_usd
        spend.add(float(charged))
    if status != "SUCCEEDED":
        raise RuntimeError(f"actor run {run_id} ended {status}")

    dataset_id = run["defaultDatasetId"]
    items: list[dict] = []
    offset = 0
    while True:
        dr = requests.get(f"{APIFY}/datasets/{dataset_id}/items",
                          params={"token": token, "offset": offset,
                                  "limit": 500, "format": "json"}, timeout=60)
        dr.raise_for_status()
        page = dr.json()
        items.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return items


def sweep_linkedin(cfg, search_urls: list[str], *, spend: SpendTracker,
                   max_charge_usd: float, results_limit: int = 100) -> list[RolePosting]:
    items = run_actor(cfg, PRIMARY_LINKEDIN,
                      {"urls": search_urls, "resultsLimit": results_limit},
                      max_charge_usd=max_charge_usd, spend=spend)
    out = []
    for j in items:
        out.append(RolePosting(
            company=j.get("companyName", ""), title=j.get("title", ""),
            url=j.get("jobUrl") or j.get("link") or "",
            source="apify_linkedin",
            location=j.get("location"),
            comp_text=j.get("salaryInfo") if isinstance(j.get("salaryInfo"), str)
            else None,
            posted_at=j.get("postedAt"), raw=j))
    return out
