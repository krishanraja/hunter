"""Companies he has not thought of, found in what hunter already discards.

His named universe was 53. On the day this was written hunter had already
seen and thrown away 2,256 other companies, and his own contacts graph held
3,449 more. The supply was never the problem: nothing was asking the company
question of any of them.
"""
import pytest

from hunter import prospect
from hunter.prospect import Candidate, tidy, _better_name, _usable


def test_a_page_title_in_the_company_field_is_trimmed_to_the_company():
    """Job boards put marketing in the employer field. Proposing a company
    to him under its tagline looks like hunter does not know its name."""
    assert tidy("Ceribell │ AI-Powered Point-of-Care EEG") == "Ceribell"
    assert tidy("Glean - Enterprise AI that Works") == "Glean"


def test_a_name_that_is_really_the_name_is_left_alone():
    assert tidy("Raptive (formerly AdThrive)") == "Raptive (formerly AdThrive)"
    assert tidy("Together AI") == "Together AI"


def test_an_ats_slug_never_wins_over_a_real_name():
    """Company fields arrive as slugs as well as names, and the first one
    seen was winning: his tab was about to be offered "tanium" and "figma"."""
    assert _better_name("tanium", "Tanium") == "Tanium"
    assert _better_name("Figma", "figma") == "Figma"


def test_a_category_masquerading_as_an_employer_is_not_a_candidate():
    for junk in ("health", "capital", "Confidential", "stealth", "ab"):
        assert not _usable(junk), f"{junk!r} was accepted as a company"
    assert _usable("Mercor") and _usable("Together AI")


def test_priority_spends_the_budget_on_what_can_be_known():
    """Nothing here judges the company. It decides which company to spend
    the evidence budget on first, and everything it reads is already free."""
    strong = Candidate(key="a", name="A", a16z=True, has_board=True,
                       in_contacts=5, senior_seen=4)
    weak = Candidate(key="b", name="B", seen=1)
    assert strong.priority > weak.priority
    known_somebody = Candidate(key="c", name="C", in_contacts=4)
    stranger = Candidate(key="d", name="D", seen=10)
    assert known_somebody.priority > stranger.priority


def test_the_reason_it_was_looked_at_is_stated_in_plain_words():
    c = Candidate(key="a", name="A", a16z=True, in_contacts=3, senior_seen=2)
    line = c.why_worth_looking
    assert "a16z" in line and "3 contact" in line and "2 senior" in line


# ---------- what becomes a proposal ----------

class _Score:
    def __init__(self, name, total, status="", category="in ai native enterprise"):
        self.name, self.total, self.status = name, total, status
        self.slug = name.lower()

        class C:
            pass
        c = C()
        c.name, c.evidenced, c.evidence = "category", bool(category), category
        self.components = [c]

    def why(self, limit=3):
        return "backed by Sequoia (+2)"


def test_only_companies_hunter_can_say_something_about_are_proposed():
    """A proposal whose evidence reads "needs evidence" is hunter asking him
    to adopt a company on no grounds at all."""
    cands = [Candidate(key="a", name="A", a16z=True),
             Candidate(key="b", name="B")]
    scores = {"a": _Score("A", 9.0), "b": _Score("B", 9.0, status="needs evidence")}
    rows = prospect.proposals(scores, cands, floor=8.0, limit=15)
    assert [r[0] for r in rows] == ["A"]


def test_a_company_below_the_discovery_floor_is_not_put_in_front_of_him():
    cands = [Candidate(key="a", name="A")]
    scores = {"a": _Score("A", 6.5)}
    assert prospect.proposals(scores, cands, floor=8.0, limit=15) == []


def test_proposals_are_ranked_and_capped():
    cands = [Candidate(key=str(i), name=str(i)) for i in range(30)]
    scores = {str(i): _Score(str(i), 8.0 + (i % 3) / 2) for i in range(30)}
    rows = prospect.proposals(scores, cands, floor=8.0, limit=15)
    assert len(rows) == 15
    assert rows[0][1] >= rows[-1][1]


def test_a_proposal_says_why_hunter_looked_at_it_as_well_as_what_it_found():
    cands = [Candidate(key="a", name="A", a16z=True, in_contacts=2)]
    rows = prospect.proposals({"a": _Score("A", 9.0)}, cands, floor=8.0, limit=15)
    assert "Found via" in rows[0][3] and "a16z" in rows[0][3]
