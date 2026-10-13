"""The shape of the tab he judges in. The whole point of this module is that a
row can be read without scrolling, so the tests assert the numbers that make
that true rather than just that the code runs."""
import pytest

from hunter import layout
from hunter.sheet import (AI_SCORE, AI_WHY, COLS, DATA_START_ROW, HEADERS,
                          KRISH_REACTION, KRISH_SCORE, N_COLS, SheetError)

EM_DASH = chr(0x2014)

SHEET_ID = 708873267


def _reqs(row_count=200):
    return layout.plan(SHEET_ID, row_count)


# ---------- the contract ----------

def test_every_column_has_a_width():
    assert set(layout.WIDTHS) == set(HEADERS)


def test_visible_and_wrapped_columns_all_exist():
    for name in layout.VISIBLE + layout.WRAPPED:
        assert name in COLS, name


def test_a_column_missing_from_widths_is_refused():
    saved = layout.WIDTHS.pop(AI_SCORE)
    try:
        with pytest.raises(SheetError, match="column contract"):
            layout.plan(SHEET_ID, 200)
    finally:
        layout.WIDTHS[AI_SCORE] = saved


def test_the_judging_columns_are_visible():
    # what a verdict actually depends on
    for name in ("Verdict", "Business", "Role", "JD Snippet", "Job Link",
                 AI_SCORE, AI_WHY, "Location", "Comp"):
        assert name in layout.VISIBLE, name


def test_his_two_columns_are_visible_at_his_widths():
    """Krish inserted them on 2026-10-11 at these widths. apply_layout resets
    every width on every run, so anything else would undo his drag hourly."""
    assert layout.WIDTHS[KRISH_SCORE] == 46 and layout.WIDTHS[KRISH_REACTION] == 77
    assert layout.WIDTHS[AI_SCORE] == 46 and layout.WIDTHS[AI_WHY] == 230
    assert KRISH_SCORE in layout.VISIBLE and KRISH_REACTION in layout.VISIBLE
    assert KRISH_REACTION in layout.WRAPPED
    order = [h for h in HEADERS if h in layout.VISIBLE]
    assert list(layout.VISIBLE) == order, "visible columns read in sheet order"


def test_the_default_only_columns_are_hidden():
    # measured 2026-09-19: these read the same canon default on all 156
    # unverdicted rows, so they are pure scroll
    for name in ("Sector", "Stage", "Next Action", "Application Format",
                 "Attachment Style", "Additional Questions", "Form Complexity",
                 "Autonomy Score", "Form Audit Date", "Materials Built"):
        assert name not in layout.VISIBLE, name


def test_it_fits_one_screen():
    # A 1440 laptop shows about 1300px of grid once the browser chrome is off.
    # The bounds were 1560 and 1310 until 2026-10-13. His score and his
    # reaction, 123px together, sit beside hunter's score and argument, and
    # they are worth the extra width: the gap between his number and hunter's
    # is what he reads the row for. The bounds are the measured widths, so
    # any further column has to argue for its own pixels.
    assert layout.VISIBLE_WIDTH <= 1662, layout.VISIBLE_WIDTH
    through_comp = sum(layout.WIDTHS[n] for n in layout.VISIBLE
                       [:layout.VISIBLE.index("Comp") + 1])
    assert through_comp <= 1432, through_comp


# ---------- the requests ----------

def test_every_column_gets_a_width_and_a_hidden_flag():
    dims = [r for r in _reqs() if "updateDimensionProperties" in r
            and r["updateDimensionProperties"]["range"]["dimension"] == "COLUMNS"]
    assert len(dims) == N_COLS
    for r in dims:
        body = r["updateDimensionProperties"]
        idx = body["range"]["startIndex"]
        name = HEADERS[idx]
        assert body["properties"]["pixelSize"] == layout.WIDTHS[name]
        assert body["properties"]["hiddenByUser"] == (name not in layout.VISIBLE)


