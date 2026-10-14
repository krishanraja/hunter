"""His two columns, K Krish Score and M Krish Reaction, inserted on 2026-10-11.

Hunter stopped for two days on the header guard, which was right: nothing was
written over his cells. These tests hold the rules that let it run again
without ever touching them: no writer puts anything into K or M, no read-back
lets them move, the Applied tab gains the same two columns before anything
writes to it, and a twin he scored is never the one stamped a duplicate.
Everything offline.
"""
import json
import re
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.sheet as sheet_mod  # noqa: E402
from hunter import alerts, invariants, layout  # noqa: E402
from hunter.sheet import (AI_SCORE, AI_WHY, ARCHIVE_WIDTH, COLS, DEFAULTS_BY_NAME,  # noqa: E402
                          HEADERS, HIS_COLS, KRISH_REACTION, KRISH_SCORE, N_COLS,
                          PREVIOUS_HEADERS, HeaderChanged, Sheet, SheetError, SheetRow,
                          col_index, hyperlink, make_row, validate_row)
from test_sheet_rows import FakeSheet, data_row, parse_range  # noqa: E402

K, M = COLS[KRISH_SCORE], COLS[KRISH_REACTION]
EM_DASH = chr(0x2014)


def his(row, score="", reaction=""):
    row[K], row[M] = score, reaction
    return row


def good_row(company="Acme AI", role="VP Strategy", n=1):
    return make_row(company=company, role=role,
                    jd_url=f"https://job-boards.greenhouse.io/acme/jobs/{n}",
                    score=7, source="ats_sweep", jd_snippet="Builds the engine.")


def pipeline(*rows):
    return [list(HEADERS), [""] * N_COLS] + [list(r) for r in rows]


def his_cells(grid):
    return [(r[COLS["Business"]], r[K], r[M]) for r in grid[2:]]


# ---------- no default, no blank check, no writer ----------

def test_his_columns_have_no_default_and_a_blank_is_valid():
    assert not HIS_COLS & set(DEFAULTS_BY_NAME)
    row = good_row()
    assert row[K] == "" and row[M] == ""
    assert validate_row(row) == [], "a blank in his column is an answer, not a hole"


def test_his_words_may_carry_any_dash_but_hunter_s_may_not():
    row = his(good_row(), "8", "Good fit " + EM_DASH + " but the pay")
    assert validate_row(row) == []
    row[COLS[AI_WHY]] = "Good fit " + EM_DASH + " really"
    assert any("em dash" in f for f in validate_row(row))


def test_the_empty_cell_repair_never_writes_into_his_columns():
    cells = good_row()
    r = SheetRow(row_number=3, cells=cells, verdict="New", company="Acme AI",
                 role="VP Strategy", jd_url=None)
    assert invariants.check_no_empty_cells([r]).state == invariants.OK

    class Spy:
        writes = []

        def _write(self, blocks, raw=False):
            self.writes.extend(blocks)
    invariants.repair_empty_cells(Spy(), [r], [3])
    assert Spy.writes == []


def test_an_append_leaves_his_cells_byte_identical_and_lands_new_rows_blank():
    s = FakeSheet(pipeline(his(data_row("New", "Initech", "Chief of Staff, Sales",
                                        "https://n.example/1"), "9", "Strong, the pay is thin"),
                           his(data_row("Yes", "Globex", "VP Ops", "https://v.example/1"),
                               "4", "")))
    before = his_cells(s.grid)
    s.append_rows([good_row()])
    assert his_cells(s.grid)[:2] == before
    assert his_cells(s.grid)[2] == ("Acme AI", "", "")


def test_an_append_refuses_a_row_carrying_his_cells():
    s = FakeSheet(pipeline())
    with pytest.raises(SheetError, match="is his and must be blank"):
        s.append_rows([his(good_row(), "8", "")])
    assert not s.posts, "nothing was written"
    # cmd_restore puts HIS row back, and his own words come with it
    s.append_rows([his(good_row(), "8", "Yes, this one")], carry_his=True)
    assert his_cells(s.grid) == [("Acme AI", "8", "Yes, this one")]


def test_an_assessment_update_writes_j_and_l_and_never_the_range_between():
    s = FakeSheet(pipeline(his(data_row("New", "Initech", "Chief of Staff, Sales",
                                        "https://n.example/1"), "9", "Strong")))
    before = his_cells(s.grid)
    s.update_assessment(3, score=6, why_it_fits="A better argument.")
    writes = [b["range"] for path, body in s.posts if path == "/values:batchUpdate"
              for b in body["data"]]
    assert writes == ["Pipeline!J3", "Pipeline!L3"], "J:L would put the prose into his score"
    assert his_cells(s.grid) == before
    assert s.grid[2][COLS[AI_SCORE]] == "6"
    assert s.grid[2][COLS[AI_WHY]] == "A better argument."


@pytest.mark.parametrize("column", [KRISH_SCORE, KRISH_REACTION])
def test_an_assessment_update_that_moves_his_cells_is_refused(column):
    class Racing(FakeSheet):
        """A write that lands in his column as well: K is the shape of the
        old J:L range, M of an L:M one."""
        def _post(self, path, body):
            out = super()._post(path, body)
            if path == "/values:batchUpdate":
                self.grid[2][COLS[column]] = "A better argument."
            return out

    s = Racing(pipeline(his(data_row("New", "Initech", "CoS", "https://n.example/1"),
                            "9", "Strong")))
    with pytest.raises(SheetError, match=column):
        s.update_assessment(3, score=6, why_it_fits="A better argument.")


def test_a_sort_carries_his_cells_with_their_rows():
    """The fake sorts only the columns the request names, as the API does,
    and every row here differs in its last two columns, so a request that
    stops at J (his cells left behind) or at AD (the old width, leaving JD
    URL Verified and Materials Built behind) fails."""
    built = sheet_mod.letter("Materials Built")
    assert built == "AF"
    grid = pipeline(his(data_row("Yes", "Low", "CoS", "https://a.example/1", score="6",
                                 **{"Materials Built": "2026-10-01"}), "8", "a"),
                    his(data_row("New", "High", "GTM", "https://a.example/2", score="10",
                                 **{"JD URL Verified": "TRUE",
                                    "Materials Built": "2026-10-02"}), "3", "b"),
                    his(data_row("New", "Mid", "VP", "https://a.example/3", score="8",
                                 **{"Materials Built": "2026-10-03"}), "", "c"))
    def rows_of(g):   # the sort rewrites AI Score as a number first
        return sorted(tuple(str(c) for c in r[:N_COLS]) for r in g[2:])
    before = rows_of(grid)
    s = FakeSheet(grid)
    s.sort_by_score()
    assert his_cells(s.grid) == [("High", "3", "b"), ("Mid", "", "c"), ("Low", "8", "a")]
    assert rows_of(s.grid) == before, "every cell moved with its row"
    sort = [r["sortRange"] for p, b in s.posts if p == ":batchUpdate"
            for r in b["requests"] if "sortRange" in r]
    assert [(x["range"]["startColumnIndex"], x["range"]["endColumnIndex"]) for x in sort] \
        == [(0, N_COLS)]


