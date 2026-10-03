"""The Company Radar: the companies he wants, ranked, with the roles open at
each that pass his rules.

His words, 2026-10-03: "I need clues and breadcrumbs and opportunities". The
roles reach Pipeline; the companies reach this tab, so he can see the trail:
where each company came from, why it is here, how many of its open roles are
his shape, and the best three, linked.

Ranked by how sure hunter is that he wants the company (his list, his Target
Companies and his Yes companies first, then lookalikes by score) and then by
how many roles there pass his rules. A company with no readable job board is
still listed, and says so, because a gap he can see is better than a company
silently missing.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json

from . import lookalike, universe
from .config import Config, db_insert
from .sources import ats_for, slugify

TAB = "Company Radar"
HEADERS = ["Rank", "Company", "Why it is here", "Area", "Stage", "Roles for you",
           "Best role", "Second role", "Third role", "Open roles", "Board", "Swept"]
MAX_PROBES = 80
BOARD_URL = {"ashby": "https://jobs.ashbyhq.com/{slug}",
             "greenhouse": "https://job-boards.greenhouse.io/{slug}",
             "lever": "https://jobs.lever.co/{slug}"}


def certainty(c: universe.Company) -> float:
    """How sure hunter is that he wants this company, for ranking only."""
    if universe.YOUR_LIST in c.sources or universe.SAID_YES in c.sources:
        return 11.0
    if any(s.startswith(universe.TARGETS) for s in c.sources):
        return 10.5
    return c.lookalike or 0.0


def resolve(cfg: Config, companies: list[universe.Company], *, probe=True,
            max_probes: int = MAX_PROBES) -> tuple[int, int]:
    """Give each company its job board: from hunter's board map, its own
    cache of resolved boards, the a16z index, then by probing a bounded number
    of new ones. Returns (resolved, probed)."""
    from .ats import discover as disc
    cache = disc.load_cache(cfg)
    probed = changed = 0
    for c in companies:
        if c.ats and c.slug:
            continue
        hit = ats_for(c.name)
        for k in [slugify(c.name)] + [a for a in c.aliases]:
            h = cache.get(k)
            if not hit and h and not disc.is_miss(h):
                hit = (h["ats"], h["slug"])
        if not hit and probe and probed < max_probes:
            probed += 1
            hit = disc.discover(cfg, c.name, cache)
            changed += 1
        if hit:
            c.ats, c.slug = hit
    if changed:
        disc.save_cache(cfg, cache)
    return sum(1 for c in companies if c.ats and c.slug), probed


def sweep(companies: list[universe.Company], *, workers: int = 8
          ) -> tuple[dict[str, list[universe.Opening]], list[str]]:
    """Every open role at every company with a board, his rules applied."""
    out: dict[str, list[universe.Opening]] = {}
    problems: list[str] = []

    def one(c):
        return c, universe.openings(c, universe.board_postings(c.ats, c.slug))
    todo = [c for c in companies if c.ats and c.slug]
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, c) for c in todo]
        for fut in futures:
            try:
                c, ops = fut.result()
                out[c.key] = ops
            except Exception as e:
                problems.append(f"board failed: {e.__class__.__name__}")
    return out, problems


def best(ops: list[universe.Opening], n: int = 3) -> list[universe.Opening]:
    """The roles that pass, most senior first, newest first within a level."""
    import re
    rank = [r"\bchief\b|\bc[a-z]o\b", r"\b(svp|evp|vp|vice president)\b", r"\bhead\b",
            r"\b(general manager|managing director|country manager)\b", r"\bdirector\b",
            r"\b(lead|principal)\b", r"\bmanager\b"]

    def level(o):
        for i, pat in enumerate(rank):
            if re.search(pat, o.title, re.I):
                return i
        return len(rank)
    passing = sorted((o for o in ops if o.passes), key=lambda o: o.posted or "", reverse=True)
    return sorted(passing, key=level)[:n]


def identity_ok(company: universe.Company, ops: list[universe.Opening]) -> bool:
    """A board found by guessing its address must be the company's own: some
    posting on it names the company. Guessing once resolved Gamma to a
    security vendor (CLAUDE.md section 3)."""
    import re
    words = [w for w in re.findall(r"[A-Za-z0-9]+", company.name) if len(w) >= 3]
    if not words or not ops:
        return True
    text = " ".join((o.description or "")[:3000] for o in ops[:8]).lower()
    if not text.strip():
        return True        # nothing to read either way; the judge reads the posting
    return any(w.lower() in text for w in words[:2])


def link(o: universe.Opening) -> str:
    label = f"{o.title} ({o.location or 'location not stated'})".replace('"', "'")[:180]
    return f'=HYPERLINK("{o.url}", "{label}")' if o.url else label


def rows_for(companies: list[universe.Company], found: dict[str, list], when: str) -> list[list]:
    ranked = sorted(companies, key=lambda c: (
        -certainty(c), -sum(1 for o in found.get(c.key, []) if o.passes), c.name.lower()))
    out = []
    for i, c in enumerate(ranked, start=1):
        ops = found.get(c.key)
        top3 = best(ops or [])
        why = c.why or c.lookalike_why
        source = "; ".join(c.sources)
        if c.lookalike is not None and universe.YOUR_LIST not in c.sources:
            source += f"; lookalike {c.lookalike:g}/10"
        out.append([
            str(i), c.name, f"{source}. {why}".strip(". ")[:500], c.area, c.stage,
            "no readable board" if ops is None else str(sum(1 for o in ops if o.passes)),
            *(link(o) for o in top3), *([""] * (3 - len(top3))),
            "" if ops is None else str(len(ops)),
            (f'=HYPERLINK("{BOARD_URL[c.ats].format(slug=c.slug)}", "{c.ats}")'
             if c.ats in BOARD_URL and c.slug else ""),
            when[:10]])
    return out


def write_tab(sheet, rows: list[list]) -> None:
    """The tab, styled like Pipeline's header, and read back."""
    from .sheet import col_letter
    m = sheet._get("", {"fields": "sheets(properties(sheetId,title))"})
    titles = {s["properties"]["title"]: s["properties"]["sheetId"] for s in m.get("sheets", [])}
    if TAB not in titles:
        sheet._post(":batchUpdate", {"requests": [{"addSheet": {"properties": {
            "title": TAB, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2}}}}]})
        m = sheet._get("", {"fields": "sheets(properties(sheetId,title))"})
        titles = {s["properties"]["title"]: s["properties"]["sheetId"] for s in m.get("sheets", [])}
    tab_id = titles[TAB]
    pipe_id = titles.get("Pipeline")
    last_col = col_letter(len(HEADERS) - 1)
    sheet._post("/values:batchClear", {"ranges": [f"'{TAB}'!A1:Z3000"]})
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": tab_id, "gridProperties": {
        "rowCount": max(len(rows) + 10, 100), "columnCount": len(HEADERS),
        "frozenRowCount": 1, "frozenColumnCount": 2}},
        "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)"}}]
    if pipe_id is not None:
        reqs.append({"copyPaste": {
            "source": {"sheetId": pipe_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": 0, "endColumnIndex": 1},
            "destination": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1,
                            "startColumnIndex": 0, "endColumnIndex": len(HEADERS)},
            "pasteType": "PASTE_FORMAT"}})
    widths = [44, 150, 360, 140, 80, 70, 260, 260, 260, 70, 70, 80]
    for i, w in enumerate(widths):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    reqs.append({"repeatCell": {
        "range": {"sheetId": tab_id, "startRowIndex": 1, "endRowIndex": len(rows) + 1,
                  "startColumnIndex": 0, "endColumnIndex": len(HEADERS)},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP",
                                       "textFormat": {"fontSize": 10}}},
        "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,textFormat.fontSize)"}})
    sheet._post(":batchUpdate", {"requests": reqs})
    sheet._write([(f"'{TAB}'!A1:{last_col}{len(rows) + 1}", [HEADERS] + rows)])
    back = sheet.read_tab_values(f"'{TAB}'!A1:B{len(rows) + 1}")
    if [r[1] if len(r) > 1 else "" for r in back[1:]] != [r[1] for r in rows]:
        raise RuntimeError(f"the {TAB} tab did not read back as written")


