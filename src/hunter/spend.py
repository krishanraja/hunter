"""What every model call cost, and the ceiling on what a month may cost.

Why this exists. On 2026-10-07 Krish asked for the Anthropic bill to come down
by three times, and the first thing found was that nobody could say where it
went. hunter_judge_calls recorded the judge, $8.17 for the Sunday run of 4
October. The Console said that day cost about $21 on hunter's key. The other
thirteen dollars, the case, the CV and letter, the essays, the rationale, the
newsletter and the cold targets, were spent with no record at all, so no
change to any of them could be shown to have saved anything.

Every call now lands in hunter_judge_calls with a purpose, read back by
`python -m hunter.run spend` and summed at the end of every run.

The prices are the published per-million-token rates, in one table. There were
three copies of it (judge, lookalike, triage), and judge.usd_of returned 0.0
for any model not in its copy, so pointing hunter_judge_model at another model
would have switched the judge's dollar cap off without a word. A model with no
price here is costed at the dearest price in the table: a cap that counts too
much stops early, a cap that counts nothing never stops.
"""
from __future__ import annotations

import atexit
import datetime
import threading

# Per million tokens. Cache writes are the five minute rate, 1.25 times input;
# cache reads are the published cache price. The Batches API halves all four.
PRICES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-opus-5": {"in": 5.0, "out": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_read": 0.10, "cache_write": 1.25},
}
DEAREST = max(PRICES.values(), key=lambda p: p["out"])
# A web search is billed per search on top of the tokens: $10 a thousand.
WEB_SEARCH_USD = 0.01

# The ceiling on a calendar month's model spend, in dollars, across every
# purpose. At about $21 a full run and two runs a week the month came to about
# $180 before the 2026-10-07 changes; this leaves room for a measurement day
# and still stops a runaway. Override with hunter_llm_max_usd_per_month; 0
# switches it off.
DEFAULT_MONTH_CAP = "150"

TABLE = "hunter_judge_calls"

_LOCK = threading.Lock()
_PENDING: list[dict] = []       # rows not yet written
_RUN: dict[str, float] = {}     # purpose -> usd, this process
_CALLS: dict[str, int] = {}
_MONTH: dict = {}               # {"month": "2026-10", "usd": float} once read
_CFG = None
_REGISTERED = False


def price_of(model: str) -> dict:
    """The model's price, matching a dated or suffixed id to its family."""
    model = model or ""
    if model in PRICES:
        return PRICES[model]
    for name in sorted(PRICES, key=len, reverse=True):
        if model.startswith(name):
            return PRICES[name]
    return DEAREST


def usd_of(model: str, usage: dict, *, batch: bool = False) -> float:
    p = price_of(model)
    usd = (usage.get("input_tokens", 0) * p["in"]
           + usage.get("output_tokens", 0) * p["out"]
           + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
           + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]) / 1e6
    usd = usd / 2 if batch else usd
    return round(usd + usage.get("web_search_requests", 0) * WEB_SEARCH_USD, 6)


