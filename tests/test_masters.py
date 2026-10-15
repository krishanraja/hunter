"""Letter master v5 from his edited Sonos letter (package/masters.py), run
through the emulator on a letter of the same shape with invented words."""
import pytest

from contact_bases import BASES, US_BASE
from fake_docs import FakeDocBuild
from hunter.apply.merge import page_count
from hunter.package import house, masters
from test_house import letter


def made(pages=(2, 1)):
    db = FakeDocBuild({"spec": letter()})
    db.export_pages = list(pages)
    out = masters.build_v5(db, "spec", folder_id="f", page_count=page_count, apply=True)
    return db, out


def test_a_dry_run_plans_and_makes_nothing():
    db = FakeDocBuild({"spec": letter()})
    out = masters.build_v5(db, "spec", folder_id="f", page_count=page_count, apply=False)
    assert out["slots"] == 14 and out["bullets"] == 4 and out["made"] == ""
    assert list(db.docs) == ["spec"]


def test_the_master_is_his_letter_with_slots_and_his_margins_brought_in():
    db, out = made()
    assert out["problems"] == [] and out["margin_step"] == 4.0
    doc = db.get(out["made"])
    assert doc["documentStyle"]["marginLeft"]["magnitude"] == 50.0
    paras = house.snapshot(doc)
    texts = [house.norm(p.text) for p in paras]
    assert texts[2] == "{{PLACE}} · {{PHONE}}"
    assert texts[7:13] == ["{{DATE}}", "{{HIRING_LEAD}}", "{{COMPANY}}", "Re: {{ROLE}}",
                           "Dear {{HIRING_LEAD}},", "{{HOOK}}"]
    assert [t for t, p in zip(texts, paras) if p.bullet] == [
        "{{BULLET_1}}", "{{BULLET_2}}", "{{BULLET_3}}", "{{BULLET_4}}"]
    # His header and sign-off untouched: the link row, banner and closing line.
    lay = house.layout(paras)
    assert paras[lay.banner].images and texts[lay.link_row] == house.norm(house.LINK_ROW)
    assert house.SIGN_OFF in texts and texts[-1] == house.SIGNATURE
    # The original is untouched.
    assert house.check(house.snapshot(db.get("spec")), kind="letter", base=US_BASE,
                       bases=BASES) == []


def test_each_slot_carries_his_style_whole():
    db, out = made()
    paras = house.snapshot(db.get(out["made"]))
    by = {house.norm(p.text): p.runs[0] for p in paras if p.runs and p.text.strip()}
    for slot, bold in (("{{DATE}}", True), ("Re: {{ROLE}}", True), ("{{HOOK}}", False),
                       ("{{BULLET_2}}", False), ("Dear {{HIRING_LEAD}},", False)):
        r = by[slot]
        assert (r.font, r.size, r.bold) == ("Arial", 9.0, bold), slot
    assert by["Re: {{ROLE}}"].color == "#14213d" and by["{{PLACE}} · {{PHONE}}"].color == "#5a6472"


def test_a_letter_that_never_fits_one_page_is_reported_not_slotted():
    db, out = made(pages=(2,))
    assert out["problems"] == ["his letter does not fit one page even at the margin floor"]
    assert "{{HOOK}}" not in "".join(p.text for p in house.snapshot(db.get(out["made"])))


@pytest.mark.parametrize("bullets", [3, 5])
def test_a_letter_of_another_shape_is_refused_before_anything_is_made(bullets):
    db = FakeDocBuild({"spec": letter(bullets=bullets)})
    with pytest.raises(masters.MasterError, match="expected four bullets"):
        masters.build_v5(db, "spec", folder_id="f", page_count=page_count, apply=True)
    assert list(db.docs) == ["spec"]


def test_the_margins_never_go_below_the_floor():
    req = masters.margin_request(40.0)["updateDocumentStyle"]["documentStyle"]
    assert {v["magnitude"] for v in req.values()} == {masters.MARGIN_FLOOR}


def test_the_read_back_finds_a_lost_slot_or_a_wrong_size():
    db, out = made()
    db.batch(out["made"], [{"replaceAllText": {"containsText": {"text": "{{WHY_NOW}}",
                                                                "matchCase": True},
                                               "replaceText": "gone"}}])
    assert "{{WHY_NOW}} appears 0 times, not 1" in masters.template_problems(
        house.snapshot(db.get(out["made"])))


def test_the_command_prints_counts_and_the_new_id_only(monkeypatch, capsys):
    import hunter.run as run_mod
    db = FakeDocBuild({"spec": letter()})
    db.export_pages = [1]
    monkeypatch.setattr(run_mod, "load", lambda: type("C", (), {"raw": {}})())
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": "t"})())
    monkeypatch.setattr(run_mod, "DocBuild", lambda tok: db)
    assert run_mod.main(["letter-master", "--from", "spec", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "14 slots, 4 bullets" in out and "made copy_1_" in out
    for leak in ("Zentrox", "partner engine", "Krish Raja", "555"):
        assert leak not in out, leak
    assert run_mod.main(["letter-master"]) == 2


def test_a_refusal_names_the_shape_never_the_letter(monkeypatch, capsys):
    import hunter.run as run_mod
    db = FakeDocBuild({"spec": letter(bullets=3)})
    monkeypatch.setattr(run_mod, "load", lambda: type("C", (), {"raw": {}})())
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": "t"})())
    monkeypatch.setattr(run_mod, "DocBuild", lambda tok: db)
    assert run_mod.main(["letter-master", "--from", "spec"]) == 1
    assert "refused, expected four bullets in one block, found 3" in capsys.readouterr().out
