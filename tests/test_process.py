"""The process command and the paths under it: building a Yes row whose URL
has no ATS key, the honest dead branch, selection without a cap, G12 at
staging, the warm path cells, and the one-command drain. Everything offline:
every network and DB call is monkeypatched."""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as run_mod  # noqa: E402
import hunter.sheet as sheet_mod  # noqa: E402
from hunter.sheet import COLS, HEADERS, N_COLS, hyperlink, parse_hyperlink  # noqa: E402
from test_sheet_rows import FakeSheet, data_row  # noqa: E402

C = COLS


class Cfg:
    def __init__(self, raw=None):
        self.raw = raw or {}

    def optional(self, key, default=""):
        return self.raw.get(key) or default

    def require(self, key):
        return self.raw[key]

    def require_json(self, key):
        import json
        return json.loads(self.raw[key])


class FakeCanon:
    sheet_headers = HEADERS
    bar = 8
    universe = []


# ---------- resolve_for_build ----------

def test_a_linkedin_url_with_a_live_page_builds_unverified(monkeypatch):
    """Ten of the 26 Yes rows had no ATS key and were refused as dead."""
    monkeypatch.setattr(run_mod, "linkedin_state", lambda url: (None, "stub"))
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("unknown", None, "no job board found"))
    monkeypatch.setattr(run_mod, "fetch_jd_plain",
                        lambda url: (None, "Centari builds contract intelligence. " * 20))
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Founding GTM Lead", "company": "Centari",
               "url": "https://www.linkedin.com/jobs/view/founding-gtm-lead-1"}, {})
    assert role.liveness == "unverified" and not role.live and relink is None
    assert len(role.jd_text) > 200
    assert run_mod.package_status_for(role, {}) == sheet_mod.PKG_BUILT_UNVERIFIED


def test_a_board_that_lacks_the_title_is_verifiably_dead(monkeypatch):
    monkeypatch.setattr(run_mod, "linkedin_state", lambda url: (None, "stub"))
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("absent", None, "not on the ashby/x board (12 jobs)"))
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Chief of Staff", "company": "January",
               "url": "https://www.linkedin.com/jobs/view/cos-2"}, {})
    assert role.liveness == "checked" and not role.live


def test_a_404_page_is_verifiably_dead(monkeypatch):
    monkeypatch.setattr(run_mod, "linkedin_state", lambda url: (None, "stub"))
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("unknown", None, "no board"))
    monkeypatch.setattr(run_mod, "fetch_jd_plain", lambda url: (False, ""))
    role, _, _ = run_mod.resolve_for_build(
        None, {"title": "Chief of Staff", "company": "Setpoint",
               "url": "https://www.linkedin.com/jobs/view/cos-3"}, {})
    assert role.liveness == "checked" and not role.live


def test_a_thin_page_falls_back_to_the_sheets_own_words(monkeypatch):
    monkeypatch.setattr(run_mod, "linkedin_state", lambda url: (None, "stub"))
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("unknown", None, "no board"))
    monkeypatch.setattr(run_mod, "fetch_jd_plain", lambda url: (None, "Sign in to view"))
    role, _, flags = run_mod.resolve_for_build(
        None, {"title": "Chief Revenue Officer", "company": "Denodo",
               "url": "https://www.linkedin.com/jobs/view/cro-4"}, {},
        sheet_snippet="Denodo sells data virtualization.", sheet_why="FIT: builds the engine.")
    assert "Denodo sells data virtualization." in role.jd_text
    assert any("thin JD" in f for f in flags)


