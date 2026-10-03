"""The only Apify path in the repo. Every call goes through run_actor.

What a paid run must never do, each one a thing it did:

1. Run without a cap Apify enforces. maxTotalChargeUsd was put in the actor's
   INPUT, which the actor ignores; Apify reads it from the request. Live runs
   showed isMaxTotalChargeUsdSetByUser false and a cap of about $191, which was
   simply the account balance. The cap and maxItems now travel as query
   parameters, and the run's own options are read back: a run whose cap is not
   the one asked for is aborted before it buys anything.
2. Buy what it already bought. 69 percent of the $55 spent on LinkedIn sweeps
   between 1 September and 3 October paid for postings an earlier sweep had
   already bought: every search asked for "past week" and sweeps ran two or
   more times a week, three times on some days. Each sweep's window is now the
   time since the last successful sweep plus a small overlap.
3. Start a second paid run for one sweep. A connection error while polling
   used to start a whole new run with the first still going and unrecorded.
   Only a failed START may be retried, because only then does no run exist.
4. Put the token where it can leak. It travelled as ?token= and an HTTP error
   message carries the URL into the emailed summary. It is a header now.
5. Spend without a ceiling. A per-run tracker existed and nothing above it.
   hunter_apify_max_usd_per_month is checked before any request, from hunter's
   own ledger of runs (hunter_apify_runs) cross-checked against Apify's.

The forbidden actors still hard-fail before any HTTP: BHzefUZlZRKWxkTck returns
cached global data regardless of filters; pZezG04IIqOdtiwu7 is a rented actor
Krish does not have.
"""
from __future__ import annotations

import datetime
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests.exceptions import ConnectionError as TransportResetError

from ..sources import RolePosting

APIFY = "https://api.apify.com/v2"

PRIMARY_LINKEDIN = "hKByXkMQaC5Qt9UMN"
SECONDARY_WORKDAY = "FJKQ5hqMjjwEVXdHG"     # filtering broken; filter client-side
BACKUP_CAREER_SITE = "s3dtSTZSZWFtAVLn5"    # $0.012/job; budget-gated, sparingly
FORBIDDEN_ACTORS = frozenset({"BHzefUZlZRKWxkTck", "pZezG04IIqOdtiwu7"})
LEDGER = "hunter_apify_runs"

# The LinkedIn actor's own price on a paid plan, per result, and per start.
PRICE_PER_ITEM = 0.001
# A window shorter than this asks LinkedIn for almost nothing; one longer than
# a month is not "since the last sweep" any more, it is a backfill.
MIN_WINDOW = datetime.timedelta(hours=6)
MAX_WINDOW = datetime.timedelta(days=30)
OVERLAP = datetime.timedelta(hours=3)


class ForbiddenActorError(RuntimeError):
    """Hard-fails the entire run; never catch this to continue sourcing."""


class ApifyStartFailed(RuntimeError):
    """No run exists. The only Apify failure that is safe to retry."""


class ApifyRunError(RuntimeError):
    """A run exists and failed or could not be followed. It has been aborted;
    retrying would start a second paid run."""


class ApifyCapNotApplied(ApifyRunError):
    """Apify did not apply the cap that was asked for. The run was aborted
    before it could buy anything."""


class ApifyCeilingReached(RuntimeError):
    """This month's ceiling would be passed. Nothing was started."""


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


TOKENISH = re.compile(r"(token=)[^&\s'\"]+|apify_api_[A-Za-z0-9]+")


def redact(text: str) -> str:
    """An Apify token, wherever it appears in a line, reduced to a marker."""
    return TOKENISH.sub(lambda m: (m.group(1) or "") + "[redacted]", text or "")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _db(cfg):
    from ..config import db_get, db_insert, db_patch
    return db_get, db_insert, db_patch


