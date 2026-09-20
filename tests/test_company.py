"""The company scorer, and the evidence rule that holds it up.

Every test here was written to fail against a specific way of being wrong:
scoring a company hunter knows nothing about, defaulting an absent
observation, or storing a claim with no source. Those are the same defect
CLAUDE.md section 1 names, moved from a click to a number.
"""
import pytest

from hunter.company import (
    Fact, Facts, CompanyScore, Component, MIN_EVIDENCED, MIN_DENOMINATOR,
    NEEDS_EVIDENCE, SWEEP_FLOOR, WEIGHTS, score_company, sweep_set,
    category_fit, backing, venture_stage, commercial_whitespace, geography,
    disqualifier, public_or_pe,
)

WEB = "https://example.com/about"


def f(**kw):
    """A Facts with sensible identity and whatever the test cares about."""
    return Facts(slug=kw.pop("slug", "acme"), name=kw.pop("name", "Acme"), **kw)


# ---------- a fact without a source is not a fact ----------

def test_a_fact_with_no_source_is_refused():
    """An unsourced claim is the thing this module exists to prevent. It is
    refused where it is built, not filtered later, so there is no path that
    stores one."""
    with pytest.raises(ValueError, match="no source"):
        Fact("Series B, led by Sequoia", "")


def test_a_fact_with_no_value_is_refused():
    with pytest.raises(ValueError, match="absent observation"):
        Fact("", WEB)


# ---------- an absent observation is unknown, never zero-by-default ----------

def test_an_unobserved_component_says_unknown_rather_than_scoring_it():
    c = backing(f())
    assert c.points == 0 and c.unknown
    assert "not established" in c.evidence
    assert c.source == "", "an unknown has nothing to cite"


def test_no_backer_on_record_is_a_different_answer_from_not_asked():
    """Nought points twice, but only one of them is an observation. The
    difference decides whether the company has enough evidence to be scored
    at all, so collapsing them would let a company hunter knows nothing
    about reach the sweep set."""
    asked = backing(f(investors=Fact("bootstrapped, no outside capital", WEB)))
    not_asked = backing(f())
    assert asked.points == not_asked.points == 0
    assert asked.evidenced and not not_asked.evidenced


def test_a_company_with_too_little_evidence_is_not_given_a_score():
    s = score_company(f(locations=Fact("New York", WEB)))
    assert s.status == NEEDS_EVIDENCE
    assert s.tier is None and not s.sweeps, (
        "a company hunter knows one thing about must not enter the sweep set")


def test_a_company_whose_business_is_unknown_is_never_scored():
    """Ramp, Cursor and CoreWeave each scored a perfect 10 because their job
    boards mentioned London. Knowing what a business does is not one
    consideration among five, it is the prerequisite for having a view."""
    s = score_company(f(locations=Fact("New York and London", WEB),
                        investors=Fact("Series B led by Sequoia", WEB),
                        stage=Fact("Series B", WEB),
                        founded=Fact("founded 2021", WEB)))
    assert s.status == NEEDS_EVIDENCE and not s.sweeps


def test_one_observation_is_never_treated_as_certainty():
    """Normalising over only what was observed made a single match look like
    a perfect score. Dividing by a floor says plainly that one thing known
    is not a judgement."""
    s = score_company(f(
        what_it_does=Fact("Agentic AI for enterprise support teams", WEB),
        locations=Fact("New York", WEB)))
    top = score_company(f(
        what_it_does=Fact("Agentic AI for enterprise support teams", WEB),
        locations=Fact("New York", WEB),
        investors=Fact("Series A led by Accel", WEB),
        stage=Fact("Series A", WEB), founded=Fact("founded 2023", WEB)))
    assert s.total < top.total
    assert s.total == round(10 * 5.0 / MIN_DENOMINATOR, 1)


def test_the_evidence_floor_is_what_lets_a_company_be_scored():
    s = score_company(f(
        what_it_does=Fact("Agentic AI for enterprise support teams", WEB),
        investors=Fact("Series A led by Accel", WEB),
        locations=Fact("New York and London", WEB)))
    assert s.status != NEEDS_EVIDENCE
    assert s.evidenced_count >= MIN_EVIDENCED


# ---------- category, the heaviest weight ----------

@pytest.mark.parametrize("sentence,expect", [
    ("Agentic AI that resolves customer support tickets", 4.0),
    ("Text to video generation for marketing teams", 4.0),
    ("Pays publishers when AI crawlers read their content", 4.0),
    ("GPU cloud and inference platform for model serving", 4.0),
    ("A retail media and connected TV advertising platform", 4.0),
    ("Developer tools for building data pipelines", 2.0),
    ("A chain of dental practices across the midwest", 0.0),
])
def test_category_scores_what_the_business_says_it_does(sentence, expect):
    assert category_fit(f(what_it_does=Fact(sentence, WEB))).points == expect


