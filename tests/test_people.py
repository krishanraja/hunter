"""The bridge layer offline: export parsing on synthesized CSVs, the
strength matrix, the privacy shape of evidence, bridge tiering and ranking,
and the never-send guarantee (drafts only, template text)."""
import csv
import datetime

import pytest

from hunter.people import li_slug, norm_name
from hunter.people.strength import EVIDENCE_KEYS, MAX_EVIDENCE_STR, compute_strength

NOW = datetime.date(2026, 8, 31)


# ---------- identity normalization ----------

def test_li_slug_forms():
    assert li_slug("https://www.linkedin.com/in/krish-raja") == "krish-raja"
    assert li_slug("www.linkedin.com/in/sajeev-narayanan") == "sajeev-narayanan"
    assert li_slug("https://www.linkedin.com/in/%C3%A5selinn-krane-heimdal-76675123") \
        == "åselinn-krane-heimdal-76675123"
    assert li_slug("https://www.linkedin.com/company/acme") is None
    assert li_slug("") is None and li_slug(None) is None


# ---------- strength matrix ----------

def test_two_way_recent_messaging_dominates():
    warm, _ = compute_strength({"msgs_in": 4, "msgs_out": 6,
                                "last_message_at": "2026-08-06"}, now=NOW)
    cold, _ = compute_strength({"msgs_out": 2,
                                "last_message_at": "2021-01-01"}, now=NOW)
    assert warm >= 55 and cold <= 15 and warm > cold


def test_priors_add_without_export_signals():
    score, ev = compute_strength({"warmth_prior": 80, "email_inbound": 12,
                                  "email_outbound": 9, "email_last": "2026-05-01"},
                                 now=NOW)
    assert score == 8 + 10 + 5
    assert ev["warmth_prior"] == 80


def test_score_clamps_at_100():
    score, _ = compute_strength({
        "msgs_in": 50, "msgs_out": 50, "last_message_at": "2026-08-01",
        "invite_out_personal": True, "invite_in": True,
        "endorsements_received": 3, "endorsements_given": 2,
        "recommendation_received": True, "warmth_prior": 100,
        "email_inbound": 5, "email_outbound": 5, "email_last": "2026-08-01"},
        now=NOW)
    assert score == 100


def test_evidence_shape_is_aggregates_only():
    """The privacy contract: no message bodies, no prose, keys pinned."""
    _, ev = compute_strength({"msgs_in": 2, "msgs_out": 1,
                              "last_message_at": "2026-01-15",
                              "last_message_direction": "out",
                              "ci_tier": "inner"}, now=NOW)
    assert set(ev) <= EVIDENCE_KEYS
    for v in ev.values():
        assert isinstance(v, (int, bool, str, type(None)))
        if isinstance(v, str):
            assert len(v) <= MAX_EVIDENCE_STR


# ---------- export parsing on synthesized files ----------

