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

from .gates import FLOOR, band_tops_out_at, parse_comp_band, parse_comp_bottom
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
# Being listed is not the stage canon 9.1 asks about. "public|nasdaq|nyse|ipo"
# used to be here and handed a free point to every megacap, which is half of
# why Citi scored 9 and Foundry scored 6.
STAGE_OK = re.compile(r"series [b-z]|late.stage|venture.backed|"
                      r"newly funded|recently raised", re.I)

# There is deliberately no junior-seat title rule here.
#
# One was written on 2026-09-20 because "Founding GTM Lead" at $140,000 scored
# 10, and the measurement against his 148 verdicts killed it immediately: he
# approved Harvey "Business Development Lead" at $240K to $360K, Writer
# "Strategic AI Transformation Lead" at $205K to $259K and LangChain
# "Monetization Programs and Operations Lead", and any rule matching "manager"
# also matched the General Manager seats at ElevenLabs, Foundry and Suno that
# he approved. Lead is not a junior word to him.
#
# The ThoughtSpot role was junior because it paid $140,000, and the reason that
# did not stop it is that the posting's band never reached hunter: the sheet
# read "Not disclosed". Canon 9.3 already auto-rejects a band bottom below the
# floor. The defect is comp capture, not the title, and it is fixed there.
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
    # The role judged on its own merits, with the employer left out entirely.
    #
    # Krish 2026-09-20: "citi is an example where I'd reject that company
    # unless the role was ideal, which that one was." A company he declined
    # is a strong default no, not an absolute one, so the question at such a
    # company is how good the ROLE is. Scoring it with the company penalty
    # already applied and then asking whether it cleared a high bar is
    # circular: the penalty is what stops it clearing.
    merit: int = 0
    employer_kind: str = ""


def _hits(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.I))


# What each component is worth when it can be determined at all.
WEIGHTS = {"engine_builder": 3, "title": 1, "comp_250k": 1, "geography": 1,
           "stage": 1, "ai": 1, "mandate": 1, "strategic_scope": 1,
           "employer": 2}


def score_role(role: ResolvedRole, *, floor: int = FLOOR,
               equity_override: bool = False,
               universe: tuple | list = (),
               employer_index=None) -> ScoreResult:
    hay = f"{role.title}\n{role.jd_text}"
    engine = _hits(ENGINE_SIGNALS, hay)
    quota = _hits(QUOTA_SIGNALS, hay)

    # canon 9.3 auto-reject 1: the posted band below the floor, no override.
    #
    # Measured against his own verdicts 2026-09-20: reading the BOTTOM alone
    # rejects Phantom at $165,000 to $280,000 and Cloudflare at $220,000 to
    # $280,000, both of which he approved. A band whose top clears the floor
    # is negotiable. A band whose top does not is a cheap seat, which is what
    # AKASA "Sales Director, $150,000 to $185,000" is.
    bottom, top = parse_comp_band(role.comp)
    ceiling = band_tops_out_at(role.comp)
    if ceiling is not None and ceiling < floor and not equity_override:
        return ScoreResult(
            score=1, auto_rejected=True,
            rejection_reason=f"the whole band tops out at ${ceiling:,}, below the "
                             f"${floor:,} floor, with no approved equity "
                             f"override (canon 9.3)")

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
    # An unknown is not a zero. Most UK and EU postings publish no band, and
    # G2 already treats that as "flag for review" rather than as evidence
    # against the role; scoring it zero as well counted the same silence
    # twice and held a point hostage on nearly every posting. Same for stage:
    # a JD rarely says "Series C", but membership of the canon 9.1 universe
    # settles the question, and absent both the component is unknown rather
    # than failed. Unknown components leave both the numerator and the
    # denominator, and the score is expressed out of ten.
    # Exact lowercased string equality, which is what this was, means "Clay
    # Labs" is not Clay and "Cursor (Anysphere)" is not Anysphere. hunter has
    # distinctive_tokens for exactly this and uses it everywhere else, so the
    # one signal that carried his named list into the score fired almost
    # never.
    from .sources import distinctive_tokens
    role_tokens = distinctive_tokens(role.company or "", "")
    in_universe = any(role_tokens & distinctive_tokens(str(c), "")
                      for c in universe)
    # STAGE_OK used to match "public", "nasdaq", "nyse" and "ipo", so a listed
    # bank earned this point for being a listed bank. Krish 2026-09-20 on
    # exactly those roles: "legacy businesses like SiriusXM, Citi, Omnicom".
    # Being publicly traded is not evidence of the stage canon 9.1 wants, so
    # it is no longer read as such; the employer component below is what
    # carries company quality now.
    stage_stated = bool(STAGE_OK.search(f"{role.stage} {role.jd_text}"))

    # Who the employer is, on record rather than on a taxonomy. See
    # employer.py: a sector blocklist was measured against his 148 verdicts
    # first and would have killed BioSpace, Recursion, Talkspace, Harvey and
    # Razorfish, all of which he approved.
    from . import employer as employer_mod
    emp = employer_mod.classify(role.company or "", employer_index)

    components: dict[str, int | None] = {
        "engine_builder": min(3, engine),
        "title": 1 if SENIOR_TITLE.search(role.title) else 0,
        "comp_250k": (1 if bottom >= 250_000 else 0) if bottom is not None else None,
        "geography": 1 if GEO_POINT.search(f"{role.location} {role.jd_text[:300]}") else 0,
        "stage": 1 if (stage_stated or in_universe) else None,
        "ai": 1 if AI_SIGNALS.search(f"{role.company} {hay}") else 0,
        "mandate": 1 if MANDATE_KEYWORDS.search(hay) else 0,
        "strategic_scope": 1 if STRATEGIC_SCOPE.search(hay) else 0,
        "employer": max(0, emp.points),
    }
    penalties = 0
    if REVOPS.search(role.title):
        penalties -= 1
    if IC_SEAT.search(role.jd_text):
        penalties -= 1
    if ADTECH.search(hay) and not AI_SIGNALS.search(hay):
        penalties -= 1
    if emp.points < 0:
        penalties += emp.points
    components["penalties"] = penalties

    known = {k: v for k, v in components.items()
             if k in WEIGHTS and v is not None}
    available = sum(WEIGHTS[k] for k in known) or 1
    earned = sum(known.values()) + penalties
    total = max(1, min(10, round(earned / available * 10)))
    # The same arithmetic with the employer component and its penalty removed.
    merit_known = {k: v for k, v in known.items() if k != "employer"}
    merit_available = sum(WEIGHTS[k] for k in merit_known) or 1
    merit_penalties = penalties - min(0, emp.points)
    merit = max(1, min(10, round(
        (sum(merit_known.values()) + merit_penalties) / merit_available * 10)))
    unknown = sorted(k for k in WEIGHTS if components.get(k) is None)
    why = (f"Engine-Builder signals {engine}, mandate "
           f"{'present' if components['mandate'] else 'absent'}, "
           f"employer {emp.kind} ({emp.evidence}), "
           f"band bottom {'$' + format(bottom, ',') if bottom else 'not posted'}"
           + (f"; not determinable from the posting: {', '.join(unknown)}"
              if unknown else "") + ".")
    return ScoreResult(score=total, auto_rejected=False, rejection_reason=None,
                       components=components, why_it_fits=why,
                       merit=merit, employer_kind=emp.kind)