def test_a_sort_that_strands_his_cells_raises():
    class ShortSort(FakeSheet):
        """A sortRange that moves A to J only, so K onward stay where they were."""
        def _post(self, path, body):
            if path == ":batchUpdate" and any("sortRange" in r for r in body["requests"]):
                sr = body["requests"][0]["sortRange"]
                r0, r1 = sr["range"]["startRowIndex"], sr["range"]["endRowIndex"]
                j = COLS[AI_SCORE]
                left = sorted((row[:j + 1] for row in self.grid[r0:r1]),
                              key=lambda c: -int(c[j]))
                for row, new in zip(self.grid[r0:r1], left):
                    row[:j + 1] = new
                return {}
            return super()._post(path, body)

    grid = pipeline(his(data_row("Yes", "Low", "CoS", "https://a.example/1", score="6"), "8", "a"),
                    his(data_row("New", "High", "GTM", "https://a.example/2", score="10"), "3", "b"))
    with pytest.raises(SheetError, match="changed the set of rows"):
        ShortSort(grid).sort_by_score()


def test_a_sort_refuses_a_column_after_the_last_one_hunter_knows():
    grid = pipeline(data_row("New", "A", "VP", "https://a.example/1"))
    grid[0] = list(HEADERS) + ["Notes"]
    s = FakeSheet(grid)
    with pytest.raises(HeaderChanged, match="AG reads 'Notes'"):
        s.sort_by_score()
    assert not s.posts


# ---------- a header change announces itself ----------

def test_the_header_change_of_2026_10_11_reads_in_plain_words():
    s = FakeSheet(pipeline())
    with pytest.raises(HeaderChanged) as e:
        s.read_pipeline(PREVIOUS_HEADERS)
    text = str(e.value)
    assert isinstance(e.value, SheetError)
    assert "J reads 'AI Score' where hunter expects 'Score'" in text
    assert "K reads 'Krish Score' where hunter expects 'Why It Fits'" in text
    assert "Hunter is not writing to the sheet" in text
    assert "migrate-columns" not in text and "(" not in text.split(":")[0]


def test_a_column_after_af_stops_the_read():
    grid = pipeline(data_row("New", "A", "VP", "https://a.example/1"))
    grid[0] = list(HEADERS) + ["", "My notes"]
    with pytest.raises(HeaderChanged, match="AH reads 'My notes', a column past the last one"):
        FakeSheet(grid).read_pipeline(HEADERS)


def test_the_watchdog_fingerprint_is_the_change_not_the_hour(monkeypatch):
    import datetime as dt
    s = FakeSheet(pipeline())
    s.grid[0][COLS[KRISH_SCORE]] = "My Score"
    monkeypatch.setattr(alerts, "_now", lambda: dt.datetime(2026, 10, 13, 9, 5,
                                                           tzinfo=dt.timezone.utc))
    one = alerts.header_problem(s)
    monkeypatch.setattr(alerts, "_now", lambda: dt.datetime(2026, 10, 14, 16, 5,
                                                           tzinfo=dt.timezone.utc))
    two = alerts.header_problem(s)
    assert one["fingerprint"] == two["fingerprint"]
    assert one["fingerprint"].startswith("columns:")
    assert one["kind"] == "Pipeline columns changed"
    assert "K reads 'My Score' where hunter expects 'Krish Score'" in one["detail"]
    assert EM_DASH not in one["detail"]
    s.grid[0][COLS[KRISH_REACTION]] = "Notes"
    assert alerts.header_problem(s)["fingerprint"] != one["fingerprint"]
    s.grid[0] = list(HEADERS)
    assert alerts.header_problem(s) is None


def test_the_watchdog_and_the_doctor_read_past_the_last_column_hunter_knows():
    """Read only to AF, a column he adds at AG is invisible to both, while
    every run stops on it: the 56-hour silence again."""
    from hunter import doctor
    s = FakeSheet(pipeline())
    s.grid[0] = list(HEADERS) + ["Notes"]
    found = alerts.header_problem(s)
    assert found and "AG reads 'Notes', a column past the last one" in found["detail"]
    check = doctor.check_sheet_header(None, read_header=s.read_header,
                                      read_archive_grid=lambda: [],
                                      canon_headers=list(HEADERS))
    assert check.state == doctor.FAIL and "AG reads 'Notes'" in check.detail


def test_an_inserted_column_is_called_an_insertion_and_he_is_told_what_is_left():
    live = list(HEADERS)
    live.insert(COLS["CV Doc"], "Notes")
    s = FakeSheet(pipeline())
    s.grid[0] = live
    detail = alerts.header_problem(s)["detail"]
    assert detail.startswith("a column was inserted: 'Notes' at F.")
    assert "undo the change, or ask Claude to adopt it" in detail
    assert "where hunter expects 'CV Doc'" not in detail, "not two dozen renames"
    gone = [h for h in HEADERS if h != "Sector"]
    assert sheet_mod.header_shift(gone, HEADERS) == \
        f"a column was removed: 'Sector', which was {sheet_mod.letter('Sector')}"
    renamed = list(HEADERS)
    renamed[K] = "My Score"
    assert sheet_mod.header_shift(renamed, HEADERS) == "", "a rename is a rename"


class Db:
    def __init__(self, sent=()):
        self.sent, self.inserted = list(sent), []

    def get(self, cfg, table, params):
        if table == alerts.TABLE:
            return [{"id": 1}] if params.get("fingerprint", "")[3:] in self.sent else []
        if table == "workflow_runs":
            return [{"run_at": alerts._now().isoformat(), "status": "success",
                     "outcome": {}, "error_message": None}]
        return []

    def insert(self, cfg, table, rows, **kw):
        self.inserted.append((table, rows))


def test_the_first_sighting_records_one_failed_run_and_the_second_none(monkeypatch):
    from hunter import report
    db = Db()
    for mod in (alerts, report):
        monkeypatch.setattr(mod, "db_get", db.get)
        monkeypatch.setattr(mod, "db_insert", db.insert)
    monkeypatch.setattr(report, "db_patch", lambda *a, **k: None)
    monkeypatch.setattr(alerts, "db_delete", lambda *a, **k: None)
    mails = []
    monkeypatch.setattr(alerts.notify, "send_email",
                        lambda cfg, subject, body, text="": mails.append(subject) or {"sent": True})
    s = FakeSheet(pipeline())
    s.grid[0][COLS[AI_SCORE]] = "Score"
    out = alerts.send_trouble(None, sheet=s)
    assert out["sent"] and mails == ["hunter needs you: Pipeline columns changed"]
    runs = [rows for table, rows in db.inserted if table == "workflow_runs"]
    assert len(runs) == 1 and runs[0][0]["status"] == "error"
    assert "J reads 'Score'" in runs[0][0]["error_message"]
    db.sent = [p["fingerprint"] for p in alerts.trouble_checks(None, sheet=s)]
    db.inserted.clear()
    again = alerts.send_trouble(None, sheet=s)
    assert again["sent"] is False and len(mails) == 1
    assert not [t for t, _ in db.inserted if t == "workflow_runs"], "once, never hourly"


class Ledger:
    """hunter_alerts and workflow_runs in memory, hour after hour: marks are
    inserted and deleted the way PostgREST would, and the watchdog's own
    failure rows come back on the next read of workflow_runs."""

    def __init__(self):
        self.marks, self.runs, self.mails = set(), [], []

    def get(self, cfg, table, params):
        if table == alerts.TABLE:
            return [{"id": 1}] if params["fingerprint"][3:] in self.marks else []
        if table == "workflow_runs":
            healthy = {"run_at": alerts._now().isoformat(), "status": "success",
                       "outcome": {}, "error_message": None}
            return list(reversed(self.runs)) + [healthy]
        return []

    def insert(self, cfg, table, rows, **kw):
        if table == alerts.TABLE:
            self.marks.update(r["fingerprint"] for r in rows)
        elif table == "workflow_runs":
            self.runs.extend(rows)

    def delete(self, cfg, table, params):
        assert table == alerts.TABLE and params["fingerprint"].startswith("like.")
        prefix = params["fingerprint"][5:].rstrip("*")
        self.marks = {m for m in self.marks if not m.startswith(prefix)}

    def install(self, monkeypatch):
        from hunter import report
        for mod in (alerts, report):
            monkeypatch.setattr(mod, "db_get", self.get)
            monkeypatch.setattr(mod, "db_insert", self.insert)
        monkeypatch.setattr(alerts, "db_delete", self.delete)
        monkeypatch.setattr(report, "db_patch", lambda *a, **k: None)
        monkeypatch.setattr(alerts.notify, "send_email",
                            lambda cfg, subject, body, text="":
                            self.mails.append(text) or {"sent": True})
        return self


