"""Deterministic scoring. Canon 9.2 sets the bar (8 of 10, lowered from 9 on
2026-08-24) and canon 9.3 allows EXACTLY TWO auto-rejects: a posted band
bottom below the floor with no approved equity override, and pure quota
carrying with no architecture, build or operating-model mandate. RevOps
scope, senior IC seats and adtech without an AI angle are penalties, never
rejects; all three were previously hard rejects and all three were
contradicted by roles Krish actually applied to.

The point weights follow the workbook Scoring Reference rubric with the
Engine-Builder signal as the 3-point anchor. Scoring is rule-based; if a
model-assisted component is ever wanted, it goes through workflow_proposals
first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gates import FLOOR, parse_comp_bottom
from .sources import ResolvedRole

BAR = 8  # canon 9.2

ENGINE_SIGNALS = [
    r"founding", r"first (?:\w+ )?hire", r"from scratch", r"zero.to.one",
    r"0.to.1", r"build the", r"design the", r"architect", r"establish",
    r"create the playbook", r"operating cadence", r"transform",
    r"moderni[sz]e", r"market entry", r"next stage of growth",
    r"own the operating model", r"build the (?:us|team|function)",
]
QUOTA_SIGNALS = [
    r"quota", r"ramp to", r"hit aggressive", r"close (?:\$|pipeline)",
    r"deliver against", r"existing motion", r"book of business",
    r"step into (?:the )?existing team",
]
MANDATE_KEYWORDS = re.compile(
    r"p&l|p and l|operating model|gtm design|partnerships|market entry|"
    r"corp(?:orate)? dev", re.I)
STRATEGIC_SCOPE = re.compile(r"strategy|strategic|architect|design", re.I)
AI_SIGNALS = re.compile(r"\bai\b|agentic|genai|generative|machine learning|llm", re.I)
STAGE_OK = re.compile(r"series [b-z]|late.stage|public|nasdaq|nyse|ipo", re.I)
GEO_POINT = re.compile(r"london|\buk\b|new york|\bnyc\b|remote", re.I)
REVOPS = re.compile(r"revenue operations|revops|sales operations|salesops|fp&a", re.I)
IC_SEAT = re.compile(r"individual contributor|personally close", re.I)
ADTECH = re.compile(r"adtech|ad tech|advertising technology|programmatic", re.I)


@dataclass
class ScoreResult:
    score: int
    auto_rejected: bool
    rejection_reason: str | None
    components: dict[str, int] = field(default_factory=dict)
    why_it_fits: str = ""


def _hits(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.I))


def score_role(role: ResolvedRole, *, floor: int = FLOOR,
               equity_override: bool = False) -> ScoreResult:
    hay = f"{role.title}\n{role.jd_text}"
    engine = _hits(ENGINE_SIGNALS, hay)
    quota = _hits(QUOTA_SIGNALS, hay)

    # canon 9.3 auto-reject 1: posted band bottom below the floor, no override
    bottom = parse_comp_bottom(role.comp)
    if bottom is not None and bottom < floor and not equity_override:
        return ScoreResult(
            score=1, auto_rejected=True,
            rejection_reason=f"band bottom ${bottom:,} below the ${floor:,} floor "
                             f"with no approved equity override (canon 9.3)")

    # canon 9.3 auto-reject 2: pure quota carrying with no build mandate
    if quota >= 2 and engine == 0:
        return ScoreResult(
            score=1, auto_rejected=True,
            rejection_reason="pure quota carrying with no architecture, build or "
                             "operating-model mandate (canon 9.3)")

    from .gates import SENIOR_TITLE
    components = {
        "engine_builder": min(3, engine),
        "title": 1 if SENIOR_TITLE.search(role.title) else 0,
        "comp_250k": 1 if (bottom or 0) >= 250_000 else 0,
        "geography": 1 if GEO_POINT.search(f"{role.location} {role.jd_text[:300]}") else 0,
        "stage": 1 if STAGE_OK.search(f"{role.stage} {role.jd_text}") else 0,
        "ai": 1 if AI_SIGNALS.search(f"{role.company} {hay}") else 0,
        "mandate": 1 if MANDATE_KEYWORDS.search(hay) else 0,
        "strategic_scope": 1 if STRATEGIC_SCOPE.search(hay) else 0,
    }
    penalties = 0
    if REVOPS.search(role.title):
        penalties -= 1
    if IC_SEAT.search(role.jd_text):
        penalties -= 1
    if ADTECH.search(hay) and not AI_SIGNALS.search(hay):
        penalties -= 1
    components["penalties"] = penalties

    total = max(1, min(10, sum(components.values())))
    why = (f"Engine-Builder signals {engine}, mandate "
           f"{'present' if components['mandate'] else 'absent'}, "
           f"band bottom {'$' + format(bottom, ',') if bottom else 'not posted'}.")
    return ScoreResult(score=total, auto_rejected=False, rejection_reason=None,
                       components=components, why_it_fits=why)
