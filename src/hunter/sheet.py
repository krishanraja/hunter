"""Pipeline sheet layer. Canon 9.13 is the contract:

- Row 1 is the header (asserted against canon's exact names, in order), row 2
  is intentionally blank, data starts at row 3.
- Column positions come from ONE map, COLS, built from HEADERS. Every range
  string in this module is derived from it, so a column move is a change to
  HEADERS and canon 9.13, never a hunt for magic numbers.
- The link columns (Job Link, CV Doc, Cover Letter Doc, CV PDF, CL PDF) hold
  =HYPERLINK("url","label") formulas. Reads use valueRenderOption=FORMULA; a
  rendered read loses every URL.
- Writes never use values.append with INSERT_ROWS (rows created that way
  inherit nothing). The writer finds the last populated row and batchUpdates
  an explicit range with USER_ENTERED, validates every cell before writing,
  and reads the range back to assert what landed.
- Column A belongs to Krish. It is read, never overwritten, except for the
  three hunter-owned system codes written through set_verdicts. Package
  writes touch the four link columns, Package Status and Materials Built;
  warm path writes touch Warm Path and Path Evidence; sorting moves whole
  rows so column A travels with its row.
- No cell hunter owns is left blank; fill defaults are canon 9.13's,
  verbatim. Krish Score and Krish Reaction (HIS_COLS) are his: hunter never
  writes into them and never gives them a default. The only writes that
  carry them are the ones that move his whole row (to Applied, and back by
  restore), and those read his two cells back before anything is deleted.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Callable

import requests

from . import config

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
TAB = "Pipeline"
DATA_START_ROW = 3  # 1-based; row 2 is intentionally blank

# The two columns hunter writes its own judgement into. Named, because until
# 2026-10-11 they were "Score" and "Why It Fits", and a name typed out at forty
# call sites is a name that gets missed at one of them.
AI_SCORE = "AI Score"
AI_WHY = "Why AI thinks it Fits"
# Krish inserted these on 2026-10-11, between hunter's score and hunter's
# argument: his score out of 10 and his reaction in his own words. They are
# his. Hunter writes into neither, defaults neither, and a blank in either is
# an answer rather than a hole to fill.
KRISH_SCORE = "Krish Score"
KRISH_REACTION = "Krish Reaction"
HIS_COLS = frozenset({KRISH_SCORE, KRISH_REACTION})

# Canon 9.13, exact, in order. run.assert_canon_alignment refuses to run
# when canon says otherwise, so this list and the canon body cannot drift
# apart silently.
HEADERS = [
    "Verdict", "Business", "Role", "JD Snippet", "Job Link", "CV Doc",
    "Cover Letter Doc", "CV PDF", "CL PDF", AI_SCORE, KRISH_SCORE, AI_WHY,
    KRISH_REACTION, "Sector", "Stage", "Location", "Comp", "Warm Path",
    "Path Evidence", "Package Status", "Source", "Application Status",
    "Applied Date", "Next Action", "Application Format", "Attachment Style",
    "Additional Questions", "Form Complexity", "Autonomy Score",
    "Form Audit Date", "JD URL Verified", "Materials Built",
]
# The 30-column layout Pipeline carried until he inserted his two columns, and
# the Applied tab still carries until ensure_archive_columns moves it. Read
# only by that method and by read_archive, which reads either layout.
PREVIOUS_HEADERS = [
    "Verdict", "Business", "Role", "JD Snippet", "Job Link", "CV Doc",
    "Cover Letter Doc", "CV PDF", "CL PDF", "Score", "Why It Fits", "Sector",
    "Stage", "Location", "Comp", "Warm Path", "Path Evidence", "Package Status",
    "Source", "Application Status", "Applied Date", "Next Action",
    "Application Format", "Attachment Style", "Additional Questions",
    "Form Complexity", "Autonomy Score", "Form Audit Date", "JD URL Verified",
    "Materials Built",
]
PREVIOUS_NAMES = {"Score": AI_SCORE, "Why It Fits": AI_WHY}
N_COLS = len(HEADERS)
COLS = {name: i for i, name in enumerate(HEADERS)}
HIS_IDX = frozenset(COLS[n] for n in HIS_COLS)
ARCHIVE_TRAILING = ("Archived On",)
ARCHIVE_WIDTH = N_COLS + len(ARCHIVE_TRAILING)
# How far right a header read looks. Reading only to the last column hunter
# knows makes a column he adds after it invisible: the guard compared the
# first thirty cells, saw them agree, and the sort would then have moved every
# row and left his new column where it was.
SCAN_WIDTH = 52

# canon 9.13 fill defaults, by name
DEFAULTS_BY_NAME = {
    "Verdict": "New", "JD Snippet": "Not captured", "CV Doc": "Not built",
    "Cover Letter Doc": "Not built", "CV PDF": "Not built", "CL PDF": "Not built",
    AI_WHY: "Not assessed", "Sector": "Not stated", "Stage": "Not stated",
    "Location": "Not stated", "Comp": "Not disclosed", "Warm Path": "None found",
    "Path Evidence": "n/a", "Package Status": "Not started", "Source": "Unknown",
    "Application Status": "Not applied", "Applied Date": "n/a",
    "Next Action": "Review and score", "Application Format": "Unknown",
    "Attachment Style": "CV+CL", "Additional Questions": "standard",
    "Form Complexity": "Unknown", "Autonomy Score": "Unknown",
    "Form Audit Date": "n/a", "Materials Built": "n/a",
}
DEFAULTS = {COLS[k]: v for k, v in DEFAULTS_BY_NAME.items()}
DEFAULT_CELLS = [DEFAULTS_BY_NAME.get(h, "") for h in HEADERS]
DATE_COLS = {COLS["Applied Date"], COLS["Form Audit Date"], COLS["Materials Built"]}
LINK_COLS = {COLS[n] for n in ("Job Link", "CV Doc", "Cover Letter Doc", "CV PDF", "CL PDF")}

# Package Status vocabulary. A new value needs a canon 9.13 amendment.
PKG_BUILT_DIRECT = "Materials staged - ready to apply"
PKG_BUILT_BRIDGE = "Materials staged - bridge first"
PKG_BUILT_UNVERIFIED = "Materials staged - liveness unverified"
PKG_DEAD = "Posting dead, cannot build"
PACKAGE_STATUSES = {PKG_BUILT_DIRECT, PKG_BUILT_BRIDGE, PKG_BUILT_UNVERIFIED, PKG_DEAD}

# What the Application Status cell reads once hunter has actually sent one. The
# default in the row template is "Not applied"; nothing ever wrote the other value.
APPLIED_STATUS = "Applied"
BUILT_STATUSES = {PKG_BUILT_DIRECT, PKG_BUILT_BRIDGE, PKG_BUILT_UNVERIFIED}

WARM_NONE = DEFAULTS_BY_NAME["Warm Path"]
EVIDENCE_NONE = DEFAULTS_BY_NAME["Path Evidence"]
EVIDENCE_MAX = 500

HYPERLINK_RE = re.compile(r'^=HYPERLINK\("((?:[^"\\]|\\.)+)"\s*[,;]\s*"((?:[^"\\]|\\.)*)"\)$')
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SheetError(RuntimeError):
    pass


class HeaderChanged(SheetError):
    """Pipeline's row 1 is not the header hunter writes against.

    On 2026-10-11 Krish inserted two columns and renamed two, and the guard
    held: nothing was written over his cells. But it said so as a list of
    tuples ending "run migrate-columns", a command that no longer existed, in
    a log nobody reads, for 56 hourly runs. This says which columns moved, in
    words, and the watchdog mails the same words (alerts.trouble_checks).
    """

    def __init__(self, diffs: list[tuple[str, str, str]], shift: str = ""):
        self.diffs = diffs
        super().__init__(describe_header_change(diffs, shift=shift))


# ---------- the column map ----------

def col_letter(idx: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, 29 -> AD."""
    if idx < 0:
        raise SheetError(f"column index {idx} is negative")
    out = ""
    n = idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def col_index(letters: str) -> int:
    """A -> 0, AD -> 29. The inverse of col_letter."""
    n = 0
    for ch in letters.strip().upper():
        if not "A" <= ch <= "Z":
            raise SheetError(f"bad column letters {letters!r}")
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def col(name: str) -> int:
    try:
        return COLS[name]
    except KeyError:
        raise SheetError(f"unknown column {name!r}; HEADERS is the map") from None


def letter(name: str) -> str:
    return col_letter(col(name))