def test_a_header_change_that_clears_and_comes_back_is_reported_again(monkeypatch):
    """The fingerprint is the change, with no date in it. Without forgetting
    it on a clean read, he renames J, puts it back, renames it again a month
    later, and hears nothing while every run fails on it."""
    db = Ledger().install(monkeypatch)
    s = FakeSheet(pipeline())
    s.grid[0][COLS[AI_SCORE]] = "Score"
    assert alerts.send_trouble(None, sheet=s)["sent"] and len(db.mails) == 1
    assert alerts.send_trouble(None, sheet=s)["sent"] is False, "once while it stands"
    s.grid[0] = list(HEADERS)
    alerts.send_trouble(None, sheet=s)
    s.grid[0][COLS[AI_SCORE]] = "Score"
    assert alerts.send_trouble(None, sheet=s)["sent"] and len(db.mails) == 2
    assert len([r for r in db.runs if r["status"] == "error"]) == 2


def test_a_header_that_could_not_be_read_does_not_clear_the_mark(monkeypatch):
    db = Ledger().install(monkeypatch)
    s = FakeSheet(pipeline())
    s.grid[0][COLS[AI_SCORE]] = "Score"
    alerts.send_trouble(None, sheet=s)
    real = s.read_header

    def blip():
        raise RuntimeError("503")
    s.read_header = blip
    alerts.send_trouble(None, sheet=s)
    s.read_header = real
    assert alerts.send_trouble(None, sheet=s)["sent"] is False and len(db.mails) == 1


def test_the_watchdog_s_own_rows_are_not_runs_that_failed(monkeypatch):
    """Two header changes in two hours wrote two error rows, and the third
    hour read them as "2 of the last 3 runs failed": a second email stating
    something no run had recorded."""
    db = Ledger().install(monkeypatch)
    s = FakeSheet(pipeline())
    s.grid[0][COLS[AI_SCORE]] = "Score"
    alerts.send_trouble(None, sheet=s)
    s.grid[0][COLS[AI_WHY]] = "Why"
    alerts.send_trouble(None, sheet=s)
    assert len(db.runs) == 2 and len(db.mails) == 2
    kinds = [p["kind"] for p in alerts.trouble_checks(None, sheet=s)]
    assert "runs are failing" not in kinds


def test_an_applied_header_hunter_cannot_read_is_reported_like_pipeline_s(monkeypatch):
    """Applied's row 1 is load-bearing now: read_archive finds columns by it.
    He renames J there to match Pipeline, or types a heading after Archived
    On, and every archive and the next reconcile stop, while the Pipeline
    check reads clean."""
    renamed = previous_applied(2)
    renamed[0][COLS[AI_SCORE]] = "AI Score"
    renamed[0][COLS[AI_SCORE] + 1] = "Why AI thinks it Fits"
    extra = previous_applied(2)
    extra[0] = list(HEADERS) + ["Archived On", "Interview?"]
    for grid, says in ((renamed, "J reads 'AI Score' where hunter expects 'Score'"),
                       (extra, "AH reads 'Interview?', a column past the last one")):
        s = TwoTabs(pipeline(), grid)
        with pytest.raises(SheetError):
            s.read_archive()
        assert alerts.header_problem(s) is None, "Pipeline itself is fine"
        found = alerts.applied_header_problem(s)
        assert found["kind"] == "Applied columns changed" and found["record_run"]
        assert found["fingerprint"].startswith("applied-columns:")
        assert says in found["detail"] and "moving decided rows to Applied" in found["detail"]
    for fine in (previous_applied(2), [list(HEADERS) + ["Archived On"]], []):
        assert alerts.applied_header_problem(TwoTabs(pipeline(), fine)) is None
    db = Ledger().install(monkeypatch)
    out = alerts.send_trouble(None, sheet=TwoTabs(pipeline(), renamed))
    assert out["kinds"] == ["Applied columns changed"]
    assert [r["outcome"] for r in db.runs] == ["Applied columns changed"]


def agreed(monkeypatch, names=None, error=None):
    """canon.load_canon, offline: canon 9.13 naming `names`, or raising."""
    from hunter import canon

    def load(cfg):
        if error:
            raise error
        return type("C", (), {"sheet_headers": list(names or HEADERS)})()
    monkeypatch.setattr(canon, "load_canon", load)


def test_canon_that_disagrees_with_the_code_is_reported_by_the_watchdog(monkeypatch):
    """The PR1 window: merged, and canon 9.13 still v8. Every hourly command
    stops in build_context before it can record a run, and the header itself
    reads clean, so nothing else would say so."""
    from hunter.canon import CanonError
    db = Ledger().install(monkeypatch)
    s = FakeSheet(pipeline())
    agreed(monkeypatch, names=PREVIOUS_HEADERS)
    found = alerts.trouble_checks(None, sheet=s, check_canon=True)
    assert [p["kind"] for p in found] == ["canon and the code disagree"]
    assert "names 30 columns where the code names 32" in found[0]["detail"]
    agreed(monkeypatch, error=CanonError("canon is missing required sections: ['9.13']"))
    assert alerts.send_trouble(None, sheet=s, check_canon=True)["kinds"] == \
        ["canon and the code disagree"]
    assert [r["outcome"] for r in db.runs] == ["canon and the code disagree"]
    agreed(monkeypatch)
    observed = {}
    assert alerts.trouble_checks(None, sheet=s, observed=observed, check_canon=True) == []
    assert "canon:" in observed["clean"]


def test_the_watchdog_command_reads_both_headers_and_says_when_it_could_not(
        monkeypatch, capsys):
    from hunter import run as R
    monkeypatch.setattr(R, "load", lambda: None)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    s = TwoTabs(pipeline(), previous_applied(1))
    monkeypatch.setattr(R, "Sheet", lambda token: s)
    monkeypatch.setattr(alerts, "db_get", Db().get)
    agreed(monkeypatch)
    assert R.main(["watchdog"]) == 0
    assert "nothing wrong; the loop is turning" in capsys.readouterr().out
    reads = [p for p in s.reads]
    assert any(r.startswith("Pipeline!A1:") for r in reads)
    assert any(r.startswith("Applied!A1:") for r in reads)

    def fails():
        raise RuntimeError("503")
    real, s.read_header = s.read_header, fails
    assert R.main(["watchdog"]) == 0
    out = capsys.readouterr().out
    assert "nothing wrong; the loop" not in out
    assert "NOT read: the Pipeline header" in out

    s.read_header = real
    agreed(monkeypatch, names=PREVIOUS_HEADERS)
    R.main(["watchdog"])
    assert "canon and the code disagree" in capsys.readouterr().out