def test_a_discovered_board_relinks_and_checks_directly(monkeypatch):
    monkeypatch.setattr(run_mod, "linkedin_state", lambda url: (None, "stub"))

    class Hit:
        url = "https://jobs.ashbyhq.com/socure/11111111-2222-3333-4444-555555555555"
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("found", Hit(), "ashby/socure"))
    seen = {}

    def fake_direct(row):
        seen["url"] = row["url"]
        from hunter.sources import ResolvedRole
        return ResolvedRole(company="Socure", title=row["title"], url=row["url"],
                            jd_url=row["url"], jd_text="x" * 300, live=True, source="t")
    monkeypatch.setattr(run_mod, "_resolve_for_build", fake_direct)
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Head of Solution Strategy", "company": "Socure",
               "url": "https://www.linkedin.com/jobs/view/x"}, {})
    assert relink == Hit.url and seen["url"] == Hit.url and role.live


# ---------- build_one dead branch ----------

def test_a_dead_yes_row_keeps_column_a_and_is_never_archived(monkeypatch):
    grid = [list(HEADERS), [""] * N_COLS,
            data_row("Yes", "Fractional AI", "Head of Corporate Development",
                     "https://www.linkedin.com/jobs/view/hcd-9")]
    s = FakeSheet(grid)
    patched, archived = [], []
    monkeypatch.setattr(run_mod, "db_patch",
                        lambda cfg, table, match, values: patched.append((match, values)))
    monkeypatch.setattr(s, "archive_rows", lambda *a, **k: archived.append(a))
    from hunter.sources import ResolvedRole
    monkeypatch.setattr(run_mod, "resolve_for_build",
                        lambda cfg, row, cache, **kw: (
                            ResolvedRole(company="Fractional AI", title=row["title"],
                                         url=row["url"], jd_url=row["url"], jd_text="",
                                         live=False, source="t"), None, ["not on the board"]))
    summary = []
    row = {"job_id": "fractional-ai:head-of-corporate-development",
           "company": "Fractional AI", "title": "Head of Corporate Development",
           "url": "https://www.linkedin.com/jobs/view/hcd-9", "status": "staging"}
    ok = run_mod.build_one(Cfg(), FakeCanon(), s, row, summary, cache={})
    assert ok is False
    assert s.grid[2][C["Verdict"]] == "Yes"
    assert s.grid[2][C["Package Status"]] == sheet_mod.PKG_DEAD
    assert not archived
    assert patched[0][1]["status"] == "dead"
    assert summary and summary[0].startswith("DEAD fractional-ai")


# ---------- select_for_build ----------

def test_select_for_build_uncapped_takes_every_yes_and_skips_dead_and_built(monkeypatch):
    from hunter import router
    grid = [list(HEADERS), [""] * N_COLS,
            data_row("Yes", "Centari", "Founding GTM Lead", "https://a.example/1", score="10"),
            data_row("Yes", "Legora", "Director of Corporate Development",
                     "https://a.example/2", score="9",
                     **{"CV Doc": hyperlink("https://d/cv", "CV"),
                        "Cover Letter Doc": hyperlink("https://d/cl", "CL")}),
            data_row("Yes", "Dead Co", "Chief of Staff", "https://a.example/3", score="8",
                     **{"Package Status": sheet_mod.PKG_DEAD}),
            data_row("Declined - stage wrong", "Nope", "Head of GTM", "https://a.example/4"),
            data_row("Yes", "Fleek", "Chief of Staff", "https://a.example/5", score="7")]
    s = FakeSheet(grid)
    known = [{"job_id": "centari:founding-gtm-lead", "company": "Centari",
              "title": "Founding GTM Lead", "url": "https://a.example/1", "score": 10,
              "package_status": "none"},
             {"job_id": "legora:director-of-corporate-development", "company": "Legora",
              "title": "Director of Corporate Development", "url": "https://a.example/2",
              "score": 9, "package_status": "built"},
             {"job_id": "dead-co:chief-of-staff", "company": "Dead Co",
              "title": "Chief of Staff", "url": "https://a.example/3", "score": 8,
              "package_status": "blocked"},
             {"job_id": "fleek:chief-of-staff", "company": "Fleek",
              "title": "Chief of Staff", "url": "https://a.example/5", "score": 7,
              "package_status": "none"}]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    picked = router.select_for_build(Cfg({"hunter_max_packages_per_run": "1"}), s, HEADERS, cap=0)
    assert [d["job_id"] for d in picked] == ["centari:founding-gtm-lead", "fleek:chief-of-staff"]
    capped = router.select_for_build(Cfg({"hunter_max_packages_per_run": "1"}), s, HEADERS)
    assert [d["job_id"] for d in capped] == ["centari:founding-gtm-lead"]
    retry = router.select_for_build(Cfg(), s, HEADERS, cap=0, retry_dead=True)
    assert "dead-co:chief-of-staff" in [d["job_id"] for d in retry]


