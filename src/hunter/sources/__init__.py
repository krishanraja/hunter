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