def test_a_broken_sheet_key_is_a_problem_and_the_database_checks_still_run(monkeypatch,
                                                                         capsys):
    from hunter import run as R
    from hunter.config import ConfigError
    monkeypatch.setattr(R, "load", lambda: None)

    def broken(cfg):
        raise ConfigError("system_config key 'hunter_google_service_account_json' "
                          "is not valid JSON")
    monkeypatch.setattr(R, "GoogleServiceAccount", broken)
    agreed(monkeypatch)
    db = Db()
    asked = []
    monkeypatch.setattr(alerts, "db_get",
                        lambda cfg, table, params: asked.append(table) or db.get(cfg, table, params))
    assert R.main(["watchdog"]) == 0
    out = capsys.readouterr().out
    assert "the sheet could not be opened: ConfigError" in out
    assert "workflow_runs" in asked, "the database checks ran anyway"


def test_doctor_compares_the_live_header_the_code_and_canon():
    from hunter import doctor
    previous = lambda: previous_applied(1)    # noqa: E731
    ok = doctor.check_sheet_header(None, read_header=lambda: list(HEADERS),
                                   read_archive_grid=previous,
                                   canon_headers=list(HEADERS))
    assert ok.state == doctor.OK and "Applied is on the previous layout" in ok.detail
    live = list(HEADERS)
    live[K] = "Score"
    bad = doctor.check_sheet_header(None, read_header=lambda: live,
                                    read_archive_grid=previous,
                                    canon_headers=list(HEADERS))
    assert bad.state == doctor.FAIL and "K reads 'Score'" in bad.detail
    stale = doctor.check_sheet_header(None, read_header=lambda: list(HEADERS),
                                      read_archive_grid=previous,
                                      canon_headers=list(PREVIOUS_HEADERS))
    assert stale.state == doctor.FAIL and "canon 9.13 and sheet.HEADERS disagree" in stale.detail
    applied = previous_applied(1)
    applied[0][COLS[AI_SCORE]] = "AI Score"
    odd = doctor.check_sheet_header(None, read_header=lambda: list(HEADERS),
                                    read_archive_grid=lambda: applied,
                                    canon_headers=list(HEADERS))
    assert odd.state == doctor.FAIL and "Applied columns changed" in odd.detail


def test_the_doctor_runs_the_header_check_online(monkeypatch):
    from hunter import doctor
    ran = []
    for name in dir(doctor):
        if name.startswith("check_") and callable(getattr(doctor, name)):
            monkeypatch.setattr(doctor, name, (lambda n: lambda *a, **k: ran.append(n)
                                               or doctor.Check(n, doctor.OK, ""))(name))
    doctor.run(None, "", offline=False)
    assert "check_sheet_header" in ran
    ran.clear()
    doctor.run(None, "", offline=True)
    assert "check_sheet_header" not in ran


# ---------- the Applied tab ----------

ARCHIVE_ID = 2


class TwoTabs(Sheet):
    """Pipeline and Applied behind the real Sheet methods, close enough to the
    API to matter: trailing empty cells and rows are trimmed on read, column
    inserts shift every row, RAW and USER_ENTERED writes are recorded."""

    def __init__(self, pipe, applied, applied_cols=31):
        super().__init__(token="offline", workbook_id="wb", sheet_id=1)
        self.grids = {"Pipeline": pipe, "Applied": applied}
        self.ids = {1: "Pipeline", ARCHIVE_ID: "Applied"}
        self.cols = {"Pipeline": N_COLS, "Applied": applied_cols}
        self.posts, self.raw_ranges, self.reads = [], [], []

    def _get(self, path, params=None):
        if path == "":
            return {"sheets": [{"properties": {
                "sheetId": sid, "title": tab,
                "gridProperties": {"rowCount": 2000, "columnCount": self.cols[tab]}}}
                for sid, tab in self.ids.items()]}
        self.reads.append(path.split("/values/", 1)[1])
        tab, c0, r0, c1, r1 = parse_range(path.split("/values/", 1)[1])
        grid = self.grids[tab.strip("'")]
        out = []
        for i in range(r0 - 1, min(r1 or len(grid), len(grid))):
            cells = list(grid[i][c0:c1 + 1])
            while cells and not str(cells[-1]).strip():
                cells.pop()
            out.append(cells)
        while out and not out[-1]:
            out.pop()
        return {"values": out}

    def _post(self, path, body):
        self.posts.append((path, body))
        if path == "/values:batchUpdate":
            for block in body["data"]:
                tab, c0, r0, _, _ = parse_range(block["range"])
                grid = self.grids[tab.strip("'")]
                if body["valueInputOption"] == "RAW":
                    self.raw_ranges.append(block["range"])
                for i, vals in enumerate(block["values"]):
                    while len(grid) <= r0 - 1 + i:
                        grid.append([])
                    row = grid[r0 - 1 + i]
                    row.extend([""] * (c0 + len(vals) - len(row)))
                    row[c0:c0 + len(vals)] = vals
            return {}
        for req in body.get("requests", []):
            if "insertDimension" in req:
                rng = req["insertDimension"]["range"]
                tab = self.ids[rng["sheetId"]]
                for row in self.grids[tab]:
                    if len(row) > rng["startIndex"]:
                        row[rng["startIndex"]:rng["startIndex"]] = [""] * (
                            rng["endIndex"] - rng["startIndex"])
                self.cols[tab] += rng["endIndex"] - rng["startIndex"]
            elif "updateCells" in req:
                uc = req["updateCells"]
                grid = self.grids[self.ids[uc["range"]["sheetId"]]]
                vals = [v["userEnteredValue"]["stringValue"] for v in uc["rows"][0]["values"]]
                grid[0] = vals + grid[0][len(vals):]
            elif "appendDimension" in req:
                self.cols[self.ids[req["appendDimension"]["sheetId"]]] += req["appendDimension"]["length"]
            elif "deleteDimension" in req:
                rng = req["deleteDimension"]["range"]
                del self.grids[self.ids[rng.get("sheetId", 1)]][rng["startIndex"]:rng["endIndex"]]
        return {}

    def delete_rows(self, row_numbers, **kw):
        # Pipeline's real delete re-reads column A; this one only removes
        for n in sorted(row_numbers, reverse=True):
            del self.grids["Pipeline"][n - 1]
        return len(row_numbers)


def previous_row(company, role, *, score="7", why="Runs the engine.", sector="Software",
                 stamp="2026-09-02"):
    cells = ["x"] * len(PREVIOUS_HEADERS)
    for name, val in (("Verdict", "Declined - business uninteresting"), ("Business", company),
                      ("Role", role), ("Job Link", hyperlink("https://a.example/j", "JD")),
                      ("Score", score), ("Why It Fits", why), ("Sector", sector)):
        cells[PREVIOUS_HEADERS.index(name)] = val
    return cells + [stamp]


def previous_applied(n=3):
    return ([list(PREVIOUS_HEADERS) + ["Archived On"]]
            + [previous_row(f"Co{i}", f"Role {i}", score=str(i + 3)) for i in range(n)])


