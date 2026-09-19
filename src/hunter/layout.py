"""How the Pipeline tab looks, as code rather than as something Krish drags.

Krish 2026-09-19: "ensure everything in the sheet is optimal visually for me
without scrolling that I can make quick judgements and approvals on each row".

Measured before this existed: of the thirty columns, nine carried any signal on
the 156 unverdicted rows and the other twenty-one held the same canon default on
every single one. Role was 366px wide, JD Snippet 420, Why It Fits 380, and the
row ran well past two screens. Judging a row meant scrolling right past columns
that never say anything.

So the layout has one job: put everything a verdict depends on inside one
screen, and hide the rest rather than deleting it. Hiding is reversible and
keeps the 30-column contract canon 9.13 fixes; deleting would break it.

Three rules the numbers follow:

  the identity columns freeze, so Verdict, Business and Role never leave the
  screen however far right he scrolls;

  every visible column is one he can act on: what the job is, what it pays,
  where it is, what hunter thinks, who he knows there, and whether the
  materials exist;

  rows are a fixed height with wrapped text, so a long JD Snippet cannot push
  the next role off the screen. Uniform rows are what make a column scannable.

Idempotent by construction: it sets absolute widths and an explicit hidden
flag on every one of the thirty columns, so running it twice changes nothing
and running it after Krish has dragged a column puts it back.
"""
from __future__ import annotations

from .sheet import COLS, DATA_START_ROW, HEADERS, N_COLS, Sheet, SheetError

# name -> pixel width. Every column in HEADERS appears exactly once, so a new
# column added to the contract fails the guard below instead of silently
# inheriting a default width.
WIDTHS: dict[str, int] = {
    "Verdict": 104,
    "Business": 116,
    "Role": 200,
    "JD Snippet": 250,
    "Job Link": 56,
    "CV Doc": 56,
    "Cover Letter Doc": 56,
    "CV PDF": 52,
    "CL PDF": 52,
    "Score": 46,
    "Why It Fits": 230,
    "Sector": 96,
    "Stage": 90,
    "Location": 90,
    "Comp": 105,
    "Warm Path": 120,
    "Path Evidence": 220,
    "Package Status": 110,
    "Source": 96,
    "Application Status": 96,
    "Applied Date": 90,
    "Next Action": 110,
    "Application Format": 110,
    "Attachment Style": 96,
    "Additional Questions": 110,
    "Form Complexity": 96,
    "Autonomy Score": 96,
    "Form Audit Date": 96,
    "JD URL Verified": 96,
    "Materials Built": 96,
}

# What a verdict actually depends on. Everything else is hidden.
#
# CV PDF and CL PDF are hidden even though they are built, because the Doc
# links next to them open the same material and are the ones he edits. Path
# Evidence is hidden because Warm Path already carries the person and the
# evidence is a paragraph; it is in the approval email instead.
VISIBLE: tuple[str, ...] = (
    "Verdict", "Business", "Role", "JD Snippet", "Job Link", "CV Doc",
    "Cover Letter Doc", "Score", "Why It Fits", "Location", "Comp",
    "Warm Path", "Package Status",
)

# Wrapped, so the whole sentence is there when the row is tall enough to show
# it, clipped rather than overflowing when it is not.
WRAPPED: tuple[str, ...] = ("JD Snippet", "Why It Fits", "Role", "Comp",
                            "Warm Path", "Package Status")

FROZEN_COLUMNS = 3          # Verdict, Business, Role stay put
FROZEN_ROWS = 2             # header plus the intentional blank
ROW_HEIGHT = 62             # three lines of 10pt, uniform down the column
HEADER_HEIGHT = 34

VISIBLE_WIDTH = sum(WIDTHS[n] for n in VISIBLE)


def _guard() -> None:
    missing = [h for h in HEADERS if h not in WIDTHS]
    extra = [k for k in WIDTHS if k not in COLS]
    if missing or extra:
        raise SheetError(
            f"layout disagrees with the column contract: missing {missing}, "
            f"unknown {extra}. HEADERS is the map; update WIDTHS with it.")
    unknown = [n for n in VISIBLE + WRAPPED if n not in COLS]
    if unknown:
        raise SheetError(f"layout names columns that do not exist: {unknown}")


