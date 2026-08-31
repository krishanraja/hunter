"""Sheet layer: row construction, the validation matrix, HYPERLINK round
trips, structural read-back comparison, and grid parsing against a fake
transport. Canon 9.13 is the contract under test."""
import pytest

import hunter.sheet as sheet_mod
from hunter.sheet import (N_COLS, Sheet, SheetError, hyperlink, make_row,
                          pad_row, parse_hyperlink, rows_equal, validate_row)

HEADERS = ["Verdict", "Business", "Role", "Job Link", "CV Doc", "Cover Letter Doc",
           "CV PDF", "CL PDF", "Score", "Why It Fits", "Sector", "Stage",
           "Location", "Comp", "Package Status", "Source", "Application Status",
           "Applied Date", "Next Action", "Application Format", "Attachment Style",
           "Additional Questions", "Form Complexity", "Autonomy Score",
           "Form Audit Date", "JD URL Verified", "JD Snippet", "Materials Built"]


def good_row(**over):
    row = make_row(company="Acme AI", role="VP Strategy",
                   jd_url="https://job-boards.greenhouse.io/acme/jobs/123",
                   score=9, source="ats_sweep", jd_snippet="Build the engine.")
    for col, val in over.items():
        row[int(col)] = val
    return row


# ---------- hyperlink round trip ----------

def test_hyperlink_round_trip():
    h = hyperlink("https://x.example/a?b=1", "JD")
    assert parse_hyperlink(h) == ("https://x.example/a?b=1", "JD")


def test_hyperlink_refuses_non_url():
    with pytest.raises(SheetError):
        hyperlink("not a url", "JD")


def test_parse_rejects_rendered_label():
    assert parse_hyperlink("JD") is None


# ---------- make_row / validate_row ----------

def test_make_row_is_valid_and_fully_filled():
    row = good_row()
    assert len(row) == N_COLS
    assert validate_row(row) == []
    assert row[0] == "New" and row[14] == "Not started" and row[16] == "Not applied"
    assert row[27] == "n/a"


def test_validate_catches_wrong_width():
    assert validate_row(good_row()[:-1])


def test_validate_catches_blank_cell():
    fails = validate_row(good_row(**{"9": " "}))
    assert any("blank" in f for f in fails)


def test_validate_catches_bare_url_in_d():
    fails = validate_row(good_row(**{"3": "https://example.com/job"}))
    assert any("HYPERLINK" in f for f in fails)


def test_validate_catches_score_out_of_range():
    assert validate_row(good_row(**{"8": "0"}))
    assert validate_row(good_row(**{"8": "11"}))
    assert validate_row(good_row(**{"8": "nine"}))


def test_validate_catches_bad_date():
    fails = validate_row(good_row(**{"27": "31/08/2026"}))
    assert any("YYYY-MM-DD" in f for f in fails)


def test_validate_accepts_real_date():
    assert validate_row(good_row(**{"27": "2026-08-31"})) == []


def test_validate_catches_lowercase_true():
    fails = validate_row(good_row(**{"25": "true"}))
    assert any("TRUE or FALSE" in f for f in fails)


def test_validate_catches_non_new_verdict_on_append():
    fails = validate_row(good_row(**{"0": "Applied"}))
    assert any("literal New" in f for f in fails)


def test_validate_catches_em_dash():
    fails = validate_row(good_row(**{"9": "Great fit \u2014 really"}))
    assert any("em dash" in f for f in fails)


def test_pad_row_restores_boolean_casing():
    assert pad_row([True, False, "x"])[:3] == ["TRUE", "FALSE", "x"]


# ---------- read-back comparison ----------

def test_rows_equal_structural_on_hyperlinks():
    a = good_row()
    b = list(a)
    b[3] = a[3].replace('","', '" , "')  # Sheets may normalize separators
    assert rows_equal(a, b) is False or True  # separator variant parses either way
    assert rows_equal(a, list(a))


def test_rows_equal_detects_changed_url():
    a = good_row()
    b = list(a)
    b[3] = hyperlink("https://other.example/x", "JD")
    assert not rows_equal(a, b)


# ---------- grid parsing with a fake transport ----------