def test_applied_on_the_previous_layout_gains_his_two_columns_once():
    s = TwoTabs(pipeline(), previous_applied())
    report = s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    assert report["state"] == "previous" and report["applied"] and report["verified"]
    assert report["rows"] == 3
    batches = [b for p, b in s.posts if p == ":batchUpdate"]
    assert len(batches) == 2, "the two inserts, read back, and only then the header"
    inserts, header = batches[0]["requests"], batches[1]["requests"]
    assert [r["insertDimension"]["range"]["startIndex"] for r in inserts] == [K, M]
    assert all(r["insertDimension"]["inheritFromBefore"] for r in inserts)
    assert [list(r) for r in header] == [["updateCells"]]
    app = s.grids["Applied"]
    assert app[0][:ARCHIVE_WIDTH] == HEADERS + ["Archived On"]
    first = app[1]
    assert first[COLS["Business"]] == "Co0" and first[COLS[AI_SCORE]] == "3"
    assert first[COLS[AI_WHY]] == "Runs the engine." and first[COLS["Sector"]] == "Software"
    assert first[K] == "" and first[M] == ""
    assert first[ARCHIVE_WIDTH - 1] == "2026-09-02", "the stamp moved from AE to AG"
    n = len(s.posts)
    again = s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    assert again["noop"] and again["state"] == "current" and len(s.posts) == n


def test_a_dry_run_says_what_it_would_do_and_does_nothing():
    s = TwoTabs(pipeline(), previous_applied())
    report = s.ensure_archive_columns(apply=False, archive_sheet_id=ARCHIVE_ID)
    assert report["state"] == "previous" and not report["applied"] and not s.posts


def test_an_unrecognised_applied_header_is_refused_and_untouched():
    grid = previous_applied()
    grid[0][5] = "Letter"
    s = TwoTabs(pipeline(), grid)
    with pytest.raises(SheetError, match="neither the previous nor the current"):
        s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    assert not s.posts
    blank_header = previous_applied()
    blank_header[0] = [""] * 31
    with pytest.raises(SheetError, match="neither"):
        TwoTabs(pipeline(), blank_header).ensure_archive_columns(archive_sheet_id=ARCHIVE_ID)


def test_an_insert_that_does_not_land_is_reported_not_trusted():
    class HeaderOnly(TwoTabs):
        def _post(self, path, body):
            if path == ":batchUpdate":
                body = {"requests": [r for r in body["requests"] if "insertDimension" not in r]}
            return super()._post(path, body)

    s = HeaderOnly(pipeline(), previous_applied())
    with pytest.raises(SheetError, match="did not verify"):
        s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)


class Landing(TwoTabs):
    """The inserts land, then `spoil` does one thing to Applied before the
    read-back, so each read-back check is shown to fire on its own."""
    spoil = staticmethod(lambda grid: None)

    def _post(self, path, body):
        out = super()._post(path, body)
        if path == ":batchUpdate" and any("insertDimension" in r for r in body["requests"]):
            self.spoil(self.grids["Applied"])
        return out


def _lose_last_row(grid):
    grid.pop()


def _value_in_his_column(grid):
    grid[2][K] = "copied by the insert"


def _stamp_lost(grid):
    grid[1][ARCHIVE_WIDTH - 1] = ""


@pytest.mark.parametrize("spoil, says", [
    (_lose_last_row, "3 rows before, 2 after"),
    (_value_in_his_column, "row 3 has a value in a column just inserted"),
    (_stamp_lost, "row 2 Archived On did not land at AG"),
])
def test_each_insert_read_back_check_fires_alone_and_the_failure_is_remembered(spoil, says):
    """A failed read-back used to leave row 1 reading the current layout,
    written in the same request as the inserts, so the very next
    archive_rows and read_archive trusted the rows it had just doubted."""
    s = Landing(pipeline(decided(3, "Globex", "VP Ops", "4", "")), previous_applied(3))
    s.spoil = spoil
    with pytest.raises(SheetError, match="did not verify") as e:
        s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    assert says in str(e.value)
    assert sheet_mod.archive_layout(s.grids["Applied"]) == "", \
        "row 1 is neither layout, so everything that touches Applied stops"
    assert not [b for p, b in s.posts if p == ":batchUpdate"
                and any("updateCells" in r for r in b["requests"])], "no header written"
    with pytest.raises(SheetError, match="neither the previous nor the current"):
        s.read_archive()
    with pytest.raises(SheetError, match="neither the previous nor the current"):
        s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                       archive_sheet_id=ARCHIVE_ID, headers=HEADERS)
    assert len(s.grids["Pipeline"]) == 3, "the decided row is still on Pipeline"


def test_a_column_of_his_after_archived_on_is_refused_before_any_write():
    grid = previous_applied()
    grid[0] = grid[0] + ["Notes"]
    s = TwoTabs(pipeline(), grid)
    with pytest.raises(SheetError, match="neither the previous nor the current"):
        s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    assert not s.posts, "Applied untouched"


def decided(n, company, role, score, reaction):
    cells = his(data_row("Declined - comp below bar", company, role,
                         f"https://a.example/{n}"), score, reaction)
    return cells


def test_archive_rows_inserts_first_then_moves_his_cells_raw():
    s = TwoTabs(pipeline(decided(3, "Globex", "VP Ops", "4", "- pay is thin")), previous_applied(2))
    rows = s.read_pipeline(HEADERS)
    moved = s.archive_rows(rows, archive_tab="Applied", archive_sheet_id=ARCHIVE_ID,
                           headers=HEADERS)
    assert moved == 1 and len(s.grids["Pipeline"]) == 2
    app = s.grids["Applied"]
    assert app[0][:ARCHIVE_WIDTH] == HEADERS + ["Archived On"]
    landed = app[3]
    assert (landed[COLS["Business"]], landed[K], landed[M]) == ("Globex", "4", "- pay is thin")
    assert landed[ARCHIVE_WIDTH - 1], "dated"
    assert "Applied!K4:K4" in s.raw_ranges and "Applied!M4:M4" in s.raw_ranges, \
        "his cells land as the text he typed, never parsed as a formula or a date"


@pytest.mark.parametrize("column", [KRISH_SCORE, KRISH_REACTION])
def test_archive_rows_keeps_the_pipeline_row_when_his_cell_lands_altered(column):
    """His score and reaction exist nowhere else once the Pipeline row is
    deleted, so a copy that did not land as he typed it deletes nothing."""
    at = sheet_mod.letter(column)

    class Coerced(TwoTabs):
        def _post(self, path, body):
            out = super()._post(path, body)
            if path == "/values:batchUpdate" and body["valueInputOption"] == "RAW":
                for block in body["data"]:
                    if block["range"].startswith(f"Applied!{at}"):
                        _, c0, r0, _, _ = parse_range(block["range"])
                        self.grids["Applied"][r0 - 1][c0] = "#ERROR!"
            return out

    s = Coerced(pipeline(decided(3, "Globex", "VP Ops", "9", "- pay is thin")),
                previous_applied(1))
    with pytest.raises(SheetError, match=f"{column}.*nothing deleted from Pipeline"):
        s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                       archive_sheet_id=ARCHIVE_ID, headers=HEADERS)
    assert len(s.grids["Pipeline"]) == 3, "the decided row is still on Pipeline"


def test_archive_rows_refuses_the_previous_header_without_the_column_insert(monkeypatch):
    s = TwoTabs(pipeline(decided(3, "Globex", "VP Ops", "4", "")), previous_applied(2))
    monkeypatch.setattr(s, "ensure_archive_columns", lambda **kw: {"noop": True})
    rows = s.read_pipeline(HEADERS)
    with pytest.raises(SheetError, match="nothing deleted from Pipeline"):
        s.archive_rows(rows, archive_tab="Applied", archive_sheet_id=ARCHIVE_ID,
                       headers=HEADERS)
    assert len(s.grids["Pipeline"]) == 3, "the decided row is still on Pipeline"
    assert not [p for p, _ in s.posts if p == "/values:batchUpdate"], "Applied untouched"