def cell_range(first: str, row: int, last: str | None = None,
               row_end: int | None = None, tab: str = TAB) -> str:
    """Pipeline!F3:I3 from names, so a column move never edits a caller."""
    a = f"{letter(first)}{row}"
    if last is None and row_end is None:
        return f"{tab}!{a}"
    b = f"{letter(last or first)}{row_end or row}"
    return f"{tab}!{a}:{b}"


LAST_COL = col_letter(N_COLS - 1)
FULL_RANGE = f"{TAB}!A1:{LAST_COL}"
ARCHIVE_LAST_COL = col_letter(ARCHIVE_WIDTH - 1)
SCAN_LAST_COL = col_letter(SCAN_WIDTH - 1)
HEADER_SCAN = f"{TAB}!A1:{SCAN_LAST_COL}1"


def header_diffs(found: list, expected: list[str]) -> list[tuple[str, str, str]]:
    """(column letter, what row 1 reads, what hunter expects) for every
    column that disagrees, including anything written after the last
    column hunter knows."""
    got = [str(x if x is not None else "") for x in found]
    width = max(len(got), len(expected))
    got += [""] * (width - len(got))
    want = list(expected) + [""] * (width - len(expected))
    return [(col_letter(i), got[i], want[i]) for i in range(width)
            if got[i] != want[i] and (got[i].strip() or want[i].strip())]


def header_shift(found: list, expected: list[str]) -> str:
    """One phrase when row 1 is `expected` with one or two columns inserted
    or removed inside it and nothing else changed, else "". One column
    inserted at F read as two dozen renames, F to AF, and never said
    "inserted". A column added after the last one is left to the cell list,
    which already says it is past the end."""
    got = [str(x if x is not None else "") for x in found]
    while got and not got[-1].strip():
        got.pop()
    want = list(expected)
    if len(got) == len(want):
        return ""
    longer, shorter = (got, want) if len(got) > len(want) else (want, got)
    extra, i, j = [], 0, 0
    while i < len(longer):
        if j < len(shorter) and longer[i] == shorter[j]:
            j += 1
        else:
            extra.append((i, longer[i]))
        i += 1
    if j != len(shorter) or not 1 <= len(extra) <= 2:
        return ""
    if longer is got:
        if all(i >= len(want) for i, _ in extra):
            return ""
        names = " and ".join(f"{name!r} at {col_letter(i)}" if name.strip()
                             else f"a blank column at {col_letter(i)}"
                             for i, name in extra)
        return f"a column was inserted: {names}" if len(extra) == 1 else \
            f"columns were inserted: {names}"
    names = " and ".join(f"{name!r}, which was {col_letter(i)}" for i, name in extra)
    return f"a column was removed: {names}" if len(extra) == 1 else \
        f"columns were removed: {names}"


PIPELINE_STOPPED = ("Hunter is not writing to the sheet until the code and canon "
                    "9.13 agree with the header again: undo the change, or ask "
                    "Claude to adopt it.")
APPLIED_STOPPED = ("Hunter is not moving decided rows to Applied, and the next "
                   "batch cannot read it, until its row 1 is a layout hunter "
                   "knows again: undo the change, or ask Claude to adopt it.")


def describe_header_change(diffs: list[tuple[str, str, str]], *, limit: int = 6,
                           lead: str = "Pipeline columns changed: ", shift: str = "",
                           consequence: str = PIPELINE_STOPPED) -> str:
    """The diff in words he can act on without reading code, ending with what
    is stopped and what he can do. `shift` (header_shift) replaces the cell
    list when the change was an insert or a removal."""
    if shift:
        return f"{lead}{shift}. {consequence}"
    parts = []
    for letter_, got, want in diffs[:limit]:
        if not want:
            parts.append(f"{letter_} reads {got!r}, a column past the last "
                         f"one hunter knows")
        elif not got:
            parts.append(f"{letter_} is empty where hunter expects {want!r}")
        else:
            parts.append(f"{letter_} reads {got!r} where hunter expects {want!r}")
    more = f", and {len(diffs) - limit} more" if len(diffs) > limit else ""
    return lead + "; ".join(parts) + more + ". " + consequence


def archive_layout(grid: list[list]) -> str:
    """Which layout the Applied tab is on, from its own rows: "empty" when it
    holds nothing, "current" (HEADERS plus Archived On), "previous"
    (PREVIOUS_HEADERS plus Archived On), or "" for neither, which every
    reader and writer of Applied refuses. One judgement, so the watchdog and
    the doctor see the header exactly as the writers do."""
    if not grid:
        return "empty"
    header = pad_row(grid[0], SCAN_WIDTH)
    for state, names in (("current", HEADERS), ("previous", PREVIOUS_HEADERS)):
        want = list(names) + list(ARCHIVE_TRAILING)
        if header[:len(want)] == want and not any(x.strip() for x in header[len(want):]):
            return state
    return ""


def archive_header_diffs(grid: list[list]) -> list[tuple[str, str, str]]:
    """Applied's row 1 against whichever known layout it is nearer."""
    header = pad_row(grid[0] if grid else [], SCAN_WIDTH)
    return min((header_diffs(header, list(names) + list(ARCHIVE_TRAILING))
                for names in (HEADERS, PREVIOUS_HEADERS)), key=len)


def his_insert_indexes() -> list[int]:
    """Where his two columns go into a tab still on PREVIOUS_HEADERS, in
    ascending order, so each index is right at the moment it is inserted.
    Derived and checked rather than typed."""
    at = sorted(COLS[n] for n in HIS_COLS)
    grown = [PREVIOUS_NAMES.get(h, h) for h in PREVIOUS_HEADERS]
    for i in at:
        grown.insert(i, "")
    if grown != ["" if h in HIS_COLS else h for h in HEADERS]:
        raise SheetError("HEADERS is not PREVIOUS_HEADERS with his two columns "
                         "inserted; fix the map first")
    return at


@dataclass
class SheetRow:
    row_number: int            # 1-based sheet row
    cells: list[str]           # N_COLS raw values, formulas as text
    verdict: str
    company: str
    role: str
    jd_url: str | None
    package_urls: dict[str, str | None] = field(default_factory=dict)
    archived: bool = False   # lives on the Applied tab, decided, read only
    # The Applied tab's stamp, kept off `cells` so cells stays N_COLS wide
    # for every caller. check_archive_stamped read cells[30] of a row padded
    # to 30, so it reported all 369 dated rows as undated.
    archived_on: str = ""

    def cell(self, name: str) -> str:
        return self.cells[col(name)]


def hyperlink(url: str, label: str) -> str:
    if "http" not in url:
        raise SheetError(f"refusing to build a HYPERLINK without a URL: {url!r}")
    escaped = url.replace('"', '""')
    label = (label or "").replace('"', "'")
    return f'=HYPERLINK("{escaped}","{label}")'


def parse_hyperlink(cell: str) -> tuple[str, str] | None:
    m = HYPERLINK_RE.match(cell.strip()) if cell else None
    return (m.group(1).replace('""', '"'), m.group(2)) if m else None


def pad_row(row: list, width: int = N_COLS) -> list[str]:
    """Normalize an API row to `width` strings. USER_ENTERED turns TRUE/FALSE
    text into booleans, which read back as JSON booleans; restore sheet
    casing."""
    cells = []
    for v in row:
        if v is None:
            cells.append("")
        elif isinstance(v, bool):
            cells.append("TRUE" if v else "FALSE")
        else:
            cells.append(str(v))
    return (cells + [""] * width)[:width]


def plain_text(text: str) -> str:
    """Text with the dashes Krish does not use.

    The no-em-dash rule covers everything hunter writes, and a JD excerpt it
    copies onto the sheet is something hunter wrote there. A posting using an
    em dash used to fail the row guard and abort the whole append, losing
    every staged role in the batch.
    """
    return (text or "").replace("\u2014", " - ").replace("\u2013", " - ")


# What Sheets turns into something else under USER_ENTERED: a formula, a
# number, money, a percentage, a date. "$225,000" in Comp landed as the number
# 225000 on 2026-10-03, and the append's read-back refused the batch. Such a
# value is written with a leading apostrophe, which Sheets keeps as text and
# does not show; row_diff compares without it.
_COERCED = re.compile(
    r"^\s*([=+@]|-\S|\$?\(?-?[\d,]+(\.\d+)?\)?%?\s*$|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}/\d{2,4}\s*$)")


def as_text(value: str) -> str:
    """The value, kept as text when Sheets would parse it."""
    v = value or ""
    return "'" + v if v and _COERCED.match(v) else v


