"""The order he reads Pipeline in, measured against his own scores
(rankscore.py, tests/fixtures/rank_eval.json).

Fitted on the roles he scored first and measured once on the ones after, so
it is never graded on the scores it was fitted to. It replaced the AI Score
only because it beat it there; a change that does worse is a regression."""
import json
import pathlib

from hunter import rankscore as R

FX = json.loads((pathlib.Path(__file__).parent / "fixtures" / "rank_eval.json").read_text())
ROWS = FX["rows"]
CUT = int(len(ROWS) * 0.65)


def test_the_fixture_reads_the_signals_the_model_reads():
    assert FX["names"] == R.NAMES and all(len(r["x"]) == len(R.NAMES) for r in ROWS)


def test_fitted_on_his_earlier_scores_it_ranks_his_later_ones_better_than_the_ai_score(capsys):
    train, test = ROWS[:CUT], ROWS[CUT:]
    model = R.fit_his([r["x"] for r in train], [float(r["his"]) for r in train])
    his = [float(r["his"]) for r in test]
    new = R.spearman([R.predict(model, r["x"]) for r in test], his)
    old = R.spearman([float(r["ai_score"] or 0) for r in test], his)
    judge = R.spearman([float(r["judge_fit"] or 0) for r in test], his)
    with capsys.disabled():
        print(f"\nrank eval: {len(test)} later scores, Spearman against his score: "
              f"new {new:.2f}, AI Score {old:.2f}, judge fit {judge:.2f}")
    assert new > old + 0.2
    assert new >= 0.3


def test_no_signal_he_named_counts_against_a_role():
    model = R.fit_his([r["x"] for r in ROWS], [float(r["his"]) for r in ROWS])
    assert all(model["weights"][n] >= 0 for n in R.HIS_SIGNALS)


def test_the_shown_score_is_the_old_one_until_a_model_is_switched_on():
    kw = dict(title="Head of GTM", location="New York (Hybrid)", comp="", jd_text="")
    assert R.shown(None, 6, **kw) == 6
    model = {"weights": {n: 0.0 for n in R.NAMES}, "intercept": 42.0}
    assert R.shown(model, 6, **kw) == 10
