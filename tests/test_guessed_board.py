"""A posting is dead only when its company's own board does not list it.

2026-10-20: a one-job Lever board called "horizon" was taken for Horizon
Media's, and the posting he sent himself was marked dead.
"""
from types import SimpleNamespace as NS

import hunter.run as R
from hunter.ats import discover as disc


def _world(monkeypatch, postings, own=None):
    monkeypatch.setattr(disc, "discover", lambda *a, **k: ("lever", "horizon"))
    monkeypatch.setattr(disc, "from_careers_page", lambda url: own)
    from hunter.ats import lever
    monkeypatch.setattr(lever, "board", lambda slug: postings)


def _p(title, text):
    return NS(title=title, raw={"descriptionPlain": text}, ats_posting_id="1")


def test_a_guessed_board_that_never_names_the_company_is_no_answer(monkeypatch):
    _world(monkeypatch, [_p("Account Executive", "Horizon builds solar farms.")])
    got = R.discover_posting(None, "Horizon Media", "VP, AI Agentic Platforms", {})
    assert got[0] == "unknown"


def test_the_companys_own_board_still_says_absent(monkeypatch):
    _world(monkeypatch, [_p("Account Executive", "At Horizon Media we plan media.")])
    got = R.discover_posting(None, "Horizon Media", "VP, AI Agentic Platforms", {})
    assert got[0] == "absent"
    _world(monkeypatch, [_p("Account Executive", "We plan media.")], own=("lever", "horizon"))
    got = R.discover_posting(None, "Horizon Media", "VP, AI Agentic Platforms", {},
                             careers_url="https://horizonmedia.com/careers")
    assert got[0] == "absent"


def test_the_slug_in_a_link_is_not_the_company_naming_itself(monkeypatch):
    p = NS(title="Account Executive", ats_posting_id="1",
           raw={"hostedUrl": "https://jobs.lever.co/horizon-media/1", "descriptionPlain": "x"})
    _world(monkeypatch, [p])
    assert R.discover_posting(None, "Horizon Media", "VP", {})[0] == "unknown"