class Parsing(TwoTabs):
    """USER_ENTERED as Sheets applies it to the two shapes his reactions
    take: a leading minus or plus is a formula, a day and month a date."""

    def _post(self, path, body):
        if path == "/values:batchUpdate" and body["valueInputOption"] == "USER_ENTERED":
            def parse(v):
                v = str(v)
                if v[:1] in "-+" and len(v) > 1:
                    return "#ERROR!"
                if re.fullmatch(r"\d{1,2}/\d{1,2}( .*)?", v):
                    return "46307"
                return v
            body = dict(body, data=[dict(b, values=[[parse(v) for v in row]
                                                     for row in b["values"]])
                                    for b in body["data"]])
        return super()._post(path, body)


def test_restore_brings_his_score_and_reaction_back_as_he_typed_them(monkeypatch):
    """archive_rows writes his two cells to Applied RAW. The way back went
    through USER_ENTERED, so '- pay is thin' came back a formula error, the
    read-back raised after the write, and the role sat on both tabs."""
    import hunter.run as R
    from hunter import config as config_mod
    applied = [list(HEADERS) + ["Archived On"],
               his(data_row("Declined - comp below bar", "Globex", "VP Operations",
                            "https://a.example/9"), "9", "- pay is thin, 10/12 call")
               + ["2026-10-12"]]
    s = Parsing(pipeline(data_row("New", "Initech", "Chief of Staff",
                                  "https://a.example/1")), applied, applied_cols=33)
    monkeypatch.setattr(config_mod, "ARCHIVE_SHEET_ID", ARCHIVE_ID)
    monkeypatch.setattr(R, "build_context", lambda: (None, None))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: s)
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [{
        "job_id": "globex:vp-operations", "company": "Globex",
        "title": "VP Operations", "krish_verdict": "Yes"}])
    monkeypatch.setattr(R, "db_patch", lambda *a, **k: None)
    assert R.cmd_restore(["globex:vp-operations"], apply=True) == 0
    back = s.grids["Pipeline"][3]
    assert (back[COLS["Business"]], back[K], back[M]) == \
        ("Globex", "9", "- pay is thin, 10/12 call")
    assert back[COLS["Verdict"]] == "Yes"
    assert len(s.grids["Applied"]) == 1, "off Applied, once it is safely on Pipeline"


def test_read_archive_reads_either_layout_by_name_and_never_writes():
    s = TwoTabs(pipeline(), previous_applied(2))
    rows = s.read_archive()
    assert not s.posts
    assert [r.company for r in rows] == ["Co0", "Co1"]
    r = rows[1]
    assert r.cell(AI_SCORE) == "4" and r.cell(AI_WHY) == "Runs the engine."
    assert r.cell("Sector") == "Software" and r.cell(KRISH_SCORE) == ""
    assert r.archived_on == "2026-09-02" and len(r.cells) == N_COLS
    s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    s.grids["Applied"][1][K] = "6"
    after = s.read_archive()
    assert after[0].cell(KRISH_SCORE) == "6" and after[0].cell(AI_SCORE) == "3"
    assert after[0].archived_on == "2026-09-02"


def test_read_archive_refuses_a_header_it_does_not_know():
    grid = previous_applied()
    grid[0][9] = "Rating"
    with pytest.raises(SheetError, match="neither the previous nor the current"):
        TwoTabs(pipeline(), grid).read_archive()


def test_an_applied_tab_that_could_not_be_read_is_unchecked_not_fine():
    grid = previous_applied()
    grid[0][5] = "Letter"
    s = TwoTabs(pipeline(data_row("New", "A", "VP", "https://a.example/1")), grid)
    out = invariants.enforce(s, HEADERS, apply=False)
    f = next(f for f in out["findings"] if f.name == "archive stamped")
    assert f.state == invariants.WATCH and "was not read" in f.detail
    assert "0 archived row(s) dated" not in f.detail


def test_the_applied_state_line_names_application_status_where_it_is_now():
    r = SheetRow(row_number=3, cells=good_row(), verdict="New", company="Acme AI",
                 role="VP Strategy", jd_url=None)
    f = invariants.check_applied_state_agrees([r])
    assert f.state == invariants.OK and f.detail == "column A and column V match"


def test_the_stamp_check_reads_what_read_archive_returns():
    """check_archive_stamped read cells[30] of rows padded to 30, so it
    reported all 369 dated Applied rows as undated."""
    s = TwoTabs(pipeline(), previous_applied(3))
    assert invariants.check_archive_stamped(s.read_archive()).state == invariants.OK
    s.ensure_archive_columns(apply=True, archive_sheet_id=ARCHIVE_ID)
    assert invariants.check_archive_stamped(s.read_archive()).state == invariants.OK
    s.grids["Applied"][2][ARCHIVE_WIDTH - 1] = ""
    f = invariants.check_archive_stamped(s.read_archive())
    assert f.state == invariants.WATCH and f.rows == [3]


def test_settle_and_process_insert_the_columns_before_anything_else(monkeypatch):
    from hunter import run as R
    calls = []

    class Spy:
        def ensure_archive_columns(self, *, apply, archive_tab, archive_sheet_id):
            calls.append(("ensure", apply))
            return {"noop": True, "state": "current", "rows": 0}

    monkeypatch.setattr(R, "build_context", lambda: (None, None))
    monkeypatch.setattr(R, "GoogleServiceAccount", lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: Spy())
    monkeypatch.setattr(R, "settle_step", lambda cfg, canon, sheet, summary, apply:
                        calls.append(("settle", apply)) or {"decided": 0})
    assert R.cmd_settle(apply=False) == 0
    assert calls == [("ensure", False), ("settle", False)], "a dry run inserts nothing"
    calls.clear()

    class Refuses(Spy):
        def ensure_archive_columns(self, **kw):
            raise SheetError("Applied header is neither the previous nor the current layout")
    monkeypatch.setattr(R, "Sheet", lambda token: Refuses())
    assert R.cmd_settle(apply=True) == 1, "the hourly step goes red, it does not pass"
    calls.clear()

    class Stop(Exception):
        pass
    monkeypatch.setattr(R, "amendment_step", lambda *a: calls.append(("amend", None)) or 0)
    monkeypatch.setattr(R, "stored_company_declines", lambda cfg: ({}, []))

    def reconcile(*a, **k):
        raise Stop
    monkeypatch.setattr(R, "reconcile", reconcile)
    with pytest.raises(Stop):
        R.process_step(None, None, Spy(), [])
    assert calls == [("ensure", True), ("amend", None)]


# ---------- twins ----------

def twin(n, *, score="", reaction="", verdict="New"):
    cells = his(make_row(company="Initech", role="Chief of Staff, Sales",
                         jd_url=f"https://www.linkedin.com/jobs/view/cos-sales-{n}",
                         score=7, source="apify_linkedin", jd_snippet="x"), score, reaction)
    cells[COLS["Verdict"]] = verdict
    return SheetRow(row_number=n, cells=cells, verdict=verdict, company="Initech",
                    role="Chief of Staff, Sales",
                    jd_url=f"https://www.linkedin.com/jobs/view/cos-sales-{n}")


class Stamps:
    def __init__(self):
        self.verdicts_set = {}

    def set_verdicts(self, mapping):
        self.verdicts_set.update(mapping)
        return len(mapping)


def test_the_twin_he_scored_is_kept_and_the_one_he_called_a_duplicate_goes():
    """Pipeline on 2026-10-13: one role on two rows, the upper marked
    Duplicate in Krish Reaction, the lower scored 9. Row position alone kept
    the upper row and stamped his 9 for archiving."""
    rows = [twin(87, reaction="Duplicate"), twin(89, score="9")]
    f = invariants.check_no_duplicate_postings(rows)
    assert f.state == invariants.BROKEN and f.rows == [87]
    sheet = Stamps()
    invariants.repair_duplicates(sheet, rows, f.rows)
    assert sheet.verdicts_set == {87: "Declined - duplicate row"}


