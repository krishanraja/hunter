"""What must always be true of the sheet. Every case here is a drift that
actually happened, or one the same class of bug would produce next."""
import pytest

from hunter import invariants, verdicts
from hunter.invariants import BROKEN, OK, WATCH
from hunter.sheet import (COLS, DEFAULTS_BY_NAME, HEADERS, N_COLS, PKG_DEAD,
                          PKG_BUILT_DIRECT, SheetRow, hyperlink, make_row)


def row(n, *, verdict="New", company="Acme", role="VP Strategy",
        url="https://jobs.ashbyhq.com/acme/d7a4ca9b-90c6-43b0-91e3-fb08fc4de8de", **over):
    cells = make_row(company=company, role=role, jd_url=url, score=7,
                     source="ats_sweep", jd_snippet="Does a thing.")
    cells[COLS["Verdict"]] = verdict
    for k, v in over.items():
        cells[COLS[k]] = v
    packages = {}
    for name, key in (("CV Doc", "cv"), ("Cover Letter Doc", "letter")):
        val = cells[COLS[name]]
        packages[key] = val if str(val).startswith("=HYPERLINK") or \
            str(val).startswith("http") else None
    return SheetRow(row_number=n, cells=cells, verdict=verdict, company=company,
                    role=role, jd_url=url, package_urls=packages)


def built(n, **kw):
    kw.setdefault("Package Status", PKG_BUILT_DIRECT)
    r = row(n, **kw)
    r.cells[COLS["CV Doc"]] = hyperlink("https://docs.google.com/document/d/aaa", "CV")
    r.cells[COLS["Cover Letter Doc"]] = hyperlink(
        "https://docs.google.com/document/d/bbb", "CL")
    r.package_urls = {"cv": "https://docs.google.com/document/d/aaa",
                      "letter": "https://docs.google.com/document/d/bbb"}
    return r


# ---------- verdict vocabulary ----------

def test_a_clean_sheet_says_so():
    rows = [row(3), row(4, verdict="Yes")]
    assert invariants.check_verdict_vocabulary(rows).state == OK


def test_a_bare_no_is_flagged_because_nothing_can_be_learned_from_it():
    f = invariants.check_verdict_vocabulary([row(3, verdict="nah")])
    assert f.state == WATCH and f.rows == [3]


def test_a_reasoned_rejection_is_not_flagged():
    rows = [row(3, verdict="Declined - business uninteresting"),
            row(4, verdict="not interested in adtech as a business")]
    assert invariants.check_verdict_vocabulary(rows).state == OK


# ---------- what belongs on the tab ----------

def test_a_decided_row_still_on_pipeline_is_broken():
    f = invariants.check_no_decided_rows([row(3, verdict="Applied"), row(4)])
    assert f.state == BROKEN and f.rows == [3]


def test_yes_and_new_both_belong():
    assert invariants.check_no_decided_rows(
        [row(3), row(4, verdict="Yes")]).state == OK


# ---------- duplicates ----------

def test_the_twin_with_his_verdict_survives():
    a = row(3, verdict="New")
    b = row(9, verdict="Yes")
    f = invariants.check_no_duplicate_postings([a, b])
    assert f.state == BROKEN and f.rows == [3], "the row he ruled on must be kept"


def test_with_no_verdict_the_built_twin_survives():
    a = row(3)
    b = built(9)
    f = invariants.check_no_duplicate_postings([a, b])
    assert f.rows == [3]


def test_with_nothing_to_choose_between_them_the_higher_row_survives():
    f = invariants.check_no_duplicate_postings([row(3), row(9), row(20)])
    assert f.rows == [9, 20]


def test_two_different_postings_are_not_duplicates():
    rows = [row(3, url="https://jobs.ashbyhq.com/acme/"
                       "d7a4ca9b-90c6-43b0-91e3-fb08fc4de8de"),
            row(4, url="https://jobs.ashbyhq.com/acme/"
                       "11111111-2222-3333-4444-555555555555")]
    from hunter.run import ats_key
    assert all(ats_key(r.jd_url) for r in rows), "both URLs must resolve to a key"
    assert invariants.check_no_duplicate_postings(rows).state == OK


