"""The geography report, and the two fixes it replaced.

The first two things proposed for the geography problem were a fetch-time
filter on gates.names_foreign_geo and a Workday sweep. Both were measured
against live data and both were wrong, so the numbers that killed them are
recorded here: a rule nobody can see the evidence for gets rewritten.
"""
from hunter import geoscope
from hunter.gates import names_foreign_geo


def leg(source: str) -> str:
    return "linkedin" if source.startswith("apify") else "board"


def test_a_search_that_names_a_location_is_told_apart_from_one_that_does_not():
    scoped = geoscope.read_search(
        "https://www.linkedin.com/jobs/search/?keywords=chief+revenue+officer"
        "&geoId=90000070&f_WT=2")
    assert scoped.scoped
    assert scoped.geo == "90000070" and scoped.param == "geoId"
    assert scoped.work_type == "remote"
    assert scoped.keywords == "chief revenue officer"

    loose = geoscope.read_search(
        "https://www.linkedin.com/jobs/search/?keywords=vp+gtm")
    assert not loose.scoped
    assert loose.geo == ""


def test_the_human_readable_location_counts_as_scoped():
    """His searches are copied out of a browser, and a copied URL carries
    location= rather than geoId=."""
    r = geoscope.read_search(
        "https://www.linkedin.com/jobs/search/?keywords=cro&location=London%2C+England")
    assert r.scoped and r.param == "location" and r.geo == "London, England"


def test_a_geoid_is_reported_verbatim_and_never_translated():
    """A lookup table of LinkedIn geo ids written from memory would print a
    confident wrong city next to his own search, which is worse than
    printing the number."""
    r = geoscope.read_search("https://www.linkedin.com/jobs/search/?geoId=103644278")
    assert "103644278" in "\n".join(geoscope.search_lines([r.url]))


def test_a_url_it_cannot_parse_is_still_reported():
    """He types these by hand. A search hunter cannot read is still a search
    hunter runs, and it must not take the report down with it."""
    r = geoscope.read_search("not a url at all")
    assert not r.scoped
    assert geoscope.search_lines(["not a url at all"])


def test_the_report_says_when_there_are_no_searches_at_all():
    assert "no LinkedIn searches" in geoscope.search_lines([])[0]


def test_it_counts_what_geography_deleted_by_place_not_what_survived():
    rows = [
        {"source": "apify_linkedin", "location": "San Francisco, CA",
         "rejection_reason": "G6: London, UK-remote, NYC or US-remote"},
        {"source": "apify_linkedin", "location": "San Francisco, CA",
         "rejection_reason": "G6: London, UK-remote, NYC or US-remote"},
        {"source": "apify_linkedin", "location": "San Francisco, CA",
         "rejection_reason": ""},
        {"source": "greenhouse:acme", "location": "New York, NY",
         "rejection_reason": ""},
    ]
    ranked = geoscope.deleted_by_location(rows, leg_of=leg)
    assert ranked[0] == ("linkedin", "San Francisco, CA", 2, 3)
    # A place nothing was deleted from is not in the list at all: the number
    # this report exists to show is what an edit to a search can recover.
    assert all(loc != "New York, NY" for _, loc, _, _ in ranked)


def test_a_row_with_no_location_is_skipped_rather_than_counted_as_blank():
    rows = [{"source": "apify_linkedin", "location": None,
             "rejection_reason": "G6: out of geography"},
            {"source": "apify_linkedin", "location": "   ",
             "rejection_reason": "G6: out of geography"}]
    assert geoscope.deleted_by_location(rows, leg_of=leg) == []


def test_the_older_wording_still_reads_as_a_geography_death():
    """Rows written before the reason carried the gate number say only what
    the gate wanted. Dropping them would report the cost as zero."""
    rows = [{"source": "apify_linkedin", "location": "Austin, TX",
             "rejection_reason": "wants London, UK-remote, NYC or US-remote"}]
    assert geoscope.deleted_by_location(rows, leg_of=leg)[0][2] == 1


def test_it_says_so_when_geography_deleted_nothing():
    assert "no roles were deleted" in geoscope.deleted_lines(
        [{"source": "apify_linkedin", "location": "New York, NY",
          "rejection_reason": ""}], leg_of=leg)[0]


# The measurements that killed the two fixes proposed before this one.

RUN_126_FOREIGN = [
    # location, times seen. Every location from run 126 that
    # gates.names_foreign_geo refuses, and there were only these.
    ("Singapore", 3), ("Dubai", 2), ("Munich, Germany", 1),
]
RUN_126_DELETED_BY_G6 = 253
RUN_126_SOURCED = 567


def test_the_foreign_filter_would_have_saved_six_fetches_out_of_567():
    """Why there is no names_foreign_geo call at board fetch time.

    The proposal was to drop obviously foreign postings before paying for
    the JD fetch, on the reading that hunter was sweeping foreign boards.
    Run over all 567 locations run 126 actually sourced, the filter refuses
    six. The other 247 roles G6 deleted were in the United States, in cities
    that are neither New York nor remote.
    """
    caught = sum(n for loc, n in RUN_126_FOREIGN if names_foreign_geo(loc))
    assert caught == 6
    assert caught / RUN_126_SOURCED < 0.02
    assert RUN_126_DELETED_BY_G6 > 40 * caught


def test_the_locations_g6_actually_deleted_are_not_foreign():
    """The bulk of the cost, and none of it is reachable by a foreign check."""
    for loc in ("San Francisco, CA", "Santa Clara, CA", "United States",
                "Florham Park, NJ", "Stamford, CT", "Bridgewater, NJ",
                "McLean, VA", "Palo Alto", "Seattle Office"):
        assert not names_foreign_geo(loc), loc


def test_a_bare_united_states_is_not_evidence_of_being_out_of_geography():
    """Why there is no aggressive location-only filter either.

    68 LinkedIn roles carried the location "United States" in run 126 and 23
    of them PASSED G6, because G6 reads the job description alongside the
    location and the description said remote. Refusing them on the location
    alone is refusing on no evidence, and one of the roles it would have
    dropped reached him.
    """
    from hunter.gates import geography_ok
    assert not geography_ok("United States")
    assert geography_ok("United States") is not geography_ok(
        "United States. This role is fully remote.")