@pytest.fixture
def export_dir(tmp_path):
    d = tmp_path / "LinkedIn Connections - Test"
    d.mkdir()

    def write(name, headers, rows):
        with open(d / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)

    write("Connections.csv",
          ["First Name", "Last Name", "URL", "Email Address", "Company",
           "Position", "Connected On", ""],
          [["Ada", "Nguyen", "https://www.linkedin.com/in/ada-nguyen", "",
            "Cresta", "VP Partnerships", "12-May-17", "0"],
           ["Ben", "Okafor", "https://www.linkedin.com/in/ben-okafor", "",
            "ElevenLabs", "GM UK", "5-Jun-24", "0"],
           ["", "", "", "", "NoUrl Co", "CEO", "1-Jan-20", "0"]])
    write("messages.csv",
          ["CONVERSATION ID", "CONVERSATION TITLE", "FROM", "SENDER PROFILE URL",
           "TO", "RECIPIENT PROFILE URLS", "DATE", "SUBJECT", "CONTENT",
           "FOLDER", "ATTACHMENTS", "IS MESSAGE DRAFT", "IS CONVERSATION DRAFT"],
          [["c1", "", "Krish Raja", "https://www.linkedin.com/in/krish-raja",
            "Ada Nguyen", "https://www.linkedin.com/in/ada-nguyen",
            "2026-08-06 13:43:57 UTC", "", "SECRET BODY ONE", "INBOX", "", "No", "No"],
           ["c1", "", "Ada Nguyen", "https://www.linkedin.com/in/ada-nguyen",
            "Krish Raja", "https://www.linkedin.com/in/krish-raja",
            "2026-08-07 09:00:00 UTC", "", "SECRET BODY TWO", "INBOX", "", "No", "No"],
           ["c2", "", "Krish Raja", "https://www.linkedin.com/in/krish-raja",
            "Group", "https://www.linkedin.com/in/x, https://www.linkedin.com/in/y",
            "2026-08-01 09:00:00 UTC", "", "group noise", "INBOX", "", "No", "No"],
           ["c3", "", "Krish Raja", "https://www.linkedin.com/in/krish-raja",
            "Ben Okafor", "https://www.linkedin.com/in/ben-okafor",
            "2026-08-02 09:00:00 UTC", "", "draft", "INBOX", "", "Yes", "No"]])
    write("Endorsement_Received_Info.csv",
          ["Endorsement Date", "Skill Name", "Endorser First Name",
           "Endorser Last Name", "Endorser Public Url", "Endorsement Status"],
          [["2026/06/27 11:15:07 UTC", "Strategy", "Ada", "Nguyen",
            "www.linkedin.com/in/ada-nguyen", "ACCEPTED"],
           ["2026/06/27 11:15:07 UTC", "Sales", "Zed", "Nope",
            "www.linkedin.com/in/zed-nope", "PENDING"]])
    write("Invitations.csv",
          ["From", "To", "Sent At", "Message", "Direction",
           "inviterProfileUrl", "inviteeProfileUrl"],
          [["Krish Raja", "Ben Okafor", "8/5/26, 1:57 PM",
            "Ben, loved your take on voice GTM, keen to compare notes.",
            "OUTGOING", "https://www.linkedin.com/in/krish-raja",
            "https://www.linkedin.com/in/ben-okafor"]])
    write("Recommendations_Received.csv",
          ["First Name", "Last Name", "Company", "Job Title", "Text",
           "Creation Date", "Status"],
          [["Ada", "Nguyen", "Cresta", "VP", "Krish is excellent.",
            "2025-01-01", "VISIBLE"]])
    return d


def test_parse_connections_skips_urlless(export_dir):
    from hunter.people.ingest import parse_connections
    conns = parse_connections(export_dir)
    assert set(conns) == {"ada-nguyen", "ben-okafor"}
    assert conns["ada-nguyen"]["connected_on"] == "2017-05-12"


def test_parse_messages_one_to_one_only_and_no_content(export_dir):
    from hunter.people.ingest import parse_messages
    msgs = parse_messages(export_dir)
    assert msgs["ada-nguyen"]["msgs_out"] == 1
    assert msgs["ada-nguyen"]["msgs_in"] == 1
    assert msgs["ada-nguyen"]["last_message_at"] == "2026-08-07"
    assert msgs["ada-nguyen"]["last_message_direction"] == "in"
    assert "ben-okafor" not in msgs  # drafts and groups carry no signal
    flat = str(msgs)
    assert "SECRET BODY" not in flat  # bodies never leave the parser


def test_parse_endorsements_and_invitations(export_dir):
    from hunter.people.ingest import parse_endorsements, parse_invitations
    endo = parse_endorsements(export_dir)
    assert endo["ada-nguyen"]["endorsements_received"] == 1
    assert "zed-nope" not in endo  # PENDING does not count
    inv = parse_invitations(export_dir)
    assert inv["ben-okafor"]["invite_out_personal"] is True