def test_adjacent_is_recorded_as_adjacent_and_not_as_a_hit():
    c = category_fit(f(what_it_does=Fact("A B2B software platform", WEB)))
    assert c.points == 2.0 and "adjacent" in c.evidence


# ---------- stage and age is what sinks a holdco, without naming one ----------

def test_a_public_company_is_penalised_from_its_own_description():
    c = public_or_pe(f(stage=Fact("publicly traded (NYSE: OMC)", WEB)))
    assert c is not None and c.points < 0


def test_an_old_company_is_penalised_on_its_founding_year():
    c = public_or_pe(f(founded=Fact("founded in 1986", WEB)))
    assert c is not None and c.points < 0


def test_a_recent_series_b_scores_the_top_of_the_band():
    c = venture_stage(f(stage=Fact("Series B", WEB),
                        founded=Fact("founded 2021", WEB)))
    assert c.points == WEIGHTS["venture_stage"]


def test_a_page_that_never_mentions_funding_is_unknown_not_zero():
    """This was the defect that admitted 11 percent of his own named target
    list. A homepage does not say who led the Series B, and an absent fact
    was arithmetically identical to a bad one."""
    c = venture_stage(f(what_it_does=Fact("Agentic AI for support teams", WEB)))
    assert c.unknown and c.points == 0


def test_a_customer_logo_wall_is_not_the_company_being_public():
    """Synthesia and Writer both say they are trusted by the Fortune 100 and
    were both scored as public megacaps for saying so."""
    assert public_or_pe(f(what_it_does=Fact(
        "The AI video platform trusted by 90%+ of the Fortune 100", WEB))) is None


# ---------- the disqualifier describes a shape, it does not name a company ----------

def test_an_agency_holdco_is_disqualified_by_what_it_is():
    """Named blocklists cannot generalise to the companies he has not thought
    of, which is the entire point of scoring instead of listing."""
    c = disqualifier(f(what_it_does=Fact(
        "One of the largest advertising holding companies in the world", WEB)))
    assert c is not None and c.points == -5


def test_nothing_disqualifying_observed_occupies_no_evidence_slot():
    """An absence of bad news is not evidence of anything, and must not help
    a company over the evidence floor."""
    assert disqualifier(f(what_it_does=Fact("Agentic AI for support", WEB))) is None


def test_a_disqualified_company_cannot_be_dragged_back_over_the_floor():
    s = score_company(f(
        what_it_does=Fact("A global advertising holding company with AI agents "
                          "for enterprise marketing", WEB),
        investors=Fact("publicly held", WEB),
        stage=Fact("publicly traded (NYSE: OMC)", WEB),
        locations=Fact("New York", WEB)))
    assert s.total < SWEEP_FLOOR and not s.sweeps


# ---------- whitespace, the seat he actually wants ----------

def test_a_business_still_building_its_commercial_model_scores_whitespace():
    c = commercial_whitespace(f(commercial_leadership=Fact(
        "no CRO or CCO in post; the founder runs revenue", WEB)))
    assert c.points == 2


def test_a_company_with_a_cro_already_in_post_scores_none_and_says_so():
    c = commercial_whitespace(f(commercial_leadership=Fact(
        "CRO appointed in 2023", WEB)))
    assert c.points == 0 and c.evidenced and "already in post" in c.evidence


# ---------- the seat, and the size band where it is real ----------

def test_an_open_senior_commercial_seat_counts_as_whitespace():
    c = commercial_whitespace(f(open_roles=Fact(
        "open roles include General Manager, Europe; Account Executive", WEB)))
    assert c.points == WEIGHTS["whitespace"]


def test_having_no_senior_seat_open_today_is_not_a_fact_about_the_company():
    """An earlier build scored "no senior commercial seat open" as an
    observation worth zero. That is a question about this week's job board,
    not about the business, and it was pushing companies he had personally
    named below the floor."""
    c = commercial_whitespace(f(open_roles=Fact(
        "open roles include Staff Engineer; Recruiter", WEB)))
    assert c.unknown


# ---------- the tier ladder is his own legend, computed ----------

@pytest.mark.parametrize("total,tier", [(10, 1), (9, 1), (8, 2), (7, 2),
                                        (6, 3), (5.5, None), (0, None)])
def test_the_tier_ladder_matches_his_tier_legend(total, tier):
    s = CompanyScore(slug="x", name="X", total=total,
                     components=[Component("a", 1, True, "e")])
    assert s.tier == tier


def test_a_needs_evidence_company_has_no_tier_however_high_it_scored():
    s = CompanyScore(slug="x", name="X", total=10,
                     components=[Component("a", 10, True, "e")],
                     status=NEEDS_EVIDENCE)
    assert s.tier is None and not s.sweeps


