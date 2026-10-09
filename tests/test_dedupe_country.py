"""Same company and title in a different country is a different role.

Sierra's Regional VP, Sales in London was filed as a duplicate of the one in
South Korea on the company and title alone, and never reached him. A
different city in the same country stays one role, and an unknown place
matches anything, so a LinkedIn copy of a board posting still collapses."""
import pytest

from hunter.sources import country_of, job_id, placed_identity_keys


@pytest.mark.parametrize("location,want", [
    ("London", {"uk"}), ("Seoul, South Korea", {"south korea"}), ("New York, NY", {"us"}),
    ("New York City Metropolitan Area", {"us"}), ("US - Remote", {"us"}),
    ("Toronto, CA", {"canada"}), ("Bangalore, IN", {"india"}), ("Columbus, OH", {"us"}),
    ("Remote", set()), ("", set()),
    ("San Francisco, CA | New York City, NY", {"us"})])
def test_country_of(location, want):
    assert set(country_of(location)) == want


def seen(*rows):
    keys = set()
    for company, title, location in rows:
        keys.update(placed_identity_keys(company, title, location, seen=True))
    return keys


def is_seen(keys, company, title, location):
    return any(k in keys for k in placed_identity_keys(company, title, location, seen=False))


def test_a_different_country_is_a_different_role():
    keys = seen(("Sierra", "Regional VP, Sales", "Seoul, South Korea"))
    assert not is_seen(keys, "Sierra", "Regional VP, Sales", "London")


def test_the_same_country_written_two_ways_is_one_role():
    keys = seen(("Photoroom", "VP Sales (Global)", "New York, NY"))
    assert is_seen(keys, "Photoroom", "VP Sales (Global)", "New York City")
    assert is_seen(keys, "Photoroom", "VP Sales (Global)", "San Francisco, CA"), \
        "another office in the same country is usually the same job"


def test_an_unknown_place_matches_anything_either_way():
    assert is_seen(seen(("Writer", "VP Sales", "")), "Writer", "VP Sales", "London")
    assert is_seen(seen(("Writer", "VP Sales", "London")), "Writer", "VP Sales", "Remote")


def test_a_second_country_gets_its_own_row_id():
    from hunter.run import place_suffix
    base = job_id("Sierra", "Regional VP, Sales")
    held = {base: country_of("Seoul, South Korea")}
    suffix = place_suffix(base, country_of("London"), "London", "https://x/1", held)
    assert suffix == "-uk" and base + suffix != base
    assert place_suffix(base, country_of("Seoul"), "Seoul", "u", held) == "", "same role, same id"
    assert place_suffix("free:id", country_of("London"), "London", "u", held) == ""
    held[base + "-uk"] = {"other"}
    assert place_suffix(base, country_of("London"), "London", "https://x/1", held).startswith("-uk-")


def posting(company, title, location, url):
    from hunter.sources import RolePosting
    return RolePosting(company=company, title=title, url=url, source="test", location=location)


def test_the_sweep_keeps_the_london_seat_and_drops_a_relisting(monkeypatch):
    import hunter.run as R
    db = [{"job_id": "sierra:regional-vp-sales", "company": "Sierra", "title": "Regional VP, Sales",
           "location": "Seoul, South Korea", "url": "https://jobs.ashbyhq.com/sierra/aaa", "status": "blocked"}]
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: db)
    keys, ids = R.seen_identity(None)
    out = R.fresh_postings([
        posting("Sierra", "Regional VP, Sales", "London", "https://jobs.ashbyhq.com/sierra/bbb"),
        posting("Sierra", "Regional VP, Sales", "Seoul, South Korea", "https://jobs.ashbyhq.com/sierra/ccc"),
        posting("Sierra", "Regional VP, Sales", "London, UK", "https://www.linkedin.com/jobs/view/1"),
    ], keys, ids)
    assert [p.location for p in out] == ["London"], "London once; Seoul and the LinkedIn copy are known"
    assert out[0].raw["id_suffix"] == "-uk"
    from hunter.sources import ResolvedRole
    role = ResolvedRole(company="Sierra", title="Regional VP, Sales", url="", jd_url="", jd_text="",
                        live=True, source="", location="London", id_suffix=out[0].raw["id_suffix"])
    assert role.job_id == "sierra:regional-vp-sales-uk"


def test_the_learning_twin_is_never_in_another_country():
    from hunter.learn import _identity_twin
    london = {"job_id": "sierra:regional-vp-sales-uk", "company": "Sierra",
              "title": "Regional VP, Sales", "location": "London"}
    seoul = {"job_id": "sierra:regional-vp-sales", "company": "Sierra",
             "title": "Regional VP, Sales", "location": "Seoul, South Korea"}
    nyc = {"job_id": "sierra:regional-vp-sales-x", "company": "Sierra",
           "title": "Regional VP, Sales", "location": "London, UK"}
    assert _identity_twin(london, [london, seoul]) is None
    assert _identity_twin(london, [london, seoul, nyc]) is nyc


