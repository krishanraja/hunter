"""The archetype gate, pinned by Krish's own verdicts.

Every role he said go to is a named case here. If a change to the matcher
ever costs him one of these, this suite fails and says which. That is the
whole point: the gate decides what he is shown, so it answers to what he
has already chosen rather than to a rule someone invented.
"""
import pytest

from hunter.archetype import archetype, families


# ---------- the roles he approved. All of these must reach him. ----------

APPROVED = [
    ("Head of Enterprise GTM - Shelby Platform", "Aptos Labs"),
    ("Chief of Staff to Chief Strategy Officer", "Cloudflare"),
    ("Chief of Staff", "Cohere"),
    ("Head of Business Operations", "Common Room"),
    ("VP of Strategy and Corporate Development", "Duolingo"),
    ("General Manager - UK", "ElevenLabs"),
    ("Head of Corporate Development", "Fractional AI"),
    ("Head of GTM Strategy & Operations, AMER", "Harvey"),
    ("SVP, Strategy", "Hearst Magazines"),
    ("Director of Corporate Development", "Legora"),
    ("Managing Director, EMEA", "LogicGate"),
    ("Head of GTM", "Morpho Labs"),
    ("Head of GTM (Path to COO)", "Mutiny"),
    ("Head of Business and Corporate Development", "Phantom"),
    ("Chief of Staff, GTM", "Slingshot AI"),
    ("Chief of Staff, Go to Market", "UiPath"),
]


@pytest.mark.parametrize("title,company", APPROVED)
def test_a_role_krish_approved_reaches_him(title, company):
    assert archetype(title) in families(), f"{company}: {title!r}"


def test_the_one_approved_role_the_gate_does_not_catch():
    """Ogury. Krish said go; the title is none of canon section 5's families.

    Left failing on purpose rather than widening the matcher to fit one
    role, because widening it to admit "Global Solutions Lead" would admit
    a great deal else. This test documents the cost of the gate so it stays
    visible and is his to overrule.
    """
    assert archetype("Global Solutions Lead, CTV & Video Business") is None


# ---------- the shapes that should never reach him again ----------

@pytest.mark.parametrize("title", [
    "Enterprise Sales Director - Majors, Healthcare",     # canon 9.3 quota seat
    "Enterprise Sales Director, Financial Services",
    "Director, Government Affairs & Public Policy",
    "RVP, Sales Analytics (Tableau)",                     # he declined this
    "Head of Growth Marketing",                           # he declined this
    "Regional Director, Commercial",                      # he declined this
    "Vice President, Americas Sales",                     # he declined this
    "Head of Post Sales Technology",                      # he declined this
    "Director of Product Management, Forward Deployed",
    "Head of Compute Procurement & Strategic Supply",
    "VP, Growth Marketing",
    "Head of Americas Field Marketing",
    "Regional Vice President, Healthcare West",
    "Director, Solutions Architecture",
    "VP Finance",
])
def test_a_shape_outside_his_archetypes_never_reaches_him(title):
    assert archetype(title) is None, title


# ---------- the families themselves ----------

@pytest.mark.parametrize("title,family", [
    ("General Manager, North America", "country_regional_gm"),
    ("Country Manager, UK", "country_regional_gm"),
    ("GM, CTV & Video (London)", "country_regional_gm"),
    ("Chief Commercial Officer", "chief_commercial_strategy"),
    ("Head of GTM Innovation and Operations", "chief_commercial_strategy"),
    ("Director, Sales Strategy and Operations", "chief_commercial_strategy"),
    ("VP Corporate Development", "corp_dev_strategy"),
    ("Head of Strategy", "corp_dev_strategy"),
    ("AI Chief of Staff", "ai_chief_of_staff"),
    ("Head of AI Operations", "ai_chief_of_staff"),
    ("VP, Strategic Partnerships", "partnerships_alliances"),
])
def test_each_family_is_recognised(title, family):
    assert archetype(title) == family


def test_a_quota_seat_wearing_a_good_title_is_still_a_quota_seat():
    """The exclusion runs first. "Regional Director, Commercial" carries no
    build mandate whatever else the posting says, and Krish declined it."""
    assert archetype("Regional Director, Commercial Expansion EMEA") is None
    assert archetype("Enterprise Sales Director, Strategic, Healthcare") is None
