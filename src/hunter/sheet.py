"""Pipeline sheet layer. Canon 9.13 is the contract:

- Row 1 is the header (asserted against canon's 28 exact names), row 2 is
  intentionally blank, data starts at row 3.
- Columns D to H hold =HYPERLINK("url","label") formulas. Reads use
  valueRenderOption=FORMULA; a rendered read loses every URL.
- Writes never use values.append with INSERT_ROWS (rows created that way
  inherit nothing). The writer finds the last populated row and batchUpdates
  an explicit range with USER_ENTERED, validates every cell before writing,
  and reads the range back to assert what landed.
- Column A belongs to Krish. It is read, never overwritten. Existing-row
  updates touch only E to H, O and AB.
- No cell is ever left blank; fill defaults are canon 9.13's, verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import requests

from . import config

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
TAB = "Pipeline"
N_COLS = 28
DATA_START_ROW = 3  # 1-based; row 2 is intentionally blank

HYPERLINK_RE = re.compile(r'^=HYPERLINK\("((?:[^"\\]|\\.)+)"\s*[,;]\s*"((?:[^"\\]|\\.)*)"\)$')
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# canon 9.13 fill defaults; keys are 0-based column indexes
DEFAULTS = {
    0: "New", 4: "Not built", 5: "Not built", 6: "Not built", 7: "Not built",
    9: "Not assessed", 10: "Not stated", 11: "Not stated", 12: "Not stated",
    13: "Not disclosed", 14: "Not started", 15: "Unknown", 16: "Not applied",
    17: "n/a", 18: "Review and score", 19: "Unknown", 20: "CV+CL",
    21: "standard", 22: "Unknown", 23: "Unknown", 24: "n/a",
    26: "Not captured", 27: "n/a",
}
DATE_COLS = {17, 24, 27}   # R, Y, AB: n/a or YYYY-MM-DD
O_BUILT_DIRECT = "Materials staged - ready to apply"
O_BUILT_BRIDGE = "Materials staged - bridge first"


class SheetError(RuntimeError):
    pass


@dataclass
class SheetRow:
    row_number: int            # 1-based sheet row
    cells: list[str]           # 28 raw values, formulas as text
    verdict: str
    company: str
    role: str
    jd_url: str | None
    package_urls: dict[str, str | None] = field(default_factory=dict)
    archived: bool = False   # lives on the Applied tab, decided, read only


def hyperlink(url: str, label: str) -> str:
    if "http" not in url:
        raise SheetError(f"refusing to build a HYPERLINK without a URL: {url!r}")
    escaped = url.replace('"', '""')
    return f'=HYPERLINK("{escaped}","{label}")'


def parse_hyperlink(cell: str) -> tuple[str, str] | None:
    m = HYPERLINK_RE.match(cell.strip()) if cell else None
    return (m.group(1).replace('""', '"'), m.group(2)) if m else None


def pad_row(row: list) -> list[str]:
    """Normalize an API row to 28 strings. USER_ENTERED turns TRUE/FALSE text
    into booleans, which read back as JSON booleans; restore sheet casing."""
    cells = []
    for v in row:
        if v is None:
            cells.append("")
        elif isinstance(v, bool):
            cells.append("TRUE" if v else "FALSE")
        else:
            cells.append(str(v))
    return (cells + [""] * N_COLS)[:N_COLS]


def make_row(*, company: str, role: str, jd_url: str, score: int,
             why_it_fits: str = "", sector: str = "", stage: str = "",
             location: str = "", comp: str = "", source: str = "",
             jd_verified: bool = True, jd_snippet: str = "") -> list[str]:
    cells = [DEFAULTS.get(i, "") for i in range(N_COLS)]
    cells[1] = company
    cells[2] = role
    cells[3] = hyperlink(jd_url, "JD")
    cells[8] = str(score)
    if why_it_fits.strip():
        cells[9] = why_it_fits.strip()
    for idx, value in ((10, sector), (11, stage), (12, location), (13, comp), (15, source)):
        if value.strip():
            cells[idx] = value.strip()
    cells[25] = "TRUE" if jd_verified else "FALSE"
    if jd_snippet.strip():
        cells[26] = jd_snippet.strip()[:500]
    return cells


def validate_row(cells: list[str], *, is_append: bool = True) -> list[str]:
    fails: list[str] = []
    if len(cells) != N_COLS:
        return [f"expected {N_COLS} cells, got {len(cells)}"]
    for i, c in enumerate(cells):
        if not str(c).strip():
            fails.append(f"blank cell at column {chr(65 + i) if i < 26 else 'A' + chr(39 + i)}")
    if is_append and cells[0] != "New":
        fails.append("column A must be the literal New on appends")
    parsed = parse_hyperlink(cells[3])
    if not parsed or not parsed[0].startswith("http"):
        fails.append('column D must be =HYPERLINK("http...","JD")')
    try:
        score = int(cells[8])
        if not 1 <= score <= 10:
            fails.append("column I score out of 1..10")
    except (ValueError, TypeError):
        fails.append("column I score is not an integer")
    for i in DATE_COLS:
        if cells[i] != "n/a" and not DATE_RE.match(cells[i]):
            fails.append(f"column {i + 1} must be n/a or YYYY-MM-DD, got {cells[i]!r}")
    if cells[25] not in ("TRUE", "FALSE"):
        fails.append("column Z must be TRUE or FALSE in that casing")
    for i, c in enumerate(cells):
        if "\u2014" in str(c):
            fails.append(f"em dash in column index {i}")
    return fails


def rows_equal(expected: list[str], actual: list[str]) -> bool:
    """Structural comparison tolerant of Sheets normalization: HYPERLINKs
    compare by (url, label); everything else compares as trimmed text."""
    if len(pad_row(actual)) != len(expected):
        return False
    actual = pad_row(actual)
    for e, a in zip(expected, actual):
        pe, pa = parse_hyperlink(e), parse_hyperlink(a)
        if pe or pa:
            if pe != pa:
                return False
        elif str(e).strip() != str(a).strip():
            return False
    return True


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

    # ---------- reading ----------

    def read_grid(self) -> list[list[str]]:
        data = self._get(f"/values/{TAB}!A1:AB",
                         {"valueRenderOption": "FORMULA"})
        return [pad_row(r) for r in data.get("values", [])]

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
        if min(targets) < 3:
            raise SheetError(f"refusing to delete row {min(targets)}: rows 1 and 2 "
                             f"are the header and the intentional blank")
        grid = self._get("/values/Pipeline!A1:A2000").get("values", [])
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
        after = self._get("/values/Pipeline!A1:A2000").get("values", [])
        if len(after) != len(grid) - len(targets):
            raise SheetError(f"read-back mismatch after delete: expected "
                             f"{len(grid) - len(targets)} rows, found {len(after)}")
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
        # The archive tab ships 26 columns wide. Writing 29 into it silently
        # drops the overflow (that is how the first archived row lost every
        # column but A), so widen the grid before writing anything.
        meta = self._get("", {"fields": "sheets(properties(sheetId,gridProperties(columnCount)))"})
        cols = next((sh["properties"]["gridProperties"]["columnCount"]
                     for sh in meta["sheets"]
                     if sh["properties"]["sheetId"] == archive_sheet_id), 0)
        reqs = []
        if cols < N_COLS + 1:
            reqs.append({"appendDimension": {
                "sheetId": archive_sheet_id, "dimension": "COLUMNS",
                "length": (N_COLS + 1) - cols}})
        # The tab carried a merged banner across A1:H2 from its previous life.
        # A merged range accepts only its top-left cell, so a 29-column write
        # lands column A and silently drops the other 28. Unmerge first.
        reqs.append({"unmergeCells": {"range": {
            "sheetId": archive_sheet_id, "startRowIndex": 0,
            "startColumnIndex": 0, "endColumnIndex": N_COLS + 1}}})
        self._post(":batchUpdate", {"requests": reqs})
        existing = self._get(f"/values/{archive_tab}!A1:AC2000",
                             {"valueRenderOption": "FORMULA"}).get("values", [])
        header_written = bool(existing) and existing[0][:1] == [headers[0]]
        payload = []
        if not header_written:
            payload.append(headers + ["Archived On"])
        stamp = __import__("datetime").date.today().isoformat()
        for r in rows:
            payload.append(list(r.cells) + [stamp])
        start = (len(existing) if header_written else 0) + 1
        end = start + len(payload) - 1
        rng = f"{archive_tab}!A{start}:AC{end}"
        self._post("/values:batchUpdate", {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": rng, "values": payload}]})
        back = self._get(f"/values/{rng}", {"valueRenderOption": "FORMULA"}).get("values", [])
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
        data = [{"range": f"{TAB}!A{rn}", "values": [[text]]}
                for rn, text in sorted(mapping.items())]
        self._post("/values:batchUpdate",
                   {"valueInputOption": "USER_ENTERED", "data": data})
        for rn, text in sorted(mapping.items()):
            back = self._get(f"/values/{TAB}!A{rn}").get("values", [[""]])
            got = (back[0][0] if back and back[0] else "").strip()
            if got != text.strip():
                raise SheetError(f"column A read-back on row {rn} reads {got!r}, "
                                 f"expected {text!r}")
        return len(mapping)

    def clear_package_links(self, row_numbers: list[int]) -> int:
        """Set columns E to H back to "Not built" and read them back.

        The four package columns are the only ones this touches; column A is
        Krish's and columns I and J belong to the assessment.
        """
        if not row_numbers:
            return 0
        blank = [["Not built"] * 4]
        data = [{"range": f"{TAB}!E{rn}:H{rn}", "values": blank}
                for rn in sorted(row_numbers)]
        self._post("/values:batchUpdate",
                   {"valueInputOption": "USER_ENTERED", "data": data})
        for rn in sorted(row_numbers):
            back = self._get(f"/values/{TAB}!E{rn}:H{rn}",
                             {"valueRenderOption": "FORMULA"}).get("values", [[]])
            got = (back[0] if back else []) + [""] * 4
            if got[:4] != ["Not built"] * 4:
                raise SheetError(f"row {rn} E:H read back as {got[:4]}, "
                                 f"expected four 'Not built'")
        return len(row_numbers)

    def relink_jd_urls(self, mapping: dict[int, str]) -> int:
        """Point column D at the real ATS posting, keeping the link text.

        A row discovered through its company's board was carrying a LinkedIn
        URL that nothing could verify. Rewriting D means the next check reads
        the ATS directly instead of rediscovering the board every time.
        """
        if not mapping:
            return 0
        grid = self.read_grid()
        data = []
        for rn, url in sorted(mapping.items()):
            row = grid[rn - 1] if rn - 1 < len(grid) else []
            label = (row[2] if len(row) > 2 else "") or "Job posting"
            label = str(label).replace('"', "'")
            data.append({"range": f"{TAB}!D{rn}",
                         "values": [[f'=HYPERLINK("{url}","{label}")']]})
        self._post("/values:batchUpdate",
                   {"valueInputOption": "USER_ENTERED", "data": data})
        for rn, url in sorted(mapping.items()):
            back = self._get(f"/values/{TAB}!D{rn}",
                             {"valueRenderOption": "FORMULA"}).get("values", [[""]])
            got = (back[0][0] if back and back[0] else "")
            if url not in got:
                raise SheetError(f"row {rn} column D read back as {got[:60]!r}, "
                                 f"expected the posting URL")
        return len(mapping)

    def delete_archive_rows(self, row_numbers: list[int], *,
                            archive_sheet_id: int, expect: list[str]) -> int:
        """Remove rows from the archive tab, checking first that each still
        holds the company it did when it was chosen. Used only to undo a move
        that should never have happened; descending, like every delete here."""
        if not row_numbers:
            return 0
        grid = self._get(f"/values/{config.ARCHIVE_TAB}!A1:AC2000",
                         {"valueRenderOption": "FORMULA"}).get("values", [])
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
        return self._get(f"/values/{rng}").get("values", [])

    def update_assessment(self, row_number: int, *, score: int, why_it_fits: str) -> None:
        """Rewrite columns I and J only. Column A stays Krish's, and the
        package columns stay the build path's; this writer exists so a
        re-gate can correct a score and its rationale without touching
        anything else on the row."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write row {row_number}: header rows")
        if not 1 <= int(score) <= 10:
            raise SheetError(f"score {score} out of canon range 1..10")
        why = (why_it_fits or "").strip()
        if not why:
            raise SheetError("column J cannot be blank; use the deterministic fallback")
        if "\u2014" in why:
            raise SheetError("em dash in column J")
        self._post("/values:batchUpdate", {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": f"{TAB}!I{row_number}:J{row_number}",
                      "values": [[str(int(score)), why[:900]]]}]})
        back = self._get(f"/values/{TAB}!I{row_number}:J{row_number}").get("values", [[]])
        got = (back[0] + ["", ""])[:2] if back else ["", ""]
        if got[0] != str(int(score)):
            raise SheetError(f"read-back of row {row_number} column I gave {got[0]!r}")

    def read_archive(self) -> list[SheetRow]:
        """Archived rows are still part of "the sheet" for reconciliation.
        Leaving them out makes every archived role look missing, and
        direction 2 re-appends it to Pipeline on the next run."""
        grid = self._get(f"/values/{config.ARCHIVE_TAB}!A1:AB2000",
                         {"valueRenderOption": "FORMULA"}).get("values", [])
        rows = []
        for i, raw in enumerate(grid[1:], start=2):
            cells = pad_row(raw)
            if not cells[1].strip() and not cells[2].strip():
                continue
            parsed = parse_hyperlink(cells[3])
            rows.append(SheetRow(row_number=i, cells=cells, verdict=cells[0],
                                 company=cells[1], role=cells[2],
                                 jd_url=parsed[0] if parsed else None,
                                 archived=True))
        return rows

    def read_pipeline(self, canon_headers: list[str]) -> list[SheetRow]:
        grid = self.read_grid()
        if not grid:
            raise SheetError("Pipeline tab read returned no rows")
        if grid[0] != canon_headers:
            diffs = [(i, grid[0][i], canon_headers[i])
                     for i in range(N_COLS) if grid[0][i] != canon_headers[i]]
            raise SheetError(f"Pipeline header row disagrees with canon 9.13: {diffs[:4]}")
        if len(grid) > 1 and any(c.strip() for c in grid[1]):
            raise SheetError("Pipeline row 2 is expected to be intentionally blank")
        rows: list[SheetRow] = []
        for i, cells in enumerate(grid[DATA_START_ROW - 1:], start=DATA_START_ROW):
            if not any(c.strip() for c in cells[:3]):
                continue
            jd = parse_hyperlink(cells[3])
            packages = {}
            for col, key in ((4, "cv"), (5, "letter"), (6, "cv_pdf"), (7, "letter_pdf")):
                p = parse_hyperlink(cells[col])
                packages[key] = p[0] if p else None
            rows.append(SheetRow(
                row_number=i, cells=cells, verdict=cells[0].strip(),
                company=cells[1].strip(), role=cells[2].strip(),
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
        if start <= 40:
            # canon 9.13: data currently ends at 40 and appends land at 41+;
            # a computed start inside the historical range means the read is
            # wrong, and writing there could overwrite Krish's rows.
            if start < DATA_START_ROW:
                raise SheetError(f"computed append start {start} is impossible")
        rng = f"{TAB}!A{start}:AB{start + len(new_rows) - 1}"
        self._post("/values:batchUpdate", {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": rng, "values": new_rows}],
        })
        back = self._get(f"/values/{rng}", {"valueRenderOption": "FORMULA"})
        landed = [pad_row(r) for r in back.get("values", [])]
        if len(landed) != len(new_rows) or not all(
                rows_equal(e, a) for e, a in zip(new_rows, landed)):
            raise SheetError(f"read-back mismatch on {rng}; inspect before retrying")
        return rng

    def update_package_cells(self, row_number: int, *, cv_url: str, letter_url: str,
                             cv_pdf_url: str, letter_pdf_url: str,
                             package_status: str, built_date: str) -> None:
        """Package fields only: E to H, O, AB. Never A, never Q."""
        if row_number < DATA_START_ROW:
            raise SheetError(f"refusing to write into header rows: {row_number}")
        if package_status not in (O_BUILT_DIRECT, O_BUILT_BRIDGE):
            raise SheetError(f"unknown package status {package_status!r}; a new "
                             f"column O value needs a workflow_proposal first")
        if not DATE_RE.match(built_date):
            raise SheetError(f"built_date must be YYYY-MM-DD: {built_date!r}")
        e_to_h = [[hyperlink(cv_url, "CV"), hyperlink(letter_url, "CL"),
                   hyperlink(cv_pdf_url, "CV PDF"), hyperlink(letter_pdf_url, "CL PDF")]]
        self._post("/values:batchUpdate", {
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": f"{TAB}!E{row_number}:H{row_number}", "values": e_to_h},
                {"range": f"{TAB}!O{row_number}", "values": [[package_status]]},
                {"range": f"{TAB}!AB{row_number}", "values": [[built_date]]},
            ],
        })

    # ---------- the one-off formatting migration ----------

    def _sheet_meta(self) -> dict:
        data = self._get("", {"fields": "sheets(properties(sheetId,title,"
                                        "gridProperties(rowCount)),"
                                        "conditionalFormats,bandedRanges)"})
        for s in data.get("sheets", []):
            if s["properties"]["sheetId"] == self.sheet_id:
                return s
        raise SheetError(f"sheetId {self.sheet_id} not found in workbook")

    def _validation_at(self, row: int) -> dict | None:
        data = self._get("", {"ranges": f"{TAB}!A{row}",
                              "fields": "sheets(properties(sheetId),"
                                        "data(rowData(values(dataValidation))))"})
        for s in data.get("sheets", []):
            if s.get("properties", {}).get("sheetId") != self.sheet_id:
                continue
            for block in s.get("data", []):
                for row in block.get("rowData", []):
                    for v in row.get("values", []):
                        if "dataValidation" in v:
                            return v["dataValidation"]
        return None

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