def usage_of(message) -> dict:
    """An Anthropic response's usage as plain ints. Missing fields are zero."""
    u = getattr(message, "usage", None)
    out = {k: int(getattr(u, k, 0) or 0) for k in
           ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens")}
    searches = int(getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0)
    if searches:
        out["web_search_requests"] = searches
    return out


def record(cfg, purpose: str, model: str, usage: dict, *, served_model: str = "",
           job_id: str | None = None, batch: bool = False) -> float:
    """Count one call and queue its row. Returns what it cost.

    Rows are written when the process ends (or on flush), not per call: a
    run makes hundreds of calls and the ledger must never be the thing that
    slows one down or fails it.
    """
    global _CFG, _REGISTERED
    usd = usd_of(model, usage, batch=batch)
    row = {"purpose": purpose, "model": model, "served_model": served_model or None,
           "job_id": job_id, "input_tokens": usage.get("input_tokens", 0),
           "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
           "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
           "output_tokens": usage.get("output_tokens", 0), "usd": usd}
    with _LOCK:
        _PENDING.append(row)
        _RUN[purpose] = _RUN.get(purpose, 0.0) + usd
        _CALLS[purpose] = _CALLS.get(purpose, 0) + 1
        if _live(cfg):
            _CFG = cfg
            if not _REGISTERED:
                atexit.register(flush)
                _REGISTERED = True
    return usd


def count(purpose: str, usd: float, calls: int = 1) -> None:
    """Add spend that another module already wrote to the ledger itself (the
    judge's own rows), so the run total and the month ceiling include it."""
    with _LOCK:
        _RUN[purpose] = _RUN.get(purpose, 0.0) + usd
        _CALLS[purpose] = _CALLS.get(purpose, 0) + calls
        if _MONTH:
            _MONTH["usd"] += usd


def _live(cfg) -> bool:
    """True only for a configuration read from Supabase by config.load().
    The offline tests build their own and must never reach the network."""
    from . import config
    return bool(getattr(config, "LOADED", False)) and bool(getattr(cfg, "supabase_url", ""))


def flush(cfg=None) -> str:
    """Write the queued rows. Returns a line saying what happened."""
    cfg = cfg or _CFG
    with _LOCK:
        rows, _PENDING[:] = list(_PENDING), []
    if not rows:
        return ""
    if cfg is None or not _live(cfg):
        return f"spend: {len(rows)} call(s) not written, no live configuration"
    from .config import db_insert
    try:
        db_insert(cfg, TABLE, rows)
    except Exception as e:
        return f"spend: {len(rows)} call(s) NOT written ({e.__class__.__name__})"
    with _LOCK:
        if _MONTH:
            _MONTH["usd"] += sum(r["usd"] for r in rows)
    return f"spend: {len(rows)} call(s) written"


def summary() -> list[str]:
    """One line per purpose for the run summary, most expensive first."""
    with _LOCK:
        run = dict(_RUN)
        calls = dict(_CALLS)
    if not run:
        return []
    total = sum(run.values())
    lines = [f"model spend this run: ${total:.2f} in {sum(calls.values())} call(s)"]
    for p, usd in sorted(run.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {p}: ${usd:.2f} in {calls[p]} call(s)")
    return lines


def run_total() -> float:
    with _LOCK:
        return round(sum(_RUN.values()), 6)


def _month_start() -> datetime.datetime:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spent(cfg) -> float | None:
    """This calendar month's recorded spend, or None when it cannot be read.
    Read from the ledger once per process, then kept current as calls land."""
    key = _month_start().strftime("%Y-%m")
    with _LOCK:
        if _MONTH.get("month") == key:
            return _MONTH["usd"] + sum(r["usd"] for r in _PENDING)
    if not _live(cfg):
        return None
    from .config import ALL_ROWS, db_get
    try:
        rows = db_get(cfg, TABLE, {"select": "id,usd",
                                   "at": f"gte.{_month_start().isoformat()}",
                                   "limit": ALL_ROWS})
    except Exception:
        return None
    usd = sum(float(r.get("usd") or 0) for r in rows)
    with _LOCK:
        _MONTH.clear()
        _MONTH.update({"month": key, "usd": usd})
        return usd + sum(r["usd"] for r in _PENDING)


def over_budget(cfg) -> str | None:
    """Why no more model calls may be made this month, or None.

    A ledger that cannot be read does not stop anything: the ceiling is a
    backstop against a runaway, and refusing every call because Supabase
    blinked would be the runaway's opposite.
    """
    try:
        cap = float(cfg.optional("hunter_llm_max_usd_per_month", DEFAULT_MONTH_CAP) or 0)
    except ValueError:
        cap = float(DEFAULT_MONTH_CAP)
    if cap <= 0:
        return None
    spent = month_spent(cfg)
    if spent is None or spent < cap:
        return None
    return (f"this month's model spend is ${spent:.2f}, at the "
            f"${cap:.0f} ceiling (hunter_llm_max_usd_per_month)")


def reset() -> None:
    """Tests share a process."""
    global _CFG
    with _LOCK:
        _PENDING.clear()
        _RUN.clear()
        _CALLS.clear()
        _MONTH.clear()
        _CFG = None
