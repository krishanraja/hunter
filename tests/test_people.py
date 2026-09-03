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
    calls = {"insert": [], "patch": []}
    monkeypatch.setattr(bridges_mod, "target_roles", lambda cfg, limit=60: roles)
    monkeypatch.setattr(bridges_mod, "db_get",
                        lambda cfg, table, params: contacts)
    monkeypatch.setattr(bridges_mod, "db_insert",
                        lambda cfg, table, rows, **kw: calls["insert"].extend(rows))
    monkeypatch.setattr(bridges_mod, "db_patch",
                        lambda cfg, table, match, values: calls["patch"].append((match, values)))
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