# ---------- G12 at staging never fetches ----------

def test_a_declined_company_is_recorded_blocked_and_never_fetched(monkeypatch):
    from hunter.sources import RolePosting
    inserted = []
    monkeypatch.setattr(run_mod, "seen_identity_keys", lambda cfg: set())
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [])
    monkeypatch.setattr(run_mod, "db_insert",
                        lambda cfg, table, rows, **kw: inserted.extend(rows))
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)

    def boom(*a, **k):
        raise AssertionError("a declined company's posting was fetched")
    import hunter.ats.ashby as ashby
    monkeypatch.setattr(ashby, "fetch_posting", boom)
    p = RolePosting(company="flex", title="Chief of Staff", url="https://jobs.ashbyhq.com/flex/1",
                    source="ats", ats="ashby", ats_slug="flex", ats_posting_id="1")
    declines = {"flex": {"company": "flex", "code": "business_uninteresting",
                         "date": "2026-09-07", "job_id": "flex:x", "quote": ""}}
    summary = []
    counts = run_mod.stage_postings(Cfg({"hunter_never_apply": "[]"}), FakeCanon(),
                                    FakeSheet([list(HEADERS), [""] * N_COLS]), [p], summary,
                                    company_declines=declines)
    assert counts["staged"] == 0 and len(counts["g12_blocked"]) == 1
    assert inserted[0]["status"] == "blocked"
    assert inserted[0]["rejection_reason"] == \
        "G12: company declined by Krish on 2026-09-07 (business_uninteresting)"


# ---------- warm path cells ----------

def test_warm_path_cells_link_the_person_or_say_none_found(monkeypatch):
    import hunter.people.bridges as bridges_mod

    def fake_get(cfg, table, params):
        if table == "bridge_candidates":
            return [{"job_id": "elevenlabs:gm-uk", "contact_key": "ashley-kramer",
                     "path_tier": "current_employee", "bridge_score": 75,
                     "path_evidence": "Ashley Kramer is CRO at ElevenLabs now; strength 70",
                     "draft_ask": "15 minutes before I apply?", "state": "proposed"},
                    {"job_id": "elevenlabs:gm-uk", "contact_key": "peer:unidentified",
                     "path_tier": "peer_transition", "bridge_score": 10,
                     "path_evidence": "x", "draft_ask": "y", "state": "proposed"},
                    {"job_id": "mutiny:head-of-gtm", "contact_key": "peer:unidentified",
                     "path_tier": "peer_transition", "bridge_score": 10,
                     "path_evidence": "x", "draft_ask": "y", "state": "proposed"}]
        if table == "network_contacts":
            return [{"contact_key": "ashley-kramer", "full_name": "Ashley Kramer",
                     "current_title": "CRO", "current_company": "ElevenLabs",
                     "linkedin_url": "https://www.linkedin.com/in/ashley-kramer"}]
        return []
    monkeypatch.setattr(bridges_mod, "db_get", fake_get)
    cells = bridges_mod.warm_path_cells(None, ["elevenlabs:gm-uk", "mutiny:head-of-gtm"])
    warm, evidence = cells["elevenlabs:gm-uk"]
    assert parse_hyperlink(warm) == ("https://www.linkedin.com/in/ashley-kramer",
                                     "Ashley Kramer, CRO at ElevenLabs")
    assert evidence.startswith("current_employee: Ashley Kramer is CRO")
    assert cells["mutiny:head-of-gtm"] == (sheet_mod.WARM_NONE, sheet_mod.EVIDENCE_NONE)


