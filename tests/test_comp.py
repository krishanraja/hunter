"""Reading pay out of a posting body.

The hard part is not finding dollars, it is not finding the WRONG dollars. A
posting says "$2B in revenue", "we raised $50M", "a $10M quota" and "a $50,000
signing bonus" in the same few paragraphs as the band. A wrong number here
auto-rejects a role Krish wants, which is worse than reading nothing, so every
trap below is a test and the default on doubt is silence.
"""
import pytest

from hunter import comp


# ---------- what it should read ----------

@pytest.mark.parametrize("body,want", [
    ("The base salary range for this role is $250,000 - $300,000.",
     "$250,000 - $300,000"),
    ("Base salary: $250K-$300K plus equity.", "$250,000 - $300,000"),
    ("The pay range is $210,000 to $260,000.", "$210,000 - $260,000"),
    ("Compensation for this role is $210,000 annually.", "$210,000"),
    ("The expected salary range is $180,000 through $240,000.",
     "$180,000 - $240,000"),
])
def test_a_stated_band_is_read(body, want):
    assert comp.extract(body) == want


def test_the_currency_is_preserved_not_assumed():
    """Rendering a sterling band as dollars is a different number, and the
    floor is judged against it."""
    body = "The salary range for this London role is £180,000 - £220,000."
    assert comp.extract(body) == "£180,000 - £220,000"


def test_a_real_band_survives_a_revenue_figure_in_the_same_posting():
    body = ("A company with $2,000,000,000 in revenue. The salary range is "
            "$210,000 to $260,000.")
    assert comp.extract(body) == "$210,000 - $260,000"


def test_a_signing_bonus_does_not_become_the_bottom_of_the_band():
    """Collecting every figure in the paragraph produced
    "$50,000 - $270,000" here. A range the posting writes AS a range wins."""
    body = ("A signing bonus of $50,000 is available. The base pay range is "
            "$230,000 - $270,000.")
    assert comp.extract(body) == "$230,000 - $270,000"


# ---------- what it must never read ----------

@pytest.mark.parametrize("body", [
    "We passed $100,000,000 in annual recurring revenue.",
    "We raised $250,000,000 led by a16z.",
    "You will carry a $12,000,000 quota.",
    "Our customers save $400,000 annually on average.",
    "Assets under management of $900,000,000.",
    "You will receive equity worth up to $500,000 over four years.",
    "A great place to work with excellent benefits.",
    "Compensation is $85 per hour.",
    "The pay range is $45 - $60 per hour.",
])
def test_money_that_is_not_this_job_s_pay_is_left_alone(body):
    assert comp.extract(body) == "", body


def test_a_figure_with_no_pay_sentence_near_it_is_ignored():
    assert comp.extract("We serve 400,000 customers across $300,000 deals.") == ""


# ---------- choosing between the field and the body ----------

def test_the_employers_own_field_always_wins():
    assert comp.best("$300K - $400K", "salary range is $100,000 - $120,000") \
        == "$300K - $400K"


@pytest.mark.parametrize("placeholder", ["", "   ", "Not disclosed", "not stated",
                                         "Not posted", "n/a", "unknown", "none", "-"])
def test_the_placeholders_the_sheet_uses_count_as_no_field(placeholder):
    assert comp.best(placeholder, "The salary range is $250,000 - $300,000") \
        == "$250,000 - $300,000"


def test_no_field_and_no_band_stays_empty():
    assert comp.best("", "A great role at a great company.") == ""


def test_a_missing_body_never_raises():
    assert comp.best(None, None) == ""
    assert comp.extract(None) == ""


# ---------- the shape the rest of the system reads ----------

def test_what_it_produces_parses_back_to_the_same_numbers():
    from hunter.gates import parse_comp_band
    assert parse_comp_band(comp.extract(
        "The base salary range is $250,000 - $300,000.")) == (250_000, 300_000)


def test_a_band_it_reads_can_fail_the_floor():
    """The whole point. AKASA "Sales Director, $150,000 - $185,000" was live
    on a board hunter reads and reached the sheet with no band at all."""
    from hunter.gates import FLOOR, band_tops_out_at
    band = comp.extract("The base salary range is $150,000 - $185,000.")
    assert band_tops_out_at(band) < FLOOR
