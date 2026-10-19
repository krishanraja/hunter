"""The package bar, measured against what he ruled (tests/fixtures/package_verdicts.json).

The sibling of test_taste.py, for what he reads after a role is chosen. Until
2026-10-19 every check on a letter asked whether it was true and well formed,
none whether it persuades the person who reads it, so a Corporate Development
letter selling a builder of agents passed them all and reached him.

  rejected   packages he ruled against, with his reasons. Each must be refused.
  his_lines  sentences he wrote into letters himself: the altitude he writes at.
             Each must pass, or the bar is stricter than he is.
  record     every letter used before the reader brief existed. Not ruled on one
             by one, so measured and printed, never asserted.
"""
import json
import pathlib

import pytest

from hunter.apply import essays
from hunter.package import compose, proof

FX = json.loads((pathlib.Path(__file__).parent / "fixtures" / "package_verdicts.json")
                .read_text(encoding="utf-8"))


def letter_of(item):
    data = dict(item["letter"], hiring_lead="Hiring Team", asks=[])
    return compose.parse(data)


def altitude(item):
    return compose.altitude_problems(letter_of(item), item["posting"], family=item["family"])


@pytest.mark.parametrize("item", FX["rejected"], ids=lambda i: i["title"])
def test_every_package_he_rejected_is_refused(item):
    got = altitude(item)
    assert any("speak of his AI system" in g for g in got), got
    form = essays.form_problems({e["question"]: e["answer"] for e in item.get("essays", [])},
                                posting=item["posting"], family=item["family"])
    assert any("says how something is built" in g for g in form), form
    assert any("answers speak of his AI system" in g for g in form), form


def test_the_rejected_corporate_development_seat_is_read_as_one():
    assert FX["rejected"][0]["family"] == "corp_dev_strategy"
    assert "build, buy or partner" in compose.role_type("corp_dev_strategy")


@pytest.mark.parametrize("line", FX["his_lines"])
def test_the_lines_he_writes_himself_pass(line):
    assert proof.mechanics_in(line) == []
    assert not proof.says_ai_system(line)


def test_the_record_before_the_reader_brief_is_measured(capsys):
    tools = budget = 0
    for item in FX["record"]:
        got = altitude(item)
        tools += any("says how it is built" in g for g in got)
        budget += any("speak of his AI system" in g for g in got)
    with capsys.disabled():
        print(f"\npackage taste: {len(FX['rejected'])} rejected package(s) refused; "
              f"{len(FX['his_lines'])} line(s) of his own passed; of {len(FX['record'])} "
              f"letters before the reader brief, {tools} name a tool or internal and "
              f"{budget} speak of his AI system more than the seat allows")
    assert len(FX["record"]) >= 20


@pytest.mark.parametrize("form", FX["rejected_forms"], ids=lambda f: f["title"])
def test_a_form_that_tells_one_story_twice_is_refused(form):
    """His ruling, 2026-10-19: the Replit answers, rewritten without tools,
    both told the same publisher story. One proof point once per form."""
    answers = {a["question"]: a["answer"] for a in form["answers"]}
    got = essays.form_problems(answers, posting="", family=form["family"])
    assert any("retells a story" in g for g in got), got
    first, second = list(answers.values())
    assert essays.repeats(second, [first])
    assert not essays.repeats(second, [])
