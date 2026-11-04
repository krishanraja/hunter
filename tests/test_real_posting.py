"""The real posting, every time, and the systems hunter can fill.

His words, 2026-11-04: "You should be checking for the real job posting all
the time. What system do you not have a reader for and is it impossible to
build one?" The LinkedIn sweep returned the employer's own apply link on every
item and hunter threw it away; Workday was read on the wrong data centre; and
Workday and iCIMS forms had no filler at all.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import hunter.run as run_mod  # noqa: E402
from test_process import Cfg, FakeCanon  # noqa: E402
from test_sheet_rows import FakeSheet  # noqa: E402
from hunter.sheet import HEADERS, N_COLS  # noqa: E402

ASHBY_LINK = "https://jobs.ashbyhq.com/inworld/0f8b2c3e-1111-2222-3333-444455556666"
WD_LINK = ("https://sonos.wd1.myworkdayjobs.com/en-US/Sonos/job/Boston-MA/"
           "Head-of-Partnerships_R2900-1")


# ---------- the LinkedIn sweep keeps the employer's link ----------

def test_the_sweep_keeps_the_employer_link_the_actor_returns(monkeypatch):
    import hunter.sources.apify_linkedin as al
    monkeypatch.setattr(al, "run_actor", lambda *a, **k: [
        {"companyName": "Inworld AI", "title": "Head of Sales", "jobUrl": "https://www.linkedin.com/jobs/view/1",
         "applyUrl": ASHBY_LINK, "descriptionText": "x"},
        {"companyName": "Acme", "title": "GM", "jobUrl": "https://www.linkedin.com/jobs/view/2",
         "applyUrl": "https://www.linkedin.com/jobs/view/2/apply", "descriptionText": "x"},
        {"companyName": "Beta", "title": "GM", "jobUrl": "https://www.linkedin.com/jobs/view/3",
         "descriptionText": "x"}])

    class C:
        def optional(self, k, d=""):
            return d
    out = al.sweep_linkedin(C(), ["u"], spend=al.SpendTracker(cap_usd=1), max_charge_usd=1)
    assert out[0].employer_url == ASHBY_LINK
    assert out[0].url == "https://www.linkedin.com/jobs/view/1"
    # Easy Apply's link is LinkedIn itself: no employer link, never a pretend one.
    assert out[1].employer_url is None and out[2].employer_url is None


def test_staging_reads_the_employer_board_instead_of_guessing(monkeypatch):
    """A LinkedIn posting whose employer link is an Ashby posting is read off
    Ashby directly: no slug guessing, the row keeps the link."""
    from hunter.ats import ashby
    from hunter.sources import RolePosting
    import hunter.ats.discover as disc
    import hunter.package.rationale as rationale_mod
    inserted, fetched, guessed = [], [], []
    monkeypatch.setattr(run_mod, "seen_identity_keys", lambda cfg: set())
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [])
    monkeypatch.setattr(run_mod, "db_insert", lambda cfg, table, rows, **kw: inserted.extend(rows))
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(disc, "discover", lambda cfg, company, cache: guessed.append(company))
    jd = ("Inworld is hiring a Chief of Staff in New York. You will build the operating "
          "model with the CEO and own P&L reviews. AI native company. ") * 6

    def fetch(slug, pid):
        fetched.append((slug, pid))
        return True, jd, ASHBY_LINK
    monkeypatch.setattr(ashby, "fetch_posting", fetch)
    monkeypatch.setattr(rationale_mod, "write_rationale_and_snippet",
                        lambda *a, **k: ("Own the operating model. FIT: x. RISK: none.",
                                         "Inworld builds voice AI.", []))
    p = RolePosting(company="Inworld AI", title="Chief of Staff",
                    url="https://www.linkedin.com/jobs/view/chief-of-staff-at-inworld-9",
                    source="apify_linkedin", location="New York, NY",
                    raw={"descriptionText": jd}, employer_url=ASHBY_LINK)
    s = FakeSheet([list(HEADERS), [""] * N_COLS])
    counts = run_mod.stage_postings(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, [p], [])
    assert fetched == [("inworld", "0f8b2c3e-1111-2222-3333-444455556666")]
    assert guessed == []
    assert counts.get("boards_from_apply_link") == 1
    assert counts["from_description"] == 0
    assert inserted[0]["employer_url"] == ASHBY_LINK
    assert inserted[0]["url"] == ASHBY_LINK


# ---------- Workday on its own data centre ----------

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


LIVE = {"jobPostingInfo": {"jobPostingId": "R2900", "jobDescription": "<p>Lead partnerships.</p>"}}


def test_workday_reads_the_data_centre_in_the_link(monkeypatch):
    """Sonos is on wd1. The host was hard-coded to wd3, so a live Sonos
    posting answered 404 and read as dead."""
    from hunter.ats import workday
    asked = []

    def get(url, **kw):
        asked.append(url)
        return _Resp(200, LIVE) if ".wd1." in url else _Resp(404)
    monkeypatch.setattr(workday.requests, "get", get)
    key = run_mod.board_key(WD_LINK)
    assert key == ("workday", "sonos", "Sonos/job/Boston-MA/Head-of-Partnerships_R2900-1")
    role = run_mod._resolve_for_build({"title": "Head of Partnerships", "url": WD_LINK})
    assert role.live is True and "Lead partnerships." in role.jd_text
    assert all(".wd1." in u for u in asked)


def test_workday_with_only_a_tenant_asks_each_data_centre(monkeypatch):
    from hunter.ats import workday
    asked = []

    def get(url, **kw):
        asked.append(url)
        return _Resp(200, LIVE) if ".wd5." in url else _Resp(404)
    monkeypatch.setattr(workday.requests, "get", get)
    live, text, url = workday.fetch_posting("acme", "Acme/job/NYC/GM_R1")
    assert live and ".wd5." in url
    assert [u.split(".")[1] for u in asked][:3] == ["wd1", "wd3", "wd5"]
    # Dead on every data centre is dead.
    monkeypatch.setattr(workday.requests, "get", lambda url, **kw: _Resp(404))
    assert workday.fetch_posting("acme", "Acme/job/NYC/GM_R1")[0] is False


# ---------- naming the system ----------

def test_icims_links_are_postings_and_every_system_is_named():
    assert run_mod.ats_key("https://careers-publicis.icims.com/jobs/123456/svp/job?in_iframe=1") == (
        "icims", "careers-publicis", "123456")
    assert run_mod.system_of(WD_LINK) == "workday"
    assert run_mod.system_of("https://jobs.smartrecruiters.com/Acme/123") == "smartrecruiters"
    assert run_mod.system_of("https://careers.ralphlauren.com/job/123") == "own site"
    assert run_mod.system_label("https://careers.ralphlauren.com/job/123") == \
        "the company's own careers site"
    assert run_mod.system_label("https://careers-x.icims.com/jobs/1/job") == "iCIMS"
    assert run_mod.system_of("") == ""


def test_the_census_counts_by_the_employer_link_and_never_guesses():
    rows = [
        {"url": "https://www.linkedin.com/jobs/view/1", "employer_url": ASHBY_LINK,
         "status": "staging"},
        {"url": "https://www.linkedin.com/jobs/view/2", "status": "scanned"},
        {"url": WD_LINK, "status": "scanned"},
        {"url": "https://careers.ralphlauren.com/job/9", "presented_at": "2026-11-01"},
    ]
    got = run_mod.census(rows)
    assert got["ashby"] == {"all": 1, "shown": 1, "fillable": 1}
    assert got["linkedin, employer unknown"]["all"] == 1
    assert got["workday"]["fillable"] == 1
    assert got["own site"] == {"all": 1, "shown": 1, "fillable": 0}


# ---------- the build and the form read the employer's link ----------

def test_the_form_is_read_from_the_employer_link_first():
    linkedin = "https://www.linkedin.com/jobs/view/9"
    assert run_mod.form_url(ASHBY_LINK, linkedin) == ASHBY_LINK
    # An employer link no board serves still beats LinkedIn's page about it.
    own = "https://careers.ralphlauren.com/job/9"
    assert run_mod.form_url(own, linkedin) == own


def test_the_build_relinks_to_a_live_employer_posting(monkeypatch):
    from hunter.ats import ashby
    monkeypatch.setattr(ashby, "fetch_posting", lambda s, p: (True, "x" * 300, ASHBY_LINK))
    row = {"title": "Chief of Staff", "company": "Inworld AI",
           "url": "https://www.linkedin.com/jobs/view/9", "employer_url": ASHBY_LINK}
    role, relink, flags = run_mod.resolve_for_build(Cfg(), row, {})
    assert relink == ASHBY_LINK and role.live
    assert "the employer's own link, read directly" in flags


def test_a_dead_employer_link_never_decides_alone(monkeypatch):
    """Not live on the employer link falls through to discovery, as before."""
    from hunter.ats import ashby
    monkeypatch.setattr(ashby, "fetch_posting", lambda s, p: (False, "", ASHBY_LINK))
    seen = []
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache, **k: seen.append(company) or ("absent", None, "gone"))
    row = {"title": "Chief of Staff", "company": "Inworld AI",
           "url": "https://www.linkedin.com/jobs/view/9", "employer_url": ASHBY_LINK}
    role, relink, flags = run_mod.resolve_for_build(Cfg(), row, {})
    assert seen == ["Inworld AI"] and relink is None


def test_the_by_hand_email_names_where_the_application_is(monkeypatch):
    from hunter import notify
    sent = {}
    monkeypatch.setattr(notify, "send_email", lambda cfg, subj, html, **k: sent.update(html=html) or {"sent": True})
    monkeypatch.setattr(notify, "mailbox", lambda cfg: "x")
    run_mod.send_apply_by_hand(Cfg(), company="Ralph Lauren", role="Head of AI",
                               jd_url="https://careers.ralphlauren.com/job/9",
                               cv_url="", letter_url="", essays={}, why="w")
    assert "Apply on the company&#x27;s own careers site" in sent["html"]
    assert "LinkedIn" not in sent["html"]


# ---------- the Workday and iCIMS forms ----------

def test_a_live_workday_posting_declares_its_first_step(monkeypatch):
    from hunter.apply import fetch
    from hunter.ats import workday
    monkeypatch.setattr(workday, "fetch_posting", lambda t, p, host="": (True, "jd", WD_LINK))
    spec = fetch.form_for(WD_LINK + "/apply?source=LinkedIn", run_mod.ats_key)
    assert spec.readable and spec.ats == "workday"
    assert spec.slug == "sonos.wd1.myworkdayjobs.com"
    assert spec.posting_id == "Sonos/job/Boston-MA/Head-of-Partnerships_R2900-1"
    labels = {f.label for f in spec.fields}
    assert {"First Name", "Last Name", "Email Address", "Phone Number", "Resume/CV"} <= labels


def test_a_dead_workday_posting_is_never_offered_as_a_form(monkeypatch):
    from hunter.apply import fetch
    from hunter.ats import workday
    monkeypatch.setattr(workday, "fetch_posting", lambda t, p, host="": (False, "", WD_LINK))
    spec = fetch.form_for(WD_LINK, run_mod.ats_key)
    assert not spec.readable and spec.fields == ()


def test_icims_reads_the_posting_before_declaring_a_form(monkeypatch):
    from hunter.apply import fetch
    from hunter.ats import icims
    monkeypatch.setattr(icims, "fetch_posting", lambda s, p: (True, "jd", "u"))
    spec = fetch.form_for("https://careers-publicis.icims.com/jobs/123456/svp/job", run_mod.ats_key)
    assert spec.readable and spec.ats == "icims" and spec.slug == "careers-publicis.icims.com"
    monkeypatch.setattr(icims, "fetch_posting", lambda s, p: (False, "", "u"))
    assert not fetch.form_for("https://careers-publicis.icims.com/jobs/1/x/job",
                              run_mod.ats_key).readable


def test_an_icims_page_that_says_the_job_is_gone_is_dead(monkeypatch):
    from hunter.ats import icims

    class R:
        status_code = 200
        text = "<html><body>Sorry, this job is no longer available.</body></html>"
    monkeypatch.setattr(icims.requests, "get", lambda *a, **k: R())
    assert icims.fetch_posting("careers-x", "1")[0] is False
    R.text = "<html><body>" + "Lead the commercial team. " * 20 + "</body></html>"
    assert icims.fetch_posting("careers-x", "1")[0] is True


# ---------- the payload for a step-by-step form ----------

class _E:
    def __init__(self, name, value, sensitive=False):
        self.field_name, self.value = name, value
        self.usable, self.always_flagged = bool(value), sensitive


class _Bank:
    def __init__(self, entries):
        self.entries = {e.field_name.lower(): e for e in entries}

    def get(self, name):
        return self.entries.get(name.lower())


def test_a_workday_payload_fills_step_by_step_from_his_own_wording():
    from hunter.apply import payload as P
    from hunter.apply.fill import FillPlan
    plan = FillPlan(ats="workday", slug="sonos.wd1.myworkdayjobs.com",
                    posting_id="Sonos/job/Boston-MA/Head_R1", company="Sonos", role="Head")
    bank = _Bank([_E("Are you legally authorized to work in the United States?", "Yes"),
                  _E("Gender", "Male", sensitive=True),
                  _E("Standing: headline", "x"),
                  _E("Empty", "")])
    doc = P.build(plan, bank=bank)
    assert doc["multi_step"] is True and doc["system"] == "workday"
    assert doc["url"] == "https://sonos.wd1.myworkdayjobs.com/en-US/Sonos/job/Boston-MA/Head_R1/apply"
    labels = [a["label"] for a in doc["answers"]]
    assert labels == ["Are you legally authorized to work in the United States?"]
    assert doc["needs_extension"] == P.MIN_EXTENSION == "1.7.0"


def test_a_board_form_payload_is_unchanged():
    from hunter.apply import payload as P
    from hunter.apply.fill import FillPlan
    doc = P.build(FillPlan(ats="ashby", slug="a", posting_id="b", company="C", role="R"))
    assert "multi_step" not in doc and "answers" not in doc


def test_neither_new_driver_can_press_submit():
    from hunter.apply.fill import FillPlan
    from hunter.apply.submit import DRIVERS, SubmitBlocked
    for ats in ("workday", "icims"):
        d = DRIVERS[ats](None, FillPlan(ats=ats, slug="s", posting_id="p", company="C", role="R"))
        with pytest.raises(SubmitBlocked):
            d.press_submit()


def test_the_extension_answers_only_on_an_exact_label():
    """A guessed answer under his name is the failure. The matcher compares
    the plain label for equality, never a substring or a closest row."""
    src = (ROOT / "extension" / "fill.js").read_text()
    body = src[src.index("async function actAnswers"):src.index("function emptyRequired")]
    assert "bank.has(q)" in body
    assert "includes(" not in body and "indexOf(q)" not in body


def test_the_apply_email_reads_the_form_from_the_employer_link():
    import inspect
    src = inspect.getsource(run_mod.build_fill_plan)
    assert 'form_url(row.get("employer_url"), role.jd_url' in src
    src = inspect.getsource(run_mod.cmd_approvals)
    assert 'where = row.get("employer_url") or plan.jd_url' in src


def test_workday_that_never_answers_is_unknown_not_dead(monkeypatch):
    import requests
    from hunter.ats import workday

    def down(url, **kw):
        raise requests.ConnectionError("reset")
    monkeypatch.setattr(workday.requests, "get", down)
    with pytest.raises(requests.RequestException):
        workday.fetch_posting("sonos", "Sonos/job/X/Y_R1", host="sonos.wd1.myworkdayjobs.com")
