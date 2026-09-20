"""Companies he has not thought of, found in what hunter already discards.

His instruction on 2026-09-20: "There are so many companies I am not
thinking about that has good backing, potential, mission, leadership and
opportunity."

Hunter's named universe was 53 companies. Measured the same day, hunter had
already SEEN 2,256 other companies and thrown every one of them away, plus
3,449 more sitting in his own contacts graph. The supply was never the
problem. Nothing was asking the companies question of any of them, so a
LinkedIn keyword sweep decided what he looked at.

This module builds the candidate list, ranks it by what can be known for
nothing, and hands the top of it to the scorer. It never contacts anybody
and never writes to his own columns: a company that scores well becomes a
row in the proposal block under his list, with its score, its category and
the sentence and URL that earned it, and he adopts it by typing a tier.

Scoring is capped per run and cached, so the list works through itself over
weeks instead of spending three hours in one Sunday morning.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .config import ALL_ROWS, Config, db_get
from .sources import company_key

# Names that are not companies. They arrive from job boards that put a
# category where the employer should be, and from aggregators reposting
# other people's roles under their own name.
NOT_A_COMPANY = {
    "", "health", "capital", "ladders", "jobgether", "confidential",
    "undisclosed", "stealth", "various", "multiple", "recruiter", "agency",
    "talent", "staffing", "consulting", "consultancy", "group", "partners",
    "company", "unknown", "none", "self", "freelance", "contract",
}
MIN_NAME = 3


@dataclass
class Candidate:
    key: str
    name: str
    seen: int = 0                 # postings hunter discarded at this company
    senior_seen: int = 0          # of those, ones matching a senior title
    in_contacts: int = 0          # people he knows there
    a16z: bool = False
    has_board: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def priority(self) -> float:
        """What to spend the scoring budget on first.

        Everything here is free and already on disk. It is not a judgement
        about the company, only an ordering: a company he already knows
        somebody at, whose board hunter can read, is worth establishing the
        facts about before one seen once in a keyword sweep.
        """
        return (4.0 * self.a16z
                + 3.0 * self.has_board
                + 2.0 * min(self.in_contacts, 5) / 5
                + 1.5 * min(self.senior_seen, 4) / 4
                + 0.5 * min(self.seen, 10) / 10)

    @property
    def why_worth_looking(self) -> str:
        bits = []
        if self.a16z:
            bits.append("a16z portfolio")
        if self.in_contacts:
            bits.append(f"{self.in_contacts} contact(s) of yours there")
        if self.senior_seen:
            bits.append(f"{self.senior_seen} senior role(s) seen")
        if self.has_board:
            bits.append("readable job board")
        return ", ".join(bits)


# A company field that is really a page title: "Ceribell | AI-Powered
# Point-of-Care EEG". Everything after the separator is marketing, and
# proposing it to him under that name looks like hunter does not know what
# the company is called.
TITLE_TAIL = re.compile(r"\s*[|\u2502\u2013-]\s+.*$")


def tidy(name: str) -> str:
    n = TITLE_TAIL.sub("", (name or "").strip()).strip(" ,.;:")
    return n or (name or "").strip()


def _better_name(current: str, other: str) -> str:
    """The name to show him when two sources disagree.

    Company fields arrive from job boards as ATS slugs ("tanium", "figma")
    as well as proper names, and the first one seen was winning. A
    capitalised name beats a lowercase one; after that, the shorter is
    usually the company and the longer is usually its tagline.
    """
    a, b = tidy(current), tidy(other)
    if not a:
        return b
    if not b:
        return a
    a_cased = a != a.lower()
    b_cased = b != b.lower()
    if a_cased != b_cased:
        return a if a_cased else b
    return a if len(a) <= len(b) else b


def _usable(name: str) -> bool:
    n = tidy(name)
    if len(n) < MIN_NAME:
        return False
    k = company_key(n)
    return bool(k) and k not in NOT_A_COMPANY and n.lower() not in NOT_A_COMPANY


def candidates(cfg: Config, *, exclude: set[str] | None = None,
               boards: dict | None = None) -> list[Candidate]:
    """Every company hunter could look at, best prospects first.

    exclude is the set of company keys already on his tab or already ruled
    on, because a proposal he has seen before is noise.
    """
    exclude = exclude or set()
    found: dict[str, Candidate] = {}

    def add(name: str) -> Candidate | None:
        if not _usable(name):
            return None
        k = company_key(name)
        if k in exclude:
            return None
        c = found.get(k)
        if c is None:
            c = found[k] = Candidate(key=k, name=tidy(name))
        else:
            c.name = _better_name(c.name, name)
        return c

    from .gates import SENIOR_TITLE
    try:
        rows = db_get(cfg, "hunter_seen_roles",
                      {"select": "company,title,status", "limit": ALL_ROWS})
    except Exception:
        rows = []
    for r in rows:
        # Every status except staging: a role hunter discarded still told it
        # the company exists and is hiring.
        c = add(r.get("company") or "")
        if c is None:
            continue
        c.seen += 1
        if r.get("title") and SENIOR_TITLE.search(r["title"]):
            c.senior_seen += 1

    try:
        people = db_get(cfg, "contacts", {"select": "company", "limit": ALL_ROWS})
    except Exception:
        people = []
    for r in people:
        c = add(r.get("company") or "")
        if c is not None:
            c.in_contacts += 1

    try:
        port = db_get(cfg, "hunter_a16z_companies",
                      {"select": "name,job_count", "limit": ALL_ROWS})
    except Exception:
        port = []
    for r in port:
        c = add(r.get("name") or "")
        if c is not None:
            c.a16z = True

    if boards:
        from .sources import slugify
        readable = {company_key(s) for s, hit in boards.items() if hit}
        for k, c in found.items():
            if k in readable:
                c.has_board = True

    out = sorted(found.values(), key=lambda c: -c.priority)
    return out


def proposals(scores: dict, cands: list[Candidate], *, floor: float,
              limit: int) -> list[tuple[str, float, str, str]]:
    """The rows that go under his list: (name, score, category, evidence).

    Only companies hunter can actually say something about. A proposal whose
    evidence line reads "needs evidence" is hunter asking him to adopt a
    company on no grounds at all.
    """
    by_key = {c.key: c for c in cands}
    rows = []
    for key, s in scores.items():
        if s.status or s.total < floor:
            continue
        cand = by_key.get(key)
        cat = next((c.evidence for c in s.components
                    if c.name == "category" and c.evidenced), "")
        evidence = s.why(2)
        if cand and cand.why_worth_looking:
            evidence = f"{evidence}. Found via: {cand.why_worth_looking}"
        rows.append((s.name, s.total, cat.replace("in ", "", 1), evidence[:400]))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]


def summary_line(cands: list[Candidate], scored: int, proposed: int) -> str:
    return (f"company discovery: {len(cands)} candidate(s) hunter has seen but "
            f"never asked about, {scored} scored this run, {proposed} proposed "
            f"on the Target Companies tab")