def test_cold_targets_skip_roles_that_already_have_a_person(monkeypatch):
    import hunter.people.bridges as bridges_mod
    monkeypatch.setattr(bridges_mod, "db_get", lambda cfg, table, params: [
        {"job_id": "a:b", "contact_key": "someone", "path_tier": "current_employee"}])
    stats = bridges_mod.cold_targets(Cfg(), [{"job_id": "a:b", "company": "A", "title": "B"}])
    assert stats == {"eligible": 0, "searched": 0, "found": 0, "skipped": []}


# ---------- the drain claims one command when dispatched with an id ----------

def test_drain_with_an_id_claims_only_that_row(monkeypatch):
    seen = {"params": [], "patch": []}
    monkeypatch.setattr(run_mod, "load", lambda: Cfg())
    monkeypatch.setattr(run_mod, "newsletter_step", lambda cfg, apply: {"new": 0})

    def fake_get(cfg, table, params):
        seen["params"].append(params)
        return [] if params.get("id") == "eq.42" else [{"id": 7, "command": "source"}]
    monkeypatch.setattr(run_mod, "db_get", fake_get)
    monkeypatch.setattr(run_mod, "db_patch",
                        lambda cfg, table, match, values: seen["patch"].append((match, values)))
    assert run_mod.cmd_drain("42") == 0
    assert seen["params"][-1]["id"] == "eq.42" and not seen["patch"]


def test_learning_report_names_declines_allow_list_and_g12_hits():
    out = {"company_declines": {"flex": {"company": "flex", "code": "business_uninteresting",
                                         "date": "2026-09-07"}},
           "allow": ["oscar"], "unexplained": [
               {"company": "Common Room", "title": "Head of Business Operations",
                "reason_text": "Declined - seniority below"}]}
    lines = run_mod.learning_report_lines(out, g12_hits=["flex:x: G12"])
    assert lines[0] == "LEARNING REPORT"
    assert "flex: business_uninteresting since 2026-09-07" in lines[1]
    assert "oscar" in lines[2]
    assert "1 posting(s)" in lines[3]
    assert any("Common Room" in line for line in lines)


def test_a_yes_row_builds_over_a_geography_or_band_gate(monkeypatch):
    """Seven Yes rows were refused on 2026-09-07 for a location or a band
    Krish had already read on the sheet. His Yes outranks the gates that
    decide what he is shown; dead, never-apply and the package gates still
    stop a build."""
    from hunter.gates import GateReport, GateResult
    grid = [list(HEADERS), [""] * N_COLS,
            data_row("Yes", "Denodo", "Chief Revenue Officer", "https://a.example/1")]
    s = FakeSheet(grid)
    from hunter.sources import ResolvedRole
    monkeypatch.setattr(run_mod, "resolve_for_build",
                        lambda cfg, row, cache, **kw: (
                            ResolvedRole(company="Denodo", title=row["title"], url=row["url"],
                                         jd_url=row["url"], jd_text="x" * 300, live=True,
                                         source="t", location="Palo Alto, CA"), None, []))
    calls = {}
    monkeypatch.setattr(run_mod, "run_gates", lambda role, **kw: GateReport([
        GateResult("G1", True, "live"),
        GateResult("G6", False, "location outside canon 9.4 geography: 'Palo Alto, CA'")]))
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)

    def stop_here(cfg):
        calls["reached_build"] = True
        raise RuntimeError("stop before the Docs API")
    import hunter.package.tailor as tailor_mod
    monkeypatch.setattr(tailor_mod, "load_blocks", stop_here)
    summary = []
    with pytest.raises(RuntimeError):
        run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s,
                          {"job_id": "denodo:cro", "company": "Denodo",
                           "title": "Chief Revenue Officer", "url": "https://a.example/1"},
                          summary, cache={})
    assert calls.get("reached_build"), "the build was refused on a soft gate"
    assert not any(line.startswith("BLOCKED") for line in summary)

    monkeypatch.setattr(run_mod, "run_gates", lambda role, **kw: GateReport([
        GateResult("G0", False, "company is on the hunter_never_apply list: Meta")]))
    summary = []
    ok = run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s,
                           {"job_id": "denodo:cro", "company": "Denodo",
                            "title": "Chief Revenue Officer", "url": "https://a.example/1"},
                           summary, cache={})
    assert ok is False and summary[0].startswith("BLOCKED denodo:cro: G0")