def test_identity_columns_freeze():
    frozen = [r for r in _reqs() if "updateSheetProperties" in r]
    assert len(frozen) == 1
    grid = frozen[0]["updateSheetProperties"]["properties"]["gridProperties"]
    assert grid["frozenColumnCount"] == 3
    assert grid["frozenRowCount"] == 2
    assert layout.VISIBLE[:3] == ("Verdict", "Business", "Role")


def test_data_rows_get_a_uniform_height():
    rows = [r for r in _reqs(200) if "updateDimensionProperties" in r
            and r["updateDimensionProperties"]["range"]["dimension"] == "ROWS"]
    heights = {r["updateDimensionProperties"]["properties"]["pixelSize"] for r in rows}
    assert layout.ROW_HEIGHT in heights


def test_no_row_height_request_on_an_empty_tab():
    rows = [r for r in layout.plan(SHEET_ID, 2) if "updateDimensionProperties" in r
            and r["updateDimensionProperties"]["range"]["dimension"] == "ROWS"]
    # only the header row request survives
    assert len(rows) == 1
    assert rows[0]["updateDimensionProperties"]["range"]["startIndex"] == 0


def test_wrapped_columns_are_wrapped_and_the_rest_clipped():
    wraps = {}
    for r in _reqs():
        cell = r.get("repeatCell")
        if not cell:
            continue
        strat = cell["cell"]["userEnteredFormat"].get("wrapStrategy")
        if strat is None:
            continue
        rng = cell["range"]
        if rng.get("startRowIndex") == 0:
            continue  # the header block
        width = rng["endColumnIndex"] - rng["startColumnIndex"]
        if width == N_COLS:
            assert strat == "CLIP"
        else:
            wraps[HEADERS[rng["startColumnIndex"]]] = strat
    for name in layout.WRAPPED:
        assert wraps.get(name) == "WRAP", name


def test_the_score_column_is_formatted_as_a_number():
    for r in _reqs():
        cell = r.get("repeatCell")
        if not cell:
            continue
        if cell["range"].get("startColumnIndex") != COLS[AI_SCORE]:
            continue
        fmt = cell["cell"]["userEnteredFormat"]
        if "numberFormat" in fmt:
            assert fmt["numberFormat"]["type"] == "NUMBER"
            return
    raise AssertionError("the score column never gets a number format, so a "
                         "score written as text would sort wrongly again")


def test_the_verdict_column_is_the_one_that_looks_editable():
    bolds = [r["repeatCell"] for r in _reqs() if "repeatCell" in r
             and r["repeatCell"]["range"].get("startColumnIndex") == COLS["Verdict"]
             and r["repeatCell"]["range"].get("endColumnIndex") == COLS["Verdict"] + 1]
    assert bolds and bolds[0]["cell"]["userEnteredFormat"]["textFormat"]["bold"]


def test_describe_says_something_readable():
    lines = layout.describe()
    assert any("visible" in line for line in lines)
    assert all(EM_DASH not in line for line in lines)


def test_his_score_keeps_its_own_look():
    """The number format, the bold and the centring are for AI Score alone,
    and no data-row format request touches Krish Score at all: how his
    number looks is his."""
    k = COLS[KRISH_SCORE]
    data_blocks = [r["repeatCell"] for r in _reqs() if "repeatCell" in r
                   and r["repeatCell"]["range"].get("startRowIndex") == DATA_START_ROW - 1]
    assert data_blocks
    for cell in data_blocks:
        rng = cell["range"]
        assert not rng["startColumnIndex"] <= k < rng["endColumnIndex"], rng
        if "numberFormat" in cell["cell"]["userEnteredFormat"]:
            assert (rng["startColumnIndex"], rng["endColumnIndex"]) == (
                COLS[AI_SCORE], COLS[AI_SCORE] + 1)
    covered = set()
    for cell in data_blocks:
        covered |= set(range(cell["range"]["startColumnIndex"],
                             cell["range"]["endColumnIndex"]))
    assert covered == set(range(N_COLS)) - {k}, "every other column still clipped"
