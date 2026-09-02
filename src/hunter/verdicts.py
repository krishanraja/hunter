"""The column A vocabulary: one dropdown that carries both the verdict and,
when Krish declines, the reason.

Krish only ever picks Applied or Declined, so a separate reason column would
mean scrolling 28 columns to reach it, and inserting one at B would shift
every index in sheet.py and canon 9.13's fixed 28-name order. The dropdown
carries both instead: one click, no scroll, no schema change.

The three SYSTEM codes are not taste. They are hunter failing: a dead
posting is a liveness gate miss, already-applied and duplicate are dedupe
misses. They tighten a gate and become a regression test. The taste codes
only ever raise a proposal for Krish to approve.
"""
from __future__ import annotations

import re

BUILD = "Yes"
APPLIED = "Applied"
DECLINE_PREFIX = "Declined - "

# code -> the dropdown label Krish sees
TASTE_CODES = {
    "domain_expertise": "domain expertise",
    "function_wrong": "function wrong",
    "business_uninteresting": "business uninteresting",
    "seniority_below": "seniority below",
    "seniority_above": "seniority above",
    "requirements_mismatch": "requirements mismatch",
    "geo_language": "geo or language",
    "comp_below": "comp below bar",
    "stage_wrong": "stage wrong",
    "travel": "too much travel",
}
SYSTEM_CODES = {
    "dead_posting": "dead posting",
    "already_applied": "already applied",
    "duplicate_row": "duplicate row",
}
ALL_CODES = {**TASTE_CODES, **SYSTEM_CODES}
LABEL_TO_CODE = {v: k for k, v in ALL_CODES.items()}

# which gate each system code says hunter should have caught
SYSTEM_CODE_GATE = {
    "dead_posting": "G1",
    "already_applied": "dedupe",
    "duplicate_row": "dedupe",
}


def dropdown_values() -> list[str]:
    """Exactly what column A offers, in the order Krish reads it."""
    return (["New", BUILD, APPLIED]
            + [f"{DECLINE_PREFIX}{ALL_CODES[c]}" for c in TASTE_CODES]
            + [f"{DECLINE_PREFIX}{ALL_CODES[c]}" for c in SYSTEM_CODES])


def parse(text: str) -> tuple[str, str | None]:
    """(verdict, reason_code). verdict is go | applied | rejection | none.

    Free text still works: anything unrecognised is a rejection in Krish's
    own words, quoted verbatim downstream and carrying no code.
    """
    v = (text or "").strip()
    low = v.lower()
    if not low or low == "new":
        return "none", None
    if low in ("yes", "y", "go", "build"):
        return "go", None
    # "Already applied to MongoDB above" is not an application to THIS role.
    # He is telling hunter it showed him the same target twice, so it is a
    # rejection carrying a system code, and the loop treats it as a miss.
    if low.startswith("already applied"):
        return "rejection", "already_applied"
    if low.startswith("applied"):
        return "applied", None
    if low.startswith("awaiting"):
        return "none", None
    if low.startswith("declined"):
        tail = re.sub(r"^declined\s*[-:,]?\s*", "", low).strip()
        return "rejection", LABEL_TO_CODE.get(tail)
    return "rejection", None


def is_system_code(code: str | None) -> bool:
    return code in SYSTEM_CODES
