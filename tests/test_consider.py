"""Sequoia's portfolio board, read through the Consider platform's own API.

He asked on 2026-10-05 for it to be searched alongside the a16z list. The
fixture is the live board's own answer on that day, trimmed, with the token
replaced: the tests read what the board says, never a guess at it.
"""
import json
from pathlib import Path

import pytest

from hunter import run as R
from hunter.gates import parse_comp_band, parse_comp_bottom
from hunter.sources import RolePosting, consider, portfolio

FX = json.loads((Path(__file__).parent / "fixtures" / "consider_sequoia.json").read_text())


class Resp:
    def __init__(self, payload=None, text=""):
        self._p, self.text = payload, text
    def raise_for_status(self): pass
    def json(self): return self._p


class FakeSession:
    """The board as the Session sees it: one page, then POSTs that answer
    only when the page's token comes back in the header."""
    def __init__(self, pages=None, page=FX["page"]):
        self.headers, self.page, self.pages, self.posts = {}, page, pages or {}, []
    def get(self, url, **k):
        return Resp(text=self.page)
    def post(self, url, json=None, headers=None, **k):
        self.posts.append((url, json, headers))
        assert headers.get("x-csrf-token") == "TOKEN-FROM-PAGE", "token not sent back"
        path = url.rsplit("/", 1)[1]
        seq = (json.get("meta") or {}).get("sequence")
        return Resp(self.pages[(path, seq)])


def board(monkeypatch, **kw):
    fake = FakeSession(**kw)
    monkeypatch.setattr(consider.requests, "Session", lambda: fake)
    return fake


def test_the_page_token_is_sent_back_and_every_page_is_read(monkeypatch):
    """The API refuses a call without the page's CSRF token (it answered
    412 INVALID_CSRF), and the board pages by a sequence cursor: stopping
    at the first page would read 500 of Sequoia's 10,298 roles."""
    j = FX["jobs"]
    fake = board(monkeypatch, pages={
        ("search-jobs", None): {"jobs": j[:1], "total": 2, "meta": {"sequence": "s1"}},
        ("search-jobs", "s1"): {"jobs": j[1:], "total": 2, "meta": {"sequence": "s2"}},
    })
    got = consider.fetch_jobs("Sequoia Capital")
    assert [x["title"] for x in got] == [x["title"] for x in j]
    assert all(b["board"] == {"id": "sequoia-capital", "isParent": True}
               for _, b, _ in fake.posts)


def test_a_page_with_no_token_raises_rather_than_reading_as_empty(monkeypatch):
    board(monkeypatch, page="<html>maintenance</html>")
    with pytest.raises(RuntimeError, match="no CSRF token"):
        consider.fetch_jobs("Sequoia Capital")


def test_a_firm_that_is_not_on_record_is_an_error():
    with pytest.raises(KeyError):
        consider.Board("Some Firm Nobody Added")


# ---------- companies ----------

def test_a_company_row_separates_its_funding_stage_from_its_size():
    """The board puts "1000+ employees" and "Growth" in one list. Only one
    of them is a stage."""
    verkada, gong = (consider.company_row(c, "Sequoia Capital") for c in FX["companies"])
    assert verkada["stage"] == "growth" and "Size: 1000+ employees." in verkada["description"]
    assert gong["stage"] == "seed"
    assert verkada["source_url"] == "https://jobs.sequoiacap.com/companies"
    assert verkada["activeJobsCount"] == FX["companies"][0]["numJobs"]


def test_the_other_investors_are_kept_in_its_own_words_and_sequoia_is_not_repeated():
    verkada = consider.company_row(FX["companies"][0], "Sequoia Capital")
    assert "Also backed by CapitalG, Felicis" in verkada["description"]
    assert "Sequoia Capital" not in verkada["description"].split("Also backed by")[1]


def test_its_facts_cite_the_board_and_count_as_top_tier_backing():
    from hunter.company import TIER_ONE_INVESTORS
    facts = portfolio.to_facts(consider.company_row(FX["companies"][0], "Sequoia Capital"))
    assert facts["investors"].value == "in the Sequoia Capital portfolio"
    assert all(f.source == "https://jobs.sequoiacap.com/companies" for f in facts.values())
    assert TIER_ONE_INVESTORS.search(facts["investors"].value)


