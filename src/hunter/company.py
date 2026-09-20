"""Is this business worth his time, asked before any role inside it is.

Why this module exists. Until 2026-09-20 hunter had one score, on the role.
Nothing anywhere asked whether the company was one Krish would join. The
result, measured from his own column A: accept rate fell 77 percent to 15
percent over six weeks, and 28 of the last batch's 33 declines carried a
company-level reason code. He was not rejecting the seat. He was rejecting
the business.

His instruction was not to bolt on a blocklist. It was: "There are so many
companies I am not thinking about that has good backing, potential, mission,
leadership and opportunity." So this is a score, not a list. His 53 named
targets calibrate it (tests/test_company_taste.py) rather than bound it, and
a company he has never heard of can clear the bar on its own evidence.

The evidence rule, which is the whole integrity of the thing. A component
with no recorded evidence scores ZERO and is recorded as unknown. It is never
defaulted and never guessed. A score assembled mostly from unknowns is not a
low score, it is an absent one, so a company with fewer than MIN_EVIDENCED
evidenced components is NEEDS_EVIDENCE and cannot enter the sweep set at all.
That is CLAUDE.md rule 1 applied to a number instead of a click: never report
an outcome you did not read back.

Classification here is deterministic text matching, never a model call. The
model's job is to go and find a cited sentence about the company
(companyintel.py); this module's job is to turn that sentence into points the
same way every time, so the calibration test means something and so a score
can be explained to him in one line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Iterable

# ---------- his five categories, in the words postings and about-pages use ----------
#
# Taken from the Target Companies tab, which groups his 53 named targets into
# exactly these five. The patterns are what those businesses say about
# themselves, not the tab's own label: the tab supplies the calibration LABEL
# and must not also supply the facts, or the scorer would be reading his
# enthusiasm back to him and the test would measure nothing.

CATEGORIES: dict[str, re.Pattern] = {
    # Written from what his 53 named targets actually say on their own
    # homepages, read on 2026-09-20, not from what an AI category sounds
    # like. Every alternative below is a phrase at least one of them uses.
    "creator_genai": re.compile(
        r"\bai[\s-]?(?:video|voice|audio|music|avatar|image|creative|dubbing|"
        r"editing|character)s?\b|"
        r"\b(?:video|voice|image|music|speech|avatar)[\s-]"
        r"(?:generator|generation|generating|synthesis|cloning)\b|"
        r"\b(?:video|audio|photo)[\s-](?:editor|editing|clipping)\b|"
        r"\bgenerative (?:ai )?(?:video|image|audio|voice|media|art)\b|"
        r"\btext[\s-]to[\s-](?:video|image|speech|music)\b|"
        r"\bcreative (?:suite|tools?|ai platform|work)\b|"
        r"\bcreators? (?:economy|independence|advertising|monetis|monetiz|tools?)\b|"
        r"\bfor (?:creators|creatives)\b|\bbuilt for creatives\b|"
        r"\bnewsletter (?:platform|tools?)\b|\bmonetize your newsletter\b|"
        r"\bdigital identity\b|\bvirtual (?:avatars?|humans?)\b", re.I),
    "content_data_licensing": re.compile(
        r"\b(?:content|data|rights?) licensing\b|"
        r"\blicens\w+ (?:content|data|catalog\w*) (?:to|for) ai\b|"
        r"\bpublishers?\b.{0,60}\b(?:monetis|monetiz|paid|payment|revenue|licen)|"
        r"\b(?:monetis|monetiz)\w+.{0,40}\bpublishers?\b|"
        r"\bai (?:crawler|scraper|bot)s?\b|\bbot (?:traffic|management)\b|"
        r"\bagentic internet\b|\bagent[\s-]optimi[sz]ed\b|\bagent era\b|"
        r"\bopen web\b|\bclean[\s-]?rooms?\b|\bdata collaboration\b|"
        r"\bprivacy[\s-]preserving (?:identity|data)\b|"
        r"\bpays? (?:publishers|creators|rights ?holders)\b|"
        r"\bdata (?:marketplace|exchange)\b", re.I),
    "ai_infrastructure": re.compile(
        r"\bai (?:infrastructure|cloud|factory|compute)\b|\bai[\s-]native cloud\b|"
        r"\binference (?:platform|api|engine|stack)\b|"
        r"\b(?:training|fine[\s-]tuning|inference)\b.{0,40}\b(?:platform|cloud|gpu)\b|"
        r"\bgpu (?:cloud|compute|infrastructure|cluster)\b|"
        r"\bdeploy (?:ai|ml|open[\s-]source) models\b|"
        r"\bserve and scale\b|\bserverless (?:gpu|compute|platform)\b|"
        r"\bvector (?:database|search)\b|"
        r"\b(?:llm|model) (?:evaluation|observability|gateway|router|serving)\b",
        re.I),
    "adtech_hypergrowth": re.compile(
        r"\b(?:ad ?tech|advertising) (?:platform|technology|infrastructure)\b|"
        r"\bagentic advertising\b|\bcreator advertising\b|"
        r"\bmedia buying\b|\bad revenue\b|\bretail media\b|"
        r"\b(?:demand|supply)[\s-]side platform\b|\bdsp\b|\bssp\b|"
        r"\b(?:connected|performance) tv\b|\bctv\b|"
        r"\bad (?:measurement|attribution|serving|exchange|monetis|monetiz)\w*\b|"
        r"\bprogrammatic\b|\bcookieless\b|\bomnichannel media\b|"
        r"\bfor advertisers\b|\bbrand growth partner\b|"
        r"\bcommerce monetis|\bcommerce monetiz", re.I),
    "ai_native_enterprise": re.compile(
        r"\benterprise ai\b|\b(?:legal|finance|revenue|sales) ai\b|"
        r"\bai (?:agents?|agent platform|platform|assistant|concierge|"
        r"teammate|employee|copilot|coworker|workforce|os)\b|"
        r"\bagentic (?:ai|workflows?|work|platform|solutions?)\b|"
        r"\bai[\s-](?:native|first|powered|driven) (?:platform|search|workflow|"
        r"automation|support|sales|monetis|monetiz)\w*\b|"
        r"\bai\b[\w\s]{0,12}for (?:legal|sales|support|finance|recruiting|security|"
        r"healthcare|engineering|revenue|marketing)\b|"
        r"\bbuild .{0,20}ai agents\b|\bcustomer experiences with ai\b|"
        r"\brevenue ai\b|\bgtm (?:plays|engineering|platform)\b|"
        r"\bapplied research lab\b|\bfrontier (?:ai|models?)\b|"
        r"\bai (?:research|lab)\b|\banswer engine\b|\bai search\b|"
        r"\b(?:world|foundation(?:al)?|frontier) models?\b|"
        r"\breal[\s-]world intelligence\b|"
        r"\bknowledge (?:assistant|management|search)\b|\benterprise search\b|"
        r"\bhow the world gets work done\b", re.I),
}

# Near his categories without being in them. A developer tools company or a
# vertical SaaS business is not what he asked for, but it is not Omnicom
# either, and scoring it zero alongside an agency holdco would be a lie about
# the distance. Half of category weight, and the evidence line says adjacent.
ADJACENT = re.compile(
    r"\b(?:developer|devtools?|data) (?:tools?|platform|infrastructure)\b|"
    r"\bmachine learning\b|\bartificial intelligence\b|\bsaas (?:platform|company)\b|"
    r"\b(?:b2b|enterprise) software\b|\bapi (?:platform|company|first)\b|"
    r"\bmarketing (?:technology|automation|platform)\b|\bmartech\b|"
    r"\bdata (?:analytics|warehouse|pipeline|quality)\b|\bfintech infrastructure\b", re.I)

# ---------- what he will not join, described rather than named ----------
#
# A name blocklist was considered and rejected: it cannot generalise to the
# companies he has not thought of, which is the whole point of this module.
# These match what the business IS, from its own description, and each one
# lands on the sheet as a quoted evidence line he can overrule.
DISQUALIFYING = [
    ("agency holdco", re.compile(
        r"\b(?:advertising|marketing|communications?|media) (?:holding "
        r"(?:compan(?:y|ies)|group)|agency network|agency group)\b|"
        r"\bholding compan(?:y|ies) (?:of|for) .{0,40}agenc", re.I)),
    ("consultancy", re.compile(
        r"\b(?:management|strategy|technology|it) consult(?:ing|ancy)\b|"
        r"\bprofessional services firm\b|\bsystems integrator\b", re.I)),
    ("staffing", re.compile(
        r"\b(?:staffing|recruitment|recruiting|executive search|talent acquisition) "
        r"(?:agency|firm|company|business)\b|\bjob board\b", re.I)),
    ("regulated incumbent", re.compile(
        r"\b(?:retail|commercial|investment|global) bank\b|\bbanking (?:group|corporation)\b|"
        r"\b(?:insurance|insurer|reinsur\w+) (?:company|group|carrier)\b|"
        r"\basset (?:management|manager) (?:firm|company)\b|\bwealth management firm\b|"
        r"\b(?:pharmaceutical|biopharmaceutical) (?:company|corporation|giant)\b|"
        r"\bhealth (?:system|insurer|plan)\b|\bcredit (?:bureau|rating agency)\b|"
        r"\b(?:utility|telecom(?:munications)?) (?:company|operator|provider)\b", re.I)),
]

# ---------- backing ----------
#
# a16z portfolio membership is a recorded fact hunter already holds for 867
# companies, so it scores without a web call. The rest are the firms whose
# presence on a cap table says the same thing about ambition and access.
TIER_ONE_INVESTORS = re.compile(
    r"\b(?:andreessen horowitz|a16z|sequoia|benchmark|greylock|accel|index ventures|"
    r"lightspeed|kleiner perkins|thrive capital|iconiq|general catalyst|"
    r"founders fund|khosla|insight partners|bessemer|first round|craft ventures|"
    r"redpoint|battery ventures|menlo ventures|nea|new enterprise associates|"
    r"coatue|tiger global|spark capital|union square ventures|usv|"
    r"initialized|y combinator|felicis|scale venture|emergence capital|"
    r"salesforce ventures|google ventures|gv|nvidia|openai startup fund|"
    r"balderton|atomico|northzone|eqt ventures|hoxton|local globe|"
    r"forerunner|bond capital|altimeter|dragoneer|ribbit)\b", re.I)

# ---------- stage and age ----------
EARLY_STAGE = re.compile(
    r"\b(?:pre[\s-]?seed|seed|series [abcd])\b|\bseed[\s-]stage\b|\bearly[\s-]stage\b", re.I)
LATE_PRIVATE = re.compile(r"\bseries [efgh]\b|\bgrowth[\s-]stage\b|\bpre[\s-]?ipo\b", re.I)
PUBLIC_OR_PE = re.compile(
    r"\bpublicly traded\b|\bpublic company\b|\blisted on (?:the )?(?:nasdaq|nyse|lse)\b|"
    r"\b(?:nasdaq|nyse|lse):\s*[a-z]{1,5}\b|\bipo(?:'?d| in \d{4})\b|"
    r"\bprivate equity[\s-](?:owned|backed|backed rollup)\b|\bpe[\s-]backed rollup\b|"
    # "trusted by the Fortune 100" is a customer logo wall, and it read
    # Synthesia and Writer as public megacaps and scored them -3. A
    # company saying it IS one says "a Fortune 500 company", which the
    # article makes unambiguous.
    r"\b(?:a|the) fortune (?:100|500) (?:company|firm)\b|"
    r"\blisted (?:company|on the s&p 500)\b", re.I)
YEAR = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

# ---------- commercial whitespace ----------
#
# His AdFixus playbook, in the words his own tab uses for it: "Publisher
# payment infrastructure for AI - matches AdFixus reposition playbook",
# "Building commercial model in real time", "New revenue category being
# built". A business inventing its commercial model is the seat he wants.
WHITESPACE = re.compile(
    r"\bnew (?:revenue|commercial|pricing) (?:category|model|stream)\b|"
    r"\b(?:building|defining|creating) (?:its|their|the) (?:commercial|revenue|"
    r"go[\s-]to[\s-]market|pricing) (?:model|motion|function|category)\b|"
    r"\bfirst (?:commercial|revenue|gtm|go[\s-]to[\s-]market|sales) (?:hire|leader)\b|"
    r"\bno (?:cro|cco|chief revenue officer|chief commercial officer)\b|"
    r"\b(?:category|market)[\s-]creat(?:ing|ion|or)\b|"
    r"\bmonetis\w+ (?:something|a market|an asset) that\b|"
    r"\bcommercial (?:leadership|team) (?:is )?(?:being built|not yet|yet to be)\b", re.I)
HAS_COMMERCIAL_CHIEF = re.compile(
    r"\b(?:cro|cco|chief revenue officer|chief commercial officer|"
    r"chief business officer) (?:since|appointed|hired|named|joined|in post)\b", re.I)

SENIOR_COMMERCIAL_SEAT = re.compile(
    r"\b(?:general manager|country manager|managing director|chief of staff|"
    r"chief commercial|chief revenue|chief strategy|head of (?:gtm|commercial|"
    r"strategy|partnerships|revenue)|vp,? (?:of )?(?:strategy|commercial|gtm|"
    r"partnerships)|svp|corporate development)\b", re.I)

# ---------- geography ----------
HIS_GEOGRAPHY = re.compile(
    r"\b(?:new york|nyc|ny,|brooklyn|manhattan|london|united kingdom|uk\b)|"
    r"\bremote\b.{0,30}\b(?:us|usa|united states|uk|global|anywhere)\b|"
    r"\b(?:us|usa|united states|uk)[\s-]remote\b", re.I)

HEADCOUNT = re.compile(r"\b(\d[\d,]*)\s*(?:to|-|–)\s*(\d[\d,]*)\b|\b(\d[\d,]*)\+?\s*(?:employees|people|staff|headcount)\b", re.I)
BAND = re.compile(r"\b(\d+)\s*(k?)\s*(?:to|-|–)\s*(\d+)\s*(k?)\b", re.I)

# One, because knowing what the business does is already mandatory and
# MIN_DENOMINATOR already says arithmetically that a single observation is
# not certainty: a company known only by its category lands at 6.7, which is
# Tier 3, swept last. Requiring two observations on top of that was the same
# caution charged twice, and it left 35 percent of the companies he had
# personally named unswept. Measured on his 138 labelled companies: at one,
# the bar sweeps 80 percent of his targets and blocks 87 percent of his
# declines; at two, 65 and 93. Blocking a company he chose costs him a role
# he wants; admitting one he declined costs him a row to reject, and the
# role level gates still stand behind it.
MIN_EVIDENCED = 1
SWEEP_FLOOR = 6.0
DISCOVERY_FLOOR = 8.0
MAX_SCORE = 10.0

NEEDS_EVIDENCE = "needs evidence"

# What each positive component is worth when it is observed. The score is
# normalised over the components that were actually observed, which is the
# repo's existing rule ("never block on no evidence", CLAUDE.md section 2)
# applied to a company instead of a role.
#
# This replaced a first design that scored out of a fixed 10 and marked six
# components unknown. Measured against his own list, that bar admitted 11
# percent of the 53 companies he had personally named as targets, because a
# homepage does not state who led the Series B and an absent fact was
# arithmetically identical to a bad one. Being unable to find something out
# is a fact about hunter, not about the company.
WEIGHTS = {"category": 4.0, "backing": 2.0, "venture_stage": 2.0,
           "whitespace": 2.0, "geography": 1.0}

# Penalties are not normalised. They apply at full strength to the final
# number, because "this is an advertising holding company" is not one
# consideration among several to be averaged away.
PUBLIC_PENALTY = -4.0
DISQUALIFIER_PENALTY = -5.0

# Normalising over only what was observed makes one lucky match look like
# certainty: a company whose board happened to list a London office, and
# nothing else, scored a perfect 10 on that alone. Dividing by at least this
# much weight says plainly that one observation is not a judgement. It is
# the weight of the category component plus one more, so a company in his
# categories and nothing else lands at Tier 3 and gets swept, rather than
# leading the list.
MIN_DENOMINATOR = 6.0


@dataclass(frozen=True)
class Fact:
    """One observed thing about a company, and where it was observed.

    source is a URL for anything fetched, or a named internal source such as
    "a16z portfolio index" or "greenhouse:gleanwork". A Fact with no source
    is refused at construction: an unsourced fact is the thing this whole
    module exists to prevent.
    """
    value: str
    source: str

    def __post_init__(self):
        if not (self.value or "").strip():
            raise ValueError("a Fact with no value is an absent observation, not a fact")
        if not (self.source or "").strip():
            raise ValueError(
                f"fact {self.value[:40]!r} carries no source; an unsourced fact is "
                f"discarded, never stored")


@dataclass
class Facts:
    """Everything recorded about one company. Every field is optional and
    None means not observed, which is scored as unknown and never as zero."""
    slug: str
    name: str
    what_it_does: Fact | None = None
    investors: Fact | None = None
    stage: Fact | None = None
    founded: Fact | None = None
    headcount: Fact | None = None
    commercial_leadership: Fact | None = None
    open_roles: Fact | None = None
    locations: Fact | None = None
    a16z_portfolio: bool = False

    def text(self) -> str:
        parts = [f.value for f in (self.what_it_does, self.investors, self.stage,
                                   self.founded, self.headcount,
                                   self.commercial_leadership, self.open_roles,
                                   self.locations) if f]
        return " \u00b7 ".join(parts)


@dataclass
class Component:
    name: str
    points: float
    evidenced: bool
    evidence: str            # the sentence, in plain words, or why it is unknown
    source: str = ""

    @property
    def unknown(self) -> bool:
        return not self.evidenced

    @property
    def weight(self) -> float:
        return WEIGHTS.get(self.name, 0.0)


@dataclass
class CompanyScore:
    slug: str
    name: str
    total: float
    components: list[Component] = field(default_factory=list)
    status: str = ""

    @property
    def evidenced_count(self) -> int:
        return sum(1 for c in self.components if c.evidenced and c.name in WEIGHTS)

    @property
    def tier(self) -> int | None:
        """His own TIER LEGEND, given a computed basis.

        Tier 1: "Top priority. Active sweep + Apify search + headhunter mention."
        Tier 2: "Active sweep, lower frequency."
        Tier 3: "Reference list. Only sweep if we hear about specific role."
        """
        if self.status == NEEDS_EVIDENCE:
            return None
        if self.total >= 9:
            return 1
        if self.total >= 7:
            return 2
        if self.total >= SWEEP_FLOOR:
            return 3
        return None

    @property
    def sweeps(self) -> bool:
        return self.tier is not None

    def why(self, limit: int = 3) -> str:
        """The one line that goes on the sheet next to the number."""
        ranked = sorted((c for c in self.components if c.evidenced),
                        key=lambda c: -abs(c.points))
        return "; ".join(f"{c.evidence} ({c.points:+g})" for c in ranked[:limit])

    def unknowns(self) -> list[str]:
        return [c.name for c in self.components if c.unknown]

    def as_row(self) -> dict:
        return {"slug": self.slug, "name": self.name, "total": self.total,
                "tier": self.tier, "status": self.status,
                "evidenced": self.evidenced_count,
                "components": [asdict(c) for c in self.components]}


# ---------- the components ----------
#
# Each returns points on its own 0 to WEIGHTS[name] scale, or an unknown.
# "Unknown" and "observed to be zero" are different answers and the
# difference decides whether the company can be scored at all, so no
# component is allowed to collapse them.

def _unknown(name: str, what: str) -> Component:
    return Component(name, 0.0, False, f"{what} not established")


def category_fit(f: Facts) -> Component:
    """0 to 4, the dominant signal, because it is what he complained about."""
    if not f.what_it_does:
        return _unknown("category", "what the business does is")
    text = f.what_it_does.value
    # Best match, not first match. Clay describes itself as infrastructure
    # for agentic GTM workflows and also happens to use the phrase "creative
    # tools", and first-wins ordering labelled it a creator economy company.
    # The points are the same either way, but the line he reads on the sheet
    # has to be right or the score cannot be argued with.
    hits = []
    for name, pat in CATEGORIES.items():
        found = pat.findall(text)
        if found:
            hits.append((len(found), name, pat.search(text).group(0).strip()))
    if hits:
        hits.sort(key=lambda h: (-h[0], list(CATEGORIES).index(h[1])))
        _, name, phrase = hits[0]
        return Component("category", WEIGHTS["category"], True,
                         f"in {name.replace('_', ' ')} ({phrase})",
                         f.what_it_does.source)
    if ADJACENT.search(text):
        return Component("category", WEIGHTS["category"] / 2, True,
                         "adjacent to his categories, not in one",
                         f.what_it_does.source)
    return Component("category", 0.0, True,
                     "outside his five categories", f.what_it_does.source)


def backing(f: Facts) -> Component:
    """0 to 2. Unknown when nobody has told hunter who backs it."""
    if f.a16z_portfolio:
        return Component("backing", WEIGHTS["backing"], True, "a16z portfolio company",
                         "a16z portfolio index")
    if not f.investors:
        return _unknown("backing", "who backs it is")
    m = TIER_ONE_INVESTORS.search(f.investors.value)
    if m:
        return Component("backing", WEIGHTS["backing"], True,
                         f"backed by {m.group(0)}", f.investors.source)
    return Component("backing", 0.0, True, "no tier one backer on record",
                     f.investors.source)


def venture_stage(f: Facts) -> Component:
    """0 to 2 for a young venture-backed business.

    A page that simply does not mention a funding round is an unknown, not a
    company without one. The old code called that "stage gives no signal
    either way" and counted it as an observation worth zero, which dragged
    every company with a plain homepage below the floor.
    """
    blob = " ".join(x.value for x in (f.stage, f.founded, f.what_it_does) if x)
    src = f.stage or f.founded or f.what_it_does
    if not blob.strip():
        return _unknown("venture_stage", "stage and age are")
    years = [int(y) for y in YEAR.findall(f.founded.value)] if f.founded else []
    age = (2026 - min(years)) if years else None
    m = EARLY_STAGE.search(blob)
    if m and (age is None or age <= 10):
        detail = m.group(0) + (f", {age} years old" if age is not None else "")
        return Component("venture_stage", WEIGHTS["venture_stage"], True, detail,
                         src.source)
    if LATE_PRIVATE.search(blob):
        return Component("venture_stage", WEIGHTS["venture_stage"] / 2, True,
                         "later stage private",
                         src.source)
    if age is not None:
        if age <= 10:
            return Component("venture_stage", WEIGHTS["venture_stage"], True,
                             f"{age} years old",
                             src.source)
        if age <= 20:
            return Component("venture_stage", WEIGHTS["venture_stage"] / 2, True,
                             f"{age} years old",
                             src.source)
        return Component("venture_stage", 0.0, True, f"{age} years old",
                         src.source)
    return _unknown("venture_stage", "stage and age are")


def commercial_whitespace(f: Facts) -> Component:
    """0 to 2. The seat he actually wants: a business inventing its
    commercial model, not one defending an existing number."""
    blob = " ".join(x.value for x in (f.commercial_leadership, f.open_roles) if x)
    src = f.commercial_leadership or f.open_roles
    if not blob.strip():
        return _unknown("whitespace", "commercial maturity is")
    m = WHITESPACE.search(blob)
    if m:
        return Component("whitespace", WEIGHTS["whitespace"], True,
                         f"commercial model still being built ({m.group(0).strip()})",
                         src.source)
    if SENIOR_COMMERCIAL_SEAT.search(blob):
        return Component("whitespace", WEIGHTS["whitespace"], True,
                         "hiring into senior commercial leadership", src.source)
    if HAS_COMMERCIAL_CHIEF.search(blob):
        return Component("whitespace", 0.0, True,
                         "commercial leadership already in post", src.source)
    return _unknown("whitespace", "commercial maturity is")


def geography(f: Facts) -> Component:
    """0 to 1. New York, London, or genuinely remote across both."""
    if not f.locations:
        return _unknown("geography", "where it operates is")
    m = HIS_GEOGRAPHY.search(f.locations.value)
    if m:
        return Component("geography", WEIGHTS["geography"], True,
                         f"present in {m.group(0).strip()}", f.locations.source)
    return Component("geography", 0.0, True,
                     f"not in his geography ({f.locations.value[:60]})",
                     f.locations.source)


# ---------- penalties, which are not averaged away ----------

def public_or_pe(f: Facts) -> Component | None:
    """A listed megacap or a PE rollup, said in its own words.

    Returns None when nothing of the sort was observed, so an absence never
    occupies an evidence slot.
    """
    blob = " ".join(x.value for x in (f.stage, f.founded, f.what_it_does) if x)
    src = f.stage or f.founded or f.what_it_does
    if not blob.strip():
        return None
    m = PUBLIC_OR_PE.search(blob)
    if m:
        return Component("public_or_pe", PUBLIC_PENALTY, True,
                         f"public or PE owned ({m.group(0).strip()})", src.source)
    years = [int(y) for y in YEAR.findall(f.founded.value)] if f.founded else []
    if years and (2026 - min(years)) > 25:
        return Component("public_or_pe", PUBLIC_PENALTY, True,
                         f"{2026 - min(years)} years old", src.source)
    return None


def disqualifier(f: Facts) -> Component | None:
    """-5, and only ever from a description of what the business is."""
    if not f.what_it_does:
        return None
    for label, pat in DISQUALIFYING:
        m = pat.search(f.what_it_does.value)
        if m:
            return Component("disqualifier", DISQUALIFIER_PENALTY, True,
                             f"{label} ({m.group(0).strip()})",
                             f.what_it_does.source)
    return None


COMPONENTS = (category_fit, backing, venture_stage, commercial_whitespace,
              geography)
PENALTIES = (public_or_pe, disqualifier)


def score_company(f: Facts) -> CompanyScore:
    comps = [fn(f) for fn in COMPONENTS]
    penalties = [c for c in (fn(f) for fn in PENALTIES) if c]
    earned = sum(c.points for c in comps if c.evidenced)
    possible = max(MIN_DENOMINATOR,
                   sum(c.weight for c in comps if c.evidenced))
    base = MAX_SCORE * earned / possible
    total = base + sum(c.points for c in penalties)
    total = max(0.0, min(MAX_SCORE, total))
    s = CompanyScore(slug=f.slug, name=f.name, total=round(total, 1),
                     components=comps + penalties)
    # Too little observed is not a low score, it is an absent one. Saying
    # "8" about a company hunter read one sentence about is the manufactured
    # outcome this repo exists to refuse.
    #
    # Knowing what the business does is not one consideration among five, it
    # is the prerequisite for having an opinion at all. Without it hunter was
    # scoring Ramp, Cursor and CoreWeave a perfect 10 because their job
    # boards mentioned London, which is not a view about the company.
    known_category = any(c.name == "category" and c.evidenced for c in comps)
    if not known_category or s.evidenced_count < MIN_EVIDENCED:
        s.status = NEEDS_EVIDENCE
    return s


def sweep_set(scores: Iterable[CompanyScore]) -> list[CompanyScore]:
    """Companies whose boards are worth fetching, best tier first."""
    keep = [s for s in scores if s.sweeps]
    keep.sort(key=lambda s: (s.tier, -s.total))
    return keep
