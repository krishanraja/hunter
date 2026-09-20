"""The bar, measured against Krish's own verdicts.

Krish 2026-09-20: "I was hoping that making this a dedicated github repo would
eliminate drift in quality but the drift has increased 100X".

He is right that the repo did not stop it, and this file is why. Every other
test in this suite checks that the code does what the code says. None of them
could notice that the OUTPUT had stopped being good, so the quality bar drifted
for weeks while 848 tests stayed green.

tests/fixtures/krish_verdicts.json is 148 roles he personally ruled on, 44 yes
and 104 no, taken from the Pipeline and Applied tabs on 2026-09-20. It is the
only ground truth this system has. A change to gates, scoring or sourcing that
makes these numbers worse is a regression whatever else it improves.

What this can and cannot measure, said plainly. hunter_seen_roles has never
stored the job description text, so a role cannot be replayed through the full
scorer offline. What is replayable is exactly where the three defects were: the
employer, the seat named by the title, and the gate that the employer decides.
The JD-dependent components (engine signals, mandate wording) are not tested
here and are not what broke.
"""
import json
import pathlib

import pytest

from hunter import comp, employer, score
from hunter.employer import Index

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "krish_verdicts.json"


@pytest.fixture(scope="module")
def verdicts():
    rows = json.loads(FIXTURE.read_text())
    assert len(rows) >= 140, "the labelled set shrank; it is the only ground truth"
    return rows


@pytest.fixture(scope="module")
def yes(verdicts):
    return [r for r in verdicts if r["label"] == "yes"]


@pytest.fixture(scope="module")
def no(verdicts):
    return [r for r in verdicts if r["label"] == "no"]


# ---------- the one sector his data supports ----------

def test_the_institution_list_hits_his_declines_and_none_of_his_approvals(yes, no):
    """Banks, insurers and asset managers. The measurement that earned this
    list a place, and the reason no other sector has one."""
    killed = sorted({r["company"] for r in yes
                     if employer.INSTITUTIONS.search(r["company"])})
    caught = sorted({r["company"] for r in no
                     if employer.INSTITUTIONS.search(r["company"])})
    assert killed == [], f"the institution list would block roles he approved: {killed}"
    assert len(caught) >= 5, f"only catches {caught}, which is not worth a gate"


@pytest.mark.parametrize("company", [
    "Citi", "BNY", "TIAA", "New York Life", "Janus Henderson Investors",
    "TD Securities",
])
def test_the_banks_he_declined_are_recognised(company):
    assert employer.classify(company).kind == employer.INSTITUTION


@pytest.mark.parametrize("company", [
    "Harvey",           # "ey" inside a name is not EY
    "Razorfish", "BioSpace", "Recursion", "Talkspace", "Vista Equity Partners",
    "Lumida Wealth", "Socure", "Trulioo", "Consello",
])
def test_companies_he_approved_are_never_called_institutions(company):
    """Every one of these is in a sector a blunter rule would have blocked.
    He approved a role at each."""
    assert employer.classify(company).kind != employer.INSTITUTION


# ---------- the portfolio is positive evidence, not a filter ----------

def test_portfolio_membership_is_ai_native_and_earns_points():
    idx = Index(portfolio={"fleek": "Fleek"}, portfolio_tokens={},
                approved=set(), declined={})
    v = employer.classify("Fleek", idx)
    assert v.kind == employer.PORTFOLIO and v.is_ai_native and v.points > 0


def test_a_company_he_approved_before_is_ai_native_too():
    idx = Index(portfolio={}, portfolio_tokens={}, approved={"mutiny"}, declined={})
    assert employer.classify("Mutiny", idx).is_ai_native


def test_an_unknown_company_is_worth_nothing_either_way():
    v = employer.classify("Some Startup Nobody Has Heard Of")
    assert v.kind == employer.UNKNOWN and v.points == 0
    assert not v.is_ai_native, "no record is not evidence of being AI native"


def test_a_company_he_declined_outranks_everything_else():
    idx = Index(portfolio={"flex": "Flex"}, portfolio_tokens={},
                approved={"flex"},
                declined={"flex": {"company": "Flex", "date": "2026-09-07",
                                   "code": "business_uninteresting",
                                   "job_id": "x", "quote": ""}})
    v = employer.classify("Flex", idx)
    assert v.kind == employer.DECLINED and v.points < 0


# ---------- the junior seat, and why there is no rule for it ----------

def test_lead_is_not_a_junior_word_to_him(yes):
    """A title rule for junior seats was written and then deleted, because
    this is what his own verdicts say. Harvey "Business Development Lead" at
    $240K to $360K, Writer "Strategic AI Transformation Lead" at $205K to
    $259K, LangChain "Monetization Programs and Operations Lead": all approved.
    """
    leads = [r for r in yes if "lead" in r["title"].lower()]
    assert len(leads) >= 3, "the evidence that killed the title rule is gone"
    assert not hasattr(score, "JUNIOR_SEAT"), (
        "a title rule for junior seats contradicts his verdicts; the defect "
        "was comp capture, not the title")


