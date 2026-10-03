"""The company universe: start from the companies he wants, then find their roles.

Until 2026-10-03 roles arrived first and companies were judged after. About 70
percent of what reached his sheet came from a LinkedIn keyword sweep, which
returns whoever posts the most jobs, and the free board sweep that did reach
the AI startups he names threw their roles away on a location string before
anything with judgement saw them. In the thirty days to 3 October hunter saw
249 roles at the hundred AI companies he listed, blocked 103 of them on
location and 38 on a title pattern, and showed him 3. His words that day: "why
can't you identify the next unicorns in the creative industries that are well
backed? ... I need clues and breadcrumbs and opportunities".

So this lane runs the other way round:

  companies   his list (data/ai_universe.csv, 100 he wrote), his Target
              Companies tab, every company he has said Yes to, and lookalikes:
              venture-backed companies from the a16z, Accel, General Catalyst
              and Thrive portfolios, scored against his list (lookalike.py).
  roles       read from each company's own job board, free, every run.
  his rules   decided 2026-10-03, at TOP companies only (the list above):
              - New York, London, UK or US remote, and the San Francisco Bay
                Area ("SF for top AI cos"); a remote-eligible posting passes.
              - Director and Manager titles pass in commercial, partnerships,
                business development, GTM and strategy ("bend at top
                companies"); pay is read on the top of the range or OTE.
              - Engineering, research, design, legal, finance, people, IT and
                junior seats never pass; they are not his shape anywhere.
  judgement   what passes goes to the judge, which knows the company is one
              of his top companies.

Nothing here invents a fact about a company: every source line names where the
company came from, and a lookalike carries the reason it was matched.
"""
from __future__ import annotations

import csv
import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ALL_ROWS, Config, db_get
from .sources import slugify

SEED_FILE = Path(__file__).resolve().parent / "data" / "ai_universe.csv"
LOOKALIKE_MIN = 7          # a lookalike scoring this or more is a top company
SAID_YES_MIN = 5           # a company he said Yes to, scored this or more

YOUR_LIST = "your list"
TARGETS = "Target Companies"
SAID_YES = "you said Yes"


@dataclass
class Company:
    name: str
    key: str
    sources: list = field(default_factory=list)
    area: str = ""
    why: str = ""
    stage: str = ""
    description: str = ""
    jobs: int | None = None
    ats: str = ""
    slug: str = ""
    lookalike: float | None = None
    lookalike_why: str = ""
    aliases: list = field(default_factory=list)

    @property
    def top(self) -> bool:
        """His list and his Target Companies are top by his word. A company he
        said Yes to once is top only when its own description also scores 5 or
        more against his list: one Yes made Citi, PayPal and a job-ad network
        top companies, which his cut of 2026-10-03 says they are not."""
        if YOUR_LIST in self.sources or any(s.startswith(TARGETS) for s in self.sources):
            return True
        if SAID_YES in self.sources and (self.lookalike or 0) >= SAID_YES_MIN:
            return True
        return (self.lookalike or 0) >= LOOKALIKE_MIN


_LEGAL = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|plc|co|gmbh)\b\.?", re.I)


def key_of(name: str) -> str:
    """The company's whole name, squeezed: "Higgsfield AI" and "higgsfieldai"
    meet, and "Physical Intelligence" stays itself. The first version keyed
    on hunter's shortest distinctive token, which filed Physical Intelligence
    under "intelligence", so any company called something Intelligence would
    have counted as one of his top companies. A second stripped a trailing
    "ai" and made OpenAI "open". Only parentheses and legal suffixes written
    as their own word are dropped."""
    n = re.sub(r"\(.*?\)", "", name or "")
    n = _LEGAL.sub("", n)
    return re.sub(r"[^a-z0-9]+", "", n.lower())


def seeds(path: Path = SEED_FILE) -> list[Company]:
    """His hundred, as he wrote them. "Cursor / Anysphere" is one company
    known by two names, so both keys point at it."""
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names = [n.strip() for n in row["Company"].split("/") if n.strip()]
            c = Company(name=names[0], key=key_of(names[0]), sources=[YOUR_LIST],
                        area=row.get("Primary area", "").strip(),
                        why=row.get("Why it matters", "").strip(),
                        aliases=[key_of(n) for n in names[1:]])
            out.append(c)
    return out


def from_targets(sheet) -> list[Company]:
    from . import targets
    out = []
    for t in targets.read(sheet):
        tier = f" tier {t.tier}" if getattr(t, "tier", "") else ""
        out.append(Company(name=t.name, key=key_of(t.name), sources=[TARGETS + tier],
                           area=getattr(t, "category", "") or "",
                           why=getattr(t, "why", "") or ""))
    return out


