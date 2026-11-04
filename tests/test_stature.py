"""A19: a letter at his rank (his words, 2026-11-04: "I honestly don't think you
make me sound impressive enough on any application"). Made-up companies only."""
import json
import pathlib
from types import SimpleNamespace as NS

import pytest

from hunter.package import compose, stature

FX = json.loads((pathlib.Path(__file__).parent / "fixtures" / "package_verdicts.json")
                .read_text(encoding="utf-8"))
ACME = frozenset({"Acme"})


def letter(**over):
    a = {"hiring_lead": "Hiring Team",
         "hook": "As Head of Platform Sales I scaled revenue from $4M to $38M across 12 "
                 "markets, and Acme's next market is exactly that kind of build.",
         "identity": "I'm a commercial leader, formerly Head of Platform Sales at Nexxen, "
                     "with 16 years building revenue in media and data.",
         "bullets": [{"label": "Nexxen, Head of Platform Sales:", "text": "scaled revenue from $4M to $38M."},
                     {"label": "Captify:", "text": "built a region from $0 to $12M ARR."},
                     {"label": "Nine:", "text": "grew data revenue from $9M to $61M."}],
         "why_now": "Acme's buyers are about to move their budgets to agents, and the "
                    "commercial model for that does not exist yet.",
         "close": "I'll bring the first draft of that model to our first conversation.",
         "cv_summary": "Sixteen years building revenue in media and data."}
    a.update(over)
    return compose.parse(a)


def got(l, employers=("Nexxen", "Captify", "Nine")):
    return stature.problems(l, ACME, employers)


def test_a_letter_at_his_rank_passes():
    assert got(letter()) == []


@pytest.mark.parametrize("opening", [
    "Acme needs a Regional Sales Director who can lead the team.",
    "Acme wants the person who can answer the agent question.",
    "Acme's team is looking for a leader who has done this.",
    "Acme is hiring its first Head of Partnerships.",
])
def test_opening_by_restating_the_job_is_refused(opening):
    assert any("opens by restating" in p for p in got(letter(hook=opening + " I have done it.")))


def test_mapping_a_part_back_to_the_posting_is_refused():
    l = letter(bullets=[{"label": "Captify:", "text": "built a region, the first ask in the posting."},
                        {"label": "Nine:", "text": "grew revenue; Acme asks for exactly this."},
                        {"label": "Nexxen:", "text": "scaled revenue from $4M to $38M."}])
    assert sum("maps itself back" in p for p in got(l)) == 2


@pytest.mark.parametrize("hedge", ["I backed the wrong product and was wrong.",
                                   "While I lack a STEM degree, I learn fast."])
def test_a_volunteered_weakness_is_refused(hedge):
    assert any("hedges" in p for p in got(letter(close=hedge)))


def test_no_rank_no_employer_no_headline_are_each_refused():
    l = letter(identity="I'm a commercial leader who builds sales engines.",
               hook="I build sales engines.", cv_summary="I build sales engines.")
    p = got(l)
    assert any("never says his rank" in x for x in p)
    assert any("names an employer" in x for x in p)
    assert any("no headline figure" in x for x in p)


def test_with_no_employers_recorded_the_employer_half_is_skipped():
    l = letter(identity="I'm a Head of Sales who builds engines.", hook="I build engines for 16 years.")
    assert not any("names an employer" in x for x in got(l, employers=()))


@pytest.mark.parametrize("item", FX["undersold"], ids=lambda i: i["title"])
def test_the_letters_he_said_undersold_him_are_refused(item):
    l = compose.parse(dict(item["letter"], hiring_lead="Hiring Team", asks=[]))
    assert got(l), "A19 must refuse a letter he said did not make him sound impressive"


def test_the_standing_block_is_his_rows_only():
    bank = {"a": NS(field_name="Standing: headline", value="16 years, four markets", usable=True),
            "b": NS(field_name="Standing: employers", value="Nexxen, Captify", usable=True),
            "c": NS(field_name="Email (job search)", value="x", usable=True),
            "d": NS(field_name="Standing: press", value="", usable=True)}
    assert stature.block(bank) == "- headline: 16 years, four markets"
    assert stature.employers(bank) == ("Nexxen", "Captify")
    assert stature.block({}) == ""


def test_a_form_answer_that_apologises_is_refused():
    from hunter.apply import essays
    got = essays.form_problems({"Why you?": "Admittedly I have not run this function, but I learn fast."},
                               posting="Acme hires a VP.")
    assert any("hedges or apologises" in g for g in got)
    assert not any("hedges" in g for g in essays.form_problems(
        {"Why you?": "I built a region from $0 to $12M ARR."}, posting="Acme hires a VP."))