def test_general_manager_is_a_seat_he_approves(yes):
    gms = {r["company"] for r in yes if "general manager" in r["title"].lower()}
    assert len(gms) >= 2, gms


# ---------- pay, which is the real reason a $140k seat looked like a $350k one ----------

def test_a_band_in_the_body_is_read():
    body = ("The base salary range for this position is $250,000 - $300,000 "
            "plus equity and benefits.")
    assert comp.extract(body) == "$250,000 - $300,000"


def test_a_single_stated_figure_is_read():
    assert comp.extract("Compensation for this role is $210,000 annually.") \
        == "$210,000"


@pytest.mark.parametrize("body", [
    "We have grown to $2B in annual revenue.",
    "The company raised $50M in its Series C.",
    "You will own a $10M quota and a pipeline of $40M.",
    "Our customers save $500,000 a year on average.",
    "Assets under management of $900,000,000.",
])
def test_money_that_is_not_pay_is_left_alone(body):
    """A wrong number here auto-rejects a role he wants, which is worse than
    reading nothing."""
    assert comp.extract(body) == "", body


def test_a_revenue_figure_next_to_a_real_band_does_not_win():
    body = ("A company with $2B in revenue. The salary range for this role "
            "is $260,000 - $320,000.")
    assert comp.extract(body) == "$260,000 - $320,000"


def test_an_hourly_or_tiny_figure_is_not_a_salary():
    assert comp.extract("The pay range is $45 - $60 per hour.") == ""


def test_the_structured_field_always_wins():
    assert comp.best("$300K - $400K", "salary range is $100,000 - $120,000") \
        == "$300K - $400K"


@pytest.mark.parametrize("empty", ["", "Not disclosed", "not stated",
                                   "Not posted", "n/a", "unknown"])
def test_the_placeholders_the_sheet_uses_count_as_absent(empty):
    assert comp.best(empty, "The salary range is $250,000 - $300,000") \
        == "$250,000 - $300,000"


def test_a_band_below_the_floor_now_auto_rejects():
    """AKASA "Sales Director, $150,000 - $185,000" was live on a board hunter
    reads, and reached the sheet with no band because only Ashby filled that
    field. Canon 9.3 rejects a bottom below $200,000; it could not fire."""
    from hunter.gates import FLOOR
    band = comp.extract("The base salary range is $150,000 - $185,000.")
    role = _role("AKASA", "Head of Partnerships",
                 "Build the partnerships function. Operating model.", comp=band)
    result = score.score_role(role)
    assert result.auto_rejected
    assert str(FLOOR) in result.rejection_reason.replace(",", "")


# ---------- the employer moves the score the right way ----------

def _role(company, title="Head of GTM", jd="Build the function. Partnerships.",
          location="London", comp="", stage=""):
    from hunter.sources import ResolvedRole
    return ResolvedRole(company=company, title=title, url="https://x.test/1",
                        jd_url="https://x.test/1", jd_text=jd, live=True,
                        source="test", location=location, comp=comp, stage=stage)


def test_a_bank_no_longer_outscores_a_portfolio_company():
    """Citi scored 9 and Foundry 6. Being listed used to earn a stage point."""
    idx = Index(portfolio={"acme-ai": "Acme AI"}, portfolio_tokens={},
                approved=set(), declined={})
    bank = score.score_role(
        _role("Citi", "Head of AI-First Development",
              "Build the AI function. Artificial intelligence transformation "
              "across the firm. Partnerships. Public company, NYSE listed.",
              location="New York, NY"), employer_index=idx)
    native = score.score_role(
        _role("Acme AI", "Head of GTM",
              "Build the go to market function. Partnerships. Operating model."),
        employer_index=idx)
    assert native.score > bank.score, (native.score, bank.score)


def test_being_publicly_listed_is_no_longer_evidence_of_stage():
    assert not score.STAGE_OK.search("public company listed on the NYSE")
    assert not score.STAGE_OK.search("NASDAQ traded, post IPO")
    assert score.STAGE_OK.search("Series C")


# ---------- the gate that was switched off ----------

def test_the_word_artificial_intelligence_no_longer_opens_the_domain_gate():
    """G7 exists to block banking and insurance. It exempted anything whose
    text matched AI_TRANSFORMATION, and every AI role at a bank does."""
    from hunter.gates import run_gates
    jd = ("Lead our artificial intelligence transformation across the "
          "insurance carrier business. Agentic systems, genai. Build the "
          "function and own the operating model.")
    report = run_gates(_role("New York Life", "Corporate Vice President, AI Enablement",
                             jd, location="New York, NY"))
    g7 = next(g for g in report.results if g.gate == "G7")
    g13 = next(g for g in report.results if g.gate == "G13")
    assert not g7.passed, g7.reason
    assert not g13.passed, g13.reason


