"""Which venture-backed companies look like the ones he named.

He gave hunter a hundred AI companies on 2026-10-03 and asked why it could not
find "the next unicorns in the creative industries that are well backed".
Hunter already held about two thousand venture-backed companies with their own
descriptions, stages and open-job counts, from the a16z index and the Accel,
General Catalyst and Thrive portfolio boards, and had never compared one of
them with his list.

This compares them. Each company's own description is read against his
hundred, and scored 0 to 10 for how much it is the same kind of company, with
the company on his list it most resembles named in the reason. Backing is not
guessed: it is the portfolio the company came from, which is the source line
on the Company Radar. A score of LOOKALIKE_MIN or more makes it a top company,
and its roles go through his top-company rules.

Scores are kept in hunter_company_radar and only new or stale companies are
scored again.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json

from .config import ALL_ROWS, Config, db_get, db_insert
from .universe import Company, key_of, seeds as load_seeds

TABLE = "hunter_company_radar"
MODEL = "claude-opus-5-5"
EFFORT = "low"
BATCH = 40
RESCORE_DAYS = 30
PRICES = {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0}

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["companies"],
          "properties": {"companies": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["key", "ai_native", "lookalike", "area", "resembles", "why"],
              "properties": {"key": {"type": "string"}, "ai_native": {"type": "boolean"},
                             "lookalike": {"type": "integer"}, "area": {"type": "string"},
                             "resembles": {"type": "string"}, "why": {"type": "string"}}}}}}

INSTRUCTIONS = """Krish Raja wants to join a fast-growing, well-backed, AI-native company: the
kind on his own list below. You are given venture-backed companies, each with
its own description, and you score how much each one is the same kind of
company as his list.

10 or 9: the same kind of company as one on his list, in the same or an
adjacent category (name it in "resembles"). 8 or 7: clearly the same type, an
AI-native product company building in a frontier or creative category, or AI
for a large market, even if no single company on his list matches. 6 to 4: a
technology company but not AI-native, or AI-native in a category far from his
list. 3 to 0: legacy, services, consultancy, agency, staffing, bank, insurer,
asset manager, healthcare provider, or a business whose description does not
say what it sells.

