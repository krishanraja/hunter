"""Board discovery and the three-state liveness answer.

Ten Pipeline rows carried a URL no ATS client could read, so hunter could not
say whether the role was live. On the sheet that looked identical to a live
role, which is how five roles Krish had said go to sat there after the
postings closed.
"""
import pytest

from hunter.ats.discover import slug_candidates
from hunter.run import ats_key


@pytest.mark.parametrize("company,expected", [
    # Both variants exist because the real boards needed them.
    ("Higgsfield AI", "higgsfieldai"),
    ("The Trade Desk", "thetradedesk"),
    ("Hearst Magazines", "hearst"),
    ("Socure", "socure"),
])
def test_the_slug_a_real_board_answered_on_is_tried(company, expected):
    assert expected in slug_candidates(company)


def test_a_candidate_too_short_to_name_a_company_is_never_tried():
    """"The Trade Desk" once produced "the", which would match some unrelated
    board and report a role live that hunter never saw."""
    assert "the" not in slug_candidates("The Trade Desk")
    assert all(len(c) >= 4 for c in slug_candidates("The Trade Desk"))


def test_candidates_are_ordered_most_likely_first():
    cands = slug_candidates("Higgsfield AI")
    assert cands[0] == "higgsfield-ai"


def test_a_workday_posting_url_resolves():
    """Criteo's row was unverifiable only because Workday had no URL pattern."""
    key = ats_key("https://criteo.wd3.myworkdayjobs.com/en-US/Criteo_Careers"
                  "/job/London/VP-of-Commercialization_R12345")
    assert key and key[0] == "workday" and key[1] == "criteo"


def test_the_other_ats_patterns_still_win_their_own_urls():
    assert ats_key("https://jobs.ashbyhq.com/socure/"
                   "89a663ab-a9ce-4bfd-9918-efea5c276232")[0] == "ashby"
    assert ats_key("https://boards.greenhouse.io/cloudflare/jobs/8076815")[0] == "greenhouse"
    assert ats_key("https://www.linkedin.com/jobs/view/svp-strategy-4423181366") is None