def test_ingest_end_to_end_offline(export_dir, monkeypatch):
    import hunter.people.ingest as ingest_mod
    inserted = []

    def fake_db_get(cfg, table, params):
        if table == "contacts":
            return [{"id": "u-1",
                     "linkedin_url_norm": "https://linkedin.com/in/ada-nguyen",
                     "email_normalized": None}]
        if table == "contact_intelligence":
            return [{"contact_id": "u-1", "warmth": 80, "email_inbound": 3,
                     "email_outbound": 4, "email_last": "2026-06-01",
                     "network_tier": "inner"}]
        if table == "linkedin_connections":
            return [{"linkedin_slug": "old-friend", "linkedin_url": None,
                     "first_name": "Old", "last_name": "Friend",
                     "full_name": "Old Friend", "email": None,
                     "company": "Legacy Co", "position": "CTO",
                     "connected_on": "2015-02-03"}]
        raise AssertionError(table)

    monkeypatch.setattr(ingest_mod, "db_get", fake_db_get)
    monkeypatch.setattr(ingest_mod, "db_insert",
                        lambda cfg, table, rows, **kw: inserted.extend(rows))
    stats = ingest_mod.ingest(None, str(export_dir), now=NOW)
    assert stats["connections"] == 3  # two export + one table-only
    by_key = {r["contact_key"]: r for r in inserted}
    ada = by_key["ada-nguyen"]
    assert ada["ci_matched"] and ada["contact_id"] == "u-1"
    assert ada["strength_score"] >= 70  # messages + endorsement + rec + priors
    assert set(ada["strength_evidence"]) <= EVIDENCE_KEYS
    assert "SECRET BODY" not in str(inserted)
    assert by_key["old-friend"]["source"] == "linkedin_connections_table"


# ---------- bridges ----------

def make_contact(key, name, company, strength, history=None, last_msg=None):
    ev = {"last_message_at": last_msg} if last_msg else {}
    return {"contact_key": key, "full_name": name, "current_company": company,
            "current_title": "VP", "strength_score": strength,
            "strength_evidence": ev, "employment_history": history}


def test_bridge_tiers_rank_and_warm_path(monkeypatch):
    import hunter.people.bridges as bridges_mod
    roles = [{"job_id": "cresta:vp-partnerships", "company": "Cresta",
              "title": "VP Partnerships", "score": 9, "status": "staging",
              "krish_verdict": None, "warm_path_person": None},
             {"job_id": "lonely:cro", "company": "Lonely AI",
              "title": "Chief Revenue Officer", "score": 8, "status": "staging",
              "krish_verdict": None, "warm_path_person": None}]
    contacts = [
        make_contact("ada-nguyen", "Ada Nguyen", "Cresta", 80, last_msg="2026-08-07"),
        make_contact("cy-example", "Cy Example", "Elsewhere", 60,
                     history=[{"companyName": "Cresta", "title": "Director"}]),
        make_contact("weak-tie", "Weak Tie", "Cresta", 10),
    ]
    # A proposed bridge from an earlier, looser run: the same role, a contact this
    # pass no longer derives. It has to be retired, not left naming a stranger.
    existing_bridges = [{"bridge_id": "b-stale-rule", "job_id": "cresta:vp-partnerships",
                         "contact_key": "wrong-person", "path_tier": "current_employee"}]
    calls = {"insert": [], "patch": [], "delete": []}
    monkeypatch.setattr(bridges_mod, "target_roles", lambda cfg, limit=60: roles)
    # Table-aware, because build_bridges reads two tables and handing contact rows
    # back for a bridge_candidates query is not a useful stand-in for either.
    monkeypatch.setattr(bridges_mod, "db_get",
                        lambda cfg, table, params:
                        existing_bridges if table == "bridge_candidates" else contacts)
    monkeypatch.setattr(bridges_mod, "db_insert",
                        lambda cfg, table, rows, **kw: calls["insert"].extend(rows))
    monkeypatch.setattr(bridges_mod, "db_patch",
                        lambda cfg, table, match, values: calls["patch"].append((match, values)))
    monkeypatch.setattr(bridges_mod, "db_delete",
                        lambda cfg, table, params: calls["delete"].append((table, params)))
    monkeypatch.setattr(bridges_mod, "load_headhunters", lambda sheet: [])

    stats = bridges_mod.build_bridges(None, sheet=None)
    by_tier = {}
    for r in calls["insert"]:
        by_tier.setdefault(r["path_tier"], []).append(r)
    current = by_tier["current_employee"]
    assert len(current) == 1 and current[0]["contact_key"] == "ada-nguyen"
    assert by_tier["ex_employee"][0]["contact_key"] == "cy-example"
    assert current[0]["bridge_score"] > by_tier["ex_employee"][0]["bridge_score"]
    assert by_tier["peer_transition"][0]["job_id"] == "lonely:cro"
    assert by_tier["peer_transition"][0]["contact_key"] == "peer:unidentified"
    warm = calls["patch"]
    assert any(m == {"job_id": "cresta:vp-partnerships"}
               and v["warm_path_person"] == "Ada Nguyen"
               and v["warm_path_tier"] == "current_employee" for m, v in warm)
    assert not any(m == {"job_id": "lonely:cro"} for m, _ in warm)
    assert stats["warm_paths_set"] == 1
    # The bridge this pass no longer derives is gone, and only proposed rows go.
    assert stats["superseded"] == 1
    dels = [p for t, p in calls["delete"] if t == "bridge_candidates"
            and "b-stale-rule" in p.get("bridge_id", "")]
    assert dels and dels[0]["state"] == "eq.proposed"


