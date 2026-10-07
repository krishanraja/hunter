"""Shared sourcing types and the company-to-ATS map.

A posting found anywhere is discovery, never evidence. Every role must be
resolved to its live full job description before recording, and a bare
LinkedIn URL never reaches the sheet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RolePosting:
    company: str
    title: str
    url: str
    source: str
    location: str | None = None
    comp_text: str | None = None
    posted_at: str | None = None
    ats: str | None = None
    ats_slug: str | None = None
    ats_posting_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ResolvedRole:
    company: str
    title: str
    url: str
    jd_url: str
    jd_text: str
    live: bool
    source: str
    location: str = ""
    comp: str = ""
    stage: str = ""
    # "checked": live is a verified answer from the ATS. "unverified": the
    # URL has no ATS key and no board was found, so nothing could answer;
    # live stays False, and G1 says so honestly instead of calling it dead.
    liveness: str = "checked"
    # Set when this role's bare job id already belongs to the same title in
    # another country (placed_identity_keys): "-south-korea", "-uk".
    id_suffix: str = ""

    @property
    def job_id(self) -> str:
        return job_id(self.company, self.title) + self.id_suffix


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def job_id(company: str, title: str) -> str:
    """The incumbent's unique-key format: company-slug:role-slug."""
    return f"{slugify(company)}:{slugify(title)}"


# Tokens that identify no one. "AI" is in half the universe, so a shared
# "ai" must never make two companies look like one.
COMPANY_STOPWORDS = {
    "ai", "io", "inc", "llc", "ltd", "the", "co", "corp", "group", "labs",
    "lab", "technologies", "technology", "software", "systems", "holdings",
    "global", "company", "limited", "plc", "and", "of",
}


def norm_title(title: str) -> str:
    """Words only, lowercase, in order. The comparison form of a title."""
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def company_tokens(company: str, title: str = "") -> frozenset:
    """The company as tokens, not as a string.

    Two defects in the recorded data made one employer look like several,
    which is why Krish met three rows he had already applied to: rows
    carrying the role glued onto the company ("MongoDB - Head of Post Sales
    Technology"), and the same company slugged in either order
    ("cursor-anysphere" and "anysphere-cursor"). Strip a trailing copy of
    the title, then compare tokens rather than word order.
    """
    slug = slugify(company)
    tslug = slugify(norm_title(title))
    if tslug and slug.endswith("-" + tslug):
        slug = slug[: -len(tslug) - 1]
    return frozenset(t for t in slug.split("-") if t)


def distinctive_tokens(company: str, title: str = "") -> frozenset:
    """The tokens that actually name this employer. Falls back to the whole
    set rather than to nothing, so a company called only "AI Labs" still has
    an identity."""
    toks = company_tokens(company, title)
    keep = frozenset(t for t in toks if len(t) >= 4 and t not in COMPANY_STOPWORDS)
    return keep or toks


def identity_keys(company: str, title: str) -> list[tuple[str, str]]:
    """Every key under which this role counts as already seen. A role
    matching on ANY one of them is the same application target: "Cursor
    (Anysphere)" and "Anysphere" share the token that names them."""
    nt = norm_title(title)
    return [(tok, nt) for tok in sorted(distinctive_tokens(company, title))]


# Where a posting is, at country level, read from its location string. Same
# company and title in a different COUNTRY is a different role: Sierra's
# Regional VP, Sales in London was filed as a duplicate of the one in South
# Korea and never reached him (2026-10-03). A different city in the same
# country stays one role, because that is usually one job listed in several
# offices, and an unknown location matches anything, so a LinkedIn copy of a
# board posting still collapses into it.
_US = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|us|america|americas|amer|north america|new york|nyc|"
    r"brooklyn|manhattan|san francisco|bay area|los angeles|seattle|boston|chicago|"
    r"austin|denver|miami|atlanta|dallas|houston|washington|palo alto|mountain view|"
    r"menlo park|san mateo|san jose|sunnyvale|oakland|berkeley|foster city|"
    r"redwood city|cupertino|santa clara|columbus|philadelphia|pittsburgh|portland|"
    r"phoenix|salt lake|minneapolis|detroit|nashville|raleigh|charlotte|san diego)\b")
# A state abbreviation counts only when no other country is named: "CA" and
# "IN" are also Canada's and India's codes ("Toronto, CA", "Bangalore, IN").
_US_STATE = re.compile(
    r"[a-z ]+, (?:al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|"
    r"ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|dc)\b")
_UK = re.compile(r"\b(united kingdom|uk|england|scotland|wales|london|manchester|"
                 r"edinburgh|bristol|cambridge, uk|oxford, uk|great britain|gb)\b")
_ELSEWHERE = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ireland|dublin|france|paris|germany|berlin|"
    r"munich|netherlands|amsterdam|spain|madrid|barcelona|portugal|lisbon|italy|milan|"
    r"switzerland|zurich|sweden|stockholm|denmark|copenhagen|norway|oslo|finland|"
    r"poland|warsaw|czech|prague|austria|vienna|belgium|brussels|israel|tel aviv|"
    r"india|bangalore|bengaluru|mumbai|delhi|hyderabad|singapore|japan|tokyo|"
    r"south korea|korea|seoul|china|shanghai|beijing|hong kong|taiwan|australia|"
    r"sydney|melbourne|new zealand|brazil|sao paulo|mexico|argentina|colombia|chile|"
    r"uae|dubai|saudi|riyadh|qatar|south africa|nigeria|kenya|egypt|turkey|istanbul|"
    r"indonesia|philippines|vietnam|thailand|malaysia)\b")
