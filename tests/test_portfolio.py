"""VC portfolio boards, as a source of company facts.

Backing is the component the scorer can least often establish: a homepage
does not say who led the Series B, so hundreds of companies score 6.7 where
they would score 8.9 if hunter simply knew who was behind them. A firm's own
portfolio board answers that by definition, and carries a description, a
stage and a location set with it.
"""
import json

import pytest

from hunter.sources import portfolio


PAGE = (
    '<html><script>{"network":{"id":"8672","label":"accel"},'
    '"companies":[{"id":1,"name":"1Password","slug":"1password",'
    '"domain":"1password.com","description":"1Password is a secure password '
    'manager that provides businesses and families with a safe way to share '
    'passwords.","headCount":5,"locations":["Toronto, ON, Canada"],'
    '"stage":"series_c","industryTags":["Security"],"activeJobsCount":62}]}'
    "</script></html>"
)


def test_the_network_id_is_read_off_the_page():
    """The board's API wants the id the page embeds, and the first guess at
    it ("52780", scraped by a loose pattern) answered "Couldn't find
    Collection"."""
    assert portfolio.network_id(PAGE) == "8672"


def test_a_company_is_parsed_out_of_the_page_it_is_serialised_in():
    got = portfolio.parse(PAGE)
    assert len(got) == 1
    assert got[0]["name"] == "1Password" and got[0]["slug"] == "1password"


def test_a_description_containing_braces_does_not_break_the_scan():
    """A regex cannot find the object boundary: descriptions contain braces
    and the payload is one long line."""
    page = PAGE.replace("safe way to share passwords.",
                        "safe way to share {passwords} and {secrets}.")
    got = portfolio.parse(page)
    assert len(got) == 1 and "{passwords}" in got[0]["description"]


def test_headcount_is_never_read_as_a_number_of_people():
    """The platform reports head_count as a BAND INDEX. 1Password, which has
    well over a thousand employees, comes back as 5. Reading it as a
    headcount would put a confident wrong fact on his sheet, which is worse
    than the unknown it replaced."""
    assert "headCount" not in portfolio.WANTED
    facts = portfolio.to_facts(dict(portfolio.parse(PAGE)[0], firm="Accel",
                                    source_url="https://jobs.accel.com/companies"))
    assert "headcount" not in facts


def test_every_fact_from_a_portfolio_row_cites_the_board():
    row = dict(portfolio.parse(PAGE)[0], firm="Accel",
               source_url="https://jobs.accel.com/companies")
    facts = portfolio.to_facts(row)
    assert set(facts) >= {"what_it_does", "investors", "stage", "locations"}
    for name, fact in facts.items():
        assert fact.source.startswith("http"), f"{name} cites nothing"
    assert "Accel portfolio" in facts["investors"].value


def test_the_stage_is_translated_into_the_words_the_scorer_reads():
    from hunter.company import venture_stage, Facts
    row = dict(portfolio.parse(PAGE)[0], firm="Accel",
               source_url="https://jobs.accel.com/companies")
    facts = portfolio.to_facts(row)
    c = venture_stage(Facts(slug="x", name="X", stage=facts["stage"]))
    assert c.evidenced and c.points > 0


def test_a_page_with_no_companies_yields_nothing_rather_than_guessing():
    assert portfolio.parse("<html><body>no companies here</body></html>") == []


def test_a_firm_that_is_not_on_record_is_an_error_not_an_empty_list():
    """Silently returning nothing for a firm nobody wired up is how a source
    looks healthy while contributing zero."""
    with pytest.raises(KeyError):
        portfolio.fetch("Some Firm Nobody Added")


def test_pagination_does_not_stop_at_the_first_short_page(monkeypatch):
    """The board answers with a fixed page of twelve whatever per_page asks
    for, and reports the real total separately. Treating a short page as the
    last page stopped the sweep after the first twelve of Accel's 579."""
    pages = {
        0: {"count": 20, "companies": [{"name": f"C{i}", "slug": f"c{i}"}
                                       for i in range(12)]},
        1: {"count": 20, "companies": [{"name": f"C{i}", "slug": f"c{i}"}
                                       for i in range(12, 20)]},
    }

    class Resp:
        def __init__(self, payload=None, text=""):
            self._p, self.text, self.ok = payload, text, True
        def raise_for_status(self): pass
        def json(self): return self._p

    monkeypatch.setattr(portfolio.requests, "get",
                        lambda *a, **k: Resp(text=PAGE))
    monkeypatch.setattr(portfolio.requests, "post",
                        lambda url, **k: Resp({"results": pages[k["json"]["page"]]}))
    monkeypatch.setitem(portfolio.FIRMS, "Accel", "https://jobs.accel.com/companies")
    got = portfolio.fetch("Accel")
    assert len(got) == 20, "the sweep stopped at the first page"


def test_a_firm_whose_board_fails_is_named_rather_than_silently_absent(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(portfolio.requests, "get", boom)
    rows, notes = portfolio.fetch_all()
    assert rows == []
    assert all("unavailable" in n for n in notes) and len(notes) == len(portfolio.FIRMS)


# ---------- staying current without spending the run on it ----------

def test_a_fresh_cache_is_not_swept_again(monkeypatch):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(portfolio, "refresh",
                        lambda cfg: pytest.fail("swept a cache that was current"))
    import hunter.config as C
    monkeypatch.setattr(C, "db_get", lambda *a, **k: [{"last_seen": recent}])
    assert portfolio.refresh_if_stale(object()) == []


def test_a_stale_cache_is_swept(monkeypatch):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    called = {}
    monkeypatch.setattr(portfolio, "refresh",
                        lambda cfg: (called.setdefault("yes", True), (7, ["Accel: 7"]))[1])
    import hunter.config as C
    monkeypatch.setattr(C, "db_get", lambda *a, **k: [{"last_seen": old}])
    lines = portfolio.refresh_if_stale(object())
    assert called.get("yes") and any("7 companies" in x for x in lines)


def test_a_firm_being_down_does_not_take_the_sourcing_run_with_it(monkeypatch):
    """Last week's facts are worth more than a failed run."""
    def boom(cfg):
        raise ConnectionError("down")
    monkeypatch.setattr(portfolio, "refresh", boom)
    import hunter.config as C
    monkeypatch.setattr(C, "db_get", lambda *a, **k: [])
    lines = portfolio.refresh_if_stale(object())
    assert lines and "using what hunter already holds" in lines[0]
