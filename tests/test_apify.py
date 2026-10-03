"""The Apify spend controls, each one a thing that went wrong with real money.

Measured 2026-10-03 from every dataset since 1 September: $55.02 bought 53,317
LinkedIn results, 69 percent of them postings an earlier sweep had already
bought. The cap hunter set was never applied (isMaxTotalChargeUsdSetByUser
false on live runs). These tests run the real module against a fake Apify.
"""
import datetime

import pytest

import hunter.sources.apify_linkedin as ap


class Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class FakeApify:
    """Apify, as far as run_actor can see it. Records every request."""

    def __init__(self, *, applied_cap=None, set_by_user=True, poll=None,
                 final_status="SUCCEEDED", items=None, month_runs=()):
        self.posts, self.gets = [], []
        self.applied_cap, self.set_by_user = applied_cap, set_by_user
        self.poll = list(poll or [])
        self.final_status = final_status
        self.items = items if items is not None else [{"title": "VP"}]
        self.month_runs = list(month_runs)
        self.ConnectionError = ap.requests.ConnectionError
        self.Timeout = ap.requests.Timeout
        self.RequestException = ap.requests.RequestException

    def post(self, url, **kw):
        self.posts.append((url, kw))
        if url.endswith("/abort"):
            return Resp({})
        cap = kw["params"]["maxTotalChargeUsd"]
        applied = float(cap) if self.applied_cap is None else self.applied_cap
        return Resp({"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1",
                              "options": {"maxTotalChargeUsd": applied,
                                          "isMaxTotalChargeUsdSetByUser": self.set_by_user}}})

    def get(self, url, **kw):
        self.gets.append((url, kw))
        if url.endswith("/runs"):
            return Resp({"data": {"items": self.month_runs}})
        if "/actor-runs/" in url:
            if self.poll:
                step = self.poll.pop(0)
                if isinstance(step, Exception):
                    raise step
                if isinstance(step, int):
                    return Resp({}, status=step)
            return Resp({"data": {"id": "run1", "status": self.final_status,
                                  "defaultDatasetId": "ds1", "usageTotalUsd": 0.42}})
        if "/datasets/" in url:
            return Resp(self.items)
        raise AssertionError(f"unexpected GET {url}")


class Cfg:
    def __init__(self, **opts):
        self.opts = opts

    def require(self, k):
        return "apify_api_SECRETSECRETSECRET"

    def optional(self, k, d=""):
        return self.opts.get(k, d)


@pytest.fixture
def apify(monkeypatch):
    ledger = {"insert": [], "patch": [], "rows": []}
    monkeypatch.setattr(ap, "_db", lambda cfg: (
        lambda cfg, t, p: list(ledger["rows"]),
        lambda cfg, t, rows, **k: ledger["insert"].extend(rows),
        lambda cfg, t, k, v: ledger["patch"].append((k, v))))
    monkeypatch.setattr(ap.time, "sleep", lambda *_: None)

    def install(**kw):
        fake = FakeApify(**kw)
        monkeypatch.setattr(ap, "requests", fake)
        return fake
    return install, ledger


def starts(fake):
    return [p for p in fake.posts if p[0].endswith("/runs")]


def test_the_token_is_a_header_and_never_in_a_url(apify):
    install, _ = apify
    fake = install()
    ap.run_actor(Cfg(), ap.PRIMARY_LINKEDIN, {"urls": ["u"]}, max_charge_usd=1.0,
                 max_items=100, poll_seconds=0)
    for url, kw in fake.posts + fake.gets:
        assert "SECRET" not in url and "SECRET" not in str(kw.get("params"))
        assert kw["headers"]["Authorization"].startswith("Bearer ")


def test_a_cap_apify_did_not_apply_aborts_the_run_before_it_buys(apify):
    install, ledger = apify
    fake = install(applied_cap=191.24, set_by_user=False)
    with pytest.raises(ap.ApifyCapNotApplied):
        ap.run_actor(Cfg(), "act", {}, max_charge_usd=1.0, poll_seconds=0)
    assert any(u.endswith("/abort") for u, _ in fake.posts)
    assert not any("/datasets/" in u for u, _ in fake.gets), "nothing was read"
    assert ledger["insert"] == [], "an aborted, uncapped run is not a sweep"


def test_a_poll_error_follows_the_same_run_and_never_starts_another(apify):
    install, _ = apify
    fake = install(poll=[ap.requests.ConnectionError("reset"),
                         ap.requests.Timeout("slow")])
    items = ap.run_actor(Cfg(), "act", {}, max_charge_usd=1.0, poll_seconds=0)
    assert items and len(starts(fake)) == 1


def test_a_run_that_cannot_be_followed_is_aborted_not_restarted(apify):
    install, ledger = apify
    fake = install(poll=[ap.requests.ConnectionError("reset")] * 6)
    with pytest.raises(ap.ApifyRunError):
        ap.run_actor(Cfg(), "act", {}, max_charge_usd=1.0, poll_seconds=0)
    assert len(starts(fake)) == 1
    assert any(u.endswith("/abort") for u, _ in fake.posts)
    assert ledger["patch"][-1][1]["status"] == "LOST"


