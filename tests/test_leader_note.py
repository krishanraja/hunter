"""The note he sends after applying: who it goes to, and how the Apply email shows it."""
from hunter.apply import approval
from hunter.people import bridges

NOTE = "Zentrox is turning its platform into a partner business, and that is my record."


def cand(key, tier, score=10, state="proposed"):
    return {"contact_key": key, "path_tier": tier, "bridge_score": score, "state": state}


def test_the_person_who_leads_the_hire_comes_before_a_friend_inside():
    got = bridges.pick_note_recipient([cand("contact:1", "current_employee", 90),
                                       cand("cold:sam", "cold_target", 15)])
    assert got["contact_key"] == "cold:sam"


def test_within_a_tier_the_stronger_bridge_wins():
    got = bridges.pick_note_recipient([cand("contact:1", "current_employee", 40),
                                       cand("contact:2", "current_employee", 80)])
    assert got["contact_key"] == "contact:2"


def test_a_headhunter_a_peer_or_a_dismissed_bridge_never_gets_the_note():
    assert bridges.pick_note_recipient([
        cand("headhunter:acme", "headhunter", 99), cand("peer:unidentified", "peer_transition"),
        cand("contact:3", "current_employee", state="dismissed")]) is None


def render(**kw):
    return approval.render(company="Zentrox", role="VP Partnerships", jd_url="", autonomy="",
                           token="t1", to="me@example.com", lines=[], **kw)


def test_the_email_carries_the_note_addressed_and_signed_with_the_reply_address():
    to = {"name": "Sam Rivera", "title": "COO", "linkedin_url": "https://www.linkedin.com/in/sam",
          "tier": "cold_target"}
    em = render(leader_note=NOTE, note_to=to, reply_email="krish@mindmake.example")
    for part in (em.text, em.html):
        assert "Hi Sam," in part and NOTE in part
        assert "krish@mindmake.example" in part
        assert "https://www.linkedin.com/in/sam" in part
        assert "never sends" in part
    assert chr(0x2014) not in em.html + em.text


def test_with_no_name_the_note_still_shows_and_says_who_to_find():
    em = render(leader_note=NOTE)
    assert "Hi there," in em.text and "found no name" in em.text


def test_no_note_no_section():
    em = render()
    assert "SEND THIS NOTE" not in em.text and "send this note" not in em.html


def test_two_people_get_two_notes_each_with_their_own_links():
    hm = {"name": "Sam Rivera", "title": "COO", "linkedin_url": "https://www.linkedin.com/in/sam",
          "tier": "cold_target", "email": "sam@zentrox.example", "email_checked": True}
    ld = {"name": "Ada Okafor", "title": "CEO", "linkedin_url": "https://www.linkedin.com/in/ada",
          "tier": "exec_target"}
    em = render(outreach=[(NOTE, hm), ("Zentrox is about to sell to agents, and I built that once.", ld)],
                reply_email="krish@mindmake.example")
    assert "SEND THESE 2 NOTES" in em.text
    assert "Hi Sam," in em.text and "Hi Ada," in em.text
    assert "sam@zentrox.example" in em.text and "checked as deliverable" in em.text
    assert em.text.count("Email:") == 1


def test_parse_leaders_keeps_only_sourced_people_and_never_one_person_twice():
    text = ('{"hiring_manager": {"name": "Sam Rivera", "source_url": "https://z.example/team"},'
            ' "leader": {"name": "sam rivera", "source_url": "https://z.example/about"}}')
    assert list(bridges.parse_leaders(text)) == ["hiring_manager"]
    assert bridges.parse_leaders('{"hiring_manager": {"name": "Sam"}, "leader": null}') == {}
    assert bridges.parse_leaders("no json here") == {}


def test_his_nines_outrank_every_derived_path_in_control_center():
    assert min(bridges.LEADER_SCORE.values()) > max(bridges.TIER_BASE.values())
