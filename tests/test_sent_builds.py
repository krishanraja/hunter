"""A posting he sent himself asks for its own package.

His words, 2026-10-20, retiring column A: "Just produce the actively,
passionfroot and horizon packages. As you know, we are retiring column A in
favour of Columns K and M." Sending a role is asking for it; once he scores
it, his score decides.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from hunter import leave, ratings, router  # noqa: E402
from hunter.sheet import COLS, SENT_SOURCE  # noqa: E402
from test_score_builds import srow  # noqa: E402

SOURCE = COLS["Source"]


def sent(verdict="New", k="", m=""):
    r = srow(verdict, k, m)
    r.cells[SOURCE] = SENT_SOURCE
    return r


@pytest.mark.parametrize("verdict,k,m,want", [
    ("New", "", "", router.SENT_TRIGGER),            # sent, unscored: build it
    ("", "", "", router.SENT_TRIGGER),
    ("New", "8", "", "Krish Score 8"),               # scored: his score builds
    ("New", "5", "", None),                          # scored low: his score wins
    ("New", "", "Duplicate", router.SENT_TRIGGER),   # build_trigger, as a Yes
    ("Declined - stage wrong", "", "", None),        # he said no
    ("Applied", "", "", None),                       # he already acted
])
def test_a_sent_row_asks_for_its_package(verdict, k, m, want):
    assert router.build_trigger(sent(verdict, k, m)) == want


def test_a_row_hunter_found_still_needs_his_mark():
    assert router.build_trigger(srow("New", "", "")) is None


def test_wants_holds_a_sent_row_to_the_rules_of_a_yes():
    assert router.wants(sent()) == router.SENT_TRIGGER
    assert router.wants(sent(m="Duplicate")) is None
    assert router.wants(sent(m="not for me")) is None       # only a reaction: no
    assert router.is_his_ask(router.SENT_TRIGGER) and router.is_his_ask(router.YES_TRIGGER)
    assert not router.is_his_ask("Krish Score 8")


def test_a_sent_blank_never_leaves_as_one_he_scored_past():
    rr = leave.RowRating(kind=ratings.UNSCORED, job_ids=("quillfield:vp-strategy",))
    assert leave.should_leave(sent(), rr) is None
    assert leave.should_leave(srow("New", "", ""), rr) == leave.SCORED_PAST


def test_a_sent_row_he_scores_low_leaves():
    rr = leave.RowRating(kind=ratings.SCORED, job_ids=("quillfield:vp-strategy",))
    assert leave.should_leave(sent(k="4"), rr) is not None
