"""P3 regressions and guards, written before the code they test.

Two incidents drive this file and must stay red until the clients exist and
handle them: the 2026-08-11 Ashby list-API false negatives, and the Slingshot
AI years-requirement miss that created gate G4. Plus the forbidden-actor
guard, which must raise before any HTTP happens.
"""
import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def ashby_fixture():
    return json.loads((FIXTURES / "ashby_livepost.json").read_text())


@pytest.fixture(scope="session")
def slingshot_jd():
    return (FIXTURES / "jd_slingshot_ai.txt").read_text()


class SpyTransport:
    """Fails the test if any real network is attempted; serves canned pages."""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.requested: list[str] = []

    def get(self, url, **kw):
        self.requested.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected HTTP GET in test: {url}")
        return FakeResp(self.pages[url])

    def post(self, url, **kw):
        self.requested.append(url)
        raise AssertionError(f"unexpected HTTP POST in test: {url}")


class FakeResp:
    def __init__(self, body, status=200):
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.status_code = status

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ---------- Ashby: the 2026-08-11 false-negative regression ----------

def test_ashby_live_posting_from_app_data(ashby_fixture, monkeypatch):
    import hunter.ats.ashby as ashby
    pid = ashby_fixture["posting_id"]
    url = f"https://jobs.ashbyhq.com/harvey/{pid}"
    spy = SpyTransport({url: ashby_fixture["live_page_html"]})
    monkeypatch.setattr(ashby, "requests", spy)
    live, jd_text, jd_url = ashby.fetch_posting("harvey", pid)
    assert live is True
    assert "Customer Success" in jd_text or len(jd_text) > 500
    assert jd_url == url
    # The regression: liveness must come from the direct page only. The list
    # API omitted live postings on 2026-08-11; touching it here is a failure.
    assert all("posting-api" not in u for u in spy.requested), spy.requested


def test_ashby_missing_app_data_means_dead(ashby_fixture, monkeypatch):
    import hunter.ats.ashby as ashby
    url = "https://jobs.ashbyhq.com/harvey/dead-posting"
    spy = SpyTransport({url: ashby_fixture["dead_page_html"]})
    monkeypatch.setattr(ashby, "requests", spy)
    live, jd_text, _ = ashby.fetch_posting("harvey", "dead-posting")
    assert live is False


def test_ashby_unlisted_posting_is_dead(ashby_fixture, monkeypatch):
    import hunter.ats.ashby as ashby
    pid = ashby_fixture["posting_id"]
    url = f"https://jobs.ashbyhq.com/harvey/{pid}"
    spy = SpyTransport({url: ashby_fixture["unlisted_page_html"]})
    monkeypatch.setattr(ashby, "requests", spy)
    live, _, _ = ashby.fetch_posting("harvey", pid)
    assert live is False


def test_ashby_list_omission_does_not_kill_a_live_posting(ashby_fixture, monkeypatch):
    """The exact incident: the job-board list response omits the posting while
    the direct page shows it live. The verdict must be LIVE."""
    import hunter.ats.ashby as ashby
    pid = ashby_fixture["posting_id"]
    url = f"https://jobs.ashbyhq.com/harvey/{pid}"
    spy = SpyTransport({url: ashby_fixture["live_page_html"]})
    monkeypatch.setattr(ashby, "requests", spy)
    live, _, _ = ashby.fetch_posting("harvey", pid)
    assert live is True  # despite list_api_response_missing_it in the fixture


# ---------- the forbidden-actor guard ----------

def test_forbidden_actors_raise_before_any_http(monkeypatch):
    import hunter.sources.apify_linkedin as apify
    spy = SpyTransport()
    monkeypatch.setattr(apify, "requests", spy)
    for actor in ("BHzefUZlZRKWxkTck", "pZezG04IIqOdtiwu7"):
        with pytest.raises(apify.ForbiddenActorError):
            apify.run_actor(None, actor, {"urls": []}, max_charge_usd=0.5)
    assert spy.requested == []


