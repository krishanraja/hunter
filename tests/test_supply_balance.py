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


# ---------- geography decides what the scoring budget buys ----------

def test_a_company_that_hires_in_his_cities_is_scored_before_one_that_does_not():
    """Only 60 companies are scored per run (hunter_max_new_companies_per_run),
    and the order was blind to geography while G6 deleted 42 percent of the
    LinkedIn leg and 52 percent of the boards for being in the wrong place.
    """
    from hunter.prospect import Candidate
    here = Candidate(key="a", name="A", hires_in_his_cities=True)
    there = Candidate(key="b", name="B", hires_in_his_cities=False)
    assert here.priority > there.priority


def test_an_unknown_location_ranks_lower_and_is_never_dropped():
    """Never block on no evidence. A company hunter holds no location for is
    ranked below one it does, and still appears in the candidate list."""
    from hunter.prospect import Candidate
    unknown = Candidate(key="u", name="U", a16z=True)
    known = Candidate(key="k", name="K", a16z=True, hires_in_his_cities=True)
    assert known.priority > unknown.priority
    # and the unknown one still carries real priority of its own
    assert unknown.priority > 0


def test_geography_alone_does_not_outrank_the_company_signals():
    """It is a tiebreaker on where to spend the budget, not a verdict. A
    company in the right city and nothing else must not beat one his own
    contacts work at with a readable board."""
    from hunter.prospect import Candidate
    only_geo = Candidate(key="g", name="G", hires_in_his_cities=True)
    real = Candidate(key="r", name="R", a16z=True, has_board=True, in_contacts=3)
    assert real.priority > only_geo.priority


def test_the_location_test_agrees_with_the_existing_definition():
    """There are already three geography regexes in this repository and a
    fourth written here would drift from all of them.

    Checked by agreement on real portfolio strings rather than by grepping
    the source for the name: a local regex assigned to a variable called
    HIS_GEOGRAPHY passes that grep and is exactly the drift in question.
    """
    from hunter.company import HIS_GEOGRAPHY
    from hunter.prospect import hires_in_his_cities
    for loc in ("London, England", "New York", "Brooklyn, NY", "San Francisco",
                "Remote - US", "US-remote", "remote, anywhere", "Paris",
                "Berlin", "United Kingdom", "Singapore", "NYC"):
        assert hires_in_his_cities([loc]) is bool(HIS_GEOGRAPHY.search(loc)), loc


def test_a_missing_or_malformed_location_list_is_false_not_an_error():
    """These rows come from three different firms' portfolio exports."""
    from hunter.prospect import hires_in_his_cities
    for bad in (None, [], "", 0, {"city": "London"}, [None, 7]):
        assert hires_in_his_cities(bad) is False
    # a bare string is the one non-list shape worth honouring
    assert hires_in_his_cities("London, England") is True


def test_it_reads_every_location_not_only_the_first():
    """A company headquartered in Berlin that also hires US-remote is one he
    would take, and stopping at locations[0] would rank it as foreign."""
    from hunter.prospect import hires_in_his_cities
    assert hires_in_his_cities(["Berlin", "Munich", "Remote - US"]) is True
