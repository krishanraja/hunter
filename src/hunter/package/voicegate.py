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

# Ordinary English words that can open a sentence, exempt ONLY in that position.
#
# Capitalisation at the start of a sentence carries no information, so a single
# capitalised word there is not evidence of an employer. This cost two real
# packages: a summary was rejected for the word "Underneath" and a hook for
# "Designing", and a rejection drops the tailored prose back to a generic block, so
# the customisation is lost for a reason that has nothing to do with fabrication.
#
# It is a whitelist rather than a rule about position, deliberately. Exempting every
# sentence-initial token would let "Salesforce is where I built it." through, and
# catching an invented employer is the entire job of this function. The cost of a
# whitelist is that it needs occasional additions; each one is a visible commit, and
# a miss costs customisation rather than correctness.
_SENTENCE_OPENERS = frozenset({
    "Across", "After", "Against", "All", "Along", "Alongside", "Although",
    "Applying", "Around", "As", "At", "Backed", "Because", "Before", "Behind",
    "Beneath", "Between", "Beyond", "Both", "Building", "Built", "But", "By",
    "Called", "Closing", "Creating", "Currently", "Designing", "Doing", "Driving",
    "During", "Each", "Either", "Every", "Everything", "Few", "First", "Five",
    "Following", "From", "Getting", "Given", "Growing", "Having", "He", "Her",
    "Here", "His", "How", "However", "If", "Inside", "Into", "Its", "Just",
    "Keeping", "Last", "Leading", "Led", "Making", "Many", "More", "Most", "Much",
    "Neither", "Next", "No", "None", "Nor", "Not", "Nothing", "Now", "Of", "On",
    "Once", "One", "Only", "Or", "Other", "Our", "Out", "Outside", "Over", "Owning",
    "Putting", "Rather", "Running", "Scaling", "Selling", "Setting", "Several",
    "She", "Since", "So", "Some", "Something", "Starting", "Still", "Taking",
    "Talking", "Ten", "That", "Their", "Them", "Then", "There", "These", "They",
    "This", "Those", "Three", "Through", "Throughout", "Today", "Together", "Turning",
    "Twice", "Two", "Under", "Underneath", "Unlike", "Until", "Up", "Using",
    "Was", "What", "When", "Where", "Whether", "Which", "While", "Who", "Why",
    "Winning", "Within", "Without", "Working", "Writing", "Yet", "You", "Your",
})

_NAME = re.compile(r"\b([A-Z][A-Za-z0-9&/.\-]{1,}(?:\s+[A-Z][A-Za-z0-9&/.\-]+)*)\b")

# A capitalised token that follows a sentence end, or opens the text.
_SENTENCE_START = re.compile(r"(?:^|[.!?]['\")\]]?\s+)$")


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
    """Company-shaped names in the text. A single ordinary English word opening a
    sentence is not one; see _SENTENCE_OPENERS for why that is a whitelist."""
    body = text or ""
    out = set()
    for m in _NAME.finditer(body):
        name = m.group(1).strip()
        if name in _NAME_ALLOWED or len(name) < 3:
            continue
        # Only a SINGLE token gets the exemption. "Underneath Captify" keeps
        # "Captify", because a multi-word capitalised run is not sentence case.
        if " " not in name and name in _SENTENCE_OPENERS \
                and _SENTENCE_START.search(body[:m.start()]):
            continue
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
                    or token in allow_names or token in _SENTENCE_OPENERS:
                # A sentence opener swept into a multi-word match, as in
                # "Underneath Captify sat a partner engine": "Captify" still has to
                # trace, "Underneath" never did. Safe because no fabricated employer
                # is a word on that list.
                continue
            if token.lower() not in evidence:
                failures.append(
                    f"name {token!r} is not in the recorded evidence")

    return Verdict(not failures, failures)