def test_the_portfolio_sweep_includes_sequoia_and_names_it_when_it_fails(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(portfolio.requests, "get", boom)
    monkeypatch.setattr(consider.requests, "Session", boom)
    rows, notes = portfolio.fetch_all()
    assert rows == []
    assert "Sequoia Capital portfolio unavailable: ConnectionError" in notes


# ---------- jobs ----------

def test_a_yearly_range_reaches_the_pay_gate_as_a_range():
    """Pay was invisible on 92 percent of roles, so the floor never fired.
    The board states it for three quarters of its roles."""
    text = consider.comp_text(FX["jobs"][0]["salary"])
    assert text == "$195,500 - $230,000 USD a year"
    assert parse_comp_band(text) == (195500, 230000)


def test_an_hourly_figure_is_never_written_as_a_salary():
    hourly = {"period": {"value": "hour"}, "currency": {"value": "USD"},
              "minValue": 70, "maxValue": 90}
    assert consider.comp_text(hourly) is None
    assert consider.comp_text(None) is None


def test_a_currency_hunter_does_not_read_can_never_pass_for_dollars():
    cad = {"period": {"value": "year"}, "currency": {"value": "CAD"},
           "minValue": 250000, "maxValue": 300000}
    text = consider.comp_text(cad)
    assert text == "CAD 250,000 - 300,000 a year"
    assert parse_comp_bottom(text) is None


def test_a_posting_links_to_the_company_board_without_the_tracking_tag():
    p = consider.to_posting(FX["jobs"][1], "Sequoia Capital")
    assert p.url == "https://jobs.ashbyhq.com/Rogo/5973b2e4-d756-4be6-a485-50be2cc8196a"
    assert p.source == "Sequoia Capital board"
    assert R.source_leg(p.source) == "sequoia portfolio"


def test_a_remote_role_says_so_in_its_location():
    """G6 reads the location. BigPanda's role is remote and its locations
    only say United States."""
    p = consider.to_posting(FX["jobs"][0], "Sequoia Capital")
    assert p.location == "United States; USA (Remote)"
    assert p.raw["isRemote"] is True


# ---------- which roles the full board sweep carries ----------

def post(company, url):
    return RolePosting(company=company, title="Head of Partnerships", url=url,
                       source="Sequoia Capital board")


def test_a_readable_board_is_learned_and_left_to_the_full_sweep():
    """The full sweep brings the whole posting; the Sequoia board brings
    a title and a link. Taking both would stage the role twice."""
    cache = {}
    only, learned = R.route_board_postings(
        [post("Rogo", "https://jobs.ashbyhq.com/Rogo/5973b2e4-d756-4be6-a485-50be2cc8196a")],
        cache)
    assert only == [] and learned == 1
    assert cache["rogo"] == {"ats": "ashby", "slug": "Rogo"}


def test_a_role_on_a_board_hunter_cannot_read_is_taken_from_the_sequoia_board():
    only, learned = R.route_board_postings(
        [post("BigPanda", "https://jobs.gem.com/bigpanda/am9icG9zdDqiVK4WAhlUD-GAFV21Nmao")], {})
    assert [p.company for p in only] == ["BigPanda"] and learned == 0


def test_a_gh_jid_link_teaches_nothing_about_where_the_board_is():
    """gh_jid=123 matches the Greenhouse pattern with the posting id where
    the board slug should be. Learning it would sweep a board called 123."""
    cache = {}
    only, learned = R.route_board_postings(
        [post("Instacart", "https://instacart.careers/job/?gh_jid=8250361")], cache)
    assert cache == {} and learned == 0 and len(only) == 1


def test_a_board_already_known_under_another_name_is_not_learned_twice():
    cache = {"faire": {"ats": "greenhouse", "slug": "faire"}}
    only, learned = R.route_board_postings(
        [post("Faire Wholesale, Inc.", "https://boards.greenhouse.io/faire/jobs/8864857002")], cache)
    assert only == [] and learned == 0 and len(cache) == 1


def test_a_company_mapped_to_a_different_board_keeps_its_mapping():
    cache = {"rogo": {"ats": "greenhouse", "slug": "rogo-old"}}
    only, learned = R.route_board_postings(
        [post("Rogo", "https://jobs.ashbyhq.com/Rogo/5973b2e4-d756-4be6-a485-50be2cc8196a")], cache)
    assert cache["rogo"]["slug"] == "rogo-old" and learned == 0
    assert len(only) == 1, "a role on a board hunter will not sweep was dropped"


def test_a_remembered_miss_is_replaced_by_the_board_the_link_names():
    """A miss means hunter guessed and failed. An apply link is the board
    itself, so it settles the question."""
    cache = {"rogo": {"missed_at": "2026-09-01T00:00:00+00:00"}}
    only, learned = R.route_board_postings(
        [post("Rogo", "https://jobs.ashbyhq.com/Rogo/5973b2e4-d756-4be6-a485-50be2cc8196a")], cache)
    assert cache["rogo"] == {"ats": "ashby", "slug": "Rogo"} and learned == 1 and only == []
