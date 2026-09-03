"""The a16z board and newsletter, tested against pages the sites actually
served on 2026-09-03 rather than against guesses about their shape."""
import pathlib

import pytest

from hunter.sources.a16z import (excluded, parse_companies, parse_jobs,
                                 to_posting)
from hunter.sources import newsletter as N

FX = pathlib.Path(__file__).parent / "fixtures"


# ---------- the board ----------

def test_a_board_query_page_yields_jobs_with_their_ats_link():
    jobs = parse_jobs((FX / "a16z_board_query.html").read_text())
    assert len(jobs) >= 20
    first = jobs[0]
    assert first["title"] == "Chief of Staff"
    assert first["apply_url"].startswith("https://")
    assert first["company"] and first["company_slug"]
    assert first["remote"] in (True, False)


def test_the_companies_index_yields_the_portfolio_with_stage_and_markets():
    cos = parse_companies((FX / "a16z_companies_index.html").read_text())
    assert len(cos) >= 10
    anduril = next(c for c in cos if c["slug"] == "anduril-industries")
    assert anduril["name"] == "Anduril"
    assert anduril["stage"] == "Growth"
    assert anduril["markets"] == ["AI"]
    assert anduril["job_count"] and anduril["job_count"] > 1000


def test_canon_section_5_excludes_defence_by_label_or_description():
    """The index calls Anduril's market "AI". Its description says what it is."""
    assert excluded({"markets": ["Defense"]})
    cos = parse_companies((FX / "a16z_companies_index.html").read_text())
    anduril = next(c for c in cos if c["slug"] == "anduril-industries")
    assert anduril["markets"] == ["AI"]
    assert excluded(anduril), "a defence company labelled AI must still be excluded"
    assert not excluded({"markets": ["AI", "Enterprise"], "description": "an AI CRM"})
    # Morta Security, verbatim from the live index: a cyber company whose
    # founders came from the NSA. The employer name is not the business.
    morta = {"name": "Morta Security", "markets": [], "description":
             "Traditional layered network defense is broken and Morta is poised "
             "to turn the tables on advanced attackers. Led by executives and "
             "engineers from the National Security Agency, Morta's technology "
             "uniquely combats advanced malware. Morta mixes start-up innovation "
             "with military-grade technology to solve the world's toughest "
             "network security challenges."}
    assert not excluded(morta), "a founder's former employer is not a market"
    assert excluded({"name": "Anduril", "markets": ["AI"], "description":
                     "building national security capabilities for America"})


def test_a_board_job_becomes_a_posting_the_ordinary_path_can_resolve():
    from hunter.run import ats_key
    p = to_posting({"title": "Head of GTM", "company": "Truemed", "company_slug": "truemed",
                    "apply_url": "https://jobs.lever.co/truemed/7d66629f-3f4b-41ee-a324-fe0154e13c46",
                    "location": "NYC", "posted_at": "2026-09-01T00:00:00Z", "stage": "Venture"})
    assert p.source == "a16z board"
    assert ats_key(p.url) == ("lever", "truemed", "7d66629f-3f4b-41ee-a324-fe0154e13c46")


# ---------- the newsletter ----------

def _item():
    xml = "<rss><channel>" + (FX / "a16z_newsletter_item.xml").read_text() + "</channel></rss>"
    [post] = N.parse_feed(xml)
    return post


def test_a_feed_item_carries_link_title_date_and_body():
    post = _item()
    assert post["link"].startswith("https://a16zjobs.substack.com/p/")
    assert "Cursor" in post["title"]
    assert post["published"].startswith("Mon, 31 Aug 2026")
    assert len(post["html"]) > 5000


def test_the_post_names_people_with_their_linkedin_profiles():
    post = _item()
    text = N.post_text(post["html"])
    links = N.post_links(post["html"])
    assert "Mike Myer has joined Sierra" in text
    assert "https://www.linkedin.com/in/mikemyer/" in links


def test_a_url_the_post_does_not_carry_is_dropped_not_stored():
    """The model reads; it does not invent. An invented profile URL would put
    a person on Krish's bridge list who was never in the post."""
    links = ["https://www.linkedin.com/in/mikemyer/"]
    raw = {"talent_moves": [
        {"person": "Mike Myer", "linkedin_url": "https://www.linkedin.com/in/mikemyer/",
         "company": "Sierra", "title": "GTM", "previous_company": "Snowflake", "quote": "x"},
        {"person": "Someone Invented", "linkedin_url": "https://www.linkedin.com/in/invented/",
         "company": "Sierra", "title": "", "previous_company": "", "quote": "y"},
    ], "hiring": [], "reach_out_advice": []}
    signals, dropped = N.ground(raw, links)
    assert [m["person"] for m in signals["talent_moves"]] == ["Mike Myer"]
    assert dropped and "Someone Invented" in dropped[0]


def test_a_talent_move_lands_as_a_contact_with_the_sentence_that_produced_it():
    post = {"link": "https://a16zjobs.substack.com/p/x", "title": "T", "published": "Mon, 31 Aug 2026 12:00:00 GMT"}
    signals = {"talent_moves": [{
        "person": "Mike Myer", "linkedin_url": "https://www.linkedin.com/in/mikemyer/",
        "company": "Sierra", "title": "GTM team", "previous_company": "Snowflake",
        "quote": "Mike Myer has joined Sierra’s GTM team after 8+ years at Snowflake"}]}
    [row] = N.contacts_from(signals, post)
    assert row["contact_key"] == "mikemyer"
    assert row["source"] == "a16z newsletter"
    assert row["strength_score"] == 0, "the relationship is unknown, and must not pretend otherwise"
    assert row["strength_evidence"]["newsletter_post"] == post["link"]
    assert "joined Sierra" in row["strength_evidence"]["quote"]
    assert "\u2014" not in row["strength_evidence"]["quote"]


def test_a_post_already_recorded_is_not_processed_twice(monkeypatch):
    calls = []
    monkeypatch.setattr(N, "db_get", lambda cfg, t, p: [{"link": "https://a16zjobs.substack.com/p/old"}])
    posts = [{"link": "https://a16zjobs.substack.com/p/new"},
             {"link": "https://a16zjobs.substack.com/p/old"}]
    assert [p["link"] for p in N.new_posts(None, posts)] == ["https://a16zjobs.substack.com/p/new"]
