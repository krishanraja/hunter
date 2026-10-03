"""The blind set, offline: strata, liveness, the weighting, and that his Yes
reaches Pipeline once."""
import random

import pytest

from hunter import blindset, judge


def J(verdict, fit):
    return judge.Judgement(job_id="x", verdict=verdict, fit=fit, confidence="high", answers={},
                           red_flags=[], likely_decline_code="none", why_it_fits="",
                           snippet="", model="m", served_model="m")


@pytest.mark.parametrize("verdict,fit,want", [
    ("present", 8, "present"), ("present", 6, "present"), ("present", 5, "near"),
    ("hold", 7, "near"), ("reject", 4, "near"), ("reject", 3, "far"),
    ("pending", None, None)])
def test_strata(verdict, fit, want):
    assert blindset.stratum(J(verdict, fit)) == want


def row(i):
    return {"job_id": f"j{i}", "company": f"C{i}", "title": "VP", "comp": "", "location": "NYC",
            "url": f"https://x/{i}", "rejection_reason": "G11: not a family"}


def test_quotas_skip_dead_postings_and_shuffle():
    judged = ([(row(i), J("present", 7)) for i in range(20)]
              + [(row(100 + i), J("reject", 4)) for i in range(20)]
              + [(row(200 + i), J("reject", 2)) for i in range(20)])
    dead = {"j0", "j1", "j2"}
    picks, sizes = blindset.pick(judged, random.Random(1), live=lambda r: r["job_id"] not in dead)
    assert sizes == {"present": 20, "near": 20, "far": 20}
    count = {s: sum(p["stratum"] == s for p in picks) for s, _ in blindset.STRATA}
    assert count == {"present": 15, "near": 15, "far": 10}
    assert not dead & {p["job_id"] for p in picks}, "never ask him about a closed posting"
    assert [p["stratum"] for p in picks] != sorted(p["stratum"] for p in picks), "shuffled"
    assert all(p["old_gate"] == "G11" for p in picks)


def test_a_row_is_a_pipeline_row_with_no_score():
    """His words: the same column format as Pipeline, always. The Score stays
    empty: the old score points the wrong way and the judge's fit would give
    its call away."""
    from hunter.sheet import COLS, HEADERS
    p = dict(row(1), why="Runs GTM. FIT: like Clay. RISK: pay unstated.",
             snippet="C1 sells software; the seat runs GTM.", source="linkedin")
    r = blindset.row_for(p)
    assert len(r) == len(HEADERS)
    assert r[COLS["Score"]] == ""
    assert r[COLS["Why It Fits"]].startswith("Runs GTM. FIT:")
    assert r[COLS["JD Snippet"]] == "C1 sells software; the seat runs GTM."
    assert "HYPERLINK" in r[COLS["Job Link"]] and "https://x/1" in r[COLS["Job Link"]]
    assert r[COLS["Business"]] == "C1" and r[COLS["Location"]] == "NYC"


RULE = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "Yes"}]},
        "showCustomUi": True}
CF = {"ranges": [{"sheetId": 7, "startRowIndex": 2, "endRowIndex": 540,
                  "startColumnIndex": 0, "endColumnIndex": 1}],
      "booleanRule": {"condition": {"type": "TEXT_EQ"}}}
SRC = {"properties": {"sheetId": 7, "title": "Pipeline",
                      "gridProperties": {"frozenRowCount": 2, "frozenColumnCount": 3}},
       "conditionalFormats": [CF, CF],
       "bandedRanges": [{"bandedRangeId": 1, "range": {"sheetId": 7, "startRowIndex": 2,
                                                       "endRowIndex": 540},
                         "rowProperties": {"firstBandColor": {}}}],
       "data": [{"columnMetadata": [{"pixelSize": 104}, {"pixelSize": 116, "hiddenByUser": True}],
                 "rowMetadata": [{"pixelSize": 34}, {"pixelSize": 12}, {"pixelSize": 62}],
                 "rowData": [{}, {}, {"values": [{"dataValidation": RULE}]}]}]}


def test_the_layout_is_read_from_pipeline_and_moved_onto_the_tab():
    reqs = blindset.layout_requests(SRC, 99, n_rows=34)
    kinds = [list(r)[0] for r in reqs]
    grid = reqs[0]["updateSheetProperties"]["properties"]["gridProperties"]
    assert grid["frozenRowCount"] == 2 and grid["frozenColumnCount"] == 3
    pastes = [r["copyPaste"] for r in reqs if "copyPaste" in r]
    assert pastes[0]["pasteType"] == "PASTE_NORMAL" and pastes[0]["destination"]["endRowIndex"] == 2
    fmt = pastes[1]
    assert fmt["pasteType"] == "PASTE_FORMAT", "carries the column A dropdown"
    assert (fmt["source"]["startRowIndex"], fmt["source"]["endRowIndex"]) == (2, 3)
    assert (fmt["destination"]["startRowIndex"], fmt["destination"]["endRowIndex"]) == (2, 36)
    assert not [r for r in reqs if "addBanding" in r or "addConditionalFormatRule" in r], \
        "pasting Pipeline's formats brings its colours and banding; adding them again duplicates"
    dims = [r["updateDimensionProperties"] for r in reqs if "updateDimensionProperties" in r]
    cols = [d for d in dims if d["range"]["dimension"] == "COLUMNS"]
    assert cols[1]["properties"] == {"pixelSize": 116, "hiddenByUser": True}
    ref = [d for d in cols if d["range"]["startIndex"] == blindset.REF_COL]
    assert ref and ref[0]["properties"]["hiddenByUser"] is True, "the job id column is hidden"
    rows_ = [d for d in dims if d["range"]["dimension"] == "ROWS"]
    assert rows_[-1]["range"] == {"sheetId": 99, "dimension": "ROWS", "startIndex": 2,
                                  "endIndex": 36} and rows_[-1]["properties"]["pixelSize"] == 62
    assert "copyPaste" in kinds


