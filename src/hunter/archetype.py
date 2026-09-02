"""Which of Krish's role shapes a posting is, or none.

This is the filter the system was missing. Everything before it asked "is
this role bad" and needed a new exclusion for every kind of bad. Canon
section 5 already says what he is targeting, so the question is inverted: a
posting reaches his sheet only if it positively matches one of his shapes,
and nothing else needs a rule.

Measured against the 419 roles on record on 2026-09-02: 90 pass, and 16 of
the 17 roles he personally said go to are among them. Seven of the twelve he
personally rejected never get here at all.

Deliberately separate from package/tailor.py's FAMILY_PATTERNS, which look
similar and do a different job. Those choose an approved letter block after a
role is already on the sheet, so they are loose on purpose and fall back to
commercial_strategy rather than losing a package. A gate cannot be loose and
cannot have a fallback. Keeping one copy would break whichever job lost the
argument.
"""
from __future__ import annotations

import re

# Canon section 5's own table, family by family, from its title lists.
FAMILIES: list[tuple[str, str]] = [
    ("country_regional_gm",
     r"\bgeneral manager\b|\bcountry manager\b|\bcountry lead\b|"
     r"\bmanaging director\b|\bregional gm\b|\bgm\b|\bmarket entry\b"),
    ("chief_commercial_strategy",
     r"\bchief commercial\b|\bcco\b|\bchief strategy officer\b|\bcso\b|"
     r"\bhead of commercial\b|\bcommercial strategy\b|\brevenue strategy\b|"
     r"\bhead of (?:gtm|go.to.market)\b|\bgtm strategy\b|"
     r"\bgo.to.market strategy\b|\bbusiness operations\b|"
     r"\bhead of enterprise gtm\b|\bsales strategy (?:and|&) operations\b|"
     r"\bgtm (?:operations|innovation)\b"),
    ("corp_dev_strategy",
     r"\bcorporate development\b|\bcorp dev\b|\bcorporate strategy\b|"
     r"\b[se]?vp,? (?:of )?strategy\b|\bhead of strategy\b|"
     r"\bdirector,? (?:of )?strategy\b|\bstrategy (?:and|&) corporate\b|"
     r"\bbusiness (?:and|&) corporate development\b|"
     r"\bstrategy (?:and|&) operations\b"),
    ("ai_chief_of_staff",
     r"\bchief of staff\b|\bhead of ai\b|\bai operations\b|\bgtm ai\b|"
     r"\bai transformation\b|\bai enablement\b"),
    # Canon 5's table lists four families and names partner architecture as a
    # capability rather than a title. Krish approved a partnerships letter
    # block in P1.5, which implies he would take the seat, so the gate admits
    # it and the canon proposal asks him to settle the contradiction.
    ("partnerships_alliances",
     r"\bpartnerships?\b|\balliances\b|\becosystem\b"),
]

# Canon 9.3's quota seat, and the functions canon section 5 gives no
# archetype to at all. A title that is one of these is not his shape however
# the rest of it reads.
NOT_HIS_SHAPE = re.compile(
    r"\b(enterprise sales director|sales director|director,? of sales|"
    r"account (?:director|executive|manager)|strategic account|"
    r"regional (?:vice president|vp|director)|territory|sales manager|"
    r"sales enablement|growth marketing|product marketing|field marketing|"
    r"demand generation|solutions? engineer|sales engineer|"
    r"product management|product manager|procurement|government affairs|"
    r"public policy|talent acquisition|recruit\w*|people operations|"
    r"human resources|controller|general counsel|legal|compliance|"
    r"customer success|post.sales|professional services|support engineer)\b",
    re.I)


def archetype(title: str) -> str | None:
    """The canon section 5 family this title is, or None for not his shape."""
    t = (title or "").lower()
    if NOT_HIS_SHAPE.search(t):
        return None
    for name, pattern in FAMILIES:
        if re.search(pattern, t):
            return name
    return None


def families() -> list[str]:
    return [name for name, _ in FAMILIES]
