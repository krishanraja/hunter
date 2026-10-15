"""The answers Krish gave in conversation, written into the workbook.

Krish supplied his demographics and consent posture on 2026-09-13 and 2026-09-14,
and ruled on the salary field on 2026-09-14. Until they are in the sheet, the
Application Info Bank is the authority and it says those cells are empty, so the
simulation correctly reported the demographic and consent fields as unanswerable.

This module exists rather than an ad hoc script because it writes to the sheet
that governs real applications. Every value is declared as data with the ruling
that produced it, the row numbers are resolved by matching the field name rather
than hard coded, and every cell is read back and asserted after writing.

It also corrects the two superseded master document pointers. Canon 9.9 lists CV
v11 and cover letter v1 as superseded and says in terms: do not let a superseded
ID linger as if it were current anywhere else. Those four cells are the lingering.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..sheet import SheetError
from .infobank import INFO_TAB, SENSITIVE, norm_label

LOCKED_MARK = "\U0001F7E2 LOCKED"
SENSITIVE_MARK = "\U0001F534 SENSITIVE"


@dataclass(frozen=True)
class Seed:
    field_name: str          # matched against column A, not a row number
    value: str
    status: str = ""         # blank leaves the existing status alone
    notes: str = ""
    ruling: str = ""         # why this value, for the operator reading the diff
    create_if_missing: bool = False
    private: bool = False    # his own answer: never printed (private_seeds)


# His Section G answers (race, gender, veteran and disability status) are not
# in this file: the repository is public. They are in the workbook, which is
# the authority, and a re-seed reads them from system_config PRIVATE_KEY, a
# JSON list of {"field_name", "value", "status", "ruling"}. Without it, those
# rows are left exactly as they are.
PRIVATE_KEY = "hunter_private_bank_seeds"


def private_seeds(cfg) -> tuple[list[Seed], str]:
    """(his private seeds, a note in counts only). Missing or malformed reads
    as none, and says which, never a value."""
    import json
    raw = (getattr(cfg, "raw", None) or {}).get(PRIVATE_KEY)
    if not raw:
        return [], f"system_config {PRIVATE_KEY} is not set; those rows are left as they are"
    try:
        items = json.loads(raw)
        out = [Seed(str(i["field_name"]), str(i["value"]),
                    str(i.get("status") or SENSITIVE_MARK), ruling=str(i.get("ruling") or ""),
                    private=True)
               for i in items]
    except (ValueError, TypeError, KeyError):
        return [], f"system_config {PRIVATE_KEY} could not be read; those rows are left as they are"
    return out, f"{len(out)} private seed(s) read from system_config"


def seeds() -> list[Seed]:
    """Every value, with the ruling that produced it."""
    return [
        # Section G's answers are his and private (private_seeds). The status
        # stays SENSITIVE, which since 2026-09-14 means "usable but shown in
        # every approval email", not "never filled".
        Seed('Default: select "prefer not to answer"?',
             "No, answer as recorded above", LOCKED_MARK,
             ruling="Krish 2026-09-14: answers are recorded, so no default needed"),
        # Row 37 forbade entering a salary at all, which made Fleek and Trulioo
        # impossible to complete since both make the field required.
        Seed("Salary expectations (form field)",
             "Enter the recorded floor (250000) ONLY where the form makes the "
             "field required; always flagged in the approval email. Optional "
             "salary fields stay blank.", LOCKED_MARK,
             ruling="Krish 2026-09-14, replacing DO NOT ENTER"),
        # Krish consents to the acknowledgement checkboxes. No row existed.
        Seed("Consent to recruiting privacy policies and acknowledgements",
             "Yes", SENSITIVE_MARK,
             notes="Consents to all. Still surfaced in every approval email "
                   "before anything is sent.",
             ruling="Krish 2026-09-14", create_if_missing=True),
        # Canon 9.9: superseded IDs must not linger as current.
        Seed("Cover letter template ID", config.LETTER_MASTER_ID, LOCKED_MARK,
             notes="v4, canon 9.9 current",
             ruling="canon 9.9: v1 18m4bOX... is superseded"),
        Seed("CV (v11) doc ID", config.CV_MASTER_ID, LOCKED_MARK,
             notes="v14, canon 9.9 current",
             ruling="canon 9.9: v11 1ZqI8LP... is superseded"),
    ]


@dataclass
class Change:
    row: int
    field_name: str
    column: str
    before: str
    after: str
    ruling: str
    private: bool = False

    @property
    def is_new(self) -> bool:
        return self.before == "" and self.column == "A"


def plan_changes(rows: list[list], seed_list: list[Seed] | None = None
                 ) -> tuple[list[Change], list[str]]:
    """(changes, problems). Pure: takes the tab's values, returns what to write.

    Row numbers come from matching column A, so a row inserted by hand above does
    not make this write the wrong cell.
    """
    seed_list = seed_list or seeds()
    index: dict[str, int] = {}
    for i, raw in enumerate(rows, 1):
        name = (raw[0] if raw else "").strip()
        if name:
            index.setdefault(norm_label(name), i)
    last_row = len(rows)

    changes: list[Change] = []
    problems: list[str] = []
    next_new = last_row + 2  # leave a blank line after the existing block
    for s in seed_list:
        key = norm_label(s.field_name)
        row_num = index.get(key)
        if row_num is None:
            if not s.create_if_missing:
                problems.append(f"{s.field_name!r} is not on the tab and this "
                                f"seed does not create rows")
                continue
            row_num = next_new
            next_new += 1
            changes.append(Change(row_num, s.field_name, "A", "", s.field_name,
                                  s.ruling, s.private))
            before_val, before_status = "", ""
        else:
            raw = (rows[row_num - 1] + ["", "", "", ""])[:4]
            before_val, before_status = raw[1].strip(), raw[2].strip()
        if before_val.strip() != s.value.strip():
            changes.append(Change(row_num, s.field_name, "B", before_val,
                                  s.value, s.ruling, s.private))
        if s.status and before_status != s.status:
            changes.append(Change(row_num, s.field_name, "C", before_status,
                                  s.status, s.ruling, s.private))
        if s.notes:
            changes.append(Change(row_num, s.field_name, "D", "", s.notes,
                                  s.ruling, s.private))
    return changes, problems


def apply_changes(sheet, changes: list[Change]) -> int:
    """Write, then read every cell back and assert. Raises on any mismatch."""
    if not changes:
        return 0
    blocks = [(f"'{INFO_TAB}'!{c.column}{c.row}", [[c.after]]) for c in changes]
    sheet._write(blocks)
    for c in changes:
        rng = f"'{INFO_TAB}'!{c.column}{c.row}"
        got = sheet.read_tab_formulas(rng)
        have = (got[0][0] if got and got[0] else "")
        if str(have).strip() != str(c.after).strip():
            raise SheetError(
                f"read-back of {rng} did not match"
                + ("" if c.private else f": gave {have!r}, expected {c.after!r}")
                + "; stopping so the rest is not written blind")
    return len(changes)
