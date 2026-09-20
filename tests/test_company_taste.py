"""The company bar, measured against the companies Krish actually chose.

The sibling of tests/test_taste.py, and it exists for the same reason. Every
other test checks that the code does what the code says. None of them could
notice that hunter had stopped asking whether a company was one he would
join, which is how his sheet filled with Omnicom, Moody's, JPMorgan and
SiriusXM while 905 tests stayed green.

tests/fixtures/krish_companies.json is the ground truth: 138 companies, each
with the evidence hunter can gather about it for free and the label Krish
gave it. 52 are companies he named on the Target Companies tab, 17 of those
at Tier 1. 49 are companies whose roles he declined. The facts and the labels
come from different places on purpose. The labels are his; the facts are what
a company says about itself on its own site and its own job board, so the
test measures the scorer rather than measuring his enthusiasm reflected back.

What this can and cannot measure, said plainly. A company that blocks hunter
or renders its homepage client side yields no description, and without one
nothing is scored: those land as "needs evidence" and are counted separately
rather than being quietly averaged in. That number is a fact about hunter's
reach, not about the companies, and it is printed so it cannot hide.
"""
import json
import pathlib

import pytest

from hunter.company import (
    Facts, Fact, NEEDS_EVIDENCE, SWEEP_FLOOR, score_company,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "krish_companies.json"


def _facts(row: dict) -> Facts:
    kw = {a: Fact(f["value"], f["source"]) for a, f in row["facts"].items()}
    return Facts(slug=row["slug"], name=row["name"],
                 a16z_portfolio=row["a16z"], **kw)


@pytest.fixture(scope="module")
def scored():
    rows = json.loads(FIXTURE.read_text())
    assert len(rows) >= 130, "the labelled set shrank; it is the only ground truth"
    return [(score_company(_facts(r)), r) for r in rows]


def _of(scored, label):
    return [(s, r) for s, r in scored if r["label"] == label]


def _rate(rows, pred):
    hit = sum(1 for s, _ in rows if pred(s))
    return hit, len(rows)


# ---------- the two numbers that matter ----------

def test_the_bar_admits_the_companies_he_named(scored, capsys):
    """He wrote 52 company names down himself. A bar that blocks them is not
    measuring his taste, whatever else it does."""
    targets = _of(scored, "target")
    # s.sweeps, not s.total. A company can score above the floor and still
    # be held back as needs-evidence, and an earlier version of this test
    # counted those as swept, which overstated the bar by 9 companies.
    passed, total = _rate(targets, lambda s: s.sweeps)
    blocked = [r["name"] for s, r in targets if not s.sweeps]
    with capsys.disabled():
        print(f"\n  of the {total} companies he named, the bar sweeps {passed}")
        print(f"  blocked: {', '.join(sorted(blocked)) or 'none'}")
    assert passed / total >= 0.78, (
        f"the bar blocks {total - passed} of the {total} companies he chose: "
        f"{sorted(blocked)}")


def test_the_bar_blocks_the_companies_he_turned_down(scored, capsys):
    declined = _of(scored, "declined")
    blocked, total = _rate(declined, lambda s: not s.sweeps)
    admitted = [(r["name"], s.total) for s, r in declined if s.sweeps]
    with capsys.disabled():
        print(f"\n  of the {total} companies he declined, the bar blocks {blocked}")
        for name, total_score in sorted(admitted, key=lambda x: -x[1]):
            print(f"    still admitted: {name} at {total_score}")
    assert blocked / total >= 0.85, (
        f"the bar admits {total - blocked} of the {total} companies he "
        f"declined: {admitted}")


def test_the_companies_that_made_him_call_it_a_regression_are_all_blocked(scored):
    """His words on 2026-09-20: "mainly giving me legacy businesses like
    SiriusXM, Citi, Omnicom". Whatever else changes, these do not come back."""
    by_name = {r["name"].lower(): s for s, r in scored}
    for name in ("citi", "razorfish", "vista equity partners", "ladders"):
        s = by_name.get(name)
        if s is None:
            continue
        assert not s.sweeps, f"{name} is back in the sweep set at {s.total}"


# ---------- the tier ladder should agree with the one he wrote ----------

def test_his_tier_one_companies_outrank_his_tier_three(scored, capsys):
    """His TIER LEGEND is a sourcing policy: Tier 1 is "top priority", Tier 3
    is "reference list". A computed tier that inverts his is worse than no
    tier at all."""
    tiers = {"1": [], "2": [], "3": []}
    for s, r in scored:
        t = str(r.get("tier_stated") or "")
        if t in tiers and s.status != NEEDS_EVIDENCE:
            tiers[t].append(s.total)
    avg = {t: sum(v) / len(v) for t, v in tiers.items() if v}
    with capsys.disabled():
        print("\n  mean computed score by the tier he assigned: "
              + ", ".join(f"tier {t} {a:.1f}" for t, a in sorted(avg.items())))
    assert avg["1"] > avg["3"], (
        "companies he called top priority score no better than his reference "
        "list, so the computed tier is not measuring what he meant")


# ---------- the honesty rules, on real data rather than a handmade case ----------

def test_no_company_is_scored_without_knowing_what_it_does(scored):
    for s, r in scored:
        has_category = any(c.name == "category" and c.evidenced
                           for c in s.components)
        if not has_category:
            assert s.status == NEEDS_EVIDENCE and not s.sweeps, (
                f"{r['name']} was scored {s.total} without hunter ever "
                f"establishing what the business does")


def test_every_scored_component_can_point_at_where_it_was_read(scored):
    """An evidence line with no source is a claim hunter made up. On real
    data this catches a whole source going uncited, which a handmade case
    cannot."""
    for s, r in scored:
        for c in s.components:
            if c.evidenced:
                assert c.source, (
                    f"{r['name']}: component {c.name} claims "
                    f"{c.evidence!r} and cites nothing")


def test_how_far_hunter_can_actually_see_is_reported(scored, capsys):
    """Not an assertion about quality, a measurement of reach. If this climbs,
    the bar is being held up by companies hunter simply cannot read, and that
    is a fact about hunter rather than about them."""
    unread = [r["name"] for s, r in scored if s.status == NEEDS_EVIDENCE]
    with capsys.disabled():
        print(f"\n  hunter could not establish enough about {len(unread)} of "
              f"{len(scored)} companies to score them")
    assert len(unread) / len(scored) < 0.5, (
        "hunter cannot read half the companies it is asked about, so the "
        "scores it does produce are not a bar, they are a sample")