_CITY_COUNTRY = {"toronto": "canada", "vancouver": "canada", "montreal": "canada",
                 "dublin": "ireland", "paris": "france", "berlin": "germany",
                 "munich": "germany", "amsterdam": "netherlands", "madrid": "spain",
                 "barcelona": "spain", "lisbon": "portugal", "milan": "italy",
                 "zurich": "switzerland", "stockholm": "sweden", "copenhagen": "denmark",
                 "oslo": "norway", "warsaw": "poland", "prague": "czech", "vienna": "austria",
                 "brussels": "belgium", "tel aviv": "israel", "bangalore": "india",
                 "bengaluru": "india", "mumbai": "india", "delhi": "india",
                 "hyderabad": "india", "tokyo": "japan", "seoul": "south korea",
                 "korea": "south korea", "shanghai": "china", "beijing": "china",
                 "sydney": "australia", "melbourne": "australia", "sao paulo": "brazil",
                 "dubai": "uae", "riyadh": "saudi", "istanbul": "turkey"}


def country_of(location: str) -> frozenset:
    """The countries a location string names, or empty when it names none."""
    loc = " ".join((location or "").lower().replace("_", " ").split())
    if not loc:
        return frozenset()
    out = set()
    for m in _ELSEWHERE.finditer(loc):
        out.add(_CITY_COUNTRY.get(m.group(1), m.group(1)))
    if _UK.search(loc):
        out.add("uk")
    if _US.search(loc) or (not out and _US_STATE.search(loc)):
        out.add("us")
    return frozenset(out)


def placed_identity_keys(company: str, title: str, location: str, *,
                         seen: bool) -> list[tuple[str, str, str]]:
    """Identity keys that know the country.

    seen=True: the keys a role already on record contributes, one per country
    it names ("" when it names none) and a "*" key meaning "any country".
    seen=False: the keys a new posting is looked up under. A posting in a
    named country matches a role in that country or a role of unknown
    country; a posting of unknown country matches any role with its title."""
    base = identity_keys(company, title)
    countries = country_of(location)
    if seen:
        out = [(t, nt, "*") for t, nt in base]
        out += [(t, nt, c) for t, nt in base for c in (countries or {""})]
        return out
    if countries:
        return [(t, nt, c) for t, nt in base for c in countries] + [(t, nt, "") for t, nt in base]
    return [(t, nt, "*") for t, nt in base]


def company_key(company: str, title: str = "") -> str:
    """One stable representative of the token set, for grouping."""
    toks = distinctive_tokens(company, title)
    return min(toks) if toks else ""


# Company -> (ats, board slug). Base map from the workbook Tier-1 tab with the
# six canon 9.1 slug corrections applied (Perplexity, Synthesia, Writer,
# Crusoe and Sierra are Ashby, not Greenhouse; the Glean slug is gleanwork).
# Companies in the canon universe but absent here are swept only when a
# posting for them arrives with its own ATS URL; run reports name the gap.
ATS_MAP: dict[str, tuple[str, str]] = {
    "glean": ("greenhouse", "gleanwork"),
    "clay": ("ashby", "claylabs"),
    "hebbia": ("ashby", "hebbia-ai"),
    "perplexity": ("ashby", "perplexity"),
    "synthesia": ("ashby", "synthesia"),
    "writer": ("ashby", "writer"),
    "crusoe": ("ashby", "crusoe"),
    "sierra": ("ashby", "sierra"),
    "elevenlabs": ("ashby", "elevenlabs"),
    "harvey": ("ashby", "harvey"),
    "captions": ("ashby", "mirage"),  # Captions rebranded; board lives at /mirage
    "decagon": ("ashby", "decagon"),
    "modal": ("ashby", "modal"),
    "agentio": ("ashby", "agentio"),
    "heygen": ("greenhouse", "heygen"),
    "reddit": ("greenhouse", "reddit"),
    "gong": ("greenhouse", "gongio"),
    "cresta": ("greenhouse", "cresta"),
    # Runway the AI video company is /runway-ml. /runway is a finance
    # software company of the same name, with three engineering roles, and
    # its postings say "Runway", so it passed the board identity check: every
    # Runway role he could have seen was read from the wrong company until
    # 2026-10-07, including the AI Engagement Manager role he sent on 10-03.
    "runway": ("ashby", "runway-ml"),
    # Found 2026-10-07; the slug guesser had recorded both as misses.
    "n8n": ("ashby", "n8n"),
    "fal": ("ashby", "fal-ai"),
    "falai": ("ashby", "fal-ai"),
    "tollbit": ("greenhouse", "tollbit"),
    # tvScientific exposes no public ATS board (careers page carries no
    # greenhouse/lever/ashby links, 2026-08-31); discovery-only coverage.
}


def ats_for(company: str) -> tuple[str, str] | None:
    return ATS_MAP.get(slugify(company).replace("-", ""))