def test_twin_db_rows_on_one_ats_posting_still_build(monkeypatch):
    """The incumbent recorded ElevenLabs GM UK under two job_ids with one
    URL; the matcher called it ambiguous and nothing was ever built."""
    from hunter import router
    url = "https://jobs.ashbyhq.com/elevenlabs/ca1269c6-12c7-419a-ab4d-458a0a907a17"
    grid = [list(HEADERS), [""] * N_COLS,
            data_row("Yes", "ElevenLabs", "General Manager - UK", url, score="9")]
    s = FakeSheet(grid)
    known = [{"job_id": "elevenlabs:gm-uk", "company": "ElevenLabs",
              "title": "General Manager - UK", "url": url, "score": 9,
              "package_status": "none", "krish_verdict": "go", "presented_at": "x"},
             {"job_id": "elevenlabs:general-manager-uk-005f76", "company": "ElevenLabs",
              "title": "General Manager - UK", "url": url, "score": 9,
              "package_status": "none", "krish_verdict": None, "presented_at": None}]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    picked = router.select_for_build(Cfg(), s, HEADERS, cap=0)
    assert [d["job_id"] for d in picked] == ["elevenlabs:gm-uk"]


def test_the_package_stage_check_also_lets_a_yes_outrank_soft_gates():
    """The second run_gates call, after the documents exist, re-ran G2, G6 and
    G7 and blocked the same seven roles the entry check had just let through."""
    import inspect
    src = inspect.getsource(run_mod.build_one)
    assert "hard_fails = [g for g in pkg_report.failures() if g.gate not in BUILD_SOFT_GATES]" in src
    assert {"G0", "G1", "G8", "G9", "G10"}.isdisjoint(run_mod.BUILD_SOFT_GATES)


def test_a_linkedin_posting_with_its_description_is_gated_and_staged(monkeypatch):
    """The sweep returns descriptionText on every item. 1,190 postings were
    recorded unresolved on 2026-09-07 because nothing read it."""
    from hunter.sources import RolePosting
    inserted, appended = [], []
    monkeypatch.setattr(run_mod, "seen_identity_keys", lambda cfg: set())
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [])
    monkeypatch.setattr(run_mod, "db_insert", lambda cfg, table, rows, **kw: inserted.extend(rows))
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(disc, "discover", lambda cfg, company, cache: None)
    import hunter.package.rationale as rationale_mod
    monkeypatch.setattr(rationale_mod, "write_rationale_and_snippet",
                        lambda *a, **k: ("Own the GTM operating model. FIT: builds engines. RISK: none.",
                                         "Viam builds robotics software. The role runs the CEO office.", []))
    jd = ("Viam is hiring a Chief of Staff in New York. You will build the operating "
          "model with the CEO, architect the planning cadence and own P&L reviews. "
          "AI native company. ") * 6
    p = RolePosting(company="Viam", title="Chief of Staff",
                    url="https://www.linkedin.com/jobs/view/chief-of-staff-at-viam-1",
                    source="apify_linkedin", location="New York, NY",
                    raw={"descriptionText": jd})
    s = FakeSheet([list(HEADERS), [""] * N_COLS])
    counts = run_mod.stage_postings(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, [p], [])
    assert counts["from_description"] == 1 and counts["unresolved"] == 0
    assert counts["staged"] == 1
    assert inserted[0]["status"] == "staging"
    row = s.grid[2]
    assert row[C["Business"]] == "Viam" and row[C["JD URL Verified"]] == "FALSE"
    assert row[C["JD Snippet"]].startswith("Viam builds robotics")