def save(cfg: Config, companies: list[universe.Company], found: dict[str, list],
         when: str) -> None:
    lookalike.save(cfg, companies)
    rows = []
    for c in companies:
        ops = found.get(c.key)
        if ops is None:
            continue
        rows.append({"key": c.key, "name": c.name, "open_roles": len(ops),
                     "matching_roles": sum(1 for o in ops if o.passes),
                     "best_roles": [{"title": o.title, "url": o.url, "location": o.location,
                                     "comp": o.comp} for o in best(ops)],
                     "swept_at": when})
    for i in range(0, len(rows), 500):
        db_insert(cfg, lookalike.TABLE, rows[i:i + 500], on_conflict="key", merge=True)


def demote_stale(cfg: Config, top_now: set[str], have: dict | None = None) -> int:
    """A row marked top that this build no longer finds top (a key from an
    older keying, or a company whose standing changed) is marked not top, so
    the judge is never told a stale company is one of his."""
    from .config import ALL_ROWS, db_get, db_patch
    n = 0
    rows = db_get(cfg, lookalike.TABLE, {"select": "key,top", "top": "eq.true",
                                         "order": "key.asc", "limit": ALL_ROWS})
    for r in rows:
        if r["key"] not in top_now:
            db_patch(cfg, lookalike.TABLE, {"key": r["key"]}, {"top": False})
            n += 1
    return n


