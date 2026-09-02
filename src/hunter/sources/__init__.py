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

    @property
    def job_id(self) -> str:
        return job_id(self.company, self.title)


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
    "runway": ("ashby", "runway"),
    "tollbit": ("greenhouse", "tollbit"),
    # tvScientific exposes no public ATS board (careers page carries no
    # greenhouse/lever/ashby links, 2026-08-31); discovery-only coverage.
}


def ats_for(company: str) -> tuple[str, str] | None:
    return ATS_MAP.get(slugify(company).replace("-", ""))