def make_row(*, company: str, role: str, jd_url: str, score: int,
             why_it_fits: str = "", sector: str = "", stage: str = "",
             location: str = "", comp: str = "", source: str = "",
             jd_verified: bool = True, jd_snippet: str = "",
             warm_path: str = "", path_evidence: str = "") -> list[str]:
    cells = list(DEFAULT_CELLS)
    cells[COLS["Business"]] = as_text(plain_text(company))
    cells[COLS["Role"]] = as_text(plain_text(role))
    cells[COLS["Job Link"]] = hyperlink(jd_url, "JD")
    cells[COLS[AI_SCORE]] = str(score)
    if why_it_fits.strip():
        cells[COLS[AI_WHY]] = plain_text(why_it_fits).strip()
    for name, value in (("Sector", sector), ("Stage", stage), ("Location", location),
                        ("Comp", comp), ("Source", source)):
        if value.strip():
            cells[COLS[name]] = as_text(plain_text(value).strip())
    cells[COLS["JD URL Verified"]] = "TRUE" if jd_verified else "FALSE"
    if jd_snippet.strip():
        cells[COLS["JD Snippet"]] = plain_text(jd_snippet).strip()[:500]
    if warm_path.strip():
        cells[COLS["Warm Path"]] = plain_text(warm_path).strip()
    if path_evidence.strip():
        cells[COLS["Path Evidence"]] = plain_text(path_evidence).strip()[:EVIDENCE_MAX]
    return cells


def validate_row(cells: list[str], *, is_append: bool = True) -> list[str]:
    fails: list[str] = []
    if len(cells) != N_COLS:
        return [f"expected {N_COLS} cells, got {len(cells)}"]
    for i, c in enumerate(cells):
        if i in HIS_IDX:
            continue   # his: blank means he has not ruled, and it stays blank
        if not str(c).strip():
            fails.append(f"blank cell at column {col_letter(i)} ({HEADERS[i]})")
    if is_append and cells[COLS["Verdict"]] != "New":
        fails.append("column A must be the literal New on appends")
    parsed = parse_hyperlink(cells[COLS["Job Link"]])
    if not parsed or not parsed[0].startswith("http"):
        fails.append(f'column {letter("Job Link")} must be =HYPERLINK("http...","JD")')
    try:
        score = int(cells[COLS[AI_SCORE]])
        if not 1 <= score <= 10:
            fails.append(f"column {letter(AI_SCORE)} score out of 1..10")
    except (ValueError, TypeError):
        fails.append(f"column {letter(AI_SCORE)} score is not an integer")
    for i in DATE_COLS:
        if cells[i] != "n/a" and not DATE_RE.match(cells[i]):
            fails.append(f"column {col_letter(i)} must be n/a or YYYY-MM-DD, got {cells[i]!r}")
    if cells[COLS["JD URL Verified"]] not in ("TRUE", "FALSE"):
        fails.append(f"column {letter('JD URL Verified')} must be TRUE or FALSE in that casing")
    for i, c in enumerate(cells):
        if i in HIS_IDX:
            continue   # his words, with whatever dash he typed
        if "\u2014" in str(c):
            fails.append(f"em dash in column {col_letter(i)}")
    return fails


def row_diff(expected: list[str], actual: list[str]) -> list[tuple[str, str, str]]:
    """(column name, written, read back) for every cell that disagrees.

    rows_equal answered only true or false, so a failed append said
    "read-back mismatch on Pipeline!A39:AD78" and nothing else: not which of
    the forty rows, not which of the thirty columns, not what the two values
    were. That is unactionable, and it aborted a run that had just spent half
    an hour sweeping.
    """
    actual = pad_row(actual, len(expected))
    out = []
    for i, (e, a) in enumerate(zip(expected, actual)):
        pe, pa = parse_hyperlink(e), parse_hyperlink(a)
        e_text = str(e)[1:] if str(e).startswith("'") else str(e)
        same = (pe == pa) if (pe or pa) else (e_text.strip() == str(a).strip())
        if not same:
            name = HEADERS[i] if i < len(HEADERS) else f"column {i}"
            out.append((name, str(e)[:120], str(a)[:120]))
    return out


def rows_equal(expected: list[str], actual: list[str]) -> bool:
    """Structural comparison tolerant of Sheets normalization: HYPERLINKs
    compare by (url, label); everything else compares as trimmed text."""
    return not row_diff(expected, actual)