def test_headhunter_needs_three_covered_roles(monkeypatch):
    import hunter.people.bridges as bridges_mod
    mk = lambda i, title: {"job_id": f"c{i}:r", "company": f"C{i}", "title": title,
                           "score": 8, "status": "staging", "krish_verdict": None,
                           "warm_path_person": None}
    hh = [{"Priority": "A", "Fit": "5", "Firm": "Daversa Partners",
           "Partner": "Joe P", "Title": "Partner, Enterprise & AI",
           "Why-relevant": "Builds CRO/CMO/VP Sales teams"}]
    calls = []
    monkeypatch.setattr(bridges_mod, "db_get", lambda cfg, table, params: [])
    monkeypatch.setattr(bridges_mod, "db_insert",
                        lambda cfg, table, rows, **kw: calls.extend(rows))
    monkeypatch.setattr(bridges_mod, "db_patch", lambda *a, **kw: None)
    monkeypatch.setattr(bridges_mod, "load_headhunters", lambda sheet: hh)

    two = [mk(1, "Chief Revenue Officer"), mk(2, "VP Sales")]
    monkeypatch.setattr(bridges_mod, "target_roles", lambda cfg, limit=60: two)
    bridges_mod.build_bridges(None, sheet=None)
    assert not [r for r in calls if r["path_tier"] == "headhunter"]

    calls.clear()
    three = two + [mk(3, "Head of Commercial Sales")]
    monkeypatch.setattr(bridges_mod, "target_roles", lambda cfg, limit=60: three)
    bridges_mod.build_bridges(None, sheet=None)
    hh_rows = [r for r in calls if r["path_tier"] == "headhunter"]
    assert len(hh_rows) == 3
    assert all("HEADHUNTER PATH" in r["path_evidence"] for r in hh_rows)


def test_drafts_are_templates_with_no_em_dash_and_no_send():
    from hunter.people.bridges import DRAFTS
    for text in DRAFTS.values():
        assert "\u2014" not in text
    assert "[[NAME]]" in DRAFTS["peer_transition"]


def test_norm_name():
    assert norm_name("  Ada  NGUYEN ") == "ada nguyen"
    assert norm_name(None) == ""