def test_the_sweep_reads_the_salary_field_the_actor_actually_returns(monkeypatch):
    import hunter.sources.apify_linkedin as al
    monkeypatch.setattr(al, "run_actor", lambda *a, **k: [
        {"companyName": "Viam", "title": "Chief of Staff", "link": "https://l/1",
         "salary": "$250,000 - $300,000", "descriptionText": "x"}])
    out = al.sweep_linkedin(None, ["u"], spend=al.SpendTracker(cap_usd=1), max_charge_usd=1)
    assert out[0].comp_text == "$250,000 - $300,000"


def test_a_closed_linkedin_posting_is_dead_not_unverified(monkeypatch):
    """Denodo and Fractional AI were both gone on 2026-09-13 and both read as
    unverified, so packages were built for them. A LinkedIn page that says the
    posting is closed is now verifiably dead."""
    monkeypatch.setattr(run_mod, "linkedin_state",
                        lambda url: (False, "LinkedIn says no longer accepting "
                                            "applications"))
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("unknown", None, "no board"))
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Chief Revenue Officer", "company": "Denodo",
               "url": "https://www.linkedin.com/jobs/view/cro-9"}, {})
    assert role.liveness == "checked" and not role.live
    assert any("no longer accepting" in f for f in flags)


# ---------- applied state ----------

def test_applied_state_reads_column_a_not_the_application_status_cell(monkeypatch):
    """The first version read the sheet's "Application Status" column, which says
    "Not applied" on all 29 rows of his Applied tab: it was backfilled with the canon
    9.13 default and never updated. What he maintains is column A.
    """
    from hunter import run as run_mod
    from hunter import sheet as sheet_mod

    def srow(verdict, company, role):
        cells = [""] * len(sheet_mod.HEADERS)
        cells[sheet_mod.COLS["Verdict"]] = verdict
        cells[sheet_mod.COLS["Business"]] = company
        cells[sheet_mod.COLS["Role"]] = role
        cells[sheet_mod.COLS["Application Status"]] = "Not applied"
        cells[sheet_mod.COLS["Applied Date"]] = "n/a"
        return sheet_mod.SheetRow(row_number=3, cells=cells, verdict=verdict,
                                  company=company, role=role, jd_url=None)

    pipeline = [srow("Yes", "Harvey", "Head of GTM")]
    archive = [srow("Applied", "Anthropic", "Head of Enterprise Sales"),
               srow("Declined, not interested", "Salesforce", "RVP Analytics")]
    db_rows = [{"job_id": "harvey:x", "company": "Harvey", "title": "Head of GTM",
                "url": None, "job_url": None, "status": "staging",
                "application_state": None, "applied_at": None},
               {"job_id": "anthropic:y", "company": "Anthropic",
                "title": "Head of Enterprise Sales", "url": None, "job_url": None,
                "status": "staging", "application_state": None, "applied_at": None},
               {"job_id": "salesforce:z", "company": "Salesforce",
                "title": "RVP Analytics", "url": None, "job_url": None,
                "status": "staging", "application_state": None, "applied_at": None}]
    patches = []

    class FakeSheet:
        def read_pipeline(self, headers): return pipeline
        def read_archive(self): return archive

    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params:
                        db_rows if table == "hunter_seen_roles" else [])
    monkeypatch.setattr(run_mod, "db_patch", lambda cfg, table, match, values:
                        patches.append((match["job_id"], values)))

    class FakeCanon:
        sheet_headers = list(sheet_mod.HEADERS)

    out = run_mod.sync_applied_state(None, FakeSheet(), FakeCanon())
    assert out["from_sheet"] == 1 and out["written"] == 1
    assert [j for j, _ in patches] == ["anthropic:y"]
    assert patches[0][1]["application_state"] == "Applied"