def from_yes(cfg: Config) -> list[Company]:
    """Every company he has said Yes to a role at, from his recorded verdicts."""
    from .judgedata import label_of
    events = db_get(cfg, "hunter_verdict_events", {"select": "job_id,verdict", "limit": ALL_ROWS})
    yes_ids = {e["job_id"] for e in events if label_of(e.get("verdict") or "")[0] == "yes"}
    if not yes_ids:
        return []
    names: dict[str, str] = {}
    rows = db_get(cfg, "hunter_seen_roles", {"select": "job_id,company", "limit": ALL_ROWS})
    for r in rows:
        if r["job_id"] in yes_ids and r.get("company"):
            names.setdefault(key_of(r["company"]), r["company"])
    return [Company(name=n, key=k, sources=[SAID_YES]) for k, n in names.items()]


def merge(*groups: list[Company]) -> dict[str, Company]:
    """One entry per company. Sources accumulate; the first non-empty fact
    for each field wins, in the order the groups are given."""
    out: dict[str, Company] = {}
    alias: dict[str, str] = {}
    for group in groups:
        for c in group:
            k = alias.get(c.key, c.key)
            if not k:
                continue
            have = out.get(k)
            if have is None:
                out[k] = c
                for a in c.aliases:
                    alias[a] = k
                continue
            for s in c.sources:
                if s not in have.sources:
                    have.sources.append(s)
            for f_ in ("area", "why", "stage", "description", "ats", "slug", "lookalike_why"):
                if not getattr(have, f_) and getattr(c, f_):
                    setattr(have, f_, getattr(c, f_))
            if have.jobs is None and c.jobs is not None:
                have.jobs = c.jobs
            if have.lookalike is None and c.lookalike is not None:
                have.lookalike = c.lookalike
    return out


# ---------- his rules for a role at a top company ----------

NEVER = re.compile(
    r"\b(engineer\w*|scientist|research(?:er)?|designer|design|counsel|legal|attorney|"
    r"paralegal|account(?:ant|ing)|tax|payroll|controller|bookkeep\w*|recruit\w*|talent|"
    r"people (?:partner|operations|ops)|human resources|hr|it support|helpdesk|"
    r"developer|devops|sre|machine learning|ml|data scien\w*|security|intern(?:ship)?|"
    r"associate|coordinator|specialist|representative|sdr|bdr|account executive|"
    r"technician|analyst|assistant|editor|writer|animator|artist|producer|"
    r"community manager|support agent|customer support|facilities|office manager|"
    r"executive assistant)\b", re.I)
SENIOR = re.compile(
    r"\b(director|head|vp|vice president|svp|evp|chief|c[a-z]o|general manager|gm|"
    r"managing director|country manager|lead|leader|principal|founding)\b", re.I)
MANAGER = re.compile(r"\bmanager\b", re.I)
COMMERCIAL = re.compile(
    r"\b(partner\w*|alliance\w*|ecosystem|business development|bd|corp(?:orate)? dev\w*|"
    r"strateg\w*|commercial\w*|go[- ]to[- ]market|gtm|revenue|monetiz\w*|growth|"
    r"chief of staff|general manager|country|market|sales|engagement|"
    r"transformation|enablement|operations|expansion|licens\w*|creator\w*|"
    r"content partnerships|publishing|music|studio|media)\b", re.I)
PRODUCT = re.compile(r"\bproduct\b", re.I)

BAY_AREA = re.compile(
    r"san francisco|\bsf\b|bay area|palo alto|mountain view|menlo park|redwood city|"
    r"san mateo|oakland|berkeley|sunnyvale|south san francisco|san jose|foster city|"
    r"burlingame|san bruno|redwood shores|cupertino|santa clara|emeryville|daly city|"
    r"los altos|millbrae|belmont|san carlos", re.I)
HOME = re.compile(
    r"new york|\bnyc\b|brooklyn|manhattan|london|united kingdom|\buk\b|england|"
    r"^\s*(?:united states|usa|us|u\.s\.?|north america|americas)\s*$", re.I)
REMOTE = re.compile(r"\bremote\b|anywhere|distributed", re.I)


def shape_ok(title: str, department: str = "") -> tuple[bool, str]:
    """Is the title his shape at a top company, with his Director and Manager
    flex? A commercial, partnerships, BD, GTM or strategy seat at Director,
    Head, VP, Chief, GM or Lead level passes, and so does a Manager title in
    those functions. Product passes at Director level and above only."""
    t = title or ""
    if NEVER.search(t):
        return False, "not his shape: " + NEVER.search(t).group(0)
    fn = COMMERCIAL.search(t) or COMMERCIAL.search(department or "")
    if PRODUCT.search(t) and not re.search(r"\b(director|head|vp|vice president|chief)\b", t, re.I):
        return False, "a product seat below Director"
    if SENIOR.search(t) and (fn or PRODUCT.search(t)):
        return True, "senior seat in his functions"
    if MANAGER.search(t) and fn:
        return True, "Manager title in a commercial function, at a top company"
    if SENIOR.search(t):
        return True, "senior seat; the judge decides the function"
    return False, "below the seniority bar"