def test_bridges_target_only_roles_krish_has_actually_been_shown(monkeypatch):
    """A row at status staging that never reached the sheet is not a target.
    The incumbent left fifteen such rows (ElevenLabs GM Brazil among them)
    and bridges were being built into roles Krish had never seen."""
    from hunter.people import bridges as bridges_mod
    calls = []

    def fake_db_get(cfg, table, params):
        calls.append(params)
        return []

    monkeypatch.setattr(bridges_mod, "db_get", fake_db_get)
    assert bridges_mod.target_roles(None) == []
    staged = next(p for p in calls if p.get("status") == "in.(staging,presented)")
    assert staged["presented_at"] == "not.is.null"
    gos = next(p for p in calls if p.get("krish_verdict") == "not.is.null")
    assert "presented_at" not in gos, "a go verdict is a target whether or not it was staged by hunter"


def test_a_bridge_into_a_role_that_is_no_longer_a_target_is_retired(monkeypatch):
    """Databricks' Accenture lead died and was archived, and its bridge kept
    telling the Bridges tab the role was open. Only hunter's own proposed
    rows go; a row Krish acted on is his history."""
    from hunter.people import bridges as bridges_mod
    roles = [{"job_id": "cresta:vp-partnerships"}]
    proposed = [{"bridge_id": "b-live", "job_id": "cresta:vp-partnerships"},
                {"bridge_id": "b-stale", "job_id": "databricks:sr-dir-global-accenture-lead"}]
    deletes = []
    monkeypatch.setattr(bridges_mod, "db_get", lambda cfg, table, params: proposed)
    monkeypatch.setattr(bridges_mod, "db_delete",
                        lambda cfg, table, params: deletes.append((table, params)))
    assert bridges_mod.retire_stale(None, roles) == 1
    [(table, params)] = deletes
    assert table == "bridge_candidates"
    assert params["bridge_id"] == "in.(b-stale)"
    assert params["state"] == "eq.proposed", "never touches a row Krish acted on"


def test_db_delete_refuses_to_run_unfiltered():
    import pytest
    from hunter.config import db_delete
    with pytest.raises(ValueError):
        db_delete(None, "bridge_candidates", {})


# ---------- eligibility is not ranking ----------

def test_a_linkedin_connection_is_in_network():
    """Krish's ruling 2026-09-15. min_strength=25 was applied to the tier WEIGHTS,
    where 4_owned_network is 15, so his 4,693 LinkedIn connections were discarded
    before anything considered them. Measured on the live pipeline that day: 55
    contacts at his staging companies, 8 surviving. 108 of 131 rows read
    "None found" with OpenAI's CRO sitting in the graph.
    """
    from hunter.people.bridges import in_network
    for tier in ("1_reciprocated", "2_core_network", "3_known_network",
                 "4_owned_network"):
        assert in_network({"network_tier": tier, "strength_score": 15}, 25), tier


def test_a_scraped_lead_is_not_a_warm_path():
    from hunter.people.bridges import in_network
    assert not in_network({"network_tier": "5_cold_lead", "strength_score": 5}, 25)


def test_the_score_still_decides_on_the_graph_that_computes_one():
    """Two graphs, two tests. A network_contacts row's strength_score comes from
    strength.py and real signal, so min_strength means something there."""
    from hunter.people.bridges import in_network
    assert in_network({"strength_score": 60}, 25)
    assert not in_network({"strength_score": 10}, 25)


def test_the_tier_is_named_in_words_so_a_weak_tie_reads_as_one():
    from hunter.people.bridges import tier_words
    assert tier_words({"network_tier": "4_owned_network"}) == (
        "a LinkedIn connection, no recorded contact")
    assert tier_words({"strength_score": 50}) == ""


# ---------- no invented URLs ----------

