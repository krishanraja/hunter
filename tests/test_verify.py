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


# ---------- a paid run's results are never thrown away ----------

def test_a_timed_out_run_keeps_what_the_dataset_already_holds(monkeypatch):
    """On 2026-09-02 a nine-URL LinkedIn sweep was still RUNNING at the
    deadline with 2790 items collected. Hunter discarded all of them and
    reported zero roles sourced, having spent $2.67. The charge lands whether
    the dataset is read or not, so abandoning it is pure waste."""
    from hunter.sources import apify_linkedin as ap

    calls = {"abort": 0}

    class FakeResp:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    def fake_post(url, **kw):
        if url.endswith("/abort"):
            calls["abort"] += 1
            return FakeResp({})
        return FakeResp({"data": {"id": "run1", "status": "RUNNING",
                                  "defaultDatasetId": "ds1"}})

    def fake_get(url, **kw):
        if "/actor-runs/" in url:
            return FakeResp({"data": {"id": "run1", "status": "RUNNING",
                                      "defaultDatasetId": "ds1",
                                      "usage": {"TOTAL_USD": 2.67}}})
        return FakeResp([{"title": "Head of GTM"}, {"title": "General Manager"}])

    monkeypatch.setattr(ap.requests, "post", fake_post)
    monkeypatch.setattr(ap.requests, "get", fake_get)
    monkeypatch.setattr(ap.time, "sleep", lambda *_: None)

    class Cfg:
        def require(self, key): return "token"

    items = ap.run_actor(Cfg(), "someactor", {"urls": []},
                         max_charge_usd=2.0, poll_seconds=0, timeout_seconds=0)
    assert len(items) == 2, "the paid-for items must survive the timeout"
    assert calls["abort"] == 1, "the run must be stopped so it charges no more"


def test_a_posting_using_an_em_dash_does_not_abort_the_whole_append():
    """The no-em-dash rule covers everything hunter writes, and a JD excerpt
    it copies onto the sheet is something hunter wrote there. One posting
    using that dash used to fail the row guard and lose every staged role in
    the batch with it. Written as escapes so the repo guard, which forbids the
    character anywhere in the tree, stays satisfied by its own test.
    """
    from hunter.sheet import make_row, validate_row
    em, en = "\u2014", "\u2013"
    row = make_row(company="Gong", role="Director, Partnerships",
                   jd_url="https://boards.greenhouse.io/gong/jobs/1", score=9,
                   why_it_fits=f"Owns the roadmap {em} and the number.",
                   jd_snippet=f"Lead the alliance {em} across AWS {en} GCP.")
    assert validate_row(row, is_append=True) == []
    joined = "".join(row)
    assert em not in joined and en not in joined


def test_a_read_larger_than_one_page_returns_everything(monkeypatch):
    """PostgREST caps a response at its server max-rows (1000 on Supabase)
    whatever `limit` asks for. Every read passed limit=5000 and quietly got
    1000, so reconcile's id guard saw 1000 of 1888 job_ids and re-inserted
    rows it already had, and dedupe ran against a partial view. Nothing ever
    errored, which is why it went unnoticed for weeks."""
    from hunter import config as C

    rows = [{"job_id": f"c{i}:role"} for i in range(2350)]
    seen_ranges = []

    class FakeResp:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    def fake_get(url, headers=None, params=None, timeout=None):
        lo, hi = (int(x) for x in headers["Range"].split("-"))
        seen_ranges.append((lo, hi))
        return FakeResp(rows[lo:hi + 1])

    monkeypatch.setattr(C.requests, "get", fake_get)
    cfg = C.Config(supabase_url="https://x", supabase_key="k", raw={})
    got = C.db_get(cfg, "hunter_seen_roles", {"select": "job_id", "limit": "5000"})
    assert len(got) == 2350, "a read must not stop at the server's page size"
    assert len(seen_ranges) == 3

    capped = C.db_get(cfg, "hunter_seen_roles", {"select": "job_id", "limit": "7"})
    assert len(capped) == 7, "an explicit small limit is still honoured"