def test_two_linkedin_rows_for_one_job_are_still_duplicates():
    # Neither has an ATS key, and the URLs differ only by tracking parameters.
    # 14 such rows were sitting on the sheet in pairs.
    rows = [row(3, url="https://www.linkedin.com/jobs/view/vp-at-acme-1?position=3"),
            row(4, url="https://www.linkedin.com/jobs/view/vp-at-acme-1?position=9")]
    f = invariants.check_no_duplicate_postings(rows)
    assert f.state == BROKEN and f.rows == [4]


def test_two_different_roles_at_one_company_are_not_duplicates():
    rows = [row(3, role="VP Strategy", url="https://www.linkedin.com/jobs/view/1"),
            row(4, role="Head of Partnerships",
                url="https://www.linkedin.com/jobs/view/2")]
    assert invariants.check_no_duplicate_postings(rows).state == OK


def test_the_same_title_at_two_companies_is_not_a_duplicate():
    rows = [row(3, company="Acme", url="https://www.linkedin.com/jobs/view/1"),
            row(4, company="Globex", url="https://www.linkedin.com/jobs/view/2")]
    assert invariants.check_no_duplicate_postings(rows).state == OK


# ---------- the numbers ----------

def test_a_score_written_as_text_is_broken():
    f = invariants.check_scores_numeric([row(3, Score="nine")])
    assert f.state == BROKEN and f.rows == [3]


def test_an_integer_score_is_fine():
    assert invariants.check_scores_numeric([row(3, Score="9")]).state == OK


def test_out_of_order_rows_are_broken():
    rows = [row(3, Score="6"), row(4, Score="9")]
    assert invariants.check_sorted_by_score(rows).state == BROKEN
    assert invariants.check_sorted_by_score(rows[::-1]).state == OK


# ---------- the holes ----------

def test_an_empty_cell_is_broken_and_names_its_row():
    f = invariants.check_no_empty_cells([row(3, **{"Why It Fits": ""})])
    assert f.state == BROKEN and f.rows == [3]


def test_a_full_row_passes():
    assert invariants.check_no_empty_cells([row(3)]).state == OK


# ---------- approved roles ----------

def test_a_yes_queued_for_the_next_build_is_accounted_for():
    """Not started is the canon default and a real state: select_for_build
    picks the row up next pass. Calling it broken flagged all fourteen roles
    he had just approved."""
    f = invariants.check_yes_rows_accounted_for(
        [row(3, verdict="Yes", **{"Package Status": "Not started"})])
    assert f.state == OK and "queued" in f.detail


def test_a_yes_in_a_state_hunter_does_not_understand_is_broken():
    f = invariants.check_yes_rows_accounted_for(
        [row(3, verdict="Yes", **{"Package Status": "probably fine"})])
    assert f.state == BROKEN and f.rows == [3]


def test_there_is_no_repair_that_writes_a_status_the_writer_refuses():
    assert "every Yes is accounted for" not in invariants.REPAIRS
    assert not hasattr(invariants, "repair_yes_without_status")


def test_a_yes_with_materials_is_accounted_for():
    assert invariants.check_yes_rows_accounted_for(
        [built(3, verdict="Yes")]).state == OK


def test_a_yes_with_a_stated_reason_is_accounted_for():
    assert invariants.check_yes_rows_accounted_for(
        [row(3, verdict="Yes", **{"Package Status": PKG_DEAD})]).state == OK


def test_a_new_row_is_not_expected_to_have_materials():
    assert invariants.check_yes_rows_accounted_for([row(3)]).state == OK


def test_an_approved_role_that_died_is_reported_not_archived():
    f = invariants.check_dead_yes_rows(
        [row(3, verdict="Yes", **{"Package Status": PKG_DEAD})])
    assert f.state == WATCH, "hunter never archives a role he approved"
    assert f.rows == [3]


# ---------- applied state ----------