def test_a_contact_key_is_not_a_linkedin_slug():
    """bridges.py built https://www.linkedin.com/in/<contact_key> and validated it
    with li_slug, which accepted anything without a slash. Eight rows of the live
    sheet carried linkedin.com/in/cold:chad-gerhardstein, every one a 404."""
    assert li_slug("https://www.linkedin.com/in/cold:chad-gerhardstein") is None
    assert li_slug("https://www.linkedin.com/in/contact:8f2a-41bd") is None
    # A real slug still resolves, hyphens, digits and dots included.
    assert li_slug("https://www.linkedin.com/in/krish-raja") == "krish-raja"
    assert li_slug("https://linkedin.com/in/tommytop") == "tommytop"


def test_a_contact_with_no_stored_url_gets_no_url(monkeypatch):
    from hunter.people import bridges as br
    rows = [{"contact_key": "cold:abhi-arora", "full_name": "Abhi Arora",
             "current_title": "CEO", "current_company": "Fleek",
             "linkedin_url": None}]

    def fake_get(cfg, table, params):
        return rows if table == "network_contacts" else []

    monkeypatch.setattr(br, "db_get", fake_get)
    got = br._person_lookup(None, ["cold:abhi-arora"])
    assert got["cold:abhi-arora"]["linkedin_url"] == ""
    assert got["cold:abhi-arora"]["name"] == "Abhi Arora"


# ---------- a false warm path is worse than none ----------

def test_a_shared_generic_word_is_not_a_shared_employer():
    """The live pass offered Harpreet Singh, whose company is "Accel-Digital Ad
    Operations", as the inside contact at Notion, LangChain AND Render, because
    those three role rows carry a company field like "Notion - Head of GTM
    Operations" and "operations" survives distinctive_tokens as though it named an
    employer. Krish would have emailed a stranger on hunter's word.
    """
    from hunter.people.bridges import same_employer
    from hunter.sources import distinctive_tokens as d
    for role, contact in (("Notion - Head of GTM Operations", "Accel-Digital Ad Operations"),
                          ("Render - Head of Revenue Operations", "Accel-Digital Ad Operations"),
                          ("MongoDB - Head of Post Sales Technology", "Sales Impact Academy")):
        assert not same_employer(d(role), d(contact)), (role, contact)


def test_the_case_the_fallback_was_built_for_still_matches():
    from hunter.people.bridges import same_employer
    from hunter.sources import distinctive_tokens as d
    assert same_employer(d("Google"), d("Google (YouTube Partnerships)"))
    assert same_employer(d("Captify"), d("Captify APAC"))
    assert same_employer(d("Omnicom Media"), d("Omnicom Media Group"))


def test_a_different_firm_sharing_a_prefix_is_not_the_same_employer():
    from hunter.people.bridges import same_employer
    from hunter.sources import distinctive_tokens as d
    assert not same_employer(d("EY"), d("EY-Parthenon"))
    assert not same_employer(frozenset(), d("Anything"))


# ---------- the mindmake wedge ----------

def _wedge_setup(monkeypatch, *, title, leader_title, applied=None, company="Cresta"):
    import hunter.people.bridges as bridges_mod
    roles = [{"job_id": "c:1", "company": company, "title": title, "score": 9,
              "status": "staging", "krish_verdict": None, "warm_path_person": None,
              "application_state": applied}]
    contacts = [make_contact("leader", "Lin Qiao", company, 30)]
    contacts[0]["current_title"] = leader_title
    calls = {"insert": [], "patch": [], "delete": []}
    monkeypatch.setattr(bridges_mod, "target_roles", lambda cfg, limit=60: roles)
    monkeypatch.setattr(bridges_mod, "db_get", lambda cfg, table, params:
                        [] if table == "bridge_candidates" else contacts)
    monkeypatch.setattr(bridges_mod, "db_insert",
                        lambda cfg, table, rows, **kw: calls["insert"].extend(rows))
    monkeypatch.setattr(bridges_mod, "db_patch",
                        lambda cfg, table, match, values: calls["patch"].append((match, values)))
    monkeypatch.setattr(bridges_mod, "db_delete",
                        lambda cfg, table, params: calls["delete"].append(params))
    monkeypatch.setattr(bridges_mod, "load_headhunters", lambda sheet: [])
    stats = bridges_mod.build_bridges(None, sheet=None)
    tiers = [r["path_tier"] for r in calls["insert"]]
    return stats, tiers, calls