def month_to_date_usd(cfg, token: str, *, actor_id: str | None = None) -> float:
    """What hunter's Apify runs have cost since the start of this UTC month.

    hunter's own ledger first. For an actor only hunter runs (the LinkedIn
    jobs actor), Apify's own list of that actor's runs is the cross-check, and
    the larger figure wins, so a ledger write that failed cannot make the
    month look cheaper than it was.
    """
    db_get, _, db_patch = _db(cfg)
    start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    try:
        rows = db_get(cfg, LEDGER, {"select": "run_id,usage_usd,finished_at",
                                    "started_at": f"gte.{start.isoformat()}"})
    except Exception:
        rows = []
    for r in rows:
        usd = r.get("usage_usd")
        if usd is None and not r.get("finished_at"):
            try:
                got = requests.get(f"{APIFY}/actor-runs/{r['run_id']}",
                                   headers=_headers(token), timeout=30)
                if got.status_code == 200:
                    usd = (got.json().get("data") or {}).get("usageTotalUsd")
            except requests.RequestException:
                usd = None
        total += float(usd or 0)
    if actor_id == PRIMARY_LINKEDIN:
        theirs = 0.0
        try:
            got = requests.get(f"{APIFY}/acts/{actor_id}/runs",
                               params={"desc": 1, "limit": 200},
                               headers=_headers(token), timeout=30)
            if got.status_code == 200:
                for run in (got.json().get("data") or {}).get("items") or []:
                    at = str(run.get("startedAt") or "")
                    if at >= start.isoformat()[:19]:
                        theirs += float(run.get("usageTotalUsd") or 0)
        except requests.RequestException:
            pass
        total = max(total, theirs)
    return round(total, 4)