# ---------- clearing the backlog, 2026-09-20 ----------

def test_the_clearing_label_teaches_the_learning_loop_nothing():
    """Clearing 78 rows must not teach hunter that Krish dislikes 78
    companies. Three independent guarantees, all asserted here."""
    from hunter import learn, verdicts
    from hunter.run import CLEAR_LABEL

    # 1. the label carries no reason code at all
    kind, code = verdicts.parse(CLEAR_LABEL)
    assert kind == "rejection" and code is None
    # 2. nor does his own wording inference find one
    _kind, inferred, _inf = learn.classify(CLEAR_LABEL)
    assert inferred not in learn.COMPANY_CODES
    # 3. and the source marks it as hunter's own output, which the loop skips
    assert learn.is_auto({"verdict_source": learn.AUTO_SOURCE})
    events = [{"verdict": "rejection", "reason_code": inferred,
               "company": "Citi", "source": learn.AUTO_SOURCE,
               "recorded_at": "2026-09-20"}]
    assert learn.company_declines(events) == {}


def test_a_row_with_no_safe_database_row_is_left_on_the_sheet():
    """An archived row carrying a rejection in column A and no database stamp
    is read as HIS rejection by the next reconcile. Better to leave it."""
    import inspect
    from hunter import run
    src = inspect.getsource(run.cmd_clear_unverdicted)
    assert "stranded" in src and "STAY on" in src


def test_clearing_never_deletes():
    import inspect
    from hunter import run
    src = inspect.getsource(run.cmd_clear_unverdicted)
    assert "archive_rows" in src
    assert "delete_rows" not in src, "cleared rows must remain restorable"


def test_clearing_refuses_to_report_success_if_an_approval_vanished():
    import inspect
    from hunter import run
    src = inspect.getsource(run.cmd_clear_unverdicted)
    assert "REFUSING TO REPORT SUCCESS" in src
    assert "approved_before" in src and "approved_after" in src


# ---------- the staging cap, 2026-09-20 ----------

def test_staging_is_capped_so_the_sheet_stays_judgeable():
    """There was no cap, which is how the tab reached 176 rows. A sheet he
    cannot judge in one sitting is a sheet he does not judge."""
    import inspect
    from hunter import run
    src = inspect.getsource(run.stage_postings)
    assert "hunter_max_staged_per_run" in src
    assert "staged_rows[:cap]" in src


def test_the_roles_below_the_cap_are_held_not_discarded():
    import inspect
    from hunter import run
    src = inspect.getsource(run.stage_postings)
    assert "keep their" in src and "database row" in src
    # the cap is applied AFTER the sort, so what is held is the lowest scoring
    assert src.index("staged_rows.sort") < src.index("staged_rows[:cap]")


# ---------- a long run is visible while it runs ----------

def test_the_summary_says_each_line_as_it_happens(capsys):
    """Every phase appended to a plain list and nothing printed until the run
    ended. A sourcing run takes the better part of an hour, and when one died
    at the last step its whole summary died with it."""
    from hunter.run import Summary
    s = Summary()
    s.append("a16z boards: probed 120")
    s.extend(["learned boards swept in full: 265", "sourced: 40 staged"])
    out = capsys.readouterr().out
    assert "a16z boards: probed 120" in out
    assert "learned boards swept in full: 265" in out
    assert "sourced: 40 staged" in out
    assert list(s) == ["a16z boards: probed 120",
                       "learned boards swept in full: 265",
                       "sourced: 40 staged"], "it is still the summary list"


def test_the_summary_is_a_list_so_every_caller_still_works():
    from hunter.run import Summary
    s = Summary(["first"])
    s.append("second")
    assert isinstance(s, list)
    assert "\n".join(s) == "first\nsecond"
    assert len(s) == 2 and s[0] == "first"


# ---------- G14: the company question, asked before the seat ----------