def build(cfg: Config, sheet, *, score_new: bool = True, max_usd: float = 8.0,
          client=None, probe: bool = True, write: bool = True) -> dict:
    when = datetime.datetime.now(datetime.timezone.utc).isoformat()
    seed = universe.seeds()
    targets = universe.from_targets(sheet)
    yes = universe.from_yes(cfg)
    pool = lookalike.pool(cfg)
    have = lookalike.stored(cfg)
    lookalike.restore(pool, have)
    named = {c.key for c in seed + targets} | {a for c in seed for a in c.aliases}
    unique_pool = list(universe.merge(yes, pool).values())
    lookalike.restore(unique_pool, have)
    now = datetime.datetime.now(datetime.timezone.utc)
    # His Yes companies are scored like any other: one Yes is not enough on
    # its own (universe.Company.top). Without a portfolio description they are
    # scored on the opening of a posting of theirs that hunter holds.
    lookalike.describe_from_postings(cfg, [c for c in unique_pool if not c.description])
    todo = [c for c in unique_pool if c.key not in named and lookalike.due(c, have.get(c.key), now)]
    scored, usd, problems = (lookalike.score(
        cfg, todo, seed, [t.name for t in targets], client=client, max_usd=max_usd)
        if score_new and todo else (0, 0.0, []))
    companies = list(universe.merge(seed, targets, unique_pool).values())
    top = [c for c in companies if c.top]
    resolved, probed = resolve(cfg, top, probe=probe)
    found, sweep_problems = sweep(top)
    problems += sweep_problems
    doubtful = []
    for c in top:
        ops = found.get(c.key)
        if ops and not identity_ok(c, ops):
            doubtful.append(c.name)
            for o in ops:
                o.passes, o.why = False, "board not confirmed as the company's own"
    if doubtful:
        problems.append("boards not confirmed as the company's own: " + ", ".join(doubtful))
    rows = rows_for(top, found, when)
    if write:
        save(cfg, companies, found, when)
        demote_stale(cfg, {c.key for c in companies if c.top}, have)
        write_tab(sheet, rows)
    openings = [o for c in top for o in found.get(c.key, []) if o.passes]
    return {"companies": len(companies), "top": len(top), "scored": scored, "usd": usd,
            "resolved": resolved, "probed": probed, "swept": len(found),
            "openings": openings, "rows": rows, "problems": problems,
            "top_companies": top}
