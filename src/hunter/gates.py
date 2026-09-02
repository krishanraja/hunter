"""The ten verification gates, canon 9.4 verbatim in intent. G1 to G7 run at
sourcing time; G8 to G10 run again at package time against the built document
texts. The hunter_never_apply blocklist fires before any gate. run.py asserts
at startup that canon still states the constants encoded here (floor, bar);
if canon moves, the run aborts and says which side to update.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .sources import ResolvedRole

FLOOR = 200_000  # canon 6, decision 2026-08-24

# "SVP, Strategy" and "GM, UK" are how the postings Krish approves actually
# write themselves, and neither matched: \bvp never fires inside SVP, and GM
# was absent entirely, so G3 rejected the archetype A titles canon section 5
# puts first. Both are pinned by tests named for the roles they cost.
SENIOR_TITLE = re.compile(
    r"\b([se]?vp|vice president|chief|director|founding|general manager|"
    r"managing director|country manager|president|gm|c[a-z]o)\b"
    r"|\bhead\s*(?:of\b|,)", re.I)
YEARS_RANGE = re.compile(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*\+?\s*years?", re.I)
MANDATE = re.compile(
    r"architect|build|operating model|p&l|p and l|market entry|zero.to.one|"
    r"0.to.1|design the|transform|from scratch|establish", re.I)
QUOTA_ONLY = re.compile(r"quota|close pipeline|deliver against|existing motion|"
                        r"book of business", re.I)
GEO_ALLOW = re.compile(
    r"london|united kingdom|\buk\b|uk.remote|new york|\bnyc\b|remote", re.I)
# A bare "remote" is not a location. "Remote India" and "Germany
# (remote-first, in-country)" both satisfied GEO_ALLOW and passed G6 until
# 2026-09-01: naming a country canon does not cover fails the gate unless the
# posting also anchors itself in the UK, New York or the US.
FOREIGN_GEO = re.compile(
    r"\b(brazil|denmark|poland|saudi|mexico|italy|spain|germany|france|"
    r"switzerland|netherlands|sweden|norway|finland|portugal|india|singapore|"
    r"japan|korea|china|australia|new zealand|dubai|\buae\b|qatar|"
    r"ireland|austria|belgium|czech|romania|turkey|israel|"
    r"latam|\bapj\b|\bapac\b|\bdach\b)\b", re.I)
GEO_ANCHOR = re.compile(
    r"london|united kingdom|\buk\b|new york|\bnyc\b|united states|"
    r"\bus\b|\bu\.s\.?\b|americas", re.I)


def names_foreign_geo(hay: str) -> bool:
    """Names a country canon 9.4 does not cover, with no UK, NYC or US anchor.
    Unambiguous on a location string alone, so callers without the JD text
    (the sheet pruner) can use it safely."""
    return bool(FOREIGN_GEO.search(hay)) and not bool(GEO_ANCHOR.search(hay))


def geography_ok(hay: str) -> bool:
    """Canon 9.4 geography: London, UK-remote, NYC or US-remote. Needs the JD
    text alongside the location, since a US city only reads as in-geography
    once the posting says remote."""
    if names_foreign_geo(hay):
        return False
    return bool(GEO_ALLOW.search(hay))
US_RESIDENCE = re.compile(
    r"must (?:reside|be located|be based|live) in the (?:united states|u\.?s)|"
    r"u\.?s\.? residen[ct]", re.I)
DOMAIN_FAIL = re.compile(
    r"clinical|manufactur|offline retail|defen[cs]e industry|\bdefen[cs]e\b|"
    r"banking back.office|insurance carrier|\binsurance\b|real estate|"
    r"private equity fund|investment bank", re.I)
AI_TRANSFORMATION = re.compile(r"ai transformation|ai.native|agentic|genai|"
                               r"generative ai|artificial intelligence", re.I)
IC_SIGNALS = re.compile(r"individual contributor|personally close", re.I)
LEADERSHIP_SIGNALS = re.compile(r"manage|lead a team|hire|build the team|"
                                r"direct reports|leader.of.leaders", re.I)
POSITIONING_BANNED = re.compile(
    r"looking for work|between roles|transitioning from founder", re.I)


class GateError(RuntimeError):
    pass


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason: str


@dataclass
class GateReport:
    results: list[GateResult]

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.results)

    def failures(self) -> list[GateResult]:
        return [g for g in self.results if not g.passed]


def parse_comp_bottom(text: str | None) -> int | None:
    """Bottom of a posted band in dollars, or None when nothing is posted."""
    if not text:
        return None
    m = re.search(r"\$\s*([\d][\d,\.]*)\s*([kK])?", text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    if m.group(2):
        value *= 1000
    return int(value)


def run_gates(role: ResolvedRole, *, never_apply: list[str] | tuple = (),
              equity_override: bool = False,
              package_texts: tuple[str, str] | None = None) -> GateReport:
    results: list[GateResult] = []
    hay = f"{role.title}\n{role.jd_text}"

    blocked = next((n for n in never_apply
                    if n.lower().strip() == role.company.lower().strip()), None)
    results.append(GateResult(
        "G0", blocked is None,
        f"company is on the hunter_never_apply list: {blocked}" if blocked
        else "not on the never_apply list"))

    results.append(GateResult(
        "G1", role.live,
        "posting live on the board" if role.live else
        "posting is dead per the direct ATS check"))

    bottom = parse_comp_bottom(role.comp)
    if bottom is None:
        results.append(GateResult("G2", True, "no posted band; flag for review"))
    elif bottom >= FLOOR or equity_override:
        results.append(GateResult("G2", True, f"band bottom ${bottom:,}"))
    else:
        results.append(GateResult(
            "G2", False, f"band bottom ${bottom:,} is below the ${FLOOR:,} floor "
                         f"with no approved equity override"))

    title_senior = bool(SENIOR_TITLE.search(role.title))
    ic = bool(IC_SIGNALS.search(role.jd_text)) and not LEADERSHIP_SIGNALS.search(role.jd_text)
    results.append(GateResult(
        "G3", title_senior and not ic,
        "senior title with leadership scope" if title_senior and not ic else
        ("senior individual contributor seat wearing a leadership title" if ic
         else f"title below the seniority bar: {role.title!r}")))

    years = YEARS_RANGE.search(role.jd_text)
    if years and int(years.group(2)) < 8:
        results.append(GateResult(
            "G4", False,
            f"posting demands {years.group(0).strip()}, inconsistent with a "
            f"sixteen year operator (the Slingshot AI miss)"))
    else:
        results.append(GateResult("G4", True, "stated years fit a 16-year operator"))

    if MANDATE.search(hay):
        results.append(GateResult("G5", True, "carries a build or operating mandate"))
    elif QUOTA_ONLY.search(hay):
        results.append(GateResult("G5", False, "solely a number to carry, no mandate"))
    else:
        results.append(GateResult("G5", True, "no quota-only language found"))

    if US_RESIDENCE.search(role.jd_text):
        results.append(GateResult(
            "G6", False, "explicit US-residence requirement blocks the role"))
    else:
        geo_hay = f"{role.location} {role.jd_text[:400]}"
        ok = geography_ok(geo_hay)
        results.append(GateResult(
            "G6", ok, "London, UK-remote, NYC or US-remote" if ok else
            f"location outside canon 9.4 geography: {role.location!r}"))

    domain_hit = DOMAIN_FAIL.search(hay)
    if domain_hit and not AI_TRANSFORMATION.search(hay):
        results.append(GateResult(
            "G7", False, f"domain fails canon 9.4: {domain_hit.group(0)!r}"))
    else:
        results.append(GateResult("G7", True, "internet-native or AI mandate"))

    if package_texts is None:
        for g in ("G8", "G9", "G10"):
            results.append(GateResult(g, True, "deferred to package stage"))
    else:
        combined = "\n".join(package_texts)
        results.append(GateResult(
            "G8", True,
            "claims come from canon-approved blocks and canon-derived masters "
            "by construction; jd_mirror numbers are JD-verbatim checked"))
        g9_fails = []
        if "\u2014" in combined:
            g9_fails.append("em dash")
        if "{{" in combined:
            g9_fails.append("unreplaced placeholder")
        results.append(GateResult("G9", not g9_fails,
                                  ", ".join(g9_fails) or "form checks pass"))
        banned = POSITIONING_BANNED.search(combined)
        results.append(GateResult(
            "G10", banned is None,
            f"banned positioning: {banned.group(0)!r}" if banned
            else "positioning clean"))

    return GateReport(results=results)