def run_actor(cfg, actor_id: str, input_obj: dict, *, max_charge_usd: float,
              max_items: int | None = None, spend: SpendTracker | None = None,
              purpose: str = "", query_key: str | None = None,
              poll_seconds: int = 10, timeout_seconds: int = 1800,
              token_key: str = "hunter_apify_token") -> list[dict]:
    if actor_id in FORBIDDEN_ACTORS:
        raise ForbiddenActorError(
            f"actor {actor_id} is forbidden by the brief; the run must stop")
    if spend is not None and not spend.can_spend(max_charge_usd):
        raise RuntimeError(
            f"per-run Apify budget exhausted (cap ${spend.cap:.2f}); sourcing "
            f"soft-stopped, report it")
    token = cfg.require(token_key)
    ceiling = float(cfg.optional("hunter_apify_max_usd_per_month", "40"))
    spent = month_to_date_usd(cfg, token, actor_id=actor_id)
    if spent + max_charge_usd > ceiling:
        raise ApifyCeilingReached(
            f"Apify this month: ${spent:.2f} of the ${ceiling:.2f} ceiling; a run "
            f"capped at ${max_charge_usd:.2f} would pass it, so nothing was started")

    params = {"maxTotalChargeUsd": f"{max_charge_usd:.4f}"}
    if max_items:
        params["maxItems"] = str(int(max_items))

    # The egress proxy occasionally resets the first connection to Apify.
    # Retrying the start POST is safe: no run exists until it succeeds.
    r = None
    for attempt in range(3):
        try:
            r = requests.post(f"{APIFY}/acts/{actor_id}/runs", params=params,
                              headers=_headers(token), json=dict(input_obj), timeout=60)
            break
        except TransportResetError as e:
            if attempt == 2:
                raise ApifyStartFailed(f"apify start: {e.__class__.__name__}") from None
            time.sleep(2 * (attempt + 1))
    if r is None or r.status_code >= 300:
        raise ApifyStartFailed(f"apify {getattr(r, 'status_code', '?')} on start")
    run = r.json()["data"]
    run_id = run["id"]

    # Read back what Apify actually applied, before anything is bought.
    opts = run.get("options") or {}
    applied = opts.get("maxTotalChargeUsd")
    if (applied is None or not opts.get("isMaxTotalChargeUsdSetByUser", True)
            or float(applied) > max_charge_usd + 1e-6):
        _abort(run_id, token)
        raise ApifyCapNotApplied(
            f"apify run {run_id} came back with a cap of {applied!r}, not "
            f"${max_charge_usd:.2f}; aborted before it bought anything")
    _ledger_start(cfg, run_id, actor_id, purpose, query_key, max_charge_usd,
                  float(applied), max_items)

    deadline = time.time() + timeout_seconds
    status = run.get("status", "RUNNING")
    misses = 0
    while status in ("READY", "RUNNING") and time.time() < deadline:
        time.sleep(poll_seconds)
        try:
            rr = requests.get(f"{APIFY}/actor-runs/{run_id}",
                              headers=_headers(token), timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            # The same run, asked again. Never a second run.
            misses += 1
            if misses >= 6:
                _abort(run_id, token)
                _ledger_finish(cfg, run_id, "LOST", None, None)
                raise ApifyRunError(f"apify run {run_id} could not be followed; "
                                    f"aborted") from None
            continue
        if rr.status_code >= 300:
            _abort(run_id, token)
            _ledger_finish(cfg, run_id, f"HTTP {rr.status_code}", None, None)
            raise ApifyRunError(f"apify {rr.status_code} following run {run_id}; aborted")
        misses = 0
        run = rr.json()["data"]
        status = run.get("status")
    charged = (run.get("usage") or {}).get("TOTAL_USD") or run.get("usageTotalUsd")
    if spend is not None:
        spend.add(float(charged if charged is not None else max_charge_usd))

    # A run still going when the clock runs out has already filled its
    # dataset, and the charge lands whether hunter reads it or not. On
    # 2026-09-02 a nine-URL sweep was still RUNNING at the deadline with 2790
    # items collected, and hunter threw all of them away and reported zero
    # roles: it paid $2.67 for nothing. Take what the dataset holds, stop the
    # run so it charges no further, and say what happened.
    if status in ("READY", "RUNNING"):
        _abort(run_id, token)
        print(f"apify run {run_id} still {status} at the {timeout_seconds}s "
              f"deadline; aborting it and using what the dataset already holds")
        items = _dataset_items(run.get("defaultDatasetId"), token)
        _ledger_finish(cfg, run_id, "ABORTED", len(items), charged)
        return items
    items = _dataset_items(run.get("defaultDatasetId"), token)
    _ledger_finish(cfg, run_id, status, len(items), charged)
    if status != "SUCCEEDED":
        # A genuinely failed run may still have partial results worth having.
        # Only an empty dataset is a real failure.
        if not items:
            raise ApifyRunError(f"actor run {run_id} ended {status} with no results")
        print(f"apify run {run_id} ended {status} but left {len(items)} "
              f"items; using them")
    return items


def _abort(run_id: str, token: str) -> None:
    try:
        got = requests.post(f"{APIFY}/actor-runs/{run_id}/abort",
                            headers=_headers(token), timeout=30)
        if got.status_code >= 300:
            print(f"apify run {run_id}: abort answered {got.status_code}")
    except requests.RequestException as e:
        # Said, not swallowed: a run that could not be stopped keeps billing
        # until its own timeout.
        print(f"apify run {run_id}: abort failed ({e.__class__.__name__})")


def _ledger_start(cfg, run_id, actor_id, purpose, query_key, cap, applied, max_items):
    _, db_insert, _ = _db(cfg)
    try:
        db_insert(cfg, LEDGER, [{
            "run_id": run_id, "actor_id": actor_id, "purpose": purpose or "unnamed",
            "query_key": query_key, "max_charge_usd": cap, "applied_cap_usd": applied,
            "max_items": max_items, "status": "RUNNING"}])
    except Exception as e:
        print(f"apify ledger: start of {run_id} not recorded ({e.__class__.__name__})")


def _ledger_finish(cfg, run_id, status, items, usage):
    _, _, db_patch = _db(cfg)
    try:
        db_patch(cfg, LEDGER, {"run_id": run_id}, {
            "status": status, "finished_at": _now().isoformat(), "items": items,
            "usage_usd": None if usage is None else float(usage)})
    except Exception as e:
        print(f"apify ledger: end of {run_id} not recorded ({e.__class__.__name__})")


def _dataset_items(dataset_id: str | None, token: str) -> list[dict]:
    """Every item in an Apify dataset, paged. Never raises on an absent
    dataset: no dataset means no results, not a crash."""
    if not dataset_id:
        return []
    items: list[dict] = []
    offset = 0
    while True:
        dr = requests.get(f"{APIFY}/datasets/{dataset_id}/items",
                          params={"offset": offset, "limit": 500, "format": "json"},
                          headers=_headers(token), timeout=60)
        if dr.status_code >= 300:
            raise ApifyRunError(f"apify {dr.status_code} reading dataset {dataset_id}")
        page = dr.json()
        items.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return items


def last_sweep_started(cfg, token: str) -> datetime.datetime | None:
    """When the last LinkedIn sweep that returned anything started.

    hunter's ledger first; before the ledger existed, Apify's own record of the
    actor's runs. None means no sweep is known, and the caller falls back to a
    week.
    """
    db_get, _, _ = _db(cfg)
    try:
        rows = db_get(cfg, LEDGER, {
            "select": "started_at", "actor_id": f"eq.{PRIMARY_LINKEDIN}",
            "status": "in.(SUCCEEDED,ABORTED)", "items": "gt.0",
            "order": "started_at.desc", "limit": "1"})
        if rows:
            return datetime.datetime.fromisoformat(
                str(rows[0]["started_at"]).replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        got = requests.get(f"{APIFY}/acts/{PRIMARY_LINKEDIN}/runs",
                           params={"desc": 1, "limit": 20, "status": "SUCCEEDED"},
                           headers=_headers(token), timeout=30)
        if got.status_code == 200:
            for run in (got.json().get("data") or {}).get("items") or []:
                # A full sweep costs dollars; a one-search probe costs cents.
                if float(run.get("usageTotalUsd") or 0) > 0.5:
                    return datetime.datetime.fromisoformat(
                        str(run["startedAt"]).replace("Z", "+00:00"))
    except requests.RequestException:
        pass
    return None


def sweep_window(last: datetime.datetime | None, now: datetime.datetime) -> datetime.timedelta:
    """How far back this sweep should ask LinkedIn to look."""
    if last is None:
        return datetime.timedelta(days=7)
    window = (now - last) + OVERLAP
    return max(MIN_WINDOW, min(MAX_WINDOW, window))


def with_window(url: str, window: datetime.timedelta) -> str:
    """The search URL with LinkedIn's posted-within filter set to the window.
    f_TPR takes r<seconds>; any value works, not only LinkedIn's presets."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != "f_TPR"]
    query.append(("f_TPR", f"r{int(window.total_seconds())}"))
    return urlunsplit(parts._replace(query=urlencode(query)))


def sweep_linkedin(cfg, search_urls: list[str], *, spend: SpendTracker,
                   max_charge_usd: float, limit_per_source: int = 200,
                   window: datetime.timedelta | None = None) -> list[RolePosting]:
    urls = [with_window(u, window) for u in search_urls] if window else list(search_urls)
    # The cap that matters is items: at a tenth of a cent each, the dollar cap
    # and the item cap say the same thing, and both are enforced by Apify.
    max_items = min(limit_per_source * len(urls), int(max_charge_usd / PRICE_PER_ITEM))
    body = {"urls": urls, "limitPerSource": limit_per_source,
            # Only the description is read downstream (posting_text); the
            # company scrape is an extra request per job for nothing.
            "scrapeCompany": False}
    ai = cfg.optional("hunter_linkedin_auto_ai_search", "")
    if ai in ("true", "false"):
        body["autoConvertToAiSearch"] = ai == "true"
    items = run_actor(cfg, PRIMARY_LINKEDIN, body, max_charge_usd=max_charge_usd,
                      max_items=max_items, spend=spend, purpose="linkedin_sweep")
    out = []
    for j in items:
        out.append(RolePosting(
            company=j.get("companyName", ""), title=j.get("title", ""),
            url=j.get("jobUrl") or j.get("link") or "",
            source="apify_linkedin",
            location=j.get("location"),
            # the actor names the field salary, not salaryInfo; the old key
            # never matched, so every LinkedIn role reached G2 with no band
            comp_text=next((j[k] for k in ("salary", "salaryInfo")
                            if isinstance(j.get(k), str) and j[k].strip()), None),
            posted_at=j.get("postedAt"), raw=j))
    return out