def test_an_open_commercial_seat_at_a_company_whose_leader_he_knows_makes_a_wedge(monkeypatch):
    """Krish 2026-09-15: a CEO with a GTM seat open is warm to the practice, and
    the approach is either a client or the highest-level way into the role."""
    stats, tiers, calls = _wedge_setup(
        monkeypatch, title="Head of GTM Strategy", leader_title="CEO and cofounder")
    assert "mindmake_wedge" in tiers
    assert stats["mindmake_wedges"] == 1
    wedge = next(r for r in calls["insert"] if r["path_tier"] == "mindmake_wedge")
    # Opens on the observation, never the offer: no price, no programme name.
    assert "nothing to sell" in wedge["draft_ask"]
    assert "product, price, positioning or people" in wedge["draft_ask"]
    for forbidden in ("$", "Build your AI GTM", "Mindmake"):
        assert forbidden not in wedge["draft_ask"], forbidden


def test_a_role_he_already_applied_to_gets_no_wedge(monkeypatch):
    """Applying through the ATS and pitching the CEO the same week reads as "he
    will take anything" if the two ever compare notes. One road per company."""
    stats, tiers, _ = _wedge_setup(
        monkeypatch, title="Head of GTM Strategy", leader_title="CEO",
        applied="Applied")
    assert "mindmake_wedge" not in tiers
    assert stats["mindmake_wedges"] == 0


def test_a_non_leader_is_not_a_wedge(monkeypatch):
    """Peer to peer or nothing. A Programmatic Director decides neither a hire
    nor an engagement."""
    _stats, tiers, _ = _wedge_setup(
        monkeypatch, title="Head of GTM Strategy",
        leader_title="Programmatic Director ANZ")
    assert "mindmake_wedge" not in tiers


def test_a_seat_his_practice_does_not_speak_to_is_not_a_wedge(monkeypatch):
    """A company hiring a Head of Engineering has no GTM question to open on."""
    _stats, tiers, _ = _wedge_setup(
        monkeypatch, title="Head of Platform Engineering", leader_title="CEO")
    assert "mindmake_wedge" not in tiers


def test_one_leader_per_role_not_three(monkeypatch):
    import hunter.people.bridges as bridges_mod
    roles = [{"job_id": "c:1", "company": "Cresta", "title": "Head of Revenue",
              "score": 9, "status": "staging", "krish_verdict": None,
              "warm_path_person": None, "application_state": None}]
    contacts = []
    for i, t in enumerate(("CEO", "President", "Chief Revenue Officer")):
        c = make_contact(f"l{i}", f"Leader {i}", "Cresta", 30)
        c["current_title"] = t
        contacts.append(c)
    ins = []
    monkeypatch.setattr(bridges_mod, "target_roles", lambda cfg, limit=60: roles)
    monkeypatch.setattr(bridges_mod, "db_get", lambda cfg, table, params:
                        [] if table == "bridge_candidates" else contacts)
    monkeypatch.setattr(bridges_mod, "db_insert",
                        lambda cfg, table, rows, **kw: ins.extend(rows))
    monkeypatch.setattr(bridges_mod, "db_patch", lambda *a, **k: None)
    monkeypatch.setattr(bridges_mod, "db_delete", lambda *a, **k: None)
    monkeypatch.setattr(bridges_mod, "load_headhunters", lambda sheet: [])
    bridges_mod.build_bridges(None, sheet=None)
    assert len([r for r in ins if r["path_tier"] == "mindmake_wedge"]) == 1


def test_the_wedge_is_retired_like_any_other_derived_tier():
    from hunter.people.bridges import DERIVED_TIERS
    assert "mindmake_wedge" in DERIVED_TIERS