def _stage_with_company(monkeypatch, score, *, status="", merit=None,
                        title="Chief of Staff"):
    """Run one posting through stage_postings with a fixed company score."""
    from hunter.company import CompanyScore, Component
    from hunter.sources import RolePosting

    inserted = []
    monkeypatch.setattr(run_mod, "seen_identity_keys", lambda cfg: set())
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [])
    monkeypatch.setattr(run_mod, "db_insert",
                        lambda cfg, table, rows, **kw: inserted.extend(rows))
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    jd = ("Acme is the enterprise AI platform for support teams. We are "
          "hiring a commercial leader to build the go to market motion from "
          "scratch across Europe, owning the number end to end. " * 8)
    monkeypatch.setattr(run_mod, "fetch_with_retry",
                        lambda fetch, slug, pid: (True, jd,
                                                  "https://jobs.ashbyhq.com/acme/1"))
    cs = CompanyScore(slug="acme", name="Acme", total=score, status=status,
                      components=[Component("category", 4, True,
                                            "in ai native enterprise", "https://x")])
    monkeypatch.setattr(run_mod, "company_scores",
                        lambda cfg, names, sheet=None, summary=None: {"acme": cs})
    monkeypatch.setattr(run_mod, "known_company_keys", lambda cfg: set())
    p = RolePosting(company="Acme", title=title, location="London, United Kingdom",
                    comp_text="$250,000 - $320,000",
                    url="https://jobs.ashbyhq.com/acme/1", source="ats",
                    ats="ashby", ats_slug="acme", ats_posting_id="1")
    summary = []
    counts = run_mod.stage_postings(
        Cfg({"hunter_never_apply": "[]"}), FakeCanon(),
        FakeSheet([list(HEADERS), [""] * N_COLS]), [p], summary)
    return counts, inserted, summary


def test_a_role_at_a_company_below_the_floor_never_reaches_his_sheet(monkeypatch):
    """28 of the last batch's 33 declines carried a company level reason
    code. He was not rejecting the seat."""
    counts, inserted, _ = _stage_with_company(monkeypatch, 2.0)
    assert counts["staged"] == 0
    assert len(counts["g14_blocked"]) == 1
    assert inserted[0]["status"] == "blocked"
    assert "G14" in inserted[0]["rejection_reason"]
    assert "2.0 of 10" in inserted[0]["rejection_reason"]


def test_a_company_hunter_could_not_read_is_ranked_not_refused(monkeypatch):
    """CLAUDE.md, already: never block on no evidence. A company whose site
    refuses hunter is hunter's problem, not the company's, and refusing the
    role on it would be scoring an absence."""
    from hunter.company import NEEDS_EVIDENCE
    counts, inserted, _ = _stage_with_company(monkeypatch, 0.0,
                                              status=NEEDS_EVIDENCE)
    assert not counts["g14_blocked"]
    assert inserted[0]["status"] == "staging"


def test_a_company_above_the_floor_passes(monkeypatch):
    counts, inserted, _ = _stage_with_company(monkeypatch, 8.0)
    assert not counts["g14_blocked"] and inserted[0]["status"] == "staging"


def test_the_company_floor_opens_for_an_exceptional_role(monkeypatch):
    """His ruling on 2026-09-20: "citi is an example where I'd reject that
    company unless the role was ideal, which that one was". No company gate
    in this repo is absolute, and G14 is not the exception."""
    from hunter.gates import EXCEPTIONAL_MERIT
    import hunter.score as score_mod

    real = score_mod.score_role

    def exceptional(role, **kw):
        r = real(role, **kw)
        object.__setattr__(r, "merit", EXCEPTIONAL_MERIT) if hasattr(r, "__dataclass_fields__") else None
        try:
            r.merit = EXCEPTIONAL_MERIT
        except Exception:
            pass
        return r

    monkeypatch.setattr(run_mod, "score_role", exceptional)
    counts, inserted, _ = _stage_with_company(monkeypatch, 2.0)
    assert not counts["g14_blocked"], (
        "an exceptional role must survive a company he would usually skip")
    assert inserted[0]["status"] == "staging"