def test_an_error_status_after_start_aborts_the_run(apify):
    install, _ = apify
    fake = install(poll=[502])
    with pytest.raises(ap.ApifyRunError):
        ap.run_actor(Cfg(), "act", {}, max_charge_usd=1.0, poll_seconds=0)
    assert any(u.endswith("/abort") for u, _ in fake.posts)


def test_the_monthly_ceiling_refuses_before_any_request(apify):
    install, ledger = apify
    ledger["rows"] = [{"run_id": "a", "usage_usd": 39.5, "finished_at": "x"}]
    fake = install()
    with pytest.raises(ap.ApifyCeilingReached):
        ap.run_actor(Cfg(hunter_apify_max_usd_per_month="40"), "act", {},
                     max_charge_usd=1.0, poll_seconds=0)
    assert fake.posts == []


def test_the_ceiling_counts_apifys_own_record_when_the_ledger_is_short(apify):
    """A ledger write that failed cannot make the month look cheaper."""
    install, ledger = apify
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    fake = install(month_runs=[{"startedAt": now, "usageTotalUsd": 39.8}])
    with pytest.raises(ap.ApifyCeilingReached):
        ap.run_actor(Cfg(hunter_apify_max_usd_per_month="40"), ap.PRIMARY_LINKEDIN,
                     {}, max_charge_usd=1.0, poll_seconds=0)
    assert starts(fake) == []


def test_every_run_is_written_to_the_ledger_with_what_it_cost(apify):
    install, ledger = apify
    install(items=[{"title": "a"}, {"title": "b"}])
    ap.run_actor(Cfg(), "act", {}, max_charge_usd=1.0, max_items=50,
                 purpose="linkedin_sweep", poll_seconds=0)
    row = ledger["insert"][0]
    assert row["run_id"] == "run1" and row["applied_cap_usd"] == 1.0
    assert row["purpose"] == "linkedin_sweep" and row["max_items"] == 50
    done = ledger["patch"][-1][1]
    assert done["status"] == "SUCCEEDED" and done["items"] == 2 and done["usage_usd"] == 0.42


def test_each_sweep_asks_only_for_what_is_new_since_the_last():
    now = datetime.datetime(2026, 10, 4, 12, tzinfo=datetime.timezone.utc)
    last = datetime.datetime(2026, 10, 1, 15, 54, tzinfo=datetime.timezone.utc)
    w = ap.sweep_window(last, now)
    assert w == (now - last) + ap.OVERLAP
    url = ("https://www.linkedin.com/jobs/search/?keywords=%22VP%20Strategy%22%20AI"
           "&location=New%20York%20City%20Metropolitan%20Area&f_TPR=r604800")
    out = ap.with_window(url, w)
    assert "f_TPR=r604800" not in out
    assert f"f_TPR=r{int(w.total_seconds())}" in out
    assert "keywords=" in out and "location=" in out
    # no record of a sweep: a week, as the URLs asked before
    assert ap.sweep_window(None, now) == datetime.timedelta(days=7)
    # never shorter than a few hours, never a month-long backfill
    assert ap.sweep_window(now - datetime.timedelta(minutes=5), now) == ap.MIN_WINDOW
    assert ap.sweep_window(now - datetime.timedelta(days=90), now) == ap.MAX_WINDOW


def test_the_sweep_sends_the_limit_the_actor_actually_reads(apify, monkeypatch):
    seen = {}

    def capture(cfg, actor, body, **kw):
        seen.update(body=body, **kw)
        return []

    monkeypatch.setattr(ap, "run_actor", capture)
    ap.sweep_linkedin(Cfg(), ["u1", "u2"], spend=ap.SpendTracker(5), max_charge_usd=2.0,
                      limit_per_source=200)
    assert seen["body"]["limitPerSource"] == 200
    assert "resultsLimit" not in seen["body"]
    assert seen["body"]["scrapeCompany"] is False
    assert seen["max_items"] == 400
    assert "autoConvertToAiSearch" not in seen["body"], "untouched until measured"


def test_redaction_catches_a_token_in_any_line():
    line = ("apify linkedin sweep failed: HTTPError: 402 for url: "
            "https://api.apify.com/v2/acts/x/runs?token=apify_api_ABC123&x=1")
    out = ap.redact(line)
    assert "ABC123" not in out and "[redacted]" in out
    assert "apify_api_" not in ap.redact("key apify_api_ZZZ999 leaked")


def test_the_run_summary_redacts_before_it_prints_or_mails(capsys):
    from hunter.run import Summary
    s = Summary()
    s.append("failed: https://api.apify.com/v2/x?token=apify_api_LEAK")
    assert "LEAK" not in s[0] and "LEAK" not in capsys.readouterr().out


def test_a_key_pasted_back_on_the_sheet_is_reported_and_never_printed():
    from hunter import preflight
    grid = [["APIFY CONFIGURATION"], ["API Key", "apify_api_PASTEDAGAIN123"]]
    c = preflight.check_no_keys_on_the_sheet(None, read=lambda rng: grid)
    assert c.state == preflight.WARN and "B2" in c.detail
    assert "PASTEDAGAIN" not in c.line()
    clean = preflight.check_no_keys_on_the_sheet(None, read=lambda rng: [["API Key", "in system_config"]])
    assert clean.state == preflight.OK