Judge only from the description given. Where it is too thin to tell, score 4
and say so. "why" is one sentence quoting what in the description decided it.
"area" is a few words naming the category. No em dashes."""


def system_text(seed_list: list[Company], targets: list[str]) -> str:
    lines = [f"{c.name} | {c.area} | {c.why}" for c in seed_list]
    return (INSTRUCTIONS + "\n\n=== HIS LIST ===\n" + "\n".join(lines)
            + "\n\n=== HIS TARGET COMPANIES ===\n" + ", ".join(targets))


def pool(cfg: Config) -> list[Company]:
    """Every venture-backed company hunter holds a description or market tag for."""
    out = []
    for r in db_get(cfg, "hunter_a16z_companies", {
            "select": "slug,name,domain,markets,stage,band,job_count,ats,ats_slug",
            "limit": ALL_ROWS}):
        out.append(Company(
            name=r["name"], key=key_of(r["name"]), sources=["a16z portfolio"],
            stage=r.get("stage") or "",
            description=f"Markets: {', '.join(r.get('markets') or [])}. "
                        f"Size {r.get('band') or 'unknown'}. Site {r.get('domain') or 'unknown'}.",
            jobs=r.get("job_count"), ats=r.get("ats") or "", slug=r.get("ats_slug") or ""))
    for r in db_get(cfg, "hunter_portfolio_companies", {
            "select": "name,firm,description,stage,industry_tags,active_jobs_count,domain",
            "limit": ALL_ROWS}):
        tags = r.get("industry_tags") or []
        out.append(Company(
            name=r["name"], key=key_of(r["name"]), sources=[f"{r.get('firm')} portfolio"],
            stage=r.get("stage") or "",
            description=((r.get("description") or "")[:400]
                         + (f" Tags: {', '.join(tags)}." if tags else "")),
            jobs=r.get("active_jobs_count")))
    return out


def stored(cfg: Config) -> dict[str, dict]:
    """Rows held, keyed by today's key for their name, so a change to how
    companies are keyed does not throw away scores already paid for."""
    # Ordered by key: db_get pages a read and orders by id unless told
    # otherwise, and this table has no id, so the first build's read was
    # refused, caught, and read as "nothing stored", and every company was
    # paid for twice.
    rows = db_get(cfg, TABLE, {
        "select": "key,name,sources,area,why,stage,lookalike,lookalike_why,top,scored_at",
        "order": "key.asc", "limit": ALL_ROWS})
    out: dict[str, dict] = {}
    for r in rows:
        k = key_of(r.get("name") or r["key"])
        if k not in out or (out[k].get("lookalike") is None and r.get("lookalike") is not None):
            out[k] = r
    return out


def due(c: Company, have: dict | None, now: datetime.datetime) -> bool:
    if not have or have.get("lookalike") is None:
        return True
    at = have.get("scored_at")
    if not at:
        return True
    try:
        when = datetime.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (now - when).days >= RESCORE_DAYS


def score(cfg: Config, companies: list[Company], seed_list: list[Company],
          targets: list[str], *, client=None, max_usd: float = 8.0,
          workers: int = 6) -> tuple[int, float, list[str]]:
    """Score companies in batches. Returns (scored, usd, problems). A batch
    whose answer does not parse leaves its companies unscored, never guessed."""
    if not companies:
        return 0, 0.0, []
    from . import judge
    client = client or judge._client(cfg)
    system = system_text(seed_list, targets)
    by_key = {c.key: c for c in companies}
    batches = [companies[i:i + BATCH] for i in range(0, len(companies), BATCH)]
    problems: list[str] = []
    spent = 0.0

    def one(batch):
        body = [{"key": c.key, "name": c.name, "stage": c.stage,
                 "open_jobs": c.jobs, "description": c.description} for c in batch]
        msg = client.messages.create(
            model=MODEL, max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(body)}])
        return msg

    scored = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool_:
        # Sent a few at a time, so the ceiling stops new spending rather than
        # counting spending that was already in flight.
        for i in range(0, len(batches), workers):
            if spent >= max_usd:
                problems.append(f"stopped at the ${max_usd:.2f} ceiling with "
                                f"{len(batches) - i} batch(es) unsent")
                break
            futures = [pool_.submit(one, b) for b in batches[i:i + workers]]
            for fut in futures:
                try:
                    msg = fut.result()
                except Exception as e:
                    problems.append(f"a batch failed: {e.__class__.__name__}")
                    continue
                u = judge.usage_of(msg)
                spent += (u["input_tokens"] * PRICES["in"] + u["output_tokens"] * PRICES["out"]
                          + u["cache_read_input_tokens"] * PRICES["cache_read"]
                          + u["cache_creation_input_tokens"] * PRICES["cache_write"]) / 1e6
                if getattr(msg, "stop_reason", "") in ("max_tokens", "refusal"):
                    problems.append(f"a batch stopped: {msg.stop_reason}")
                    continue
                try:
                    answer = json.loads(judge.text_of(msg))
                except (ValueError, TypeError):
                    problems.append("a batch answer was not valid JSON")
                    continue
                for a in answer.get("companies", []):
                    c = by_key.get(a.get("key"))
                    n = a.get("lookalike")
                    if c is None or not isinstance(n, int) or not 0 <= n <= 10:
                        continue
                    c.lookalike = float(n)
                    c.area = c.area or (a.get("area") or "")
                    res = (a.get("resembles") or "").strip()
                    c.lookalike_why = ((f"Like {res}. " if res else "")
                                       + (a.get("why") or "")).strip()[:400]
                    scored += 1
    return scored, round(spent, 4), problems


def save(cfg: Config, companies: list[Company]) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = [{"key": c.key, "name": c.name, "sources": c.sources, "area": c.area, "why": c.why,
             "stage": c.stage, "description": c.description[:600], "ats": c.ats or None,
             "slug": c.slug or None, "lookalike": c.lookalike, "lookalike_why": c.lookalike_why,
             "top": c.top, "scored_at": now if c.lookalike is not None else None}
            for c in companies if c.key]
    for i in range(0, len(rows), 500):
        db_insert(cfg, TABLE, rows[i:i + 500], on_conflict="key", merge=True)


def restore(companies: list[Company], have: dict[str, dict]) -> None:
    """Scores already held, put back on the companies so nothing is paid twice."""
    for c in companies:
        h = have.get(c.key)
        if h and h.get("lookalike") is not None and c.lookalike is None:
            c.lookalike = float(h["lookalike"])
            c.lookalike_why = h.get("lookalike_why") or ""
            c.area = c.area or (h.get("area") or "")


def seed_list() -> list[Company]:
    return load_seeds()


def describe_from_postings(cfg: Config, companies: list[Company]) -> int:
    """A company with no portfolio description is described by the opening of
    a posting of its own that hunter already holds, which is the company's own
    words about itself. Returns how many were described."""
    if not companies:
        return 0
    want = {c.key: c for c in companies}
    n = 0
    for r in db_get(cfg, "hunter_seen_roles", {"select": "company,jd_text", "limit": ALL_ROWS}):
        c = want.get(key_of(r.get("company") or ""))
        text = (r.get("jd_text") or "").strip()
        if c is not None and not c.description and len(text) >= 200:
            c.description = " ".join(text.split())[:500]
            n += 1
    return n
