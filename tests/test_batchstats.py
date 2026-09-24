"""The accept rate, broken down by where the role came from.

Every batch was recorded while the rate fell from 77 percent to 6, and the
rate itself was not, so nothing could see the line going down. batchstats
fixed that. This covers the next layer: the rate says the funnel is
drifting, and by_source says which leg is doing the drifting.
"""
def test_the_accept_rate_by_source_is_actually_reported():
    """measure() has collected by_source since it was written and nothing ever
    printed it, so the one number that says WHICH supply leg is drifting was
    computed and thrown away on every run. Krish, 2026-09-24: "there are still
    way too many boring financial services and healthcare jobs being put in
    here"."""
    from hunter import batchstats as B
    b1 = B.Batch(key="2026-09-20", staged=10, verdicted=8, accepted=1,
                 by_source={"linkedin keyword": {"verdicted": 6, "accepted": 0},
                            "target board": {"verdicted": 2, "accepted": 1}})
    b2 = B.Batch(key="2026-09-24", staged=6, verdicted=4, accepted=2,
                 by_source={"linkedin keyword": {"verdicted": 2, "accepted": 0},
                            "target board": {"verdicted": 2, "accepted": 2}})
    rows = B.by_source([b1, b2])
    assert dict((s, (a, v)) for s, a, v in rows) == {
        "linkedin keyword": (0, 8), "target board": (3, 4)}
    # Most judged first, so a leg with two verdicts cannot head the table.
    assert rows[0][0] == "linkedin keyword"
    text = "\n".join(B.source_lines([b1, b2]))
    assert "linkedin keyword" in text and "0/8" in text
    assert "target board" in text and "3/4" in text
    assert "0%" in text and "75%" in text


def test_a_source_nobody_has_judged_says_so_rather_than_zero():
    """Zero would read as "he rejected all of them", which is the same defect
    as scoring an absent observation."""
    from hunter import batchstats as B
    b = B.Batch(key="2026-09-24", by_source={"new leg": {"verdicted": 0,
                                                         "accepted": 0}})
    text = "\n".join(B.source_lines([b]))
    assert "no verdicts" in text
    assert "0%" not in text
