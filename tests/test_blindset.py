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


def test_the_tab_shows_facts_and_nothing_of_the_judge():
    assert blindset.HEADER == ["Your verdict (Yes or No)", "Company", "Title", "Pay",
                               "Location", "Link", "Ref"]


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
    def __init__(self, grid=None, back=None):
        self.grid, self.back, self.writes, self.appended, self.posts = grid or [], back, [], [], []

    def read_tab_values(self, rng):
        return self.back if self.back is not None else self.grid

    def _get(self, path, params=None):
        return {"sheets": [{"properties": {"title": "Pipeline"}}]}

    def _post(self, path, body):
        self.posts.append(body)

    def _write(self, blocks, raw=False):
        self.writes.append((blocks, raw))

    def append_rows(self, rows):
        self.appended.extend(rows)


def test_reading_his_column_a():
    s = Sheet(grid=[["Yes", "C", "T", "", "", "u", "j1"], ["no", "C", "T", "", "", "u", "j2"],
                    ["", "C", "T", "", "", "u", "j3"], ["Declined - domain expertise", "", "", "", "",
                                                        "", "j4"]])
    assert blindset.read(s) == {"j1": "yes", "j2": "no", "j4": "no"}


def test_a_tab_that_does_not_read_back_is_an_error():
    picks = [{"job_id": "j1", "company": "C", "title": "T", "comp": "", "location": "",
              "url": "u"}]
    s = Sheet(back=[["something else"]])
    with pytest.raises(RuntimeError):
        blindset.write_tab(s, picks)
    ok = Sheet(back=[["j1"]])
    blindset.write_tab(ok, picks)
    assert ok.posts and ok.writes[0][1] is True, "a new tab, written raw"


def test_his_yes_reaches_pipeline_once_with_yes_in_column_a(monkeypatch):
    from hunter.sheet import COLS
    patched = []
    monkeypatch.setattr(blindset, "db_get", lambda *a, **k: [{"job_id": "j2"}])
    monkeypatch.setattr(blindset, "db_patch", lambda cfg, t, key, body: patched.append((key, body)))
    rec = {"picks": [dict(row(1), stratum="far", old_gate="G11"),
                     dict(row(2), stratum="present", old_gate="G11"),
                     dict(row(3), stratum="near", old_gate="G14")]}
    s = Sheet()
    moved = blindset.apply_yes(object(), s, rec, {"j1": "yes", "j2": "yes", "j3": "no"})
    assert moved == ["j1"], "j2 is already on his sheet; j3 he declined"
    assert s.appended[0][COLS["Verdict"]] == "Yes"
    assert blindset.FROM_BLIND in s.appended[0][COLS["Why It Fits"]]
    assert patched == [({"job_id": "j1"}, {"status": "staging",
                                          "presented_at": patched[0][1]["presented_at"]})]
