"""The Target Companies tab, read as policy rather than left as a note.

The workbook has carried this tab all along: 53 companies in five
categories, each with a tier, and underneath them a TIER LEGEND in his own
words that is plainly a sourcing instruction.

    Tier 1  Top priority. Active sweep + Apify search + headhunter mention.
    Tier 2  Active sweep, lower frequency. Mention to relevant headhunter.
    Tier 3  Reference list. Only sweep if we hear about specific role.

No code read any of it. Canon 9.1 held a separate copy of the same list that
had already drifted by three names, and the careers URL sitting in column G
for 50 of the 53 companies, which resolves a job board without guessing,
went unused while hunter guessed slugs.

Two rules about who owns what, because this is his tab:

- Columns A to H are his. Nothing here writes to them, ever.
- Hunter writes its own columns to the right of his, starting at HUNTER_COL,
  and reads them back to check the write landed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import Config
from .sheet import Sheet, SheetError, col_letter
from .sources import slugify

TAB = "Target Companies"
# His columns, positional because the tab has a title row rather than a
# header row. Row 1 is the title, row 2 says "Company", the list starts at 3.
FIRST_ROW = 3
COMPANY, CATEGORY, TIER, STAGE, LOCATION, WHY, CAREERS, NOTE = range(8)
HIS_WIDTH = 8
# The legend under the list. Reading past it would turn "Tier 1" into a
# company called Tier 1.
SENTINEL = "TIER LEGEND"

# What hunter owns, immediately to the right of his eight columns.
HUNTER_COL = HIS_WIDTH                      # zero based, so column I
HUNTER_HEADERS = ["Score", "Computed Tier", "Board", "Last Swept",
                  "Roles Seen", "Yes", "No", "Evidence"]

# The proposal block sits below his list with a gap and a heading, so a
# company hunter suggests can never be mistaken for one he chose.
PROPOSED_HEADING = "PROPOSED BY HUNTER (move a row up into the list above to adopt it)"
MAX_PROPOSED = 15


@dataclass
class TargetCompany:
    name: str
    category: str
    tier: str
    stage: str
    location: str
    why: str
    careers_url: str
    note: str
    row: int

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def tier_number(self) -> int | None:
        t = "".join(ch for ch in self.tier if ch.isdigit())
        return int(t) if t else None


def read(sheet: Sheet) -> list[TargetCompany]:
    """His list, stopping where his list stops."""
    rows = sheet.read_tab_values(f"{TAB}!A1:H200")
    out: list[TargetCompany] = []
    for i, r in enumerate(rows[FIRST_ROW - 1:], start=FIRST_ROW):
        name = (r[0] if r else "").strip() if r else ""
        if not name:
            break
        if name.upper().startswith(SENTINEL) or name.upper().startswith("PROPOSED"):
            break
        g = lambda n: ((r[n] if len(r) > n else "") or "").strip()
        out.append(TargetCompany(
            name=name, category=g(CATEGORY), tier=g(TIER), stage=g(STAGE),
            location=g(LOCATION), why=g(WHY), careers_url=g(CAREERS),
            note=g(NOTE), row=i))
    return out


def universe(sheet: Sheet) -> list[str]:
    """The company names, which is what sourcing sweeps."""
    return [t.name for t in read(sheet)]


def careers_urls(sheet: Sheet) -> dict[str, str]:
    """slug -> careers page, for board discovery that is an answer rather
    than a guess."""
    return {t.slug: t.careers_url for t in read(sheet) if t.careers_url}


def stated_tiers(sheet: Sheet) -> dict[str, int]:
    return {t.slug: t.tier_number for t in read(sheet) if t.tier_number}


def write_hunter_columns(sheet: Sheet, rows: dict[int, list],
                         *, apply: bool = True) -> list[str]:
    """Write hunter's own columns beside his, and read them back.

    rows maps a sheet row number to the HUNTER_HEADERS values for it. The
    read back is not ceremony: a Sheets write can answer 200 and land in the
    wrong range if the grid moved under it, and a score written into his
    "Why" column would be worse than no score at all.
    """
    if not rows:
        return []
    first = col_letter(HUNTER_COL)
    last = col_letter(HUNTER_COL + len(HUNTER_HEADERS) - 1)
    blocks = [(f"{TAB}!{first}2:{last}2", [HUNTER_HEADERS])]
    for row, values in sorted(rows.items()):
        padded = (list(values) + [""] * len(HUNTER_HEADERS))[:len(HUNTER_HEADERS)]
        blocks.append((f"{TAB}!{first}{row}:{last}{row}", [padded]))
    if not apply:
        return [f"would write {len(rows)} row(s) to {TAB}!{first}:{last}"]
    # RAW, because Last Swept is an ISO date and USER_ENTERED turns one into
    # the serial number 46280 in a cell with no date format.
    sheet._write(blocks, raw=True)
    lo = min(rows)
    hi = max(rows)
    back = sheet.read_tab_values(f"{TAB}!{first}{lo}:{last}{hi}")
    def same(a, b) -> bool:
        """Sheets stores 10.0 as 10 and 0.0 as 0, so a read back is compared
        as a number when both sides are numbers and as text otherwise."""
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return str(a).strip() == str(b).strip()

    problems = []
    for row, values in sorted(rows.items()):
        got = back[row - lo] if row - lo < len(back) else []
        want = (list(values) + [""] * len(HUNTER_HEADERS))[:len(HUNTER_HEADERS)]
        got = (list(got) + [""] * len(HUNTER_HEADERS))[:len(HUNTER_HEADERS)]
        if not all(same(g, w) for g, w in zip(got[:2], want[:2])):
            problems.append(f"row {row}: wrote {want[:2]} and read back {got[:2]}")
    if problems:
        raise SheetError("the Target Companies write did not land: "
                         + "; ".join(problems[:5]))
    return [f"wrote hunter columns for {len(rows)} company row(s)"]


def write_proposals(sheet: Sheet, proposals: list[tuple[str, float, str, str]],
                    *, after_row: int, apply: bool = True) -> list[str]:
    """Companies hunter found that he has not named, below his list.

    Each is (name, score, category, evidence line with its source). Capped,
    because a proposal list he does not read is worth less than none: it
    becomes another tab competing for the attention the Pipeline needs.
    """
    proposals = proposals[:MAX_PROPOSED]
    start = after_row + 2
    values = [[PROPOSED_HEADING]]
    values += [[name, category, "", "", "", evidence, "", "", score]
               for name, score, category, evidence in proposals]
    # Clear more rows than are written, so a shorter list this week does not
    # leave last week's tail behind pretending to be current.
    blank = [[""] * 9 for _ in range(MAX_PROPOSED + 2 - len(values))]
    rng = f"{TAB}!A{start}:I{start + len(values) + len(blank) - 1}"
    if not apply:
        return [f"would write {len(proposals)} proposal(s) at {rng}"]
    sheet._write([(rng, values + blank)], raw=True)
    back = sheet.read_tab_values(f"{TAB}!A{start}:A{start}")
    got = (back[0][0] if back and back[0] else "")
    if not got.startswith("PROPOSED"):
        raise SheetError(
            f"the proposal heading did not land at {TAB}!A{start}; read back "
            f"{got!r}")
    return [f"proposed {len(proposals)} new company(ies) on the {TAB} tab"]


def today() -> str:
    return date.today().isoformat()


def col_index_of(ref: str) -> int:
    """Zero based column index from an A1 reference such as "I3"."""
    letters = "".join(c for c in ref if c.isalpha()).upper()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1