def test_his_score_alone_outranks_row_position():
    rows = [twin(40), twin(41, score="9")]
    f = invariants.check_no_duplicate_postings(rows)
    assert f.rows == [40], "the unscored row above is the twin that goes"
    sheet = Stamps()
    invariants.repair_duplicates(sheet, rows, f.rows)
    assert sheet.verdicts_set == {40: "Declined - duplicate row"}


def test_a_scored_row_is_never_stamped_whatever_the_target_list_says():
    sheet = Stamps()
    rows = [twin(87, score="6"), twin(89, score="9")]
    invariants.repair_duplicates(sheet, rows, [87, 89])
    assert sheet.verdicts_set == {}, "both are his rulings; he decides which goes"


def test_a_reaction_in_his_words_outranks_position_and_duplicate_ranks_last():
    rows = [twin(40), twin(41, reaction="Interesting, the pay is thin")]
    assert invariants.check_no_duplicate_postings(rows).rows == [40]
    rows = [twin(40, reaction="duplicate."), twin(41)]
    assert invariants.check_no_duplicate_postings(rows).rows == [40]


def prune_with(monkeypatch, rows, db):
    """cmd_prune_sheet over these rows, offline, against the real
    Sheet.delete_rows on a FakeSheet grid built from the same rows."""
    import hunter.run as R
    grid = pipeline(*[r.cells for r in rows])
    s = FakeSheet(grid)
    srows = s.read_pipeline(HEADERS)
    monkeypatch.setattr(R, "build_context", lambda: (None, type("C", (), {
        "sheet_headers": list(HEADERS)})()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: s)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: db)
    monkeypatch.setattr(R, "match_rows",
                        lambda a, b: (list(zip(srows, db)), [], [], []))
    return R, s


def test_prune_sheet_leaves_a_new_row_he_scored_or_reacted_to_in_place(monkeypatch):
    """Live Pipeline, 2026-10-13: Notion's twins at rows 87 and 89 both read
    New in column A. Column A alone planned his 9 at row 89 as a "duplicate
    of row 87", the row he had called Duplicate, and printed that nothing he
    had written on was at risk."""
    rows = [twin(3, reaction="Duplicate"), twin(4, score="9"), twin(5)]
    db = [{"job_id": f"initech:cos-{r.row_number}", "location": "New York"} for r in rows]
    R, s = prune_with(monkeypatch, rows, db)
    assert R.cmd_prune_sheet(apply=True) == 0
    left = [(r[COLS["Business"]], r[K], r[M]) for r in s.grid[2:]]
    assert left == [("Initech", "", "Duplicate"), ("Initech", "9", "")], \
        "only the twin with nothing of his on it goes"


def test_prune_sheet_never_removes_his_row_as_outside_geography(monkeypatch):
    rows = [twin(3, reaction="Would move for this")]
    db = [{"job_id": "initech:cos", "location": "Sao Paulo, Brazil"}]
    R, s = prune_with(monkeypatch, rows, db)
    assert R.cmd_prune_sheet(apply=True) == 0
    assert s.grid[2][M] == "Would move for this"


def test_delete_rows_refuses_a_target_he_has_scored_or_reacted_to():
    """The write path's own guard, re-read at the moment of the delete, so a
    planner that forgets his columns still cannot remove them."""
    grid = pipeline(data_row("New", "A", "VP", "https://a.example/1"),
                    his(data_row("New", "B", "VP", "https://a.example/2"), "9", ""),
                    his(data_row("New", "C", "VP", "https://a.example/3"), "", "Not for me"))
    s = FakeSheet(grid)
    for targets in ([3, 4], [3, 5]):
        with pytest.raises(SheetError, match="he has written in"):
            s.delete_rows(targets)
    assert not [b for p, b in s.posts if p == ":batchUpdate"], "nothing deleted"
    # A row he names is his instruction, and goes
    assert s.delete_rows([3, 4], named={4}) == 2
    assert [r[COLS["Business"]] for r in s.grid[2:]] == ["C"]


def test_archive_rows_opts_out_because_it_has_already_copied_his_cells():
    s = TwoTabs(pipeline(decided(3, "Globex", "VP Ops", "4", "Not my domain")),
                previous_applied(1))
    calls = []

    def spy(row_numbers, **kw):
        calls.append(kw)
        return len(row_numbers)
    s.delete_rows = spy
    s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                   archive_sheet_id=ARCHIVE_ID, headers=HEADERS)
    assert calls == [{"expect_verdict": None, "his_copied": True}]
    # and the real guard honours it
    f = FakeSheet(pipeline(his(data_row("Declined - comp", "B", "VP",
                                        "https://a.example/2"), "4", "thin")))
    assert f.delete_rows([3], expect_verdict=None, his_copied=True) == 1


# ---------- regate holds every row he decided ----------

def test_regate_holds_every_row_he_decided_not_only_the_first(monkeypatch):
    """The AI Score rename put a local called `held` inside the loop that
    re-reads the held list on every pass. The first decided row rebound it
    to "7", and every later one read as undecided: dropped as the wrong
    function, its DB verdict overwritten, its row archived. The 2026-09-02
    shape cmd_restore exists to undo."""
    import hunter.run as R
    from hunter.package import rationale
    from hunter.sources import ResolvedRole

    def row(n, company, score):
        url = f"https://job-boards.greenhouse.io/{company.lower()}/jobs/{n}"
        cells = make_row(company=company, role="Chief of Staff", jd_url=url,
                         score=score, source="ats_sweep", jd_snippet="x")
        return SheetRow(row_number=n, cells=cells, verdict="New", company=company,
                        role="Chief of Staff", jd_url=url)
    rows = [row(41, "Initech", 7), row(42, "Globex", 8), row(43, "Hooli", 6)]
    db = [{"job_id": f"{r.company.lower()}:cos", "title": "Chief of Staff",
           "company": r.company, "url": r.jd_url, "krish_verdict": "Yes"} for r in rows]

    class Pipe:
        assessed, archived = [], []

        def read_pipeline(self, headers):
            return rows

        def update_assessment(self, n, *, score, why_it_fits):
            self.assessed.append((n, score))

        def archive_rows(self, movers, **kw):
            self.archived.extend(m.row_number for m in movers)

    cfg = type("Cfg", (), {"require_json": lambda self, k: []})()
    sheet = Pipe()
    monkeypatch.setattr(R, "build_context", lambda: (cfg, type("C", (), {
        "sheet_headers": list(HEADERS), "universe": ()})()))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda c: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "Sheet", lambda token: sheet)
    monkeypatch.setattr(R, "db_get", lambda c, table, params: db)
    monkeypatch.setattr(R, "stored_company_declines", lambda c: ({}, []))
    monkeypatch.setattr(R.employer, "build_index", lambda c, declines: None)
    monkeypatch.setattr(R, "archetype", lambda title: None)   # any undecided row drops
    monkeypatch.setattr(R, "_resolve_for_build", lambda d: ResolvedRole(
        company=d["company"], title=d["title"], url=d["url"], jd_url=d["url"],
        jd_text="Runs the business.", live=True, source="regate"))
    result = type("S", (), {"score": 3, "merit": 3, "why_it_fits": "w",
                            "auto_rejected": True, "rejection_reason": "x"})()
    monkeypatch.setattr(R, "score_role", lambda role, **kw: result)
    monkeypatch.setattr(R, "run_gates", lambda role, **kw: type("G", (), {
        "passed": False, "failures": lambda self: []})())
    monkeypatch.setattr(rationale, "write_rationale", lambda *a, **kw: ("The case.", []))
    patched, stamped = [], []
    monkeypatch.setattr(R, "db_patch", lambda c, t, match, values: patched.append(values))
    monkeypatch.setattr(R, "stamp_auto_verdicts",
                        lambda c, s, rs: stamped.extend(r.row_number for r, *_ in rs))

    assert R.cmd_regate(from_row=41, apply=True) == 0
    assert stamped == [] and sheet.archived == [], "a row he decided is never dropped"
    assert sorted(sheet.assessed) == [(41, 7), (42, 8), (43, 6)], \
        "each keeps the score it had"
    assert not [p for p in patched if "krish_verdict" in p]