class Sheet:
    def __init__(self, token: str | Callable[[], str],
                 workbook_id: str = config.WORKBOOK_ID,
                 sheet_id: int = config.PIPELINE_SHEET_ID):
        self._token = token
        self.workbook_id = workbook_id
        self.sheet_id = sheet_id

    @property
    def h(self) -> dict[str, str]:
        tok = self._token() if callable(self._token) else self._token
        return {"Authorization": "Bearer " + tok}

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(f"{SHEETS}/{self.workbook_id}{path}", headers=self.h,
                         params=params or {}, timeout=60)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(f"{SHEETS}/{self.workbook_id}{path}", headers=self.h,
                          json=body, timeout=60)
        r.raise_for_status()
        return r.json()

    def _values(self, rng: str, *, formulas: bool = True) -> list[list]:
        params = {"valueRenderOption": "FORMULA"} if formulas else {}
        return self._get(f"/values/{rng}", params).get("values", [])

    def _write(self, blocks: list[tuple[str, list[list]]], *, raw: bool = False) -> None:
        """USER_ENTERED by default, because most writes here are HYPERLINK formulas.

        raw=True for a value that must survive as the text it is. Sheets parses
        "2026-09-15" under USER_ENTERED into a date value, and a cell with no date
        format then displays the serial number: the first application hunter ever
        sent recorded its Applied Date as 46280.
        """
        self._post("/values:batchUpdate", {
            "valueInputOption": "RAW" if raw else "USER_ENTERED",
            "data": [{"range": rng, "values": values} for rng, values in blocks]})

    # ---------- reading ----------

    def read_grid(self) -> list[list[str]]:
        return [pad_row(r) for r in self._values(FULL_RANGE)]

    def _column_a(self, tab: str = TAB) -> list[list]:
        return self._values(f"{tab}!A1:A2000", formulas=False)

    def _his_cells(self, tab: str = TAB) -> list[dict[str, str]]:
        """Krish Score and Krish Reaction for every row, rendered, by name."""
        lo, hi = min(HIS_IDX), max(HIS_IDX)
        raw = self._values(f"{tab}!{col_letter(lo)}1:{col_letter(hi)}2000",
                           formulas=False)
        return [{n: pad_row(r, hi - lo + 1)[COLS[n] - lo] for n in HIS_COLS}
                for r in raw]

    def delete_rows(self, row_numbers: list[int], *,
                    expect_verdict: str | None = "New",
                    named: set[int] | frozenset[int] = frozenset(),
                    his_copied: bool = False) -> int:
        """Remove data rows, refusing any row Krish has written on.

        Deleting is the one destructive thing this module does, so it re-reads
        column A immediately before the write and aborts the whole batch if any
        target no longer reads exactly `expect_verdict`. Rows go in descending
        order so earlier indices stay valid.

        `named` exempts specific row numbers from that check, and nothing else
        about it changes. It exists for the one case the check cannot tell
        apart from the case it is for: on 2026-09-24 Krish asked for two rows
        off the sheet by job id, having already marked both Yes. Refusing a row
        HE named is not protecting his judgement, it is ignoring it. Passing
        expect_verdict=None instead would have disabled the check for the whole
        batch, including the duplicate and out-of-geography rows in the same
        call, which is exactly the blanket this guard exists to prevent.

        His score and his reaction are writing on the row too. Column A alone
        let prune-sheet plan his 9 on Notion for deletion as a "duplicate" of
        the twin he had marked Duplicate, because both read New. So K and M are
        re-read with column A, and a target with either filled is refused
        unless he named it. `his_copied` is for archive_rows alone, which has
        already written both cells to Applied and read them back.
        """
        targets = sorted({int(n) for n in row_numbers}, reverse=True)
        if not targets:
            return 0
        if min(targets) < DATA_START_ROW:
            raise SheetError(f"refusing to delete row {min(targets)}: rows 1 and 2 "
                             f"are the header and the intentional blank")
        grid = self._column_a()
        dirty = []
        exempt = {int(n) for n in named}
        if expect_verdict is not None:
            for n in targets:
                if n in exempt:
                    continue
                cell = (grid[n - 1][0] if len(grid) >= n and grid[n - 1] else "").strip()
                if cell != expect_verdict:
                    dirty.append((n, cell))
        if dirty:
            raise SheetError(f"refusing to delete: column A is no longer "
                             f"{expect_verdict!r} on {dirty[:5]}; re-run the plan")
        if not his_copied:
            marks = self._his_cells()
            his = []
            for n in targets:
                if n in exempt:
                    continue
                row = marks[n - 1] if len(marks) >= n else {}
                filled = [name for name in sorted(HIS_COLS)
                          if str(row.get(name, "")).strip()]
                if filled:
                    his.append((n, filled))
            if his:
                raise SheetError(f"refusing to delete: he has written in "
                                 f"{' or '.join(sorted(HIS_COLS))} on {his[:5]}; "
                                 f"nothing deleted")
        self._post(":batchUpdate", {"requests": [
            {"deleteDimension": {"range": {
                "sheetId": self.sheet_id, "dimension": "ROWS",
                "startIndex": n - 1, "endIndex": n}}}
            for n in targets]})
        after = self._column_a()
        if len(after) != len(grid) - len(targets) and len(after) > len(grid) - len(targets):
            raise SheetError(f"read-back mismatch after delete: expected at most "
                             f"{len(grid) - len(targets)} rows, found {len(after)}")
        return len(targets)

    def delete_blank_rows(self, row_numbers: list[int]) -> int:
        """Remove rows that are empty in Verdict, Business and Role. Re-read
        first: a row that has gained content since the plan is not blank."""
        targets = sorted({int(n) for n in row_numbers}, reverse=True)
        if not targets:
            return 0
        if min(targets) < DATA_START_ROW:
            raise SheetError("refusing to delete header rows")
        grid = [pad_row(r) for r in self._values(f"{TAB}!A1:C2000")]
        for n in targets:
            row = grid[n - 1] if n - 1 < len(grid) else ["", "", ""]
            if any(str(c).strip() for c in row[:3]):
                raise SheetError(f"row {n} is not blank ({row[:3]}); nothing deleted")
        self._post(":batchUpdate", {"requests": [
            {"deleteDimension": {"range": {
                "sheetId": self.sheet_id, "dimension": "ROWS",
                "startIndex": n - 1, "endIndex": n}}}
            for n in targets]})
        return len(targets)

    def set_verdict_dropdown(self, values: list[str]) -> int:
        """Replace column A's validation list. showCustomUi stays on and the
        rule stays non-strict, so Krish can still type something the list
        does not cover; parse() treats that as a rejection in his own words."""
        meta = self._get("", {"fields": "sheets(properties(sheetId,gridProperties(rowCount)))"})
        rows = next(sh["properties"]["gridProperties"]["rowCount"]
                    for sh in meta["sheets"]
                    if sh["properties"]["sheetId"] == self.sheet_id)
        self._post(":batchUpdate", {"requests": [{"setDataValidation": {
            "range": {"sheetId": self.sheet_id, "startRowIndex": DATA_START_ROW - 1,
                      "endRowIndex": rows, "startColumnIndex": 0, "endColumnIndex": 1},
            "rule": {
                "condition": {"type": "ONE_OF_LIST",
                              "values": [{"userEnteredValue": v} for v in values]},
                "inputMessage": "Pick one. Declined values carry the reason. "
                                "Free text is still allowed.",
                "strict": False, "showCustomUi": True},
        }}]})
        return rows

    def ensure_archive_columns(self, *, apply: bool = True,
                               archive_tab: str = config.ARCHIVE_TAB,
                               archive_sheet_id: int = config.ARCHIVE_SHEET_ID) -> dict:
        """Give the Applied tab his two columns, once, in place.

        Pipeline gained Krish Score and Krish Reaction on 2026-10-11 and
        Applied did not, so a row moved there would have landed 32 cells wide
        under a 31-name header: his score under Why It Fits, every column
        after it one to the right, and the stamp past the last header. Run
        before anything writes Applied.

        Reads row 1 and decides from it alone (archive_layout). The current
        layout, or an empty tab, is a no-op. The previous layout
        (PREVIOUS_HEADERS plus Archived On, nothing after it) gets both
        columns inserted where HEADERS puts them, in one atomic request.
        Anything else raises and changes nothing: a header nobody recognises
        is not hunter's to guess at. Then reads back every row (same count,
        same Business and Role, the stamp moved two to the right, his two
        cells blank) and only then writes the header and reads it back.
        """
        raw = [pad_row(r, SCAN_WIDTH) for r in self.read_archive_grid(archive_tab)]
        current = HEADERS + list(ARCHIVE_TRAILING)
        report = {"tab": archive_tab, "state": "", "rows": 0,
                  "applied": False, "noop": False}
        data = [(n, r) for n, r in enumerate(raw[1:], start=2)
                if any(str(c).strip() for c in r[:3])]
        report["rows"] = len(data)
        state = archive_layout(raw)
        if state in ("empty", "current"):
            report["state"], report["noop"] = state, True
            return report
        if state != "previous":
            raise SheetError(f"{archive_tab} header is neither the previous nor the "
                             f"current layout; first differences "
                             f"{archive_header_diffs(raw)[:4]}. Nothing was changed.")
        report["state"] = "previous"
        if not apply:
            return report

        at = his_insert_indexes()
        old_stamp = len(PREVIOUS_HEADERS)
        # The two inserts first, in one request, and the header only once
        # every row has been read back. Sent together, a read-back that failed
        # left row 1 reading the current layout over rows that may not have
        # moved, and the very next archive_rows and read_archive trusted it.
        # Sent after, a failure leaves the previous names with two blank
        # columns among them, which archive_layout knows as neither layout,
        # so everything that touches Applied stops until someone looks.
        self._post(":batchUpdate", {"requests": [{"insertDimension": {
            "range": {"sheetId": archive_sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "inheritFromBefore": True}} for i in at]})
        grown = list(PREVIOUS_HEADERS)
        for i in at:
            grown.insert(i, "")
        grown += list(ARCHIVE_TRAILING)

        after = [pad_row(r, SCAN_WIDTH) for r in self.read_archive_grid(archive_tab)]
        problems = []
        if not after or after[0][:len(grown)] != grown or any(
                x.strip() for x in after[0][len(grown):]):
            problems.append(f"row 1 reads {after[0][:4] if after else []}... where "
                            f"two blank columns should sit at "
                            f"{' and '.join(col_letter(i) for i in at)}")
        got = [(n, r) for n, r in enumerate(after[1:], start=2)
               if any(str(c).strip() for c in r[:3])]
        if len(got) != len(data):
            problems.append(f"{len(data)} rows before, {len(got)} after")
        his = [COLS[n] for n in sorted(HIS_COLS)]
        for (n, was), (m, now) in zip(data, got):
            if n != m or was[1:3] != now[1:3]:
                problems.append(f"row {n} Business and Role moved")
            elif now[ARCHIVE_WIDTH - 1] != was[old_stamp]:
                problems.append(f"row {n} Archived On did not land at "
                                f"{ARCHIVE_LAST_COL}")
            elif any(str(now[i]).strip() for i in his):
                problems.append(f"row {n} has a value in a column just inserted")
            if len(problems) > 4:
                break
        if problems:
            raise SheetError(f"{archive_tab} column insert did not verify, and its "
                             f"header was not written: " + "; ".join(problems[:5])
                             + ". Inspect the tab before any other write.")

        self._post(":batchUpdate", {"requests": [{"updateCells": {
            "range": {"sheetId": archive_sheet_id, "startRowIndex": 0,
                      "endRowIndex": 1, "startColumnIndex": 0,
                      "endColumnIndex": len(current)},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": h}}
                                 for h in current]}],
            "fields": "userEnteredValue"}}]})
        if archive_layout(self.read_archive_grid(archive_tab)) != "current":
            raise SheetError(f"{archive_tab} columns were inserted and verified but "
                             f"the header did not read back as the current layout. "
                             f"Inspect the tab before any other write.")
        report["applied"] = report["verified"] = True
        return report

    def archive_rows(self, rows: list["SheetRow"], *, archive_tab: str,
                     archive_sheet_id: int, headers: list[str]) -> int:
        """Copy decided rows to the archive tab, then delete them from
        Pipeline. Copy first and verify the landing before deleting anything:
        a half-done move that loses a row Krish decided on is unacceptable."""
        if not rows:
            return 0
        # close-submitted and confirmations archive before settle runs, so
        # the column insert cannot wait for settle to do it.
        self.ensure_archive_columns(apply=True, archive_tab=archive_tab,
                                    archive_sheet_id=archive_sheet_id)
        # The archive tab shipped narrower than the payload. Writing past the
        # grid silently drops the overflow (that is how the first archived row
        # lost every column but A), so widen the grid before writing anything.
        meta = self._get("", {"fields": "sheets(properties(sheetId,gridProperties(columnCount)))"})
        cols = next((sh["properties"]["gridProperties"]["columnCount"]
                     for sh in meta["sheets"]
                     if sh["properties"]["sheetId"] == archive_sheet_id), 0)
        reqs = []
        if cols < ARCHIVE_WIDTH:
            reqs.append({"appendDimension": {
                "sheetId": archive_sheet_id, "dimension": "COLUMNS",
                "length": ARCHIVE_WIDTH - cols}})
        # The tab carried a merged banner across A1:H2 from its previous life.
        # A merged range accepts only its top-left cell, so a wide write lands
        # column A and silently drops the rest. Unmerge first.
        reqs.append({"unmergeCells": {"range": {
            "sheetId": archive_sheet_id, "startRowIndex": 0,
            "startColumnIndex": 0, "endColumnIndex": ARCHIVE_WIDTH}}})
        self._post(":batchUpdate", {"requests": reqs})
        existing = self._values(f"{archive_tab}!A1:{SCAN_LAST_COL}2000")
        header_written = bool(existing) and any(str(c).strip() for c in existing[0])
        want = list(headers) + list(ARCHIVE_TRAILING)
        if header_written:
            # Column A alone said "Verdict" on the old layout too, which is how
            # 32-wide rows would have gone under a 31-name header. The whole
            # header, or nothing moves.
            head = pad_row(existing[0], SCAN_WIDTH)
            if head[:len(want)] != want or any(x.strip() for x in head[len(want):]):
                raise SheetError(
                    f"{archive_tab} header is not Pipeline's columns plus "
                    f"{ARCHIVE_TRAILING[0]}: {header_diffs(head, want)[:4]}; "
                    f"nothing deleted from Pipeline")
        payload = []
        if not header_written:
            payload.append(want)
        stamp = datetime.date.today().isoformat()
        for r in rows:
            payload.append(list(r.cells) + [stamp])
        start = (len(existing) if header_written else 0) + 1
        end = start + len(payload) - 1
        rng = f"{archive_tab}!A{start}:{ARCHIVE_LAST_COL}{end}"
        self._write([(rng, payload)])
        # His two cells again, RAW, so they land as the text he typed. Under
        # USER_ENTERED a reaction starting with a minus sign is a formula and
        # "8/10" is a date.
        self._write([(f"{archive_tab}!{letter(n)}{start}:{letter(n)}{end}",
                      [[row[COLS[n]]] for row in payload]) for n in sorted(HIS_COLS)],
                    raw=True)
        back = self._values(rng)
        if len(back) != len(payload):
            raise SheetError(f"archive read-back wrote {len(back)} of "
                             f"{len(payload)} rows; nothing deleted from Pipeline")
        # Count is not proof. Compare the identity columns cell by cell, or a
        # truncated write looks like a clean one and Pipeline loses the row.
        # His two cells are compared too: they exist nowhere else once the
        # Pipeline row is gone.
        for want_row, got in zip(payload, back):
            got = pad_row(got, ARCHIVE_WIDTH)
            if [str(want_row[1]).strip(), str(want_row[2]).strip()] != [got[1].strip(),
                                                                        got[2].strip()]:
                raise SheetError(
                    f"archive read-back mismatch: wrote {want_row[1]!r}/{want_row[2]!r}, "
                    f"read {got[1]!r}/{got[2]!r}; nothing deleted from Pipeline")
            for n in sorted(HIS_COLS):
                if str(want_row[COLS[n]]) != got[COLS[n]]:
                    raise SheetError(
                        f"archive read-back mismatch on {want_row[1]!r}/{want_row[2]!r} "
                        f"{n}: wrote {str(want_row[COLS[n]])[:60]!r}, read "
                        f"{got[COLS[n]][:60]!r}; nothing deleted from Pipeline")
        return self.delete_rows([r.row_number for r in rows], expect_verdict=None,
                                his_copied=True)

    def set_verdicts(self, mapping: dict[int, str]) -> int:
        """Write column A on named rows and read it back.

        Used when hunter records its own coded verdict (a re-gate drop, a
        decline), so the archive carries WHY. The caller stamps the DB row
        first with verdict_source, or reconcile reads this back as something
        Krish typed and the learning loop treats hunter's own output as his
        taste.
        """
        if not mapping:
            return 0
        self._write([(f"{TAB}!A{rn}", [[text]]) for rn, text in sorted(mapping.items())])
        # ONE read of the whole column, not one per row. The per-row version
        # made a request per verdict, and clearing 78 rows at once answered
        # 429 Too Many Requests part way through: the write had landed, the
        # verification had not, and the caller could not tell which rows were
        # confirmed. The read-back is the point of this method, so it has to
        # be cheap enough to always happen.
        column = self._column_a()
        for rn, text in sorted(mapping.items()):
            row = column[rn - 1] if len(column) >= rn else []
            got = (row[0] if row else "").strip()
            if got != text.strip():
                raise SheetError(f"column A read-back on row {rn} reads {got!r}, "
                                 f"expected {text!r}")
        return len(mapping)

    def clear_package_links(self, row_numbers: list[int]) -> int:
        """Set the four package link columns back to "Not built" and read
        them back. Column A is Krish's; AI Score and Why AI thinks it Fits
        belong to the assessment."""
        if not row_numbers:
            return 0
        blank = [["Not built"] * 4]
        self._write([(cell_range("CV Doc", rn, "CL PDF"), blank)
                     for rn in sorted(row_numbers)])
        for rn in sorted(row_numbers):
            back = self._values(cell_range("CV Doc", rn, "CL PDF")) or [[]]
            got = (back[0] if back else []) + [""] * 4
            if got[:4] != ["Not built"] * 4:
                raise SheetError(f"row {rn} package links read back as {got[:4]}, "
                                 f"expected four 'Not built'")
        return len(row_numbers)

    def relink_jd_urls(self, mapping: dict[int, str]) -> int:
        """Point Job Link at the real ATS posting, keeping the link text.

        A row discovered through its company's board was carrying a LinkedIn
        URL that nothing could verify. Rewriting the link means the next check
        reads the ATS directly instead of rediscovering the board every time.
        """
        if not mapping:
            return 0
        grid = self.read_grid()
        blocks = []
        for rn, url in sorted(mapping.items()):
            row = grid[rn - 1] if rn - 1 < len(grid) else []
            label = (row[COLS["Role"]] if len(row) > COLS["Role"] else "") or "Job posting"
            blocks.append((cell_range("Job Link", rn), [[hyperlink(url, label)]]))
        self._write(blocks)
        for rn, url in sorted(mapping.items()):
            back = self._values(cell_range("Job Link", rn)) or [[""]]
            got = (back[0][0] if back and back[0] else "")
            if url not in got:
                raise SheetError(f"row {rn} Job Link read back as {got[:60]!r}, "
                                 f"expected the posting URL")
        return len(mapping)

    def delete_archive_rows(self, row_numbers: list[int], *,
                            archive_sheet_id: int, expect: list[str]) -> int:
        """Remove rows from the archive tab, checking first that each still
        holds the company it did when it was chosen. Used only to undo a move
        that should never have happened; descending, like every delete here."""
        if not row_numbers:
            return 0
        grid = self._values(f"{config.ARCHIVE_TAB}!A1:{ARCHIVE_LAST_COL}2000")
        for rn, want in zip(row_numbers, expect):
            row = grid[rn - 1] if rn - 1 < len(grid) else []
            got = str(row[1]).strip() if len(row) > 1 else ""
            if got != want.strip():
                raise SheetError(f"archive row {rn} reads company {got!r}, "
                                 f"expected {want!r}; nothing deleted")
        reqs = [{"deleteDimension": {"range": {
            "sheetId": archive_sheet_id, "dimension": "ROWS",
            "startIndex": rn - 1, "endIndex": rn}}}
            for rn in sorted(row_numbers, reverse=True)]
        self._post(":batchUpdate", {"requests": reqs})
        return len(reqs)

    def read_tab_values(self, rng: str) -> list[list]:
        """Raw values from any tab of the workbook (read-only helper; the
        validated write path stays Pipeline-only)."""
        return self._values(rng, formulas=False)

    def update_assessment(self, row_number: int, *, score: int, why_it_fits: str) -> None:
        """Rewrite AI Score and Why AI thinks it Fits only. Column A stays
        Krish's, and the package columns stay the build path's; this writer
        exists so a re-gate can correct a score and its rationale without
        touching anything else on the row.

        Two ranges, J and L, never the one range J:L. They were adjacent until
        he put his own score between them on 2026-10-11, and the single range
        would have written the rationale into Krish Score. His two cells are
        read before and after, and the write is refused as failed if either
        moved."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write row {row_number}: header rows")
        if not 1 <= int(score) <= 10:
            raise SheetError(f"score {score} out of canon range 1..10")
        why = plain_text(why_it_fits).strip()
        if not why:
            raise SheetError(f"{AI_WHY} cannot be blank; use the deterministic fallback")
        if "\u2014" in why:
            raise SheetError(f"em dash in {AI_WHY}")
        first, last = COLS[AI_SCORE], COLS[KRISH_REACTION]
        if not first < COLS[AI_WHY] < last or not all(first <= COLS[n] <= last
                                                       for n in HIS_COLS):
            raise SheetError("AI Score to Krish Reaction no longer spans his two "
                             "columns; fix this writer rather than write blind")
        span = cell_range(AI_SCORE, row_number, KRISH_REACTION)

        def read_span() -> list[str]:
            back = self._values(span)
            return pad_row(back[0] if back else [], last - first + 1)

        before = read_span()
        self._write([(cell_range(AI_SCORE, row_number), [[str(int(score))]]),
                     (cell_range(AI_WHY, row_number), [[as_text(why[:900])]])])
        got = read_span()
        if got[0] != str(int(score)):
            raise SheetError(f"read-back of row {row_number} {AI_SCORE} gave {got[0]!r}")
        if got[COLS[AI_WHY] - first].strip() != why[:900].strip():
            raise SheetError(f"read-back of row {row_number} {AI_WHY} gave "
                             f"{got[COLS[AI_WHY] - first][:60]!r}")
        for name in sorted(HIS_COLS):
            i = COLS[name] - first
            if got[i] != before[i]:
                raise SheetError(f"row {row_number} {name} read {before[i][:40]!r} before "
                                 f"the assessment write and {got[i][:40]!r} after; "
                                 f"inspect the row before any other write")

    def read_tab_formulas(self, rng: str) -> list[list]:
        """Formula-rendered values from any tab. The answer tabs hold link cells
        (LinkedIn, website, the master doc URLs); a rendered read loses every
        URL, the same trap documented at the top of this module."""
        return self._values(rng, formulas=True)

    # The six form-audit columns are contiguous in HEADERS, so one range covers
    # them. They were declared, defaulted and asserted in tests but never
    # written by anything until the apply layer landed.
    AUDIT_FIRST = "Application Format"
    AUDIT_LAST = "Form Audit Date"
    AUDIT_NAMES = ("Application Format", "Attachment Style",
                   "Additional Questions", "Form Complexity",
                   "Autonomy Score", "Form Audit Date")

    def update_form_audit(self, row_number: int, cells: dict[str, str]) -> None:
        """Write only the six form-audit columns of one row. Never column A,
        never Application Status, never Applied Date; those belong to Krish and
        to the apply path he approves. Validates, writes an explicit range, then
        reads back and asserts, the same contract as update_assessment."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write row {row_number}: header rows")
        missing = [n for n in self.AUDIT_NAMES if n not in cells]
        if missing:
            raise SheetError(f"form audit is missing {missing}; no cell may be blank")
        values = []
        for name in self.AUDIT_NAMES:
            val = plain_text(str(cells[name])).strip()
            if not val:
                raise SheetError(f"form audit {name} is blank; canon 9.13 has no blanks")
            if "\u2014" in val:
                raise SheetError(f"em dash in form audit {name}")
            values.append(val[:500])
        first_idx, last_idx = COLS[self.AUDIT_FIRST], COLS[self.AUDIT_LAST]
        if last_idx - first_idx + 1 != len(self.AUDIT_NAMES):
            raise SheetError(
                "the form-audit columns are no longer contiguous in HEADERS; "
                "fix this writer rather than writing the wrong cells")
        rng = cell_range(self.AUDIT_FIRST, row_number, self.AUDIT_LAST)
        self._write([(rng, [values])])
        back = self._values(rng, formulas=False) or [[]]
        got = pad_row(back[0] if back else [], len(self.AUDIT_NAMES))
        if got[:len(values)] != values:
            raise SheetError(
                f"read-back of row {row_number} form audit gave {got[:len(values)]!r}, "
                f"expected {values!r}")

    def read_archive(self) -> list[SheetRow]:
        """Archived rows are still part of "the sheet" for reconciliation.
        Leaving them out makes every archived role look missing, and
        direction 2 re-appends it to Pipeline on the next run.

        Columns are found by Applied's own row 1, never by position. Applied
        kept the previous layout after Pipeline gained his two columns, and a
        positional read then took Sector for the rationale, Warm Path for the
        location and Next Action for the applied date. Either layout reads;
        anything else raises, and nothing here ever writes."""
        grid = self.read_archive_grid()
        state = archive_layout(grid)
        if state == "empty":
            return []
        if state == "current":
            where = {h: i for i, h in enumerate(HEADERS)}
            stamp_at = len(HEADERS)
        elif state == "previous":
            where = {PREVIOUS_NAMES.get(h, h): i for i, h in enumerate(PREVIOUS_HEADERS)}
            stamp_at = len(PREVIOUS_HEADERS)
        else:
            raise SheetError(
                f"{config.ARCHIVE_TAB} header is neither the previous nor the "
                f"current layout: {archive_header_diffs(grid)[:4]}")
        rows = []
        for i, raw in enumerate(grid[1:], start=2):
            line = pad_row(raw, SCAN_WIDTH)
            cells = [""] * N_COLS
            for name, at in where.items():
                cells[COLS[name]] = line[at]
            if not cells[COLS["Business"]].strip() and not cells[COLS["Role"]].strip():
                continue
            parsed = parse_hyperlink(cells[COLS["Job Link"]])
            rows.append(SheetRow(row_number=i, cells=cells, verdict=cells[COLS["Verdict"]],
                                 company=cells[COLS["Business"]], role=cells[COLS["Role"]],
                                 jd_url=parsed[0] if parsed else None,
                                 archived=True, archived_on=line[stamp_at]))
        return rows

    def read_header(self) -> list[str]:
        """Pipeline's row 1, read well past the last column hunter knows."""
        got = self._values(HEADER_SCAN)
        return pad_row(got[0] if got else [], SCAN_WIDTH)

    def read_archive_grid(self, archive_tab: str = config.ARCHIVE_TAB) -> list[list]:
        """The Applied tab as it stands, out past the last column hunter
        knows, for archive_layout to judge."""
        return self._values(f"{archive_tab}!A1:{SCAN_LAST_COL}2000")

    def _read_checked(self, expected: list[str]) -> list[list[str]]:
        """The grid, after row 1 has been checked against `expected` out to
        SCAN_LAST_COL, in one request. Raises HeaderChanged on any
        difference, a column after the last one hunter knows included."""
        raw = self._values(f"{TAB}!A1:{SCAN_LAST_COL}")
        if not raw:
            raise SheetError("Pipeline tab read returned no rows")
        found = pad_row(raw[0], SCAN_WIDTH)
        diffs = header_diffs(found, list(expected))
        if diffs:
            raise HeaderChanged(diffs, header_shift(found, list(expected)))
        return [pad_row(r) for r in raw]

    def read_pipeline(self, canon_headers: list[str]) -> list[SheetRow]:
        grid = self._read_checked(canon_headers)
        if len(grid) > 1 and any(c.strip() for c in grid[1]):
            raise SheetError("Pipeline row 2 is expected to be intentionally blank")
        rows: list[SheetRow] = []
        for i, cells in enumerate(grid[DATA_START_ROW - 1:], start=DATA_START_ROW):
            if not any(c.strip() for c in cells[:3]):
                continue
            jd = parse_hyperlink(cells[COLS["Job Link"]])
            packages = {}
            for name, key in (("CV Doc", "cv"), ("Cover Letter Doc", "letter"),
                              ("CV PDF", "cv_pdf"), ("CL PDF", "letter_pdf")):
                p = parse_hyperlink(cells[COLS[name]])
                packages[key] = p[0] if p else None
            rows.append(SheetRow(
                row_number=i, cells=cells, verdict=cells[COLS["Verdict"]].strip(),
                company=cells[COLS["Business"]].strip(), role=cells[COLS["Role"]].strip(),
                jd_url=jd[0] if jd else None, package_urls=packages))
        return rows

    def last_populated_row(self) -> int:
        grid = self.read_grid()
        last = 0
        for i, cells in enumerate(grid, start=1):
            if any(c.strip() for c in cells[:3]):
                last = i
        return max(last, DATA_START_ROW - 1)

    # ---------- writing ----------

    def append_rows(self, new_rows: list[list[str]], *, carry_his: bool = False) -> str:
        """Write new rows after the last populated one and read them back.

        A row hunter makes reaches the sheet with his two cells blank, and
        anything else is refused before a cell is written. carry_his is for
        the one caller that is putting HIS row back (cmd_restore), whose two
        cells are his own words returning with it."""
        if not new_rows:
            return ""
        for n, cells in enumerate(new_rows):
            fails = validate_row(cells, is_append=True)
            if not carry_his and len(cells) == N_COLS:
                fails += [f"{HEADERS[i]} is his and must be blank on a new row"
                          for i in sorted(HIS_IDX) if str(cells[i]).strip()]
            if fails:
                raise SheetError(f"append row {n} failed validation: {fails}; "
                                 f"nothing was written")
        start = self.last_populated_row() + 1
        if start < DATA_START_ROW:
            raise SheetError(f"computed append start {start} is impossible")
        end = start + len(new_rows) - 1
        rng = f"{TAB}!A{start}:{LAST_COL}{end}"
        # His two cells never go through USER_ENTERED: they are blanked in
        # the row write and, when carried, written again RAW, as archive_rows
        # writes them to Applied. A reaction starting with a minus sign is a
        # formula under USER_ENTERED and "10/12 call" is a date, so a restore
        # failed its read-back after the write, leaving his row on both tabs
        # with his words mangled on Pipeline.
        self._write([(rng, [[("" if i in HIS_IDX else c) for i, c in enumerate(r)]
                            for r in new_rows])])
        if carry_his:
            self._write([(f"{TAB}!{letter(n)}{start}:{letter(n)}{end}",
                          [[r[COLS[n]]] for r in new_rows]) for n in sorted(HIS_COLS)],
                        raw=True)
        landed = [pad_row(r) for r in self._values(rng)]
        if len(landed) != len(new_rows):
            raise SheetError(f"read-back on {rng} returned {len(landed)} row(s), "
                             f"expected {len(new_rows)}; inspect before retrying")
        problems = []
        for n, (want, got) in enumerate(zip(new_rows, landed)):
            diffs = row_diff(want, got)
            for name, w, g in diffs:
                problems.append(f"row {start + n} {name}: wrote {w!r}, "
                                f"sheet holds {g!r}")
            # his words byte for byte, where the trimmed comparison passed
            named = {name for name, _, _ in diffs}
            problems += [f"row {start + n} {HEADERS[i]}: wrote {str(want[i])[:60]!r}, "
                         f"sheet holds {got[i][:60]!r}"
                         for i in sorted(HIS_IDX)
                         if HEADERS[i] not in named and str(want[i]) != got[i]]
        if problems:
            raise SheetError(
                f"read-back mismatch on {rng}, {len(problems)} cell(s) differ. "
                + " | ".join(problems[:6])
                + (f" | and {len(problems) - 6} more" if len(problems) > 6 else ""))
        return rng

    def update_package_cells(self, row_number: int, *, cv_url: str, letter_url: str,
                             cv_pdf_url: str, letter_pdf_url: str,
                             package_status: str, built_date: str) -> None:
        """Package fields only: the four links, Package Status, Materials
        Built. Never column A, never Application Status."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write into header rows: {row_number}")
        if package_status not in BUILT_STATUSES:
            raise SheetError(f"unknown package status {package_status!r}; a new "
                             f"Package Status value needs a workflow_proposal first")
        if not DATE_RE.match(built_date):
            raise SheetError(f"built_date must be YYYY-MM-DD: {built_date!r}")
        links = [[hyperlink(cv_url, "CV"), hyperlink(letter_url, "CL"),
                  hyperlink(cv_pdf_url, "CV PDF"), hyperlink(letter_pdf_url, "CL PDF")]]
        self._write([
            (cell_range("CV Doc", row_number, "CL PDF"), links),
            (cell_range("Package Status", row_number), [[package_status]]),
            (cell_range("Materials Built", row_number), [[built_date]]),
        ])
        back = self._values(cell_range("Package Status", row_number), formulas=False) or [[""]]
        got = (back[0][0] if back and back[0] else "").strip()
        if got != package_status:
            raise SheetError(f"row {row_number} Package Status read back as {got!r}")

    def update_package_status(self, row_number: int, status: str) -> None:
        """Package Status alone. Used for the states that carry no links:
        a posting verifiably dead at build time."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write into header rows: {row_number}")
        if status not in PACKAGE_STATUSES:
            raise SheetError(f"unknown package status {status!r}; a new value "
                             f"needs a canon 9.13 amendment first")
        self._write([(cell_range("Package Status", row_number), [[status]])])
        back = self._values(cell_range("Package Status", row_number), formulas=False) or [[""]]
        got = (back[0][0] if back and back[0] else "").strip()
        if got != status:
            raise SheetError(f"row {row_number} Package Status read back as {got!r}")

    def mark_applied(self, row_number: int, *, when: str) -> None:
        """Application Status and Applied Date on one row, read back.

        The two cells that were never written by anything. A submission landed on
        the employer's desk, the ledger recorded it, and this row went on reading
        "Not applied" forever, which is why Krish had no confirmation that the
        machine had done anything at all.
        """
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write into header rows: {row_number}")
        when = plain_text(when).strip()
        if not when:
            raise SheetError("refusing to mark a row applied with no date")
        self._write([(cell_range("Application Status", row_number), [[APPLIED_STATUS]]),
                     (cell_range("Applied Date", row_number), [[when]])], raw=True)
        for header, expected in (("Application Status", APPLIED_STATUS),
                                 ("Applied Date", when)):
            back = self._values(cell_range(header, row_number), formulas=False) or [[""]]
            got = (back[0][0] if back and back[0] else "").strip()
            if got != expected:
                raise SheetError(f"row {row_number} {header} read back as {got!r}, "
                                 f"expected {expected!r}")

    def update_warm_paths(self, mapping: dict[int, tuple[str, str]]) -> int:
        """Warm Path and Path Evidence on named rows, validated, read back.

        Warm Path is a HYPERLINK to the person's LinkedIn profile labelled
        "Name, title at Company", plain text when no URL is known, or the
        canon default when nobody was found. Evidence is never blank.
        """
        if not mapping:
            return 0
        blocks = []
        for rn, (warm, evidence) in sorted(mapping.items()):
            if rn < DATA_START_ROW:
                raise SheetError(f"refusing to write into header rows: {rn}")
            warm = plain_text(warm).strip() or WARM_NONE
            evidence = plain_text(evidence).strip()[:EVIDENCE_MAX] or EVIDENCE_NONE
            parsed = parse_hyperlink(warm)
            if warm.startswith("=") and (not parsed or "http" not in parsed[0]):
                raise SheetError(f"row {rn} Warm Path is a formula but not a "
                                 f"HYPERLINK with a URL")
            for text in (warm, evidence):
                if "\u2014" in text:
                    raise SheetError(f"em dash in a warm path cell on row {rn}")
            blocks.append((cell_range("Warm Path", rn, "Path Evidence"), [[warm, evidence]]))
        self._write(blocks)
        for rn, _ in sorted(mapping.items()):
            back = self._values(cell_range("Warm Path", rn, "Path Evidence")) or [[]]
            got = pad_row(back[0] if back else [], 2)
            if not got[0].strip() or not got[1].strip():
                raise SheetError(f"row {rn} warm path cells read back blank: {got}")
        return len(mapping)

    def sort_by_score(self) -> dict:
        """Score descending, no blank rows inside the data block.

        sortRange moves whole rows, so column A and every other cell travel
        together and hunter rewrites nothing. The identity multiset is
        compared before and after, his score and his reaction included, so a
        sort that lost, duplicated or stranded any of them raises. It sorts
        A to the last column hunter knows, so it refuses a header with
        anything after that column: his new column would stay put while
        every row moved past it.
        """
        grid = self._read_checked(HEADERS)
        rows = list(enumerate(grid, start=1))

        def identity(c: list[str]) -> tuple:
            return tuple(c[COLS[n]] for n in ("Verdict", "Business", "Role",
                                              KRISH_SCORE, KRISH_REACTION))
        populated = [n for n, cells in rows if n >= DATA_START_ROW
                     and any(c.strip() for c in cells[:3])]
        if not populated:
            return {"rows": 0, "blank_rows_removed": 0, "verified": True}
        last = max(populated)
        blanks = [n for n in range(DATA_START_ROW, last + 1) if n not in set(populated)]
        removed = self.delete_blank_rows(blanks) if blanks else 0
        last -= removed
        before = sorted(identity(c) for n, c in rows if n in set(populated))
        # Score cells the incumbent wrote are text, and Sheets sorts text
        # after numbers, so the 10s sank below the 6s. Make the column a
        # number column and rewrite every score as a number first.
        self._post(":batchUpdate", {"requests": [{"repeatCell": {
            "range": {"sheetId": self.sheet_id,
                      "startRowIndex": DATA_START_ROW - 1, "endRowIndex": last,
                      "startColumnIndex": COLS[AI_SCORE], "endColumnIndex": COLS[AI_SCORE] + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0"}}},
            "fields": "userEnteredFormat.numberFormat"}}]})
        fixed = []
        for n, c in rows:
            if DATA_START_ROW <= n <= last + removed and n not in set(blanks):
                try:
                    fixed.append((cell_range(AI_SCORE, n - sum(1 for b in blanks if b < n)),
                                  [[int(str(c[COLS[AI_SCORE]]).strip())]]))
                except (ValueError, TypeError):
                    pass
        if fixed:
            self._write(fixed)
        self._post(":batchUpdate", {"requests": [{"sortRange": {
            "range": {"sheetId": self.sheet_id,
                      "startRowIndex": DATA_START_ROW - 1, "endRowIndex": last,
                      "startColumnIndex": 0, "endColumnIndex": N_COLS},
            "sortSpecs": [{"dimensionIndex": COLS[AI_SCORE], "sortOrder": "DESCENDING"}],
        }}]})
        after_grid = self.read_grid()
        after_rows = [c for n, c in enumerate(after_grid, start=1)
                      if DATA_START_ROW <= n <= last]
        after = sorted(identity(c) for c in after_rows)
        if after != before:
            raise SheetError("sort read-back changed the set of rows; inspect the "
                             "sheet before any further write")
        scores = []
        for c in after_rows:
            if not any(x.strip() for x in c[:3]):
                raise SheetError("a blank row remains inside the data block after sorting")
            try:
                scores.append(int(c[COLS[AI_SCORE]]))
            except (ValueError, TypeError):
                scores.append(-1)
        if scores != sorted(scores, reverse=True):
            raise SheetError(f"rows are not in descending score order after sorting: {scores}")
        return {"rows": len(after_rows), "blank_rows_removed": removed, "verified": True}

    # ---------- formatting and layout migrations ----------

    def _sheet_meta(self, sheet_id: int | None = None) -> dict:
        data = self._get("", {"fields": "sheets(properties(sheetId,title,"
                                        "gridProperties(rowCount,columnCount)),"
                                        "conditionalFormats,bandedRanges)"})
        want = self.sheet_id if sheet_id is None else sheet_id
        for s in data.get("sheets", []):
            if s["properties"]["sheetId"] == want:
                return s
        raise SheetError(f"sheetId {want} not found in workbook")

    def _validation_at(self, row: int, tab: str = TAB,
                       sheet_id: int | None = None) -> dict | None:
        want = self.sheet_id if sheet_id is None else sheet_id
        data = self._get("", {"ranges": f"{tab}!A{row}",
                              "fields": "sheets(properties(sheetId),"
                                        "data(rowData(values(dataValidation))))"})
        for s in data.get("sheets", []):
            if s.get("properties", {}).get("sheetId") != want:
                continue
            for block in s.get("data", []):
                for r in block.get("rowData", []):
                    for v in r.get("values", []):
                        if "dataValidation" in v:
                            return v["dataValidation"]
        return None

    def narrow_score_colours(self, *, sheet_id: int | None = None) -> dict:
        """The colour rules keyed on AI Score, back onto AI Score alone.

        Three custom-formula rules colour hunter's score by its value ($J at 9
        or more, at 8, at 7 or less). When Krish inserted Krish Score beside
        it on 2026-10-11 their range grew from J to J:K, so his own score was
        painted with the colour of hunter's. No code here creates those
        rules. This narrows any rule whose formula reads $J and whose range
        starts at J and runs into his columns, leaves every other rule alone,
        keeps each booleanRule byte for byte, and reads the result back.
        Idempotent: a tab already narrowed is a no-op.
        """
        sid = self.sheet_id if sheet_id is None else sheet_id
        j, his_end = COLS[AI_SCORE], COLS[KRISH_REACTION] + 1
        key = f"${letter(AI_SCORE)}"

        def keyed_on_j(rule: dict) -> bool:
            cond = (rule.get("booleanRule") or {}).get("condition") or {}
            return any(key in str(v.get("userEnteredValue", ""))
                       for v in cond.get("values", []))

        def too_wide(r: dict) -> bool:
            return (r.get("startColumnIndex") == j
                    and j + 1 < r.get("endColumnIndex", 0) <= his_end)

        rules = self._sheet_meta(sid).get("conditionalFormats", [])
        reqs, touched = [], []
        for idx, rule in enumerate(rules):
            if not keyed_on_j(rule) or not any(too_wide(r) for r in rule.get("ranges", [])):
                continue
            new = dict(rule)
            new["ranges"] = [dict(r, endColumnIndex=j + 1) if too_wide(r) else r
                             for r in rule.get("ranges", [])]
            reqs.append({"updateConditionalFormatRule": {
                "sheetId": sid, "index": idx, "rule": new}})
            touched.append(idx)
        if not reqs:
            return {"narrowed": 0, "noop": True, "verified": True}
        self._post(":batchUpdate", {"requests": reqs})
        after = self._sheet_meta(sid).get("conditionalFormats", [])
        if [r.get("booleanRule") for r in after] != [r.get("booleanRule") for r in rules]:
            raise SheetError("a colour rule changed while its range was narrowed; "
                             "inspect the conditional formats")
        wide = [i for i in touched
                if any(too_wide(r) for r in after[i].get("ranges", []))]
        if wide:
            raise SheetError(f"colour rule(s) {wide} still reach into "
                             f"{KRISH_SCORE} after narrowing")
        return {"narrowed": len(touched), "noop": False, "verified": True}

    def migrate_formatting(self) -> dict:
        """Extend the conditional-format rules, banded range, and column A
        dropdown to the sheet's full row count, preserving every booleanRule
        byte for byte. Idempotent: reports a no-op when already extended."""
        meta = self._sheet_meta()
        row_count = meta["properties"]["gridProperties"]["rowCount"]
        cf_rules = meta.get("conditionalFormats", [])
        bands = meta.get("bandedRanges", [])
        validation = self._validation_at(DATA_START_ROW)

        report = {"row_count": row_count, "cf_rules": len(cf_rules),
                  "bands": len(bands), "dropdown_found": validation is not None,
                  "changed": [], "noop": False}

        requests_body: list[dict] = []
        for idx, rule in enumerate(cf_rules):
            ranges = rule.get("ranges", [])
            if all(r.get("endRowIndex", row_count) >= row_count for r in ranges):
                continue
            new_rule = {k: v for k, v in rule.items()}
            new_rule["ranges"] = [dict(r, endRowIndex=row_count) for r in ranges]
            requests_body.append({"updateConditionalFormatRule": {
                "sheetId": self.sheet_id, "index": idx, "rule": new_rule}})
            report["changed"].append(f"cf_rule[{idx}]")
        for band in bands:
            rng = band.get("range", {})
            if rng.get("sheetId") != self.sheet_id:
                continue
            if rng.get("endRowIndex", row_count) >= row_count:
                continue
            requests_body.append({"updateBanding": {
                "bandedRange": {"bandedRangeId": band["bandedRangeId"],
                                "range": dict(rng, endRowIndex=row_count)},
                "fields": "range"}})
            report["changed"].append(f"band[{band['bandedRangeId']}]")
        if validation is not None and self._validation_at(row_count) is None:
            requests_body.append({"setDataValidation": {
                "range": {"sheetId": self.sheet_id,
                          "startRowIndex": DATA_START_ROW - 1,
                          "endRowIndex": row_count,
                          "startColumnIndex": 0, "endColumnIndex": 1},
                "rule": validation}})
            report["changed"].append("dropdown[A]")

        if not requests_body:
            report["noop"] = True
            return report

        before_rules = [r.get("booleanRule") for r in cf_rules]
        self._post(":batchUpdate", {"requests": requests_body})

        after = self._sheet_meta()
        after_rules = after.get("conditionalFormats", [])
        if [r.get("booleanRule") for r in after_rules] != before_rules:
            raise SheetError("a booleanRule changed during migration; investigate "
                             "before any append")
        for idx, rule in enumerate(after_rules):
            for r in rule.get("ranges", []):
                if r.get("endRowIndex", row_count) < row_count:
                    raise SheetError(f"cf rule {idx} did not extend to {row_count}")
        for band in after.get("bandedRanges", []):
            if band["range"].get("endRowIndex", row_count) < row_count:
                raise SheetError("banded range did not extend")
        report["verified"] = True
        return report
