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
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("absent", None, "not on the ashby/x board (12 jobs)"))
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Chief of Staff", "company": "January",
               "url": "https://www.linkedin.com/jobs/view/cos-2"}, {})
    assert role.liveness == "checked" and not role.live


def test_a_404_page_is_verifiably_dead(monkeypatch):
    monkeypatch.setattr(run_mod, "discover_posting",
                        lambda cfg, company, title, cache: ("unknown", None, "no board"))
    monkeypatch.setattr(run_mod, "fetch_jd_plain", lambda url: (False, ""))
    role, _, _ = run_mod.resolve_for_build(
        None, {"title": "Chief of Staff", "company": "Setpoint",
               "url": "https://www.linkedin.com/jobs/view/cos-3"}, {})
    assert role.liveness == "checked" and not role.live


def test_a_thin_page_falls_back_to_the_sheets_own_words(monkeypatch):
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