def place_ok(location: str, *, remote: bool = False, secondary: list | None = None) -> tuple[bool, str]:
    """New York, London, UK or US remote, or the Bay Area: his rule for a top
    company. A posting the board marks remote-eligible passes unless it pins
    itself to another country."""
    from .gates import names_foreign_geo
    places = [location or ""] + list(secondary or [])
    for p in places:
        if HOME.search(p):
            return True, f"in his geography: {p}"
        if BAY_AREA.search(p):
            return True, f"San Francisco Bay Area, allowed at a top company: {p}"
    joined = " ".join(places)
    if (remote or REMOTE.search(joined)) and not names_foreign_geo(joined):
        return True, f"remote-eligible: {location or 'remote'}"
    return False, f"outside his geography: {location or 'not stated'}"


@dataclass
class Opening:
    company: Company
    title: str
    url: str
    location: str
    comp: str
    department: str
    remote: bool
    posted: str
    ats: str
    slug: str
    posting_id: str
    description: str = ""
    passes: bool = False
    why: str = ""


def openings(company: Company, postings: list) -> list[Opening]:
    """Board postings as openings, each with his rules applied."""
    out = []
    for p in postings:
        raw = getattr(p, "raw", {}) or {}
        secondary = [s.get("location", "") for s in raw.get("secondaryLocations") or []
                     if isinstance(s, dict)]
        dept = raw.get("department") or raw.get("team") or ""
        if not dept and isinstance(raw.get("departments"), list) and raw["departments"]:
            dept = raw["departments"][0].get("name", "")
        if not dept and isinstance(raw.get("categories"), dict):
            dept = raw["categories"].get("department") or raw["categories"].get("team") or ""
        remote = bool(raw.get("isRemote")) or raw.get("workplaceType") == "remote"
        o = Opening(company=company, title=p.title or "", url=p.url or "",
                    location=p.location or "", comp=p.comp_text or "", department=dept or "",
                    remote=remote, posted=str(raw.get("publishedAt") or p.posted_at or "")[:10],
                    ats=p.ats or "", slug=p.ats_slug or "", posting_id=str(p.ats_posting_id or ""),
                    description=_text_of(raw)[:6000])
        ok_shape, why_shape = shape_ok(o.title, o.department)
        ok_place, why_place = place_ok(o.location, remote=o.remote, secondary=secondary)
        o.passes = ok_shape and ok_place
        o.why = why_shape if not ok_shape else why_place
        out.append(o)
    return out


def _text_of(raw: dict) -> str:
    """The posting's own text, from whichever field the board carries it in."""
    import html
    text = raw.get("descriptionPlain") or ""
    if not text and raw.get("content"):
        text = re.sub(r"<[^>]+>", " ", html.unescape(raw["content"]))
    return " ".join(text.split())


def board_postings(ats: str, slug: str) -> list:
    from .ats import ashby, greenhouse, lever
    fn = {"ashby": ashby.board, "greenhouse": greenhouse.board, "lever": lever.board}.get(ats)
    return fn(slug) if fn else []


def stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def top_notes(cfg: Config) -> dict[str, str]:
    """{company key: why it is one of his top companies}, from the Radar's
    table, for the judge. Falls back to his own list when the table cannot be
    read, so a lookup failure narrows the set rather than emptying it."""
    notes: dict[str, str] = {}
    try:
        rows = db_get(cfg, "hunter_company_radar", {
            "select": "key,name,sources,area,lookalike,lookalike_why", "top": "eq.true",
            "order": "key.asc", "limit": ALL_ROWS})
    except Exception:
        rows = []
    for r in rows:
        src = "; ".join(r.get("sources") or [])
        extra = (f", lookalike {float(r['lookalike']):g}/10: {r.get('lookalike_why') or ''}"
                 if r.get("lookalike") is not None and YOUR_LIST not in (r.get("sources") or [])
                 else (f" ({r['area']})" if r.get("area") else ""))
        notes[key_of(r.get("name") or r["key"])] = f"yes: {src}{extra}"[:300]
    if not notes:
        for c in seeds():
            notes[c.key] = f"yes: {YOUR_LIST} ({c.area})"
            for a in c.aliases:
                notes[a] = notes[c.key]
    return notes


def note_for(notes: dict[str, str], company: str) -> str:
    return notes.get(key_of(company or ""), "")
