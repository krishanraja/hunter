"""The gate every generated string passes before it can reach a document.

Canon 9.12 locked the CV to lossless tailoring: reorder and select, never
generate. Krish lifted that on 2026-09-14 for two pieces only, the PROFESSIONAL
SUMMARY and the cover letter hook, because 12 words of JD mirroring is not
"customised". Generation is only acceptable with a gate, and the gate that
matters is factual rather than stylistic:

  A generated claim that cannot be traced to a recorded proof point or to the
  master is a HARD FAILURE. The model may rearrange and reframe Krish's
  evidence. It may not invent a metric into his CV.

The style rules come from Profile's own VOICE AND TONE block, which
apply/infobank.parse_profile already parses into a banned phrase list, so this
module does not keep a second copy of his rules that could drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EM_DASH = "\u2014"  # escaped: a literal one here trips the repo guard

# Numbers written any of the ways they appear in his evidence.
_NUMBER = re.compile(
    r"""(
        \$\s?\d[\d,.]*\s*[KkMmBb]?         # $12M, $254K, $1.5M, $0
      | \d[\d,.]*\s*[KkMmBb]\b             # 4000k style, 12M
      | \d[\d,.]*\s*%                      # 22%
      | \b\d[\d,]*\b                       # 61, 18, 4000
    )""", re.X)

# Words that look like a number but carry no factual claim, so requiring a source
# for them would block ordinary prose.
_NUMBER_STOPWORDS = frozenset({"one", "two", "three", "four", "five", "six",
                               "seven", "eight", "nine", "ten"})

# Capitalised tokens that are ordinary English rather than an employer claim.
_NAME_ALLOWED = frozenset({
    "I", "A", "AI", "APAC", "EMEA", "US", "USA", "UK", "P", "L", "GTM", "ARR",
    "EBITDA", "POC", "M", "A", "CTV", "SaaS", "B2B", "The", "My", "Level",
    "Commercial", "Sixteen", "With", "For", "And", "In", "It", "They", "We",
})

_NAME = re.compile(r"\b([A-Z][A-Za-z0-9&/.\-]{1,}(?:\s+[A-Z][A-Za-z0-9&/.\-]+)*)\b")


class VoiceGateError(RuntimeError):
    pass


@dataclass
class Verdict:
    ok: bool
    failures: list[str] = field(default_factory=list)

    def raise_if_bad(self, what: str) -> None:
        if not self.ok:
            raise VoiceGateError(
                f"{what} failed the voice gate: " + "; ".join(self.failures))


def normalize_number(token: str) -> str:
    """Collapse a written number to a comparable form, so "$12M" in generated
    prose matches "$12M ARR" in the evidence and "12 M" does not slip past."""
    t = token.lower().replace(",", "").replace(" ", "")
    t = t.rstrip(".")
    return t


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in _NUMBER.finditer(text or ""):
        tok = normalize_number(m.group(0))
        bare = tok.lstrip("$").rstrip("%kmb")
        if bare in _NUMBER_STOPWORDS or not bare:
            continue
        out.add(tok)
    return out


def names_in(text: str) -> set[str]:
    out = set()
    for m in _NAME.finditer(text or ""):
        name = m.group(1).strip()
        if name in _NAME_ALLOWED or len(name) < 3:
            continue
        # A sentence-initial ordinary word is not a company name.
        out.add(name)
    return out


def build_evidence(*sources: str) -> str:
    """One lowercased haystack of everything Krish has actually recorded: the
    master text, the Profile proof points, the Interview Answers, the JD."""
    return "\n".join(s or "" for s in sources).lower()


def check(text: str, *, evidence: str, banned_phrases: tuple[str, ...] = (),
          max_chars: int = 0, min_chars: int = 0,
          allow_names: frozenset[str] = frozenset()) -> Verdict:
    """Every rule, in one pass. Returns every failure rather than the first, so
    a regeneration prompt can address all of them at once."""
    failures: list[str] = []
    body = text or ""

    if not body.strip():
        return Verdict(False, ["empty"])
    if EM_DASH in body:
        failures.append("contains an em dash")
    if "{{" in body or "}}" in body:
        failures.append("contains an unreplaced placeholder")
    if "[[" in body or "]]" in body:
        failures.append("contains an unfilled template slot")
    if min_chars and len(body) < min_chars:
        failures.append(f"too short ({len(body)} chars, min {min_chars})")
    if max_chars and len(body) > max_chars:
        failures.append(f"too long ({len(body)} chars, max {max_chars})")

    low = body.lower()
    for phrase in banned_phrases:
        p = (phrase or "").strip().lower()
        if p and p in low:
            failures.append(f"banned phrase {phrase!r}")

    # The factual gate. Every number must be somewhere in his recorded evidence.
    for num in sorted(numbers_in(body)):
        if num not in evidence.replace(",", "").replace(" ", ""):
            failures.append(f"number {num!r} is not in the recorded evidence")

    # And every company-shaped name. A multi-word name is checked token by
    # token, because "Captify APAC" is traceable when the evidence carries
    # "Captify" and "APAC" separately; requiring the exact pair would reject
    # ordinary rephrasing while catching nothing extra. "Salesforce" still
    # fails, which is the case that matters.
    for name in sorted(names_in(body)):
        if name in allow_names:
            continue
        for token in re.split(r"[\s/]+", name):
            token = token.strip(".,&-")
            if not token or len(token) < 3 or token in _NAME_ALLOWED \
                    or token in allow_names:
                continue
            if token.lower() not in evidence:
                failures.append(
                    f"name {token!r} is not in the recorded evidence")

    return Verdict(not failures, failures)