def test_applied_in_column_a_without_the_status_is_broken():
    f = invariants.check_applied_state_agrees(
        [row(3, verdict="Applied", **{"Application Status": "Not applied"})])
    assert f.state == BROKEN and f.rows == [3]


def test_the_status_set_without_the_verdict_is_also_broken():
    f = invariants.check_applied_state_agrees(
        [row(3, verdict="New", **{"Application Status": "Applied"})])
    assert f.state == BROKEN


def test_agreement_passes():
    rows = [row(3, verdict="Applied", **{"Application Status": "Applied"}),
            row(4, verdict="New")]
    assert invariants.check_applied_state_agrees(rows).state == OK


# ---------- package vocabulary ----------

def test_an_invented_package_status_is_noticed():
    f = invariants.check_package_status_vocabulary(
        [row(3, **{"Package Status": "probably fine"})])
    assert f.state == WATCH


# ---------- the repairs ----------

class FakeSheet:
    """Only the writes the repairs make."""
    def __init__(self):
        self.writes = []
        self.verdicts_set = {}
        self.statuses = {}
        self.dropdown_rows = 0
        self.sorted_rows = 0

    def _write(self, blocks, raw=False):
        self.writes.extend(blocks)

    def set_verdicts(self, mapping):
        self.verdicts_set.update(mapping)
        return len(mapping)

    def update_package_status(self, row_number, status):
        self.statuses[row_number] = status

    def set_verdict_dropdown(self, values):
        self.dropdown_rows = 900
        return 900

    def sort_by_score(self):
        self.sorted_rows = 5
        return {"rows": 5, "blank_rows_removed": 0}


def test_repairing_a_duplicate_only_ever_writes_the_one_system_code():
    sheet = FakeSheet()
    rows = [row(3), row(9)]
    invariants.repair_duplicates(sheet, rows, [9])
    assert sheet.verdicts_set == {9: "Declined - duplicate row"}
    assert verdicts.parse(sheet.verdicts_set[9]) == ("rejection", "duplicate_row")


def test_a_duplicate_he_has_already_ruled_on_is_left_alone():
    sheet = FakeSheet()
    invariants.repair_duplicates(sheet, [row(9, verdict="Yes")], [9])
    assert sheet.verdicts_set == {}, "column A is his once he has written on it"


def test_repairing_scores_writes_integers():
    sheet = FakeSheet()
    invariants.repair_scores(sheet, [row(3, Score="7.0")], [3])
    assert sheet.writes and sheet.writes[0][1] == [[7]]


def test_repairing_holes_uses_the_canon_default():
    sheet = FakeSheet()
    invariants.repair_empty_cells(sheet, [row(3, **{"Why It Fits": ""})], [3])
    written = dict((rng, vals[0][0]) for rng, vals in sheet.writes)
    assert DEFAULTS_BY_NAME["Why It Fits"] in written.values()


def test_repairing_applied_state_follows_column_a():
    sheet = FakeSheet()
    rows = [row(3, verdict="Applied", **{"Application Status": "Not applied"}),
            row(4, verdict="New", **{"Application Status": "Applied"})]
    invariants.repair_applied_state(sheet, rows, [3, 4])
    vals = [v[0][0] for _, v in sheet.writes]
    assert vals == ["Applied", "Not applied"]


def test_every_broken_check_has_a_repair_or_is_deliberately_reported():
    reportable = {"verdict vocabulary", "approved roles still live",
                  "archive stamped", "package status vocabulary",
                  "decided rows archived", "row headroom",
                  # a status hunter does not understand is a bug upstream;
                  # overwriting it would hide the bug rather than fix it
                  "every Yes is accounted for"}
    names = {c.name for c in invariants.check_all(_StubSheet(), [], [])}
    unhandled = names - set(invariants.REPAIRS) - reportable
    assert not unhandled, f"these checks can fail with nothing to do: {unhandled}"


class _StubSheet:
    sheet_id = 1

    def _sheet_meta(self, sheet_id=None):
        return {"properties": {"gridProperties": {"rowCount": 900}}}

    def _validation_at(self, row, tab="Pipeline", sheet_id=None):
        return {"condition": {"type": "ONE_OF_LIST"}}
