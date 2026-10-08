"""The door in, phase 1: a named leader and a warm route per top company.

Every name here is invented. The repo is public and the real ones live only on
the private page (docs/DOOR_IN.md, "no named contacts").
"""
import datetime

from hunter import doorin


def _c(name, company, title, tier="", **kw):
    row = {"contact_key": name.lower().replace(" ", "-"), "full_name": name,
           "current_company": company, "current_title": title,
           "network_tier": tier, "strength_score": 0}
    row.update(kw)
    return row


TOP = [{"key": "acmeai", "name": "Acme AI", "top": True, "sources": ["your list"]},
       {"key": "notion", "name": "Notion", "top": True, "sources": ["your list"]}]


def _one(contacts, roles=(), posts=(), radar=None):
    rows = doorin.coverage(radar or TOP[:1], contacts, list(roles), list(posts))
    return rows[0]


def test_leader_he_knows_is_a_door_on_their_own():
    cov = _one([_c("Ada Founder", "Acme AI", "Co-Founder & CEO", "2_core_network")])
    assert cov.door
    assert [r.kind for r in cov.routes] == ["leader_direct"]


def test_leader_he_does_not_know_plus_an_insider_he_does_is_a_door():
    cov = _one([_c("Ada Founder", "Acme AI", "CEO", "5_cold_lead"),
                _c("Ben Insider", "Acme AI", "Account Executive", "4_owned_network")])
    assert cov.has_leader and cov.door
    assert [r.kind for r in cov.routes] == ["inside"]


def test_a_named_leader_with_no_route_is_kept_and_not_a_door():
    """Never block on no evidence, and never route to a stranger: the company
    stays on the list with its leader named and no route."""
    cov = _one([_c("Ada Founder", "Acme AI", "CEO", "5_cold_lead")])
    assert cov.has_leader and not cov.has_route and not cov.door
    assert cov.routes == []


def test_a_company_with_nobody_is_still_listed():
    rows = doorin.coverage(TOP, [], [], [])
    assert {r.name for r in rows} == {"Acme AI", "Notion"}
    assert not any(r.has_leader or r.has_route for r in rows)


def test_a_title_that_does_not_decide_is_not_a_leader():
    cov = _one([_c("Cal Director", "Acme AI", "Programmatic Director", "1_reciprocated")])
    assert not cov.has_leader
    assert [r.kind for r in cov.routes] == ["inside"]


def test_shared_word_in_a_company_name_is_not_the_same_employer():
    """The bridges.py defect, held here too: "Accel-Digital Ad Operations" is
    not an insider at a company whose name carries "Operations"."""
    radar = [{"key": "notion", "name": "Notion Head of GTM Operations", "top": True}]
    cov = _one([_c("Harp Stranger", "Accel-Digital Ad Operations", "CEO", "1_reciprocated")],
               radar=radar)
    assert not cov.has_leader and not cov.has_route


def test_someone_who_worked_there_is_an_alumni_route():
    alum = _c("Dee Alum", "Elsewhere", "VP Sales", "3_known_network",
              employment_history=[{"companyName": "Acme AI"}])
    cov = _one([alum])
    assert [r.kind for r in cov.routes] == ["alumni"]
    assert not cov.door  # a route with no named leader is not yet a door


def test_a_newsletter_move_is_a_trigger_not_a_route():
    today = datetime.date.today().isoformat()
    post = {"link": "https://example.com/post", "published_at": today,
            "signals": {"talent_moves": [{"person": "Eve New", "company": "Acme AI",
                                          "title": "CRO"}],
                        "hiring": [{"company": "Acme AI", "roles": ["Head of GTM"],
                                    "founder": "Ada Founder"}]}}
    cov = _one([], posts=[post])
    assert not cov.has_route
    assert {t.kind for t in cov.triggers} == {"newsletter_move", "newsletter_hiring"}
    assert all(t.url == "https://example.com/post" for t in cov.triggers)
    assert cov.founder_hint == "Ada Founder"


def test_an_old_newsletter_post_is_no_trigger():
    old = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    post = {"link": "x", "published_at": old,
            "signals": {"talent_moves": [{"person": "Eve", "company": "Acme AI"}]}}
    assert _one([], posts=[post]).triggers == []


