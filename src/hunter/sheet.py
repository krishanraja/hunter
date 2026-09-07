"""Pipeline sheet layer. Canon 9.13 is the contract:

- Row 1 is the header (asserted against canon's exact names, in order), row 2
  is intentionally blank, data starts at row 3.
- Column positions come from ONE map, COLS, built from HEADERS. Every range
  string in this module is derived from it, so a column move is a change to
  HEADERS plus one migrate-columns run, never a hunt for magic numbers.
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
- No cell is ever left blank; fill defaults are canon 9.13's, verbatim.
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

# Canon 9.13, exact, in order. run.assert_canon_alignment refuses to run
# when canon says otherwise, so this list and the canon body cannot drift
# apart silently.
HEADERS = [
    "Verdict", "Business", "Role", "JD Snippet", "Job Link", "CV Doc",
    "Cover Letter Doc", "CV PDF", "CL PDF", "Score", "Why It Fits", "Sector",
    "Stage", "Location", "Comp", "Warm Path", "Path Evidence", "Package Status",
    "Source", "Application Status", "Applied Date", "Next Action",
    "Application Format", "Attachment Style", "Additional Questions",
    "Form Complexity", "Autonomy Score", "Form Audit Date", "JD URL Verified",
    "Materials Built",
]
# The 28-column layout the sheet carried until 2026-09-07. Read only by
# migrate_columns, which knows how to get from here to HEADERS.
LEGACY_HEADERS = [
    "Verdict", "Business", "Role", "Job Link", "CV Doc", "Cover Letter Doc",
    "CV PDF", "CL PDF", "Score", "Why It Fits", "Sector", "Stage", "Location",
    "Comp", "Package Status", "Source", "Application Status", "Applied Date",
    "Next Action", "Application Format", "Attachment Style",
    "Additional Questions", "Form Complexity", "Autonomy Score",
    "Form Audit Date", "JD URL Verified", "JD Snippet", "Materials Built",
]
N_COLS = len(HEADERS)
COLS = {name: i for i, name in enumerate(HEADERS)}
ARCHIVE_TRAILING = ("Archived On",)
ARCHIVE_WIDTH = N_COLS + len(ARCHIVE_TRAILING)

# canon 9.13 fill defaults, by name
DEFAULTS_BY_NAME = {
    "Verdict": "New", "JD Snippet": "Not captured", "CV Doc": "Not built",
    "Cover Letter Doc": "Not built", "CV PDF": "Not built", "CL PDF": "Not built",
    "Why It Fits": "Not assessed", "Sector": "Not stated", "Stage": "Not stated",
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
BUILT_STATUSES = {PKG_BUILT_DIRECT, PKG_BUILT_BRIDGE, PKG_BUILT_UNVERIFIED}

WARM_NONE = DEFAULTS_BY_NAME["Warm Path"]
EVIDENCE_NONE = DEFAULTS_BY_NAME["Path Evidence"]
EVIDENCE_MAX = 500

HYPERLINK_RE = re.compile(r'^=HYPERLINK\("((?:[^"\\]|\\.)+)"\s*[,;]\s*"((?:[^"\\]|\\.)*)"\)$')
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SheetError(RuntimeError):
    pass


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


def make_row(*, company: str, role: str, jd_url: str, score: int,
             why_it_fits: str = "", sector: str = "", stage: str = "",
             location: str = "", comp: str = "", source: str = "",
             jd_verified: bool = True, jd_snippet: str = "",
             warm_path: str = "", path_evidence: str = "") -> list[str]:
    cells = list(DEFAULT_CELLS)
    cells[COLS["Business"]] = plain_text(company)
    cells[COLS["Role"]] = plain_text(role)
    cells[COLS["Job Link"]] = hyperlink(jd_url, "JD")
    cells[COLS["Score"]] = str(score)
    if why_it_fits.strip():
        cells[COLS["Why It Fits"]] = plain_text(why_it_fits).strip()
    for name, value in (("Sector", sector), ("Stage", stage), ("Location", location),
                        ("Comp", comp), ("Source", source)):
        if value.strip():
            cells[COLS[name]] = plain_text(value).strip()
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
        if not str(c).strip():
            fails.append(f"blank cell at column {col_letter(i)} ({HEADERS[i]})")
    if is_append and cells[COLS["Verdict"]] != "New":
        fails.append("column A must be the literal New on appends")
    parsed = parse_hyperlink(cells[COLS["Job Link"]])
    if not parsed or not parsed[0].startswith("http"):
        fails.append(f'column {letter("Job Link")} must be =HYPERLINK("http...","JD")')
    try:
        score = int(cells[COLS["Score"]])
        if not 1 <= score <= 10:
            fails.append(f"column {letter('Score')} score out of 1..10")
    except (ValueError, TypeError):
        fails.append(f"column {letter('Score')} score is not an integer")
    for i in DATE_COLS:
        if cells[i] != "n/a" and not DATE_RE.match(cells[i]):
            fails.append(f"column {col_letter(i)} must be n/a or YYYY-MM-DD, got {cells[i]!r}")
    if cells[COLS["JD URL Verified"]] not in ("TRUE", "FALSE"):
        fails.append(f"column {letter('JD URL Verified')} must be TRUE or FALSE in that casing")
    for i, c in enumerate(cells):
        if "\u2014" in str(c):
            fails.append(f"em dash in column {col_letter(i)}")
    return fails


def rows_equal(expected: list[str], actual: list[str]) -> bool:
    """Structural comparison tolerant of Sheets normalization: HYPERLINKs
    compare by (url, label); everything else compares as trimmed text."""
    actual = pad_row(actual, len(expected))
    for e, a in zip(expected, actual):
        pe, pa = parse_hyperlink(e), parse_hyperlink(a)
        if pe or pa:
            if pe != pa:
                return False
        elif str(e).strip() != str(a).strip():
            return False
    return True


def column_state(header: list[str], trailing: tuple[str, ...] = ()) -> str:
    """Where a tab sits on the road from LEGACY_HEADERS to HEADERS.

    legacy: the 28 old names (plus trailing). moved: JD Snippet already at D
    and no Warm Path yet. inserted: the two new columns exist but the header
    is not written. done: HEADERS exactly. unknown: none of these, and the
    migration refuses to guess.
    """
    h = [str(x).strip() for x in header]
    tl = list(trailing)
    if h[:len(HEADERS) + len(tl)] == HEADERS + tl and not any(
            x for x in h[len(HEADERS) + len(tl):]):
        return "done"
    if h[:len(LEGACY_HEADERS) + len(tl)] == LEGACY_HEADERS + tl and not any(
            x for x in h[len(LEGACY_HEADERS) + len(tl):]):
        return "legacy"
    moved = list(LEGACY_HEADERS)
    moved.insert(3, moved.pop(LEGACY_HEADERS.index("JD Snippet")))
    if h[:len(moved) + len(tl)] == moved + tl and not any(x for x in h[len(moved) + len(tl):]):
        return "moved"
    insert_at = moved.index("Comp") + 1
    inserted = moved[:insert_at] + ["", ""] + moved[insert_at:]
    hh = list(h) + [""] * (len(inserted) + len(tl) - len(h))
    if all((hh[i] == inserted[i]) or (inserted[i] == "" and hh[i] in ("", "Warm Path", "Path Evidence"))
           for i in range(len(inserted))) and hh[len(inserted):len(inserted) + len(tl)] == tl:
        return "inserted"
    return "unknown"


def migration_indexes() -> dict:
    """The move and the insert, derived rather than typed, and checked."""
    src = LEGACY_HEADERS.index("JD Snippet")
    dst = LEGACY_HEADERS.index("Job Link")
    moved = list(LEGACY_HEADERS)
    moved.insert(dst, moved.pop(src))
    insert_at = moved.index("Comp") + 1
    new_names = [h for h in HEADERS if h not in LEGACY_HEADERS]
    if moved[:insert_at] + new_names + moved[insert_at:] != HEADERS:
        raise SheetError("HEADERS is not LEGACY_HEADERS with the snippet moved "
                         "and the new columns inserted; fix the map first")
    return {"move_from": src, "move_to": dst, "insert_at": insert_at,
            "insert_count": len(new_names), "new_names": new_names}


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

    def _write(self, blocks: list[tuple[str, list[list]]]) -> None:
        self._post("/values:batchUpdate", {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": rng, "values": values} for rng, values in blocks]})

    # ---------- reading ----------

    def read_grid(self) -> list[list[str]]:
        return [pad_row(r) for r in self._values(FULL_RANGE)]

    def _column_a(self, tab: str = TAB) -> list[list]:
        return self._values(f"{tab}!A1:A2000", formulas=False)

    def delete_rows(self, row_numbers: list[int], *,
                    expect_verdict: str | None = "New") -> int:
        """Remove data rows, refusing any row Krish has written on.

        Deleting is the one destructive thing this module does, so it re-reads
        column A immediately before the write and aborts the whole batch if any
        target no longer reads exactly `expect_verdict`. Rows go in descending
        order so earlier indices stay valid.
        """
        targets = sorted({int(n) for n in row_numbers}, reverse=True)
        if not targets:
            return 0
        if min(targets) < DATA_START_ROW:
            raise SheetError(f"refusing to delete row {min(targets)}: rows 1 and 2 "
                             f"are the header and the intentional blank")
        grid = self._column_a()
        dirty = []
        if expect_verdict is not None:
            for n in targets:
                cell = (grid[n - 1][0] if len(grid) >= n and grid[n - 1] else "").strip()
                if cell != expect_verdict:
                    dirty.append((n, cell))
        if dirty:
            raise SheetError(f"refusing to delete: column A is no longer "
                             f"{expect_verdict!r} on {dirty[:5]}; re-run the plan")
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

    def archive_rows(self, rows: list["SheetRow"], *, archive_tab: str,
                     archive_sheet_id: int, headers: list[str]) -> int:
        """Copy decided rows to the archive tab, then delete them from
        Pipeline. Copy first and verify the landing before deleting anything:
        a half-done move that loses a row Krish decided on is unacceptable."""
        if not rows:
            return 0
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
        existing = self._values(f"{archive_tab}!A1:{ARCHIVE_LAST_COL}2000")
        header_written = bool(existing) and existing[0][:1] == [headers[0]]
        payload = []
        if not header_written:
            payload.append(list(headers) + list(ARCHIVE_TRAILING))
        stamp = datetime.date.today().isoformat()
        for r in rows:
            payload.append(list(r.cells) + [stamp])
        start = (len(existing) if header_written else 0) + 1
        end = start + len(payload) - 1
        rng = f"{archive_tab}!A{start}:{ARCHIVE_LAST_COL}{end}"
        self._write([(rng, payload)])
        back = self._values(rng)
        if len(back) != len(payload):
            raise SheetError(f"archive read-back wrote {len(back)} of "
                             f"{len(payload)} rows; nothing deleted from Pipeline")
        # Count is not proof. Compare the identity columns cell by cell, or a
        # truncated write looks like a clean one and Pipeline loses the row.
        for want, got in zip(payload, back):
            got = (list(got) + [""] * 3)[:3]
            if [str(want[1]).strip(), str(want[2]).strip()] != [str(got[1]).strip(),
                                                                str(got[2]).strip()]:
                raise SheetError(
                    f"archive read-back mismatch: wrote {want[1]!r}/{want[2]!r}, "
                    f"read {got[1]!r}/{got[2]!r}; nothing deleted from Pipeline")
        return self.delete_rows([r.row_number for r in rows], expect_verdict=None)

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
        for rn, text in sorted(mapping.items()):
            back = self._values(f"{TAB}!A{rn}", formulas=False) or [[""]]
            got = (back[0][0] if back and back[0] else "").strip()
            if got != text.strip():
                raise SheetError(f"column A read-back on row {rn} reads {got!r}, "
                                 f"expected {text!r}")
        return len(mapping)

    def clear_package_links(self, row_numbers: list[int]) -> int:
        """Set the four package link columns back to "Not built" and read
        them back. Column A is Krish's; Score and Why It Fits belong to the
        assessment."""
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
        """Rewrite Score and Why It Fits only. Column A stays Krish's, and the
        package columns stay the build path's; this writer exists so a
        re-gate can correct a score and its rationale without touching
        anything else on the row."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write row {row_number}: header rows")
        if not 1 <= int(score) <= 10:
            raise SheetError(f"score {score} out of canon range 1..10")
        why = plain_text(why_it_fits).strip()
        if not why:
            raise SheetError("Why It Fits cannot be blank; use the deterministic fallback")
        if "\u2014" in why:
            raise SheetError("em dash in Why It Fits")
        rng = cell_range("Score", row_number, "Why It Fits")
        self._write([(rng, [[str(int(score)), why[:900]]])])
        back = self._values(rng, formulas=False) or [[]]
        got = (back[0] + ["", ""])[:2] if back else ["", ""]
        if got[0] != str(int(score)):
            raise SheetError(f"read-back of row {row_number} Score gave {got[0]!r}")

    def read_archive(self) -> list[SheetRow]:
        """Archived rows are still part of "the sheet" for reconciliation.
        Leaving them out makes every archived role look missing, and
        direction 2 re-appends it to Pipeline on the next run."""
        grid = self._values(f"{config.ARCHIVE_TAB}!A1:{LAST_COL}2000")
        rows = []
        for i, raw in enumerate(grid[1:], start=2):
            cells = pad_row(raw)
            if not cells[COLS["Business"]].strip() and not cells[COLS["Role"]].strip():
                continue
            parsed = parse_hyperlink(cells[COLS["Job Link"]])
            rows.append(SheetRow(row_number=i, cells=cells, verdict=cells[COLS["Verdict"]],
                                 company=cells[COLS["Business"]], role=cells[COLS["Role"]],
                                 jd_url=parsed[0] if parsed else None,
                                 archived=True))
        return rows

    def read_pipeline(self, canon_headers: list[str]) -> list[SheetRow]:
        grid = self.read_grid()
        if not grid:
            raise SheetError("Pipeline tab read returned no rows")
        if grid[0] != list(canon_headers):
            diffs = [(i, grid[0][i], canon_headers[i])
                     for i in range(min(N_COLS, len(canon_headers)))
                     if grid[0][i] != canon_headers[i]]
            raise SheetError(f"Pipeline header row disagrees with canon 9.13: {diffs[:4]}; "
                             f"run migrate-columns if the layout moved")
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

    def append_rows(self, new_rows: list[list[str]]) -> str:
        if not new_rows:
            return ""
        for n, cells in enumerate(new_rows):
            fails = validate_row(cells, is_append=True)
            if fails:
                raise SheetError(f"append row {n} failed validation: {fails}; "
                                 f"nothing was written")
        start = self.last_populated_row() + 1
        if start < DATA_START_ROW:
            raise SheetError(f"computed append start {start} is impossible")
        rng = f"{TAB}!A{start}:{LAST_COL}{start + len(new_rows) - 1}"
        self._write([(rng, new_rows)])
        landed = [pad_row(r) for r in self._values(rng)]
        if len(landed) != len(new_rows) or not all(
                rows_equal(e, a) for e, a in zip(new_rows, landed)):
            raise SheetError(f"read-back mismatch on {rng}; inspect before retrying")
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
        compared before and after; a sort that lost or duplicated a row
        raises.
        """
        grid = self.read_grid()
        rows = list(enumerate(grid, start=1))
        populated = [n for n, cells in rows if n >= DATA_START_ROW
                     and any(c.strip() for c in cells[:3])]
        if not populated:
            return {"rows": 0, "blank_rows_removed": 0, "verified": True}
        last = max(populated)
        blanks = [n for n in range(DATA_START_ROW, last + 1) if n not in set(populated)]
        removed = self.delete_blank_rows(blanks) if blanks else 0
        last -= removed
        before = sorted((c[COLS["Verdict"]], c[COLS["Business"]], c[COLS["Role"]])
                        for n, c in rows if n in set(populated))
        self._post(":batchUpdate", {"requests": [{"sortRange": {
            "range": {"sheetId": self.sheet_id,
                      "startRowIndex": DATA_START_ROW - 1, "endRowIndex": last,
                      "startColumnIndex": 0, "endColumnIndex": N_COLS},
            "sortSpecs": [{"dimensionIndex": COLS["Score"], "sortOrder": "DESCENDING"}],
        }}]})
        after_grid = self.read_grid()
        after_rows = [c for n, c in enumerate(after_grid, start=1)
                      if DATA_START_ROW <= n <= last]
        after = sorted((c[COLS["Verdict"]], c[COLS["Business"]], c[COLS["Role"]])
                       for c in after_rows)
        if after != before:
            raise SheetError("sort read-back changed the set of rows; inspect the "
                             "sheet before any further write")
        scores = []
        for c in after_rows:
            if not any(x.strip() for x in c[:3]):
                raise SheetError("a blank row remains inside the data block after sorting")
            try:
                scores.append(int(c[COLS["Score"]]))
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

    def migrate_columns(self, *, tab: str, sheet_id: int,
                        trailing: tuple[str, ...] = (), apply: bool = False) -> dict:
        """Take a tab from LEGACY_HEADERS to HEADERS in place.

        Two structural requests, then the header, then the fill for the new
        columns (no cell is ever blank). Column A is never part of a request,
        so the dropdown and every column A format survive; Sheets shifts the
        conditional-format and banded ranges with the grid. Verified by
        reading back every row's identity and every Job Link URL. Idempotent:
        a tab already on HEADERS reports noop.
        """
        idx = migration_indexes()
        target = HEADERS + list(trailing)
        width = max(len(target), len(LEGACY_HEADERS) + len(trailing)) + 3
        raw = self._values(f"{tab}!A1:{col_letter(width - 1)}2000")
        header = pad_row(raw[0] if raw else [], width)
        state = column_state(header, trailing)
        data = [pad_row(r, width) for r in raw[1:]]
        data_rows = [i for i, r in enumerate(data, start=2)
                     if any(str(c).strip() for c in r[:3])]
        report = {"tab": tab, "state": state, "data_rows": len(data_rows),
                  "plan": [], "applied": False, "noop": False}
        if state == "unknown":
            diffs = [(col_letter(i), header[i], (LEGACY_HEADERS + list(trailing) + [""] * width)[i])
                     for i in range(width) if header[i] != (LEGACY_HEADERS + list(trailing) + [""] * width)[i]]
            raise SheetError(f"{tab} header is neither the legacy nor the current "
                             f"layout; first diffs {diffs[:4]}; not touching it")
        if state == "done":
            report["noop"] = True
            return report

        # the Job Link column in the current state, for the read-back check
        link_before = {"legacy": LEGACY_HEADERS.index("Job Link"),
                       "moved": idx["move_to"] + 1,
                       "inserted": COLS["Job Link"]}[state]
        links_before = {n: parse_hyperlink(data[n - 2][link_before]) for n in data_rows}
        ident_before = [(data[n - 2][0], data[n - 2][1], data[n - 2][2]) for n in data_rows]

        requests_body: list[dict] = []
        if state == "legacy":
            requests_body.append({"moveDimension": {
                "source": {"sheetId": sheet_id, "dimension": "COLUMNS",
                           "startIndex": idx["move_from"], "endIndex": idx["move_from"] + 1},
                "destinationIndex": idx["move_to"]}})
            report["plan"].append(f"move column {col_letter(idx['move_from'])} (JD Snippet) "
                                  f"to {col_letter(idx['move_to'])}")
        if state in ("legacy", "moved"):
            requests_body.append({"insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": idx["insert_at"],
                          "endIndex": idx["insert_at"] + idx["insert_count"]},
                "inheritFromBefore": True}})
            report["plan"].append(f"insert {idx['insert_count']} columns at "
                                  f"{col_letter(idx['insert_at'])} for {idx['new_names']}")
        report["plan"].append(f"write header row: {len(target)} names")
        report["plan"].append(f"fill {DEFAULTS_BY_NAME['Warm Path']!r} / "
                              f"{DEFAULTS_BY_NAME['Path Evidence']!r} on {len(data_rows)} rows")
        meta_before = self._sheet_meta(sheet_id)
        report["cf_rules"] = len(meta_before.get("conditionalFormats", []))
        report["bands"] = len(meta_before.get("bandedRanges", []))
        report["dropdown_found"] = self._validation_at(DATA_START_ROW, tab, sheet_id) is not None
        if not apply:
            return report

        if requests_body:
            self._post(":batchUpdate", {"requests": requests_body})
        last_col = col_letter(len(target) - 1)
        self._write([(f"{tab}!A1:{last_col}1", [target])])
        # only fill where both cells are blank, so a rerun after a partial
        # apply never overwrites a warm path already written
        current = [pad_row(r, len(target)) for r in
                   self._values(f"{tab}!A1:{last_col}2000")]
        fills = []
        for n in data_rows:
            row = current[n - 1] if n - 1 < len(current) else [""] * len(target)
            if not row[COLS["Warm Path"]].strip() and not row[COLS["Path Evidence"]].strip():
                fills.append((f"{tab}!{letter('Warm Path')}{n}:{letter('Path Evidence')}{n}",
                              [[WARM_NONE, EVIDENCE_NONE]]))
        if fills:
            self._write(fills)

        after = [pad_row(r, len(target)) for r in self._values(f"{tab}!A1:{last_col}2000")]
        if not after or after[0] != target:
            raise SheetError(f"{tab} header read-back is {after[0][:6] if after else []}..., "
                             f"expected HEADERS; inspect before any other write")
        after_rows = [i for i, r in enumerate(after[1:], start=2)
                      if any(str(c).strip() for c in r[:3])]
        if after_rows != data_rows:
            raise SheetError(f"{tab} data row set changed during migration: "
                             f"{len(data_rows)} before, {len(after_rows)} after")
        for n, ident in zip(data_rows, ident_before):
            got = after[n - 1]
            if (got[0], got[1], got[2]) != ident:
                raise SheetError(f"{tab} row {n} identity changed: {ident} -> {got[:3]}")
            if links_before[n] != parse_hyperlink(got[COLS["Job Link"]]):
                raise SheetError(f"{tab} row {n} Job Link did not land at "
                                 f"{letter('Job Link')}")
            mb = got[COLS["Materials Built"]]
            if mb and mb != "n/a" and not DATE_RE.match(mb):
                raise SheetError(f"{tab} row {n} Materials Built reads {mb!r} at "
                                 f"{letter('Materials Built')}")
            if not got[COLS["Warm Path"]].strip() or not got[COLS["Path Evidence"]].strip():
                raise SheetError(f"{tab} row {n} warm path cells are blank after fill")
        meta_after = self._sheet_meta(sheet_id)
        b_rules = [r.get("booleanRule", {}).get("condition") for r in meta_before.get("conditionalFormats", [])]
        a_rules = [r.get("booleanRule", {}).get("condition") for r in meta_after.get("conditionalFormats", [])]
        if b_rules != a_rules:
            raise SheetError(f"{tab} conditional format rules changed during migration")
        if len(meta_before.get("bandedRanges", [])) != len(meta_after.get("bandedRanges", [])):
            raise SheetError(f"{tab} banded range count changed during migration")
        if report["dropdown_found"] and self._validation_at(DATA_START_ROW, tab, sheet_id) is None:
            raise SheetError(f"{tab} column A dropdown disappeared during migration")
        report["applied"] = True
        report["verified"] = True
        return report

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