def test_an_ai_native_company_keeps_the_escape():
    """The escape is real, it just has to be about the employer. Recursion is
    a biotech whose postings are full of clinical language, and he approved it."""
    from hunter.gates import run_gates
    idx = Index(portfolio={"recursion": "Recursion"}, portfolio_tokens={},
                approved=set(), declined={})
    jd = ("Clinical stage AI company. Build the corporate strategy function, "
          "own the operating model, partnerships.")
    report = run_gates(_role("Recursion", "Executive Director, Corporate Strategy", jd,
                             location="New York, NY"), employer_index=idx)
    g7 = next(g for g in report.results if g.gate == "G7")
    assert g7.passed, g7.reason


def test_a_company_with_no_record_keeps_the_text_escape():
    """Blocking on no evidence is a worse error than the one being fixed."""
    from hunter.gates import run_gates
    jd = ("AI-native company. Clinical data products. Build the function, "
          "own the operating model, partnerships.")
    report = run_gates(_role("Nobody Has Heard Of This One", "Head of GTM", jd))
    g7 = next(g for g in report.results if g.gate == "G7")
    assert g7.passed, g7.reason


# ---------- the whole bar, end to end on his labels ----------

def test_the_bar_separates_his_yeses_from_his_noes(yes, no, verdicts):
    """The headline number. Employer classification plus the seat rule, run
    over all 148, reported as the share of each label the bar would keep.

    This is deliberately not a threshold assertion on a single accuracy
    figure. It asserts the only two things that must never be false: the bar
    blocks none of what he approved, and it blocks a material share of what he
    declined.
    """
    def blocked(r):
        """What the restored bar refuses outright: a bank, insurer or asset
        manager, or a stated band below the floor. Everything else is ranked,
        not refused, because his verdicts do not support refusing it."""
        from hunter.gates import FLOOR, band_tops_out_at
        if employer.classify(r["company"]).kind == employer.INSTITUTION:
            return True
        ceiling = band_tops_out_at(r.get("comp"))
        return ceiling is not None and ceiling < FLOOR

    blocked_yes = [r for r in yes if blocked(r)]
    blocked_no = [r for r in no if blocked(r)]
    print(f"\n  of {len(yes)} he approved, the bar blocks {len(blocked_yes)}")
    print(f"  of {len(no)} he declined, the bar blocks {len(blocked_no)}")
    for r in blocked_yes:
        print(f"    FALSE POSITIVE: {r['company']} / {r['title']}")
    assert not blocked_yes, [f"{r['company']} / {r['title']}" for r in blocked_yes]
    assert len(blocked_no) >= 6


# ---------- the employer index, read from real sources ----------

def test_the_index_survives_a_database_it_cannot_read(monkeypatch):
    """Scoring without the index must still score. A company dimension that
    takes the run down is worse than no company dimension."""
    def boom(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr(employer, "db_get", boom)
    idx = employer.build_index(None)
    assert idx.portfolio == {} and idx.approved == set()
    assert employer.classify("Anything", idx).kind == employer.UNKNOWN


def test_the_index_never_learns_from_hunters_own_verdicts(monkeypatch):
    """The same rule learn.py has. Forty auto verdicts were once about to
    blocklist Sierra, Decagon, Cloudflare and Synthesia on no input from him."""
    rows = [{"company": "Autoco", "krish_verdict": "Yes",
             "verdict_source": "hunter regate"},
            {"company": "Realco", "krish_verdict": "Yes",
             "verdict_source": "sheet column A"}]
    monkeypatch.setattr(employer, "db_get",
                        lambda cfg, table, params: rows if table == "hunter_seen_roles" else [])
    idx = employer.build_index(None, declines={})
    assert "realco" in idx.approved and "autoco" not in idx.approved


def test_a_portfolio_company_is_matched_on_tokens_not_only_the_slug():
    idx = employer.Index(portfolio={}, portfolio_tokens={
        frozenset({"higgsfield"}): "Higgsfield"}, approved=set(), declined={})
    assert employer.classify("Higgsfield AI", idx).kind == employer.PORTFOLIO


def test_the_employer_component_is_worth_something_but_not_everything():
    """Company quality ranks, it does not decide. He approved roles at
    companies hunter has no record of and declined roles at companies it
    does."""
    assert employer.POINTS[employer.PORTFOLIO] < 3
    assert employer.POINTS[employer.UNKNOWN] == 0
