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
# Titles that ARE the quota seat, however the JD is worded.
QUOTA_SEAT_TITLE = re.compile(
    r"\b(enterprise sales director|sales director|director,? of sales|"
    r"director of sales|account (?:director|executive|manager)|"
    r"strategic account|regional (?:vice president|vp|director)"
    r"(?:,? (?:sales|business development))?|sales enablement|"
    r"territory manager|sales manager)\b", re.I)

# Functions with no canon section 5 archetype at all.
OUT_OF_SCOPE_FUNCTION = re.compile(
    r"\b(government affairs|public policy|talent acquisition|recruit\w*|"
    r"people operations|human resources|\bhr\b|workplace|facilities|"
    r"product marketing|developer experience|developer relations|"
    r"solutions engineering|sales engineering|field marketing|"
    r"growth marketing|controller|investor relations|general counsel|"
    r"legal|procurement|compliance|payroll)\b", re.I)

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

    # canon 9.3 auto-reject 2: pure quota carrying with no build mandate.
    #
    # The JD wording test alone never fired. Every enterprise sales posting
    # says "build relationships" and "own the territory", which reads as an
    # engine signal, so `engine` was never 0 and thirteen Sierra Enterprise
    # Sales Director rows sat at score 8 (2026-09-02 audit). The seat is the
    # thing canon rejects, and the title names the seat: a quota seat needs a
    # real build mandate to survive, not merely the absence of the word quota.
    seat = QUOTA_SEAT_TITLE.search(role.title or "")
    if seat:
        # No escape hatch on the JD wording. The first version let the seat
        # survive when the posting showed build language, and every sales JD
        # shows build language, so all thirteen Sierra rows survived. The
        # title names the seat, and canon 9.3 rejects the seat. Krish can
        # still override any single role with a free-text verdict.
        return ScoreResult(
            score=1, auto_rejected=True,
            rejection_reason=f"{seat.group(0)!r} is a quota-carrying seat "
                             f"(canon 9.3 auto-reject)")
    if quota >= 2 and engine == 0:
        return ScoreResult(
            score=1, auto_rejected=True,
            rejection_reason="pure quota carrying with no architecture, build or "
                             "operating-model mandate (canon 9.3)")

    # Outside every canon section 5 archetype. Krish is a commercial and GTM
    # operator; policy, HR, recruiting, finance, product marketing and
    # developer experience are somebody else's ladder, whatever the JD pays.
    out = OUT_OF_SCOPE_FUNCTION.search(role.title or "")
    if out:
        return ScoreResult(
            score=2, auto_rejected=True,
            rejection_reason=f"{out.group(0)!r} sits outside the canon section 5 "
                             f"archetypes; not a commercial or GTM mandate")

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