def test_open_commercial_seat_is_a_trigger_and_a_dead_one_is_not():
    roles = [{"company": "Acme AI", "title": "Head of Revenue", "status": "presented",
              "url": "https://jobs.example/1"},
             {"company": "Acme AI", "title": "VP Sales", "status": "dead", "url": "u"},
             {"company": "Acme AI", "title": "Staff Engineer", "status": "presented"}]
    cov = _one([], roles=roles)
    assert [(t.kind, t.url) for t in cov.triggers] == [("open_seat", "https://jobs.example/1")]


def test_an_application_marks_the_company_either_way_it_was_recorded():
    by_state = _one([], roles=[{"company": "Acme AI", "title": "x", "application_state": "submitted"}])
    by_verdict = _one([], roles=[{"company": "Acme AI", "title": "x", "krish_verdict": "Applied"}])
    neither = _one([], roles=[{"company": "Acme AI", "title": "x", "krish_verdict": "yes"}])
    assert by_state.applied and by_verdict.applied and not neither.applied


def test_only_top_radar_rows_are_read():
    radar = TOP + [{"key": "citi", "name": "Citi", "top": False}]
    assert "Citi" not in {r.name for r in doorin.coverage(radar, [], [], [])}


def test_doors_sort_first_and_summary_counts_them():
    contacts = [_c("Ada", "Acme AI", "CEO", "2_core_network")]
    rows = doorin.coverage(TOP, contacts, [], [])
    assert rows[0].name == "Acme AI"
    s = doorin.summary(rows)
    assert s == {"companies": 2, "with_leader": 1, "with_route": 1, "door": 1,
                 "leader_direct": 1, "with_trigger": 0, "applied": 0}


def test_wedge_record_counts_only_since_the_wedge_and_says_what_it_cannot_see():
    rows = [{"path_tier": "mindmake_wedge", "state": "proposed", "surfaced_at": "2026-10-04"},
            {"path_tier": "mindmake_wedge", "state": "reached_out", "outcome": "reply",
             "surfaced_at": "2026-09-20"},
            {"path_tier": "mindmake_wedge", "state": "proposed", "surfaced_at": "2026-09-01"},
            {"path_tier": "current_employee", "state": "proposed", "surfaced_at": "2026-10-04"}]
    rec = doorin.wedge_record(rows)
    assert rec["visible"] == 2 and rec["sent"] == 1
    assert rec["outcomes"] == {"reply": 1}
    assert "deleted" in rec["caveat"]


def test_report_runs_from_a_dump(tmp_path):
    import json
    (tmp_path / "radar.json").write_text(json.dumps(TOP))
    (tmp_path / "contacts.json").write_text(json.dumps(
        [_c("Ada", "Acme AI", "CEO", "1_reciprocated", contact_id="u-1")]))
    rep = doorin.report(doorin.load_dump(str(tmp_path)))
    assert rep["summary"]["door"] == 1
    assert rep["companies"][0]["leaders"][0]["contact_id"] == "u-1"


def test_a_one_word_company_does_not_match_a_longer_employer_that_contains_it():
    """The live graph, 2026-10-08: recruiters and retailers whose names begin
    with one of his one-word companies were counted as insiders there."""
    for theirs in ("Harvey Norman (GP Advertising)", "Sphere Digital Recruitment",
                   "ARTISAN - Creative and Digital Recruitment", "Nexus Adex"):
        ours = theirs.split()[0].title()
        assert not doorin.same_company(ours, theirs), theirs


def test_the_same_word_with_a_suffix_or_a_team_still_matches():
    assert doorin.same_company("Reddit", "Reddit, Inc.")
    assert doorin.same_company("Krea", "Krea.Ai")
    assert doorin.same_company("Fireworks AI", "Fireworks Ai")
    assert doorin.same_company("OpenAI", "OpenAI (Codex)")
    assert doorin.same_company("Nexus (Yc F25)", "Nexus")


def test_a_longer_name_keeps_the_containment_rule():
    assert doorin.same_company("Thinking Machines Lab", "Thinking Machines")
    assert not doorin.same_company("Notion Head of GTM Operations",
                                   "Accel-Digital Ad Operations")


def test_a_recruiter_named_like_a_top_company_is_not_a_route_there():
    radar = [{"key": "sphere", "name": "Sphere", "top": True}]
    cov = _one([_c("Rae Recruiter", "Sphere Digital Recruitment", "VP, North America",
                   "4_owned_network")], radar=radar)
    assert not cov.has_route and not cov.has_leader
