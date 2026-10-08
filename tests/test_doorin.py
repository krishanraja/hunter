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


def test_a_title_naming_another_employer_beats_a_stale_company_field():
    stale = [_c("Ann Moved", "Mercor", "CEO @ Sepal AI", "3_known_network"),
             _c("Bo Moved", "OpenAI", "Startups SA @ AWS • AI/ML", "3_known_network"),
             _c("Cy Moved", "Anthropic", "Advisory Solutions Architect at MongoDB",
                "3_known_network")]
    radar = [{"key": k, "name": k, "top": True} for k in ("Mercor", "OpenAI", "Anthropic")]
    rows = doorin.coverage(radar, stale, [], [])
    assert not any(r.has_leader or r.has_route for r in rows)


def test_a_title_naming_the_same_employer_changes_nothing():
    cov = _one([_c("Di Here", "Acme AI", "GTM @ Acme AI - Strategic Industries",
                   "4_owned_network")])
    assert [r.kind for r in cov.routes] == ["inside"]
    assert doorin.employer_of({"current_company": "Acme AI",
                               "current_title": "Head of Sales"}) == "Acme AI"
    assert doorin.employer_of({"current_company": "Crescendo",
                               "current_title": "GTM operator @ Crescendo & angel investor"}) == "Crescendo"


def test_a_vice_president_is_not_the_leader():
    cov = _one([_c("Vi Pres", "Acme AI", "Vice President, Business Development", "3_known_network"),
                _c("Ev Pee", "Acme AI", "Vice-President & Country Manager", "3_known_network")])
    assert not cov.has_leader
    assert {r.kind for r in cov.routes} == {"inside"}
    assert doorin.is_leader({"current_title": "President & COO"})


def test_a_company_written_as_one_word_still_matches():
    radar = [{"key": "higgsfieldai", "name": "Higgsfield AI", "top": True}]
    cov = _one([_c("Hal Founder", "higgsfieldai", "Founder & CEO, Higgsfield AI",
                   connected_on="2017-10-26")], radar=radar)
    assert cov.door and cov.routes[0].kind == "leader_direct"


def test_the_same_leader_in_both_graphs_is_one_leader():
    cov = _one([_c("Ada Founder", "Acme AI", "CEO", "2_core_network"),
                _c("Ada Founder", "Acme AI", "Co-founder & CEO", "4_owned_network",
                   contact_key="contact:x")])
    assert len(cov.leaders) == 1


# ---------- phase 2: cards ----------

def _door_rows(applied=False):
    contacts = [_c("Ada Founder", "Acme AI", "CEO", "2_core_network", contact_id="u-ada"),
                _c("Ben Insider", "Notion", "Account Executive", "3_known_network", contact_id="u-ben"),
                _c("Cy Boss", "Notion", "Co-founder & CEO", "5_cold_lead", contact_id="u-cy")]
    roles = [{"company": "Notion", "title": "Head of Revenue", "status": "presented",
              "url": "https://jobs.example/n1", "scanned_at": "2026-10-01"}]
    if applied:
        roles.append({"company": "Acme AI", "title": "x", "application_state": "submitted"})
    return doorin.coverage(TOP, contacts, roles, [])


def test_cards_put_a_leader_he_knows_first_and_say_buyer_or_intro():
    cards, held = doorin.make_cards(_door_rows())
    assert [(c.company, c.ask_kind) for c in cards] == [("Acme AI", "buyer"), ("Notion", "intro")]
    notion = cards[1]
    assert notion.leader.name == "Cy Boss" and notion.route.person.name == "Ben Insider"
    assert notion.trigger.url == "https://jobs.example/n1" and notion.door == "gtm"
    assert cards[0].trigger is None and cards[0].door == "brain"
    assert held == []


def test_a_live_application_holds_the_card_back():
    cards, held = doorin.make_cards(_door_rows(applied=True))
    assert [c.company for c in cards] == ["Notion"]
    assert held[0]["company"] == "Acme AI" and "one road" in held[0]["reason"]


def test_a_named_skip_holds_the_card_back():
    cards, held = doorin.make_cards(_door_rows(), skip={"acmeai"})
    assert [c.company for c in cards] == ["Notion"] and "same company" in held[0]["reason"]


def test_forbidden_words_are_whole_words_and_any_currency_sign():
    assert doorin.forbidden_in("Run a 30-day pilot") == ["pilot"]
    assert doorin.forbidden_in("Their Copilot launch") == []
    assert doorin.forbidden_in("about £5k") == ["£"]
    assert "chief of staff" in doorin.forbidden_in("a Chief of Staff in a box")


def test_deal_row_uses_values_the_pipeline_accepts():
    cards, _ = doorin.make_cards(_door_rows())
    row = doorin.deal_row(cards[1], "2026-10-08T00:00:00Z")
    assert row["sourced_by"] in ("krish", "os") and row["ask_kind"] in ("buyer", "intro", "collaborator")
    assert row["state"] == "listed" and row["notes"].startswith(doorin.NOTES_TAG)
    assert row["contact_id"] == "u-cy" and row["trigger_source_url"] == "https://jobs.example/n1"
    assert row["draft_body"] is None  # no observation drafted, so none written


class _FakeDB:
    def __init__(self, existing=()):
        self.rows = {r["contact_id"]: dict(r) for r in existing}
        self.inserts, self.patches = [], []

    def get(self, cfg, table, params):
        assert table == "pilot_deals"
        ids = params["contact_id"][4:-1].split(",")
        return [dict(self.rows[i]) for i in ids if i in self.rows]

    def insert(self, cfg, table, rows, **kw):
        assert table == "pilot_deals" and not kw
        self.inserts.extend(rows)
        for r in rows:
            self.rows[r["contact_id"]] = dict(r)

    def patch(self, cfg, table, match, values):
        self.patches.append((match, values))
        self.rows[match["contact_id"]].update(values)