def test_actor_input_always_carries_charge_cap(monkeypatch):
    import hunter.sources.apify_linkedin as apify

    captured = {}

    class CapSpy(SpyTransport):
        def post(self, url, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            raise RuntimeError("stop after capturing the request")

    monkeypatch.setattr(apify, "requests", CapSpy())

    class FakeCfg:
        def require(self, k):
            return "token-token-token"

        def optional(self, k, d=""):
            return d

    with pytest.raises(RuntimeError, match="stop after"):
        apify.run_actor(FakeCfg(), apify.PRIMARY_LINKEDIN,
                        {"urls": ["https://linkedin.example"]}, max_charge_usd=1.25)
    assert captured["json"]["maxTotalChargeUsd"] == 1.25


def test_spend_tracker_soft_stops():
    import hunter.sources.apify_linkedin as apify
    t = apify.SpendTracker(cap_usd=5.0)
    t.add(2.0)
    t.add(2.5)
    assert t.can_spend(0.4)
    assert not t.can_spend(1.0)


# ---------- gates ----------

def make_role(**over):
    from hunter.sources import ResolvedRole
    base = dict(
        company="Acme AI", title="VP of Commercial Strategy",
        url="https://job-boards.greenhouse.io/acme/jobs/1",
        jd_url="https://job-boards.greenhouse.io/acme/jobs/1",
        jd_text=("We need a leader to build the commercial operating model and "
                 "own the P&L for a new market. 10+ years experience. Remote, "
                 "United States or London."),
        live=True, source="test", location="Remote US",
        comp="$220,000 - $260,000 + equity",
    )
    base.update(over)
    return ResolvedRole(**base)


def gate_result(report, name):
    return next(g for g in report.results if g.gate == name)


def test_slingshot_jd_fails_g4(slingshot_jd):
    from hunter.gates import run_gates
    role = make_role(title="Head of Growth Partnerships", jd_text=slingshot_jd)
    report = run_gates(role)
    g4 = gate_result(report, "G4")
    assert not g4.passed, "the Slingshot miss must never pass G4 again"
    assert "4-6" in g4.reason or "4 to 6" in g4.reason


def test_ten_plus_years_passes_g4():
    from hunter.gates import run_gates
    report = run_gates(make_role())
    assert gate_result(report, "G4").passed


def test_g2_band_bottom_below_floor_fails():
    from hunter.gates import run_gates
    report = run_gates(make_role(comp="$150,000 - $180,000"))
    assert not gate_result(report, "G2").passed


def test_g2_k_notation_parses():
    from hunter.gates import run_gates
    report = run_gates(make_role(comp="$276K - $325K plus equity"))
    assert gate_result(report, "G2").passed


def test_g2_unposted_band_passes_with_note():
    from hunter.gates import run_gates
    report = run_gates(make_role(comp="not posted"))
    g2 = gate_result(report, "G2")
    assert g2.passed and "no posted band" in g2.reason


def test_g6_us_residence_requirement_blocks():
    from hunter.gates import run_gates
    role = make_role(jd_text=make_role().jd_text
                     + " Candidates must reside in the United States.")
    report = run_gates(role)
    g6 = gate_result(report, "G6")
    assert not g6.passed and "residence" in g6.reason.lower()


def test_g6_london_passes():
    from hunter.gates import run_gates
    report = run_gates(make_role(location="London, UK"))
    assert gate_result(report, "G6").passed


def test_g7_clinical_domain_fails():
    from hunter.gates import run_gates
    role = make_role(company="MedTrial",
                     jd_text="Lead commercial strategy for our clinical trials "
                             "platform serving hospital systems. 12+ years.")
    report = run_gates(role)
    assert not gate_result(report, "G7").passed


def test_never_apply_blocklist_fires_before_gates():
    from hunter.gates import run_gates
    report = run_gates(make_role(company="Meta"),
                       never_apply=["Meta", "Amazon"])
    assert not report.passed
    assert any("never_apply" in g.reason for g in report.results if not g.passed)


def test_dead_role_fails_g1():
    from hunter.gates import run_gates
    report = run_gates(make_role(live=False))
    assert not gate_result(report, "G1").passed


# ---------- scoring: exactly two auto-rejects ----------

def test_below_floor_band_auto_rejects():
    from hunter.score import score_role
    result = score_role(make_role(comp="$150,000 - $170,000"))
    assert result.auto_rejected


def test_pure_quota_auto_rejects():
    from hunter.score import score_role
    role = make_role(jd_text="Carry a $3M quota and hit aggressive Q2 targets. "
                             "Close pipeline. Deliver against the existing motion. "
                             "10+ years enterprise sales.")
    result = score_role(role)
    assert result.auto_rejected


def test_revops_is_penalized_not_rejected():
    from hunter.score import score_role
    role = make_role(title="VP Revenue Operations",
                     jd_text="Own RevOps and sales operations. Build the operating "
                             "model and design the commercial architecture. 10+ years.")
    result = score_role(role)
    assert not result.auto_rejected
    assert result.components.get("penalties", 0) < 0


def test_engine_builder_role_clears_bar():
    from hunter.score import score_role
    role = make_role(
        title="Founding Chief Commercial Officer",
        jd_text=("First commercial hire. Build the commercial engine from scratch, "
                 "design the GTM motion, own the operating model and P&L, hire and "
                 "develop the team, lead market entry. AI-native product, Series B. "
                 "10+ years. Remote US. $250,000 base plus equity."),
        comp="$250,000 - $300,000 + equity", stage="Series B")
    result = score_role(role)
    assert not result.auto_rejected
    assert result.score >= 8, result.components


# ---------- G6 geography (2026-09-01: "Remote India" passed the gate) ----------

@pytest.mark.parametrize("location,allowed", [
    ("London, UK", True),
    ("New York, NY", True),
    ("Remote - United States", True),
    ("US - Remote San Francisco, CA New York, NY", True),
    ("UK or US-remote (offices in NYC, SF, London, Dublin, Warsaw)", True),
    ("Remote India", False),                        # a bare "remote" is not a location
    ("Germany (remote-first, in-country)", False),
    ("Brazil (in-country)", False),
    ("Singapore", False),
    ("Dublin, Ireland", False),
    ("Saudi Arabia (in-country)", False),
])
def test_geography_names_a_country_canon_does_not_cover(location, allowed):
    from hunter.gates import geography_ok
    assert geography_ok(location) is allowed


def test_g6_rejects_a_remote_india_posting_end_to_end():
    from hunter.sources import ResolvedRole
    from hunter.gates import run_gates
    role = ResolvedRole(
        company="Cloudflare", title="Country Director, India",
        url="https://x.example/j", jd_url="https://x.example/j",
        jd_text="Lead Cloudflare's India business. Remote India. Build the "
                "country team and own the P&L for the market.",
        live=True, source="ats", location="Remote India", comp="$300,000 base")
    report = run_gates(role, never_apply=[])
    assert not report.passed
    assert any(g.gate == "G6" for g in report.failures())
