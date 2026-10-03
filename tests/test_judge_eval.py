"""The judge's measurements, offline: the maths is right, and the recorded runs
say what the report claims. The records are tests/fixtures/judge_eval*.json and
judge_dev.json, written by `python -m hunter.run judge-eval`."""
import json
import pathlib

import pytest

from hunter import judge_eval

FIX = pathlib.Path(__file__).parent / "fixtures"


def row(label, verdict, fit, verdict2=None, fit2=None):
    return {"label": label, "verdict": verdict, "fit": fit, "company": "c", "title": "t",
            "verdict2": verdict2, "fit2": fit2}


def test_wilson_is_honest_about_small_samples():
    rate, lo, hi = judge_eval.wilson(12, 13)
    assert rate == pytest.approx(0.923, abs=1e-3)
    assert lo < 0.7 and hi < 1.0, "12 of 13 is not certainty"
    assert judge_eval.wilson(0, 0)[0] != judge_eval.wilson(0, 0)[0], "no sample is no rate"


def test_auc_counts_order_not_threshold():
    perfect = [row("yes", "present", 8), row("no", "reject", 3)]
    assert judge_eval.auc(perfect) == 1.0
    coin = [row("yes", "hold", 5), row("no", "hold", 5)]
    assert judge_eval.auc(coin) == 0.5
    backwards = [row("yes", "reject", 2), row("no", "present", 9)]
    assert judge_eval.auc(backwards) == 0.0
    assert judge_eval.auc([row("yes", "pending", None), row("no", "reject", 2)]) != \
        judge_eval.auc([row("yes", "pending", None), row("no", "reject", 2)])


def test_the_threshold_is_the_strictest_that_keeps_nine_in_ten_of_his_yes():
    rows = ([row("yes", "present", f) for f in (9, 8, 8, 7, 7, 7, 6, 6, 6, 6)]
            + [row("no", "present", 6)] * 5 + [row("no", "reject", 2)] * 20)
    # fit 7 keeps 6 of 10, fit 6 keeps all 10
    assert judge_eval.choose_threshold(rows) == 6
    # none reaches nine in ten: the one keeping the most, strictest on a tie
    short = [row("yes", "present", 6), row("yes", "reject", 2), row("no", "present", 9)]
    assert judge_eval.choose_threshold(short) == 6
    assert judge_eval.choose_threshold([row("no", "reject", 2)]) is None


def test_a_hold_is_never_counted_as_presented():
    rows = [row("yes", "hold", 9), row("no", "present", 9)]
    m = judge_eval.metrics(rows, 8)
    assert m["presented"] == 1 and m["yes_recall"][0] == 0.0


def test_the_first_judge_failed_and_the_record_says_so():
    """v1 is kept as evidence. It blocked 99 percent of his declines and every
    one of his 13 held-out Yes roles, which is why v2 exists."""
    v1 = json.loads((FIX / "judge_eval_v1.json").read_text())
    assert v1["meta"]["prompt_version"] == "2026-10-03.1"
    m = judge_eval.metrics(v1["rows"], 8)
    assert m["yes_recall"][0] == 0.0
    assert sum(r["label"] == "yes" for r in v1["rows"]) == 13


def _rec(name):
    return json.loads((FIX / name).read_text())


def test_the_threshold_was_chosen_on_the_development_window_not_the_holdout():
    dev, hold = _rec("judge_dev.json"), _rec("judge_eval.json")
    assert dev["meta"]["window"] == "development" and hold["meta"]["window"] == "holdout"
    assert dev["meta"]["prompt_version"] == hold["meta"]["prompt_version"]
    assert judge_eval.choose_threshold(dev["rows"]) == hold["chosen_threshold"]
    assert hold["threshold_from"] == "given", "the holdout must not pick its own threshold"
    from hunter import judge
    assert judge.MIN_FIT == hold["chosen_threshold"], "live staging uses the measured threshold"


def test_the_current_judge_is_the_bar_a_prompt_change_must_clear():
    """The recorded holdout for the prompt in use. A prompt change re-runs
    judge-eval and replaces this record; one that shows fewer of his Yes roles
    or a lower precision than today's system fails here, like test_taste.py.

    Recorded 2026-10-03 for 2026-10-03.3 (his four rules of that day), on the
    same 115 roles as v2: 9 of 13 Yes shown at fit 6 (v2: 7), precision 27
    percent (v2: 22, the system that ran: 11), 74 percent of declines blocked,
    rank AUC 0.75. Three of the four Yes roles it hides are ones his own cut
    now removes (an agency holding company twice, a PE firm). One pass only,
    to save cost, so agreement with itself is not measured on this record."""
    hold = _rec("judge_eval.json")
    t = hold["chosen_threshold"]
    m = judge_eval.metrics(hold["rows"], t)
    shown = round(m["yes_recall"][0] * sum(r["label"] == "yes" for r in hold["rows"]
                                            if r["verdict"] not in (None, "pending")))
    assert shown >= 9
    assert m["precision"][0] > hold["baseline"]["precision"][0]
    assert m["declines_blocked"][0] >= 0.7
    assert judge_eval.auc(hold["rows"]) >= 0.7
    if hold["meta"].get("replicas", 2) >= 2:
        assert judge_eval.agreement(hold["rows"], t)[0] >= 0.9
    from hunter import judge
    assert hold["meta"]["prompt_version"] == judge.PROMPT_VERSION, \
        "the prompt changed without re-running judge-eval"


def test_the_judge_runs_at_the_effort_its_record_supports():
    """Low effort, measured 2026-10-03 on the development window against high:
    AUC 0.93 against 0.92, the same Yes roles shown, 24 percent cheaper."""
    from hunter import judge
    low, high = _rec("judge_dev_low.json"), _rec("judge_dev.json")
    assert judge.DEFAULT_EFFORT == low["meta"]["effort"] == "low"
    assert low["meta"]["prompt_version"] == high["meta"]["prompt_version"] == judge.PROMPT_VERSION
    assert judge_eval.auc(low["rows"]) >= judge_eval.auc(high["rows"]) - 0.02
    t = high["chosen_threshold"]
    assert judge_eval.metrics(low["rows"], t)["yes_recall"][0] >= \
        judge_eval.metrics(high["rows"], t)["yes_recall"][0] - 0.01


def test_a_cheap_first_pass_was_measured_and_is_not_worth_it():
    """Haiku as a first reader, measured on 192 roles he ruled on: at the only
    line that keeps his Yes roles it removes about a fifth of the judge's work,
    and one notch stricter it loses a third of them (later, Syntrace, Innovamat:
    his taste, not the rule). So triage.py is not in the path."""
    rows = _rec("triage_eval.json")["rows"]
    yes = [r for r in rows if r["label"] == "yes"]

    def kept(k):
        return sum(1 for r in yes if r["score"] is None or r["score"] >= k)

    def read(k):
        return sum(1 for r in rows if r["score"] is None or r["score"] >= k) / len(rows)
    assert kept(2) >= len(yes) - 1 and read(2) > 0.75
    assert kept(3) < 0.8 * len(yes)
