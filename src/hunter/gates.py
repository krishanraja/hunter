"""The verification gates, canon 9.4 verbatim in intent. G1 to G7, G11 and
G12 run at sourcing time; G8 to G10 run again at package time against the
built document texts. The hunter_never_apply blocklist fires before any
gate. run.py asserts at startup that canon still states the constants
encoded here (floor, bar); if canon moves, the run aborts and says which
side to update.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .archetype import archetype, families as archetype_families
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


# A band stated as base pay with variable on top is not the whole package.
# Talkspace "Head of Commercial Sales, $170K-$190K base + variable" is a role
# he approved; read as a total band it tops out below the floor and would have
# been auto-rejected.
VARIABLE_ON_TOP = re.compile(
    r"\bbase\b.{0,40}?\b(variable|bonus|commission|incentive|\bote\b|equity)\b|"
    r"\b(variable|bonus|commission|incentive|\bote\b|equity)\b.{0,40}?\bbase\b|"
    r"\+\s*(variable|bonus|commission|equity)", re.I | re.S)


def band_tops_out_at(text: str | None) -> int | None:
    """The most this role can pay, as far as the posting says, or None when
    the posting does not settle it.

    None means "do not judge on pay", which is what an absent band already
    means to G2. A base band with variable stacked on top is one of those:
    the number is real and it is not the ceiling.
    """
    bottom, top = parse_comp_band(text)
    ceiling = top if top is not None else bottom
    if ceiling is None:
        return None
    if VARIABLE_ON_TOP.search(text or ""):
        return None
    return ceiling


def parse_comp_band(text: str | None) -> tuple[int | None, int | None]:
    """(bottom, top) of a posted band. Top is None when only one figure.

    The top matters because canon 9.3 rejects on the bottom alone, and his
    own approvals contradict that reading: Phantom "Head of Business and
    Corporate Development" at $165,000 to $280,000 and Cloudflare "Head of
    GTM, AI Inference" at $220,000 to $280,000 are both roles he said yes to
    with a bottom at or under the floor. A band whose TOP clears the floor
    comfortably is a negotiable band, not a cheap seat. A band whose top is
    below the floor is a cheap seat.
    """
    if not text:
        return None, None
    found: list[int] = []
    for m in re.finditer(r"\$?\s*([\d][\d,\.]*)\s*([kK])?", text):
        raw = m.group(1).replace(",", "")
        if not raw or raw.count(".") > 1:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if m.group(2):
            value *= 1000
        value = int(value)
        if 1_000 <= value <= 5_000_000:
            found.append(value)
    if not found:
        return None, None
    return min(found), (max(found) if len(found) > 1 else None)


def run_gates(role: ResolvedRole, *, never_apply: list[str] | tuple = (),
              equity_override: bool = False,
              package_texts: tuple[str, str] | None = None,
              company_declines: dict | None = None,
              employer_index=None) -> GateReport:
    results: list[GateResult] = []
    hay = f"{role.title}\n{role.jd_text}"

    blocked = next((n for n in never_apply
                    if n.lower().strip() == role.company.lower().strip()), None)
    results.append(GateResult(
        "G0", blocked is None,
        f"company is on the hunter_never_apply list: {blocked}" if blocked
        else "not on the never_apply list"))

    if getattr(role, "liveness", "checked") == "unverified":
        # No ATS key and no board found: nothing could answer. Krish's Yes
        # is the authority for a build here, and Package Status records
        # that liveness was never verified.
        results.append(GateResult(
            "G1", True, "liveness unverified: no ATS key on the URL; built on "
                        "Krish's approval, Package Status records it"))
    else:
        results.append(GateResult(
            "G1", role.live,
            "posting live on the board" if role.live else
            "posting is dead per the direct ATS check"))

    bottom, top = parse_comp_band(role.comp)
    ceiling = band_tops_out_at(role.comp)
    if bottom is None or ceiling is None:
        results.append(GateResult("G2", True, "no settled band; flag for review"))
    elif ceiling >= FLOOR or equity_override:
        results.append(GateResult(
            "G2", True, f"band ${bottom:,}" + (f" to ${top:,}" if top else "")))
    else:
        results.append(GateResult(
            "G2", False, f"the whole band tops out at ${ceiling:,}, below the "
                         f"${FLOOR:,} floor, with no approved equity override"))

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

    # G11: is this even one of his shapes? Canon section 5 names the families
    # he is targeting, and until 2026-09-02 nothing asked the question before
    # presenting a role. Every gate before this one asks "is this bad" and
    # needs a new rule for every kind of bad; this one asks "is this his" and
    # needs none. Measured on the 419 roles then on record: 419 in, 101 out,
    # and 16 of the 17 he had said go to survive.
    fam = archetype(role.title)
    results.append(GateResult(
        "G11", fam is not None,
        f"canon 5 archetype: {fam}" if fam else
        f"title {role.title!r} is none of his archetypes "
        f"({', '.join(archetype_families())})"))

    # G12: a company Krish declined as business uninteresting or domain
    # expertise is a verdict on the company. Applied as written, dated, and
    # reversible through hunter_company_allow.
    from .learn import declined_company
    hit = declined_company(company_declines, role.company)
    results.append(GateResult(
        "G12", hit is None,
        f"company declined by Krish on {hit['date']} ({hit['code']})" if hit
        else "no company-level decline on record"))

    # G7, and the escape hatch that switched it off.
    #
    # This used to read: blocked unless the posting text matches
    # AI_TRANSFORMATION, which fires on "artificial intelligence", "genai" and
    # "agentic". Every AI role at every bank contains those words, so the gate
    # whose whole purpose is to block banking and insurance was disabled by
    # the job title. Citi "Head of AI-First Development", BNY, TIAA, New York
    # Life, Janus Henderson, Pfizer and Wolters Kluwer all walked through it
    # and reached Krish scored 9 and 10. He declined every one.
    #
    # The escape now asks about the EMPLOYER, not the wording: a company in
    # the a16z portfolio, or one he has already approved a role at, is an
    # AI-native business and the domain words in its posting are what it does.
    # A company hunter has no record of keeps the old text escape, because
    # blocking on no evidence would be a worse error than the one being fixed.
    from . import employer as employer_mod
    emp = employer_mod.classify(role.company or "", employer_index)
    domain_hit = DOMAIN_FAIL.search(hay)
    if not domain_hit:
        results.append(GateResult("G7", True, "internet-native or AI mandate"))
    elif emp.is_ai_native:
        results.append(GateResult(
            "G7", True, f"domain words present but the employer is AI-native: "
                        f"{emp.evidence}"))
    elif emp.blocks:
        results.append(GateResult(
            "G7", False, f"{domain_hit.group(0)!r} at {role.company}, which is "
                         f"{emp.evidence}"))
    elif AI_TRANSFORMATION.search(hay):
        results.append(GateResult(
            "G7", True, f"domain words present, AI mandate stated, and hunter "
                        f"has no record of {role.company} either way"))
    else:
        results.append(GateResult(
            "G7", False, f"domain fails canon 9.4: {domain_hit.group(0)!r}"))

    # G13: the employer itself, where there is evidence about it. A bank,
    # insurer or asset manager is the one sector his verdicts establish
    # without a counterexample: 7 declines, 0 approvals, measured before this
    # gate was written. Every other sector he declines also contains a role he
    # approved, which is why no other sector is here and why the score, not a
    # gate, carries the rest of company quality.
    results.append(GateResult(
        "G13", not emp.blocks, f"employer {emp.kind}: {emp.evidence}"))

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