def _land(monkeypatch, db, cards, apply):
    from hunter import config
    monkeypatch.setattr(config, "db_get", db.get)
    monkeypatch.setattr(config, "db_insert", db.insert)
    monkeypatch.setattr(config, "db_patch", db.patch)
    return doorin.land(object(), cards, apply=apply)


def test_land_dry_run_writes_nothing(monkeypatch):
    db = _FakeDB()
    cards, _ = doorin.make_cards(_door_rows())
    out = _land(monkeypatch, db, cards, apply=False)
    assert len(out["write"]) == 2 and db.inserts == [] and out["read_back"] is None


def test_land_writes_new_rows_and_reads_them_back(monkeypatch):
    db = _FakeDB()
    cards, _ = doorin.make_cards(_door_rows())
    out = _land(monkeypatch, db, cards, apply=True)
    assert len(db.inserts) == 2
    assert out["read_back"] == {"expected": 2, "found": 2}


def test_land_never_touches_a_row_he_owns(monkeypatch):
    db = _FakeDB([{"contact_id": "u-ada", "state": "drafted", "notes": "his own"},
                  {"contact_id": "u-cy", "state": "listed", "notes": doorin.NOTES_TAG + " old"}])
    cards, _ = doorin.make_cards(_door_rows())
    out = _land(monkeypatch, db, cards, apply=True)
    assert out["theirs"] == [{"company": "Acme AI", "state": "drafted"}]
    assert db.rows["u-ada"]["notes"] == "his own" and db.inserts == []
    assert [m["contact_id"] for m, _ in db.patches] == ["u-cy"]


def test_a_leader_outside_control_center_is_reported_not_written(monkeypatch):
    db = _FakeDB()
    rows = doorin.coverage(TOP[:1], [_c("Ada Founder", "Acme AI", "CEO", "2_core_network")], [], [])
    cards, _ = doorin.make_cards(rows)
    out = _land(monkeypatch, db, cards, apply=True)
    assert out["not_in_control_center"] == ["Acme AI"] and db.inserts == []


def test_a_draft_with_a_forbidden_word_is_never_written(monkeypatch):
    db = _FakeDB()
    cards, _ = doorin.make_cards(_door_rows())
    cards[0].observation = "Happy to run a pilot on your pricing."
    out = _land(monkeypatch, db, cards, apply=True)
    assert out["blocked_words"][0]["company"] == "Acme AI"
    assert [r["contact_id"] for r in db.inserts] == ["u-cy"]


def test_observation_passes_the_gate_or_is_left_empty_with_the_reason(monkeypatch):
    from hunter import llm
    cards, _ = doorin.make_cards(_door_rows())
    card = cards[1]
    answers = iter(["Your Head of Revenue seat says the model is being rebuilt."])
    monkeypatch.setattr(llm, "complete", lambda cfg, prompt, **kw: (next(answers), []))
    doorin.observe(None, card, "Notion is hiring a Head of Revenue to rebuild the model")
    assert card.observation.startswith("Your Head of Revenue") and card.observation_note == ""

    bad = cards[0]
    answers = iter(["We grew 340% with a pilot.", "Still a 340% pilot."])
    monkeypatch.setattr(llm, "complete", lambda cfg, prompt, **kw: (next(answers), []))
    doorin.observe(None, bad, "Acme AI builds agents")
    assert bad.observation == "" and "rejected both drafts" in bad.observation_note


def test_no_model_answer_means_no_observation_not_a_default(monkeypatch):
    from hunter import llm
    cards, _ = doorin.make_cards(_door_rows())
    monkeypatch.setattr(llm, "complete", lambda cfg, prompt, **kw: ("", ["anthropic: no key"]))
    doorin.observe(None, cards[0], "Acme AI builds agents")
    assert cards[0].observation == "" and "no model answered" in cards[0].observation_note


def test_the_leader_fixture_holds_titles_never_names():
    """krish_leaders.json is public ground truth: company and title, no person."""
    import json
    import pathlib
    data = json.loads((pathlib.Path(__file__).parent / "fixtures" / "krish_leaders.json").read_text())
    assert data["rows"], "the fixture starts with the Amperity row"
    for row in data["rows"]:
        assert {"company", "leader_title", "first_touch", "outcome"} <= set(row)
        assert not {"name", "leader_name", "full_name", "contact_id"} & set(row)


def test_refresh_only_adds_the_line_to_his_cards_and_lists_no_new_leader(monkeypatch):
    from hunter import config
    db = _FakeDB([{"contact_id": "u-cy", "state": "listed", "notes": doorin.NOTES_TAG + " old"}])
    monkeypatch.setattr(config, "db_get", db.get)
    monkeypatch.setattr(config, "db_insert", db.insert)
    monkeypatch.setattr(config, "db_patch", db.patch)
    cards, _ = doorin.make_cards(_door_rows())
    out = doorin.land(object(), cards, apply=True, refresh_only=True)
    assert db.inserts == [] and out["new_not_written"] == ["Acme AI"]
    assert [m["contact_id"] for m, _ in db.patches] == ["u-cy"]
    assert out["read_back"] == {"expected": 1, "found": 1}


def test_load_reads_the_company_words_the_opener_needs(monkeypatch):
    """The first live run selected the Radar without its descriptions, so every
    card said there was nothing to observe from. Pinned here."""
    from hunter import config
    asked = {}

    def fake_get(cfg, table, params):
        asked[table] = params.get("select", "")
        return []
    monkeypatch.setattr(config, "db_get", fake_get)
    doorin.load(object())
    for col in ("description", "why", "lookalike_why"):
        assert col in asked["hunter_company_radar"].split(",")