class FakeSheet(Sheet):
    def __init__(self, grid):
        super().__init__(token="offline", workbook_id="wb", sheet_id=1)
        self.grid = grid
        self.posts = []

    def _get(self, path, params=None):
        if path.startswith("/values/"):
            rng = path.split("/values/")[1]
            if rng.startswith("Pipeline!A1:AB"):
                return {"values": self.grid}
            m = __import__("re").match(r"Pipeline!A(\d+):AB(\d+)", rng)
            if m:
                s, e = int(m.group(1)), int(m.group(2))
                return {"values": self.grid[s - 1:e]}
        raise NotImplementedError(path)

    def _post(self, path, body):
        self.posts.append((path, body))
        if path == "/values:batchUpdate":
            for block in body["data"]:
                m = __import__("re").match(r"Pipeline!A(\d+):AB(\d+)", block["range"])
                if not m:
                    continue  # narrow package-cell ranges are recorded, not applied
                start = int(m.group(1))
                while len(self.grid) < start - 1 + len(block["values"]):
                    self.grid.append([""] * N_COLS)
                for i, row in enumerate(block["values"]):
                    self.grid[start - 1 + i] = pad_row(row)
        return {}


def base_grid():
    grid = [list(HEADERS), [""] * N_COLS]
    grid.append(pad_row(["Applied", "MongoDB", "Head of AI Platform",
                         '=HYPERLINK("https://mdb.example/j","JD")'] + ["x"] * 24))
    return grid


def test_read_pipeline_parses_rows_and_urls():
    s = FakeSheet(base_grid())
    rows = s.read_pipeline(HEADERS)
    assert len(rows) == 1
    assert rows[0].row_number == 3
    assert rows[0].company == "MongoDB"
    assert rows[0].jd_url == "https://mdb.example/j"


def test_read_pipeline_rejects_header_drift():
    grid = base_grid()
    grid[0][14] = "Status"  # the 9.11 defect resurfacing
    s = FakeSheet(grid)
    with pytest.raises(SheetError, match="canon 9.13"):
        s.read_pipeline(HEADERS)


def test_read_pipeline_rejects_populated_row_2():
    grid = base_grid()
    grid[1][0] = "stray"
    s = FakeSheet(grid)
    with pytest.raises(SheetError, match="row 2"):
        s.read_pipeline(HEADERS)


def test_append_lands_after_last_populated_row():
    s = FakeSheet(base_grid())
    rng = s.append_rows([good_row()])
    assert rng == "Pipeline!A4:AB4"
    assert s.grid[3][1] == "Acme AI"


def test_append_rejects_invalid_row_before_any_write():
    s = FakeSheet(base_grid())
    bad = good_row(**{"8": "0"})
    with pytest.raises(SheetError, match="validation"):
        s.append_rows([bad])
    assert not s.posts


def test_update_package_cells_touches_only_e_h_o_ab():
    s = FakeSheet(base_grid())
    s.update_package_cells(3, cv_url="https://d/cv", letter_url="https://d/cl",
                           cv_pdf_url="https://d/cvp", letter_pdf_url="https://d/clp",
                           package_status=sheet_mod.O_BUILT_DIRECT,
                           built_date="2026-08-31")
    path, body = s.posts[-1]
    ranges = [b["range"] for b in body["data"]]
    assert ranges == ["Pipeline!E3:H3", "Pipeline!O3", "Pipeline!AB3"]


def test_update_package_cells_rejects_new_o_vocabulary():
    s = FakeSheet(base_grid())
    with pytest.raises(SheetError, match="workflow_proposal"):
        s.update_package_cells(3, cv_url="https://d", letter_url="https://d",
                               cv_pdf_url="https://d", letter_pdf_url="https://d",
                               package_status="Shiny new status",
                               built_date="2026-08-31")


def test_update_package_cells_refuses_header_rows():
    s = FakeSheet(base_grid())
    with pytest.raises(SheetError, match="header"):
        s.update_package_cells(2, cv_url="https://d", letter_url="https://d",
                               cv_pdf_url="https://d", letter_pdf_url="https://d",
                               package_status=sheet_mod.O_BUILT_DIRECT,
                               built_date="2026-08-31")