def test_finishing_adds_only_what_the_paste_missed_and_always_the_dropdown():
    pasted = {"conditionalFormats": [CF, CF], "bandedRanges": [{"bandedRangeId": 5}]}
    reqs = blindset.finishing_requests(SRC, pasted, 99, 34)
    assert [list(r)[0] for r in reqs] == ["setDataValidation"]
    dv = reqs[0]["setDataValidation"]
    assert dv["rule"] == RULE and dv["range"]["startRowIndex"] == 2 and dv["range"]["endRowIndex"] == 36
    bare = blindset.finishing_requests(SRC, {}, 99, 34)
    kinds = [list(r)[0] for r in bare]
    assert kinds.count("addConditionalFormatRule") == 2 and "addBanding" in kinds
    band = [r["addBanding"]["bandedRange"] for r in bare if "addBanding" in r][0]
    assert band["range"]["sheetId"] == 99 and "bandedRangeId" not in band


def test_the_estimate_weights_each_stratum_by_its_size():
    """5 of 10 Yes among presents drawn from 20, 1 of 10 among near drawn from 60,
    0 of 10 among far drawn from 100. Estimated Yes in the pool: 10 + 6 + 0, so
    the judge shows 10 of 16."""
    picks = ([{"job_id": f"p{i}", "stratum": "present"} for i in range(10)]
             + [{"job_id": f"n{i}", "stratum": "near"} for i in range(10)]
             + [{"job_id": f"f{i}", "stratum": "far"} for i in range(10)])
    labels = {p["job_id"]: "no" for p in picks}
    labels.update({f"p{i}": "yes" for i in range(5)})
    labels["n0"] = "yes"
    rec = {"picks": picks, "strata": {"present": 20, "near": 60, "far": 100}}
    out = blindset.evaluate(rec, labels)
    assert out["yes_shown_est"] == pytest.approx(10 / 16, abs=1e-3)
    assert out["declines_hidden_est"] == pytest.approx((54 + 100) / (10 + 54 + 100), abs=1e-3)
    assert out["strata"]["present"]["yes"] == 5


class Sheet:
    def __init__(self, grid=None, back=None, head=None, rule=RULE):
        self.grid, self.back = grid or [], back
        self.head, self.rule = head, rule
        self.writes, self.appended, self.posts = [], [], []

    def read_tab_values(self, rng):
        if rng.endswith("1") and "A1:" in rng:
            return self.head
        return self.back if self.back is not None else self.grid

    def _get(self, path, params=None):
        # As the Sheets API does: a request with ranges answers only for the
        # sheets those ranges touch.
        dst = {"properties": {"sheetId": 99, "title": "Blind Set"},
               "conditionalFormats": [CF, CF], "bandedRanges": [{"bandedRangeId": 5}]}
        rng = (params or {}).get("ranges", "")
        if rng.startswith("'Blind Set'"):
            return {"sheets": [{"data": [{"rowData": [{"values": [
                {"dataValidation": self.rule}]}]}]}]}
        if rng:
            return {"sheets": [SRC]}
        return {"sheets": [SRC, dst]}

    def _post(self, path, body):
        self.posts.append((path, body))

    def _write(self, blocks, raw=False):
        self.writes.append((blocks, raw))

    def append_rows(self, rows):
        from hunter.sheet import validate_row
        for r in rows:
            assert not validate_row(r, is_append=True), validate_row(r, is_append=True)
        self.appended.extend(rows)
        return f"Pipeline!A12:AD{11 + len(rows)}"

    def set_verdicts(self, mapping):
        self.verdicts = mapping
        return len(mapping)


def ref_row(verdict, ref):
    return [verdict] + [""] * (blindset.REF_COL - 1) + [ref]


def test_reading_his_column_a():
    s = Sheet(grid=[ref_row("Yes", "j1"), ref_row("no", "j2"), ref_row("", "j3"),
                    ref_row("Declined - domain expertise", "j4"), ref_row("New", "j5")])
    assert blindset.read(s) == {"j1": "yes", "j2": "no", "j4": "no"}