def plan(sheet_id: int, row_count: int) -> list[dict]:
    """The batchUpdate requests that put the tab in its canonical shape.

    Pure: takes no network and returns the request list, so a test can assert
    the whole layout without a spreadsheet.
    """
    _guard()
    reqs: list[dict] = []

    for name in HEADERS:
        idx = COLS[name]
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": idx, "endIndex": idx + 1},
            "properties": {"pixelSize": WIDTHS[name],
                           "hiddenByUser": name not in VISIBLE},
            "fields": "pixelSize,hiddenByUser"}})

    # Uniform data rows. A fixed height is what makes the column scannable:
    # without it one 400 character snippet owns half the screen.
    if row_count > DATA_START_ROW:
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": DATA_START_ROW - 1, "endIndex": row_count},
            "properties": {"pixelSize": ROW_HEIGHT},
            "fields": "pixelSize"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                  "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": HEADER_HEIGHT},
        "fields": "pixelSize"}})

    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id,
                       "gridProperties": {"frozenRowCount": FROZEN_ROWS,
                                          "frozenColumnCount": FROZEN_COLUMNS}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}})

    # Text behaviour across the whole data block: top aligned so wrapped cells
    # start at the same line, clipped by default so a long cell cannot spill
    # over its neighbour.
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": DATA_START_ROW - 1,
                  "endRowIndex": max(row_count, DATA_START_ROW),
                  "startColumnIndex": 0, "endColumnIndex": N_COLS},
        "cell": {"userEnteredFormat": {"verticalAlignment": "TOP",
                                       "wrapStrategy": "CLIP",
                                       "textFormat": {"fontSize": 10}}},
        "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)"}})
    for name in WRAPPED:
        idx = COLS[name]
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": DATA_START_ROW - 1,
                      "endRowIndex": max(row_count, DATA_START_ROW),
                      "startColumnIndex": idx, "endColumnIndex": idx + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy"}})

    # The one column he edits, made to look like the one column he edits.
    verdict = COLS["Verdict"]
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": DATA_START_ROW - 1,
                  "endRowIndex": max(row_count, DATA_START_ROW),
                  "startColumnIndex": verdict, "endColumnIndex": verdict + 1},
        "cell": {"userEnteredFormat": {
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
            "textFormat": {"bold": True, "fontSize": 10}}},
        "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,"
                  "wrapStrategy,textFormat)"}})

    score = COLS["Score"]
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": DATA_START_ROW - 1,
                  "endRowIndex": max(row_count, DATA_START_ROW),
                  "startColumnIndex": score, "endColumnIndex": score + 1},
        "cell": {"userEnteredFormat": {
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "numberFormat": {"type": "NUMBER", "pattern": "0"},
            "textFormat": {"bold": True, "fontSize": 10}}},
        "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,"
                  "numberFormat,textFormat)"}})

    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": N_COLS},
        "cell": {"userEnteredFormat": {
            "verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
            "textFormat": {"bold": True, "fontSize": 10}}},
        "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat)"}})
    return reqs


def current(sheet: Sheet, sheet_id: int) -> dict:
    """What the tab looks like now, for the before and after report.

    columnMetadata is not in the field mask Sheet._sheet_meta uses, so this
    asks for it directly rather than widening a mask every other caller pays
    for.
    """
    data = sheet._get("", {"fields": "sheets(properties(sheetId,gridProperties("
                                     "rowCount,frozenRowCount,frozenColumnCount)),"
                                     "data(columnMetadata(pixelSize,hiddenByUser)))"})
    meta = next((s for s in data.get("sheets", [])
                 if s["properties"]["sheetId"] == sheet_id), None)
    if meta is None:
        raise SheetError(f"sheetId {sheet_id} not found in workbook")
    grid = meta["properties"]["gridProperties"]
    blocks = meta.get("data") or [{}]
    dims = blocks[0].get("columnMetadata", [])
    widths = [d.get("pixelSize", 0) for d in dims][:N_COLS]
    hidden = [bool(d.get("hiddenByUser")) for d in dims][:N_COLS]
    return {"row_count": grid.get("rowCount", 0),
            "frozen_rows": grid.get("frozenRowCount", 0),
            "frozen_columns": grid.get("frozenColumnCount", 0),
            "widths": widths, "hidden": hidden,
            "visible_width": sum(w for w, h in zip(widths, hidden) if not h)}


def apply_layout(sheet: Sheet, *, sheet_id: int | None = None) -> dict:
    """Put the tab in its canonical shape and verify the read back.

    Returns the before and after so a run summary can say what moved. A tab
    already in shape reports noop, which is the normal case on every run after
    the first.
    """
    sid = sheet.sheet_id if sheet_id is None else sheet_id
    before = current(sheet, sid)
    reqs = plan(sid, before["row_count"])
    sheet._post(":batchUpdate", {"requests": reqs})
    after = current(sheet, sid)

    want_hidden = [h not in VISIBLE for h in HEADERS]
    want_widths = [WIDTHS[h] for h in HEADERS]
    problems = []
    if after["hidden"] and after["hidden"] != want_hidden:
        wrong = [HEADERS[i] for i, (a, b) in
                 enumerate(zip(after["hidden"], want_hidden)) if a != b]
        problems.append(f"hidden flags did not take on {wrong}")
    if after["widths"] and after["widths"] != want_widths:
        wrong = [HEADERS[i] for i, (a, b) in
                 enumerate(zip(after["widths"], want_widths)) if a != b]
        problems.append(f"widths did not take on {wrong}")
    if after["frozen_columns"] != FROZEN_COLUMNS:
        problems.append(f"frozen columns read {after['frozen_columns']}")
    if problems:
        raise SheetError("layout did not verify: " + "; ".join(problems))

    return {"noop": before["hidden"] == want_hidden
                    and before["widths"] == want_widths
                    and before["frozen_columns"] == FROZEN_COLUMNS,
            "visible": len(VISIBLE), "hidden": N_COLS - len(VISIBLE),
            "width_before": before["visible_width"],
            "width_after": after["visible_width"],
            "frozen": f"{FROZEN_ROWS} rows, {FROZEN_COLUMNS} columns",
            "verified": True}


def describe() -> list[str]:
    """Plain lines for the run summary and the review email."""
    return [f"visible: {', '.join(VISIBLE)}",
            f"hidden: {N_COLS - len(VISIBLE)} columns that read the same canon "
            f"default on every row",
            f"one screen is {VISIBLE_WIDTH}px wide, with "
            f"{FROZEN_COLUMNS} frozen columns"]
