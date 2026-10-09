"""The function bar, measured against his own rulings before it cuts anything.

It removes a judge call for a title naming only a function he has never taken.
It must cut none of his Yes roles, wherever they are recorded."""
import json
from pathlib import Path

from hunter import companybar

FX = Path(__file__).parent / "fixtures"
EVAL = json.loads((FX / "function_bar_eval.json").read_text())


def test_it_cuts_none_of_his_yes_titles():
    cut = [t for t in EVAL["yes"] if companybar.not_his_function(t)]
    assert cut == [], cut


def test_it_cuts_none_of_the_yes_rows_in_either_ground_truth():
    verdicts = json.loads((FX / "krish_verdicts.json").read_text())
    judged = json.loads((FX / "judge_eval.json").read_text())["rows"]
    for rows in (verdicts, judged):
        yes = [r["title"] for r in rows if str(r.get("label")).lower() == "yes"]
        assert yes and not [t for t in yes if companybar.not_his_function(t)]


def test_on_the_8_october_run_it_spares_a_quarter_of_the_calls_and_no_role_he_would_see():
    judged = EVAL["judged_2026_10_08"]
    cut = [r for r in judged if companybar.not_his_function(r["title"])]
    assert len(cut) >= 20
    assert not [r for r in cut if r["verdict"] == "present"]
    assert max(r["fit"] for r in cut) <= 3


def test_his_functions_are_never_cut_even_next_to_a_word_that_is_not():
    for t in ("Head of AI Native Operations", "Head of Field Engineering Operations",
              "Director, Finance and Strategy", "Chief of Staff to the CTO",
              "VP, Revenue Operations", "Head of Corporate Development"):
        assert not companybar.not_his_function(t), t


class _C:
    def __init__(self, title):
        self.role = type("R", (), {"title": title})()
        self.row = {}


def test_a_cut_role_is_blocked_with_its_reason_in_gate_mode_and_untouched_in_shadow():
    kept, cut, lines = companybar.function_cut([_C("VP of Finance"), _C("VP Sales")])
    assert [c.role.title for c in kept] == ["VP Sales"]
    assert cut[0].row["status"] == "blocked" and "FUNCTION" in cut[0].row["rejection_reason"]
    _, cut, _ = companybar.function_cut([_C("VP of Finance")], mark=False)
    assert cut[0].row == {}