def test_a_tab_that_does_not_read_back_is_an_error():
    from hunter.sheet import HEADERS
    picks = [dict(row(1), why="w", snippet="s")]
    with pytest.raises(RuntimeError):
        blindset.write_tab(Sheet(back=[["something else"]], head=[HEADERS]), picks)
    with pytest.raises(RuntimeError, match="header"):
        blindset.write_tab(Sheet(back=[["j1"]], head=[["Verdict", "Company"]]), picks)
    with pytest.raises(RuntimeError, match="dropdown"):
        blindset.write_tab(Sheet(back=[["j1"]], head=[HEADERS], rule=None), picks)
    ok = Sheet(back=[["j1"]], head=[HEADERS])
    blindset.write_tab(ok, picks)
    assert not any("addSheet" in str(b) for _, b in ok.posts), "the tab exists; never added twice"
    batch = [b for path, b in ok.posts if path == ":batchUpdate"]
    removed = [r for r in batch[0]["requests"]]
    assert sum("deleteConditionalFormatRule" in r for r in removed) == 2
    assert [r["deleteConditionalFormatRule"]["index"] for r in removed
            if "deleteConditionalFormatRule" in r] == [1, 0], "last first"
    data, raw = ok.writes[0]
    assert not raw, "HYPERLINK formulas need USER_ENTERED, as on Pipeline"
    assert data[0][0].startswith("'Blind Set'!A3:")
    assert ok.writes[1] == ([("'Blind Set'!AE3:AE3", [["j1"]])], True)


def test_his_yes_reaches_pipeline_once_as_the_same_row(monkeypatch):
    from hunter.sheet import COLS
    patched = []

    def db_get(cfg, table, params):
        if "presented_at" in params:
            return [{"job_id": "j2"}]
        return [{"job_id": "j1", "score": 6}]
    monkeypatch.setattr(blindset, "db_get", db_get)
    monkeypatch.setattr(blindset, "db_patch", lambda cfg, t, key, body: patched.append((key, body)))
    rec = {"picks": [dict(row(1), stratum="far", old_gate="G11", why="The case. FIT: x. RISK: y.",
                          snippet="snip"),
                     dict(row(2), stratum="present", old_gate="G11"),
                     dict(row(3), stratum="near", old_gate="G14")]}
    s = Sheet()
    moved = blindset.apply_yes(object(), s, rec, {"j1": "yes", "j2": "yes", "j3": "no"})
    assert moved == ["j1"], "j2 is already on his sheet; j3 he declined"
    r = s.appended[0]
    assert r[COLS["Verdict"]] == "New", "Pipeline takes a new row only as New"
    assert s.verdicts == {12: "Yes"}, "then his Yes is written onto the row it landed on"
    assert r[COLS["Score"]] == "6" and r[COLS["Source"]] == blindset.SOURCE
    assert r[COLS["Why It Fits"]] == "The case. FIT: x. RISK: y."
    assert r[COLS["JD Snippet"]] == "snip"
    assert patched == [({"job_id": "j1"}, {"status": "staging",
                                          "presented_at": patched[0][1]["presented_at"]})]


def test_his_blind_set_declines_become_rulings_the_judge_reads(monkeypatch, tmp_path):
    import json
    from hunter import judgedata
    rec = {"drawn_at": "2026-10-03T20:00:00+00:00",
           "picks": [dict(row(1), stratum="far", old_gate="G11"),
                     dict(row(2), stratum="near", old_gate="G11"),
                     dict(row(3), stratum="present", old_gate="G11")]}
    f = tmp_path / "blind.json"
    f.write_text(json.dumps(rec))
    monkeypatch.setattr(blindset, "RECORD", f)
    s = Sheet(grid=[ref_row("Declined - business uninteresting", "j1"), ref_row("Yes", "j2"),
                    ref_row("New", "j3")])
    got = judgedata.blind_rulings(s, {"j1": {"jd_text": "posting"}}, seen={"j2"})
    assert [(r.job_id, r.label, r.code) for r in got] == [("j1", "no", "business_uninteresting")]
    assert got[0].presented_at == rec["drawn_at"] and got[0].source == "blind set"
    assert got[0].jd_text == "posting"


def test_his_verdicts_outlive_the_tab(monkeypatch, tmp_path):
    """He retired the Blind Set tab on 2026-10-03 once his Yes roles were on
    Pipeline. His verdicts were saved into the record first, so his declines
    stay rulings the judge reads with no tab to read them from."""
    import json
    from hunter import judgedata
    rec = {"drawn_at": "2026-10-03T20:00:00+00:00",
           "picks": [dict(row(1), stratum="far", old_gate="G11", his_verdict="Declined - function wrong")]}
    f = tmp_path / "blind.json"
    f.write_text(json.dumps(rec))
    monkeypatch.setattr(blindset, "RECORD", f)

    class NoTab:
        def read_tab_values(self, rng):
            raise AssertionError("the tab is gone and must not be read")
    got = judgedata.blind_rulings(NoTab(), {}, seen=set())
    assert [(r.job_id, r.label, r.code) for r in got] == [("j1", "no", "function_wrong")]