def test_a_role_already_on_his_sheet_is_seen_whatever_its_status(monkeypatch):
    """The four twins of 2026-10-13 were all first recorded unresolved. A
    later sweep staged each one, the insert kept the unresolved status, and
    the next sweep, not counting it as seen, appended it again."""
    import hunter.run as run_mod
    from hunter.sources import RolePosting as Posting
    db = [{"job_id": "initech:chief-of-staff-sales", "company": "Initech",
           "title": "Chief of Staff, Sales", "location": "New York",
           "url": "https://www.linkedin.com/jobs/view/cos-1", "job_url": None,
           "status": "unresolved", "presented_at": "2026-10-08T08:30:00Z"},
          {"job_id": "hooli:partner-development-director", "company": "Hooli",
           "title": "Partner Development Director", "location": "New York",
           "url": "https://www.linkedin.com/jobs/view/pdd-1", "job_url": None,
           "status": "unresolved", "presented_at": None}]
    def db_get(cfg, table, params):
        # PostgREST returns only the columns selected. Returning whole rows
        # whatever the select said let the fix pass with presented_at
        # dropped from the select, when in production every row would then
        # read as never shown.
        cols = params["select"].split(",")
        return [{c: r.get(c) for c in cols} for r in db]
    monkeypatch.setattr(run_mod, "db_get", db_get)
    keys, countries = run_mod.seen_identity(None)
    again = [Posting(source="apify_linkedin", company="Initech", title="Chief of Staff, Sales",
                     url="https://www.linkedin.com/jobs/view/cos-2", location="New York"),
             Posting(source="apify_linkedin", company="Hooli",
                     title="Partner Development Director",
                     url="https://www.linkedin.com/jobs/view/pdd-2", location="New York")]
    fresh = run_mod.fresh_postings(again, keys, countries)
    assert [p.company for p in fresh] == ["Hooli"], \
        "never shown, so reconsidered; shown, so never appended twice"


# ---------- the colours on AI Score ----------

def score_rule(formula, c0=9, c1=11, colour=0.2):
    return {"ranges": [{"sheetId": 1, "startRowIndex": 2, "endRowIndex": 535,
                        "startColumnIndex": c0, "endColumnIndex": c1}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                          "values": [{"userEnteredValue": formula}]},
                            "format": {"backgroundColor": {"green": colour}}}}


class Colours(FakeSheet):
    def __init__(self, rules):
        super().__init__(pipeline())
        self.meta["conditionalFormats"] = rules

    def _post(self, path, body):
        self.posts.append((path, body))
        for req in body.get("requests", []):
            if "updateConditionalFormatRule" in req:
                u = req["updateConditionalFormatRule"]
                self.meta["conditionalFormats"][u["index"]] = json.loads(json.dumps(u["rule"]))
        return {}


def test_the_score_colours_come_back_to_ai_score_alone():
    verdict_rule = {"ranges": [{"sheetId": 1, "startColumnIndex": 0, "endColumnIndex": 1}],
                    "booleanRule": {"condition": {"type": "TEXT_EQ",
                                                  "values": [{"userEnteredValue": "Yes"}]}}}
    row_rule = score_rule("=$J3>=9", c0=0, c1=N_COLS)
    # His own: a J:K rule keyed on his score, and a $J rule over the whole
    # row. Neither is one of the three that grew when he inserted K.
    his_rule = score_rule("=$K3>=7", colour=0.7)
    banner = score_rule("=$J3>=10", c0=COLS[AI_SCORE], c1=N_COLS, colour=0.1)
    rules = [score_rule("=$J3>=9"), score_rule("=$J3=8", colour=0.5),
             score_rule("=$J3<=7", colour=0.9), verdict_rule, row_rule, his_rule, banner]
    s = Colours(json.loads(json.dumps(rules)))
    out = s.narrow_score_colours()
    assert out == {"narrowed": 3, "noop": False, "verified": True}
    after = s.meta["conditionalFormats"]
    for rule in after[:3]:
        assert [(r["startColumnIndex"], r["endColumnIndex"]) for r in rule["ranges"]] == [(9, 10)]
    assert [r["booleanRule"] for r in after] == [r["booleanRule"] for r in rules]
    assert after[3:] == rules[3:], "a rule not reaching into his columns is not ours to move"
    n = len(s.posts)
    assert s.narrow_score_colours()["noop"] and len(s.posts) == n


def test_apply_layout_narrows_the_colours_and_verifies():
    class Laid(Colours):
        def _get(self, path, params=None):
            if path == "" and "columnMetadata" in str((params or {}).get("fields")):
                return {"sheets": [{"properties": {"sheetId": 1, "gridProperties": {
                    "rowCount": 200, "frozenRowCount": 2, "frozenColumnCount": 3}},
                    "data": [{"columnMetadata": [
                        {"pixelSize": layout.WIDTHS[h], "hiddenByUser": h not in layout.VISIBLE}
                        for h in HEADERS]}]}]}
            return super()._get(path, params)

    s = Laid([score_rule("=$J3>=9")])
    out = layout.apply_layout(s, sheet_id=1)
    assert out["verified"] and out["score_colours"] == 1


# ---------- every renamed column, end to end ----------

def test_the_checks_and_the_review_email_run_over_a_row_built_from_headers():
    """A missed rename raises 'unknown column' inside a try block in
    production and quietly disables a whole pass. Here it fails."""
    cells = his(make_row(company="Acme", role="VP Strategy",
                         jd_url="https://job-boards.greenhouse.io/acme/jobs/1", score=8,
                         why_it_fits="The argument.", source="ats_sweep",
                         jd_snippet="x"), "7", "")
    rows = [SheetRow(row_number=3, cells=cells, verdict="New", company="Acme",
                     role="VP Strategy", jd_url=None)]

    class Stub:
        sheet_id = 1

        def _sheet_meta(self, sheet_id=None):
            return {"properties": {"gridProperties": {"rowCount": 900}}}

        def _validation_at(self, row, tab="Pipeline", sheet_id=None):
            return {"condition": {"type": "ONE_OF_LIST"}}

    findings = invariants.check_all(Stub(), rows, [])
    assert not [f for f in findings if f.state == invariants.BROKEN]
    # The email lists what he has still to score, so its row has K and M blank.
    blank = his(list(cells), "", "")
    rows = [SheetRow(row_number=3, cells=blank, verdict="New", company="Acme",
                     role="VP Strategy", jd_url=None)]
    subject, html, text = alerts.review_email(rows, staged=1)
    assert ">AI score</th>" in html and ">Score</th>" not in html
    assert "The argument." in html and ">8<" in html
