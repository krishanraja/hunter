"""The company bar: judge the business once, cheaply, before any role there
costs a judge call.

His words, 2026-10-03: "i need your judging methodology to be more cost
efficient, and it not to be the only thing holding up quality". The judge read
every senior role in his cities, one five-cent call each, and most of what it
rejected it rejected for the business. A business does not change from one
posting to the next, so it is scored once (lookalike.py, about a third of a
cent a company, kept thirty days) against his own list, and a role at a
company that plainly is not his kind is cut without a judge call.

Measured before it was written, on the 242 roles he has ruled on (57 Yes):
cutting companies that score 0 or 1 cuts 45 of his 185 declines and 4 of his
Yes roles: Publicis twice, which his own rule of that day cuts, BlackRock's AI
transformation seat, which is the one exception he kept, and a recruiter's
posting, where the recruiter scored and the client is unknown. So AI
transformation seats and recruiters' postings are never cut here, and with
those two exceptions the bar loses none of his Yes roles beyond his own rule.
At 3 it would also lose ColdIQ, which he applied to, so the bar is 2.
Recorded in tests/fixtures/company_bar_eval.json.

What is never cut here: his list, his Target Companies, anything scoring 2 or
more, and a company that could not be scored (never block on no evidence).
"""
from __future__ import annotations

import re

from . import lookalike, universe
from .config import Config

BAR = 2
AI_SEAT = re.compile(r"\bai\b.*\b(transformation|strategy|enablement|adoption)\b|"
                     r"\b(transformation|strategy)\b.*\bai\b|\bchief ai\b|\bhead of ai\b", re.I)
RECRUITER = re.compile(r"recruit|search firm|executive search|staffing|headhunt|talent agency|"
                       r"placement|\bjobot\b|\bharnham\b", re.I)


def exempt(title: str, company: universe.Company) -> str:
    """Why this role is never cut by the bar, or ''."""
    if AI_SEAT.search(title or ""):
        return "an AI transformation seat, the exception he kept"
    if RECRUITER.search(f"{company.name} {company.area} {company.lookalike_why}"):
        return "a recruiter's posting: the client is judged, not the recruiter"
    return ""


def apply(cfg: Config, candidates: list, *, top_keys: set, max_usd: float = 1.0,
          client=None, mark: bool = True) -> tuple[list, list, list[str]]:
    """(kept candidates, cut candidates, summary lines). Candidates carry
    .role (company, title, jd_text) and .row (the database row). A company
    not yet scored is scored now, on the opening of its own posting."""
    have = lookalike.stored(cfg)
    comps: dict[str, universe.Company] = {}
    for c in candidates:
        k = universe.key_of(c.role.company)
        if k in top_keys or k in comps:
            continue
        co = universe.Company(name=c.role.company, key=k,
                              description=" ".join((c.role.jd_text or "").split())[:500])
        comps[k] = co
    lookalike.restore(list(comps.values()), have)
    todo = [co for co in comps.values() if co.lookalike is None and co.description]
    lines: list[str] = []
    if todo:
        n, usd, problems = lookalike.score(
            cfg, todo, universe.seeds(), [], client=client, max_usd=max_usd)
        lookalike.save(cfg, todo)
        lines.append(f"company bar: {n} new companies scored for ${usd:.2f}"
                     + (f" ({'; '.join(problems[:2])})" if problems else ""))
    kept, cut = [], []
    for c in candidates:
        k = universe.key_of(c.role.company)
        co = comps.get(k)
        if k in top_keys or co is None or co.lookalike is None or co.lookalike >= BAR \
                or exempt(c.role.title, co):
            kept.append(c)
            continue
        if mark:
            # In shadow mode the old rules still decide the row; the bar only
            # spares the judge the call.
            c.row["status"] = "blocked"
            c.row["rejection_reason"] = (f"COMPANY {co.lookalike:g}/10 against your list: "
                                         f"{co.lookalike_why}")[:300]
        cut.append(c)
    if cut:
        lines.append(f"company bar: {len(cut)} role(s) cut without a judge call, at "
                     + ", ".join(sorted({c.role.company for c in cut})[:8]))
    return kept, cut, lines
