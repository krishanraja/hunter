"""No one supply leg and no one company may own a batch.

Measured against his own verdicts on 2026-09-24, and this file exists because
the numbers were a surprise in both directions:

  the LinkedIn keyword sweep carried 142 of the 231 verdicts he has ever
  given, 62 percent of the funnel, and ran at 12 percent

  the company boards ran at 67 percent on six verdicts each, starved

  Sierra alone put 14 roles on his sheet and he declined all 14

His words: "there are still way too many boring financial services and
healthcare jobs being put in here... there is no way on earth that the latest
additions are all thats out there in London and New York".
"""
from __future__ import annotations

import hunter.run as R


class Role:
    def __init__(self, company, title, source):
        self.company, self.title, self.source = company, title, source


def entry(company, title, source):
    return (Role(company, title, source), None, "", "")


def test_the_same_leg_under_four_labels_is_one_leg():
    """It reached the sheet as apify_linkedin, Apify LinkedIn, Apify LinkedIn
    (2026-09-10 sweep) and linkedin. Fragmented it looked like four small
    sources, and a cap keyed on the raw label would miss the next spelling."""
    for label in ("apify_linkedin", "Apify LinkedIn",
                  "Apify LinkedIn (2026-09-10 sweep)", "linkedin"):
        assert R.source_leg(label) == "linkedin keyword sweep", label
    for label in ("ashby", "ashby:sierra", "greenhouse:flex", "lever:rembrand"):
        assert R.source_leg(label) == "company board", label
    assert R.source_leg("a16z board") == "a16z portfolio"
    assert R.source_leg("Direct ATS sweep 2026-06-09") == "legacy ats sweep"
    assert R.source_leg("") == "unknown"


def test_one_company_cannot_take_a_quarter_of_the_sheet():
    """Sierra, 14 staged, 14 declined."""
    rows = [entry("Sierra", f"role {i}", "ashby:sierra") for i in range(14)]
    notes: list[str] = []
    kept = R.cap_by_leg_and_company(rows, notes, cap=40, per_company=3,
                                    leg_share=1.0)
    assert len(kept) == 3
    assert any("per company limit" in n and "Sierra" in n for n in notes)


def test_one_leg_cannot_starve_the_others():
    """Twenty from the keyword sweep and four from boards. The sweep is held to
    its share and every board role survives, because the trim runs on the
    ranked list and takes from the top of each leg."""
    # Distinct companies, as the real sweep returns them: Citi, JPMorganChase,
    # Capital One, PayPal, Pagaya, Early Warning and the rest. All at one
    # company would be trimmed by the per company limit first, which is right
    # and would not test this.
    rows = ([entry(f"Bank{i}", "vp partnerships", "apify_linkedin")
             for i in range(20)]
            + [entry(f"Co{i}", "head of gtm", "ashby") for i in range(4)])
    notes: list[str] = []
    kept = R.cap_by_leg_and_company(rows, notes, cap=40, per_company=3,
                                    leg_share=0.35)
    legs = [R.source_leg(e[0].source) for e in kept]
    assert legs.count("linkedin keyword sweep") == 14, "35 percent of 40"
    assert legs.count("company board") == 4, "no board role may be trimmed"
    assert any("per supply leg limit" in n for n in notes)


def test_a_small_batch_is_left_alone():
    """The caps are a ceiling, not a quota. Four roles from one leg is not a
    leg owning the funnel, and trimming it would only make a thin batch thinner."""
    rows = [entry(f"Co{i}", "head of gtm", "ashby") for i in range(4)]
    notes: list[str] = []
    kept = R.cap_by_leg_and_company(rows, notes, cap=40, per_company=3,
                                    leg_share=0.35)
    assert len(kept) == 4 and notes == []


def test_the_caps_can_be_turned_off():
    """A number he can change is a number he can argue with. Zero means off,
    and off must mean nothing is held rather than everything."""
    rows = [entry("Sierra", f"role {i}", "ashby:sierra") for i in range(14)]
    notes: list[str] = []
    kept = R.cap_by_leg_and_company(rows, notes, cap=40, per_company=0,
                                    leg_share=0)
    assert len(kept) == 14 and notes == []


def test_the_leg_breakdown_reports_share_as_well_as_rate():
    """The rate alone does not show starvation. 12 percent on 62 percent of the
    funnel and 67 percent on 3 percent of it is the whole diagnosis."""
    from hunter import batchstats as B
    b = B.Batch(key="2026-09-17", by_source={
        "apify_linkedin": {"verdicted": 100, "accepted": 12},
        "Apify LinkedIn": {"verdicted": 42, "accepted": 5},
        "ashby": {"verdicted": 6, "accepted": 4},
        "greenhouse:flex": {"verdicted": 4, "accepted": 0}})
    rows = dict((l, (a, v)) for l, a, v in B.by_leg([b], R.source_leg))
    assert rows["linkedin keyword sweep"] == (17, 142), "four labels, one leg"
    assert rows["company board"] == (4, 10)
    text = "\n".join(B.leg_lines([b], R.source_leg))
    assert "93% of the funnel" in text and "12%" in text
    assert "7% of the funnel" in text and "40%" in text