def test_the_sweep_set_is_ordered_best_tier_first():
    def sc(total):
        return CompanyScore(slug=str(total), name=str(total), total=total,
                            components=[Component("a", 1, True, "e")])
    order = [s.total for s in sweep_set([sc(6), sc(9.5), sc(4), sc(7.5)])]
    assert order == [9.5, 7.5, 6], "a below-floor company must not be swept"


# ---------- the line he reads on the sheet ----------

def test_the_why_line_names_the_components_that_moved_the_number():
    s = score_company(f(
        what_it_does=Fact("Agentic AI for enterprise support", WEB),
        stage=Fact("Series A", WEB), founded=Fact("founded 2022", WEB),
        locations=Fact("New York", WEB)))
    why = s.why()
    assert "category" not in why, "the line is plain words, not field names"
    assert "ai native enterprise" in why and "(+4)" in why


def test_a_company_strong_on_every_component_reaches_the_top():
    s = score_company(f(
        what_it_does=Fact("Agentic AI for enterprise support teams", WEB),
        investors=Fact("Series A led by Sequoia", WEB),
        stage=Fact("Series A", WEB), founded=Fact("founded 2023", WEB),
        commercial_leadership=Fact("no CRO in post", WEB),
        open_roles=Fact("Head of GTM", WEB),
        locations=Fact("New York and London", WEB)))
    assert s.total == 10 and s.tier == 1


def test_no_component_can_score_more_than_its_declared_weight():
    """The weights are declared once in WEIGHTS and used to normalise the
    score. A component returning a hardcoded number instead drifts from its
    own declaration silently: gutting WEIGHTS["category"] to 0.1 once left
    every calibration number unchanged, because category_fit was still
    handing out a literal 4.0 and only the denominator moved.
    """
    from hunter import company as C
    cases = {
        "category": f(what_it_does=Fact("Agentic AI for enterprise support", WEB)),
        "backing": f(investors=Fact("Series A led by Sequoia", WEB)),
        "venture_stage": f(stage=Fact("Series A", WEB), founded=Fact("founded 2023", WEB)),
        "whitespace": f(open_roles=Fact("Head of GTM, Europe", WEB)),
        "geography": f(locations=Fact("New York and London", WEB)),
    }
    for fn in C.COMPONENTS:
        c = fn(cases[fn(f()).name])
        assert c.points <= c.weight, (
            f"{c.name} scored {c.points} against a declared weight of {c.weight}")
        assert c.points == c.weight, (
            f"{c.name} cannot reach its own declared weight of {c.weight}")


# ---------- a company hunter could not read is not a permanent verdict ----------

def test_a_company_it_could_not_read_is_asked_about_again():
    """Without a retry, a page that was down for one afternoon becomes a
    permanent exclusion, which turns a temporary failure into a policy."""
    from datetime import datetime, timedelta, timezone
    from hunter import companyintel as intel

    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    assert intel.is_stale({"status": NEEDS_EVIDENCE, "scored_at": old})
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert not intel.is_stale({"status": NEEDS_EVIDENCE, "scored_at": recent})


def test_a_score_that_stands_is_refreshed_more_slowly():
    """A company raises, hires a CRO, or changes what it sells, and none of
    that happens weekly."""
    from datetime import datetime, timedelta, timezone
    from hunter import companyintel as intel

    month_old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert not intel.is_stale({"status": "", "scored_at": month_old})
    ancient = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    assert intel.is_stale({"status": "", "scored_at": ancient})


def test_a_row_with_no_timestamp_is_always_worth_asking_about_again():
    from hunter import companyintel as intel
    assert intel.is_stale({"status": "", "scored_at": ""})


def test_a_slogan_is_not_enough_to_conclude_a_company_is_outside_his_world():
    """Lightning AI's homepage says "From the PyTorch Lightning creators. Own
    your AI" and nothing else. Scoring that as outside his categories blocked
    an AI infrastructure company on sixty characters of marketing. Too
    little to tell is unknown, which costs the company nothing and comes back
    on the fortnightly retry."""
    c = category_fit(f(what_it_does=Fact(
        "Lightning AI. Own your AI, don't rent it.", WEB)))
    assert c.unknown, "a slogan was read as a verdict"


def test_a_real_description_that_is_outside_is_still_called_outside():
    """The short-description rule must not become a way for every company to
    avoid the only negative the category component can give."""
    c = category_fit(f(what_it_does=Fact(
        "Acme operates a nationwide chain of dental practices, providing "
        "routine and cosmetic dentistry to families across the midwest.", WEB)))
    assert c.evidenced and c.points == 0 and "outside" in c.evidence
