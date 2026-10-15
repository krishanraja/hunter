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



# ---------- the CV master ----------

def cv_doc():
    """A CV shaped like his master v14 on 2026-10-15: Georgia name and
    headings, Calibri body at about 10pt, an email in the contact line.
    Invented words and numbers."""
    from test_house import GREY, INK, NAVY, ts
    def gts(size, bold=True, color=NAVY):
        return dict(ts(size, bold, color), weightedFontFamily={"fontFamily": "Georgia", "weight": 700})
    def cts(size, bold=False, color=INK, weight=400):
        return dict(ts(size, bold, color), weightedFontFamily={"fontFamily": "Calibri", "weight": weight})
    rows = [
        ([("KRISH RAJA", gts(25))], False),
        ([("AI-Native Commercial Strategy Leader", cts(12.5, color=NAVY, weight=600))], False),
        ([("someone@example.com \u00a0\u00b7 \u00a0+1 (212) 555-0101 \u00a0\u00b7 \u00a0example.com",
           cts(8.9, color=GREY))], False),
        ([("Full working rights in the USA, UK and Australia.", cts(8.9, color=GREY))], False),
        ([("PROFESSIONAL SUMMARY", gts(10.5))], False),
        ([("With years of invented commercial work, ", cts(10.3)), ("$1M to $9M", cts(10.3, True))], False),
        ([("CAREER HIGHLIGHTS", gts(10.5))], False),
        ([("Built an invented region from ", cts(10.2)), ("$0 to $5M", cts(10.2, True))], True),
        ([("Grew an invented line ", cts(10.2)), ("$2M to $8M", cts(10.2, True))], True),
        ([("CORE COMPETENCIES", gts(10.5))], False),
        ([("Partnerships \u00b7 Pricing \u00b7 Teams", cts(10.2))], False),
        ([("PROFESSIONAL EXPERIENCE", gts(10.5))], False),
        ([("Invented Co. | Head of Things", cts(10.9, True))], False),
        ([("EDUCATION AND RECOGNITION", gts(10.5))], False),
        ([("MA Invented Studies", cts(9.8))], False),
    ]
    content, idx = [], 1
    for runs, bullet in rows:
        els = []
        for text, style in runs:
            els.append({"startIndex": idx, "endIndex": idx + len(text),
                        "textRun": {"content": text, "textStyle": style}})
            idx += len(text)
        els.append({"startIndex": idx, "endIndex": idx + 1,
                    "textRun": {"content": "\n", "textStyle": cts(10.2)}})
        idx += 1
        para = {"elements": els, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}
        if bullet:
            para["bullet"] = {"listId": "kix.cv"}
        content.append({"paragraph": para})
    return {"namedStyles": {"styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {}}]},
            "body": {"content": content}}


def cv_made(pages=(3, 2)):
    db = FakeDocBuild({"cv": cv_doc(), "spec": letter()})
    db.export_pages = list(pages)
    out = masters.build_cv_master(db, "cv", "spec", folder_id="f", page_count=page_count,
                                  apply=True)
    return db, out


def test_the_cv_dry_run_plans_and_makes_nothing():
    db = FakeDocBuild({"cv": cv_doc(), "spec": letter()})
    out = masters.build_cv_master(db, "cv", "spec", folder_id="f", page_count=page_count,
                                  apply=False)
    assert out["made"] == "" and out["headings"] == 5 and list(db.docs) == ["cv", "spec"]


def test_the_cv_takes_his_letter_header_font_and_sizes():
    db, out = cv_made()
    assert out["problems"] == [] and out["margin_step"] == 4.0 and out["banner_kept"]
    paras = house.snapshot(db.get(out["made"]))
    texts = [house.norm(p.text) for p in paras]
    assert texts[:3] == ["KRISH RAJA", "AI-Native Commercial Strategy Leader",
                         "{{PLACE}} \u00b7 {{PHONE}}"]
    assert texts[3] == house.norm(house.LINK_ROW) and paras[4].images
    assert texts[5].startswith("Full working rights")
    links = {house.norm(r.text): r.link for r in paras[3].runs if r.link}
    assert links == {"Bio": "http://www.krishraja.com",
                     "LinkedIn": "http://linkedin.com/in/krish-raja",
                     "Media channel": "http://makeyourmindup.ai"}
    assert not any("example.com" in t for t in texts[:6]), "no email in the header"
    # Filled for a US role, it passes his gates as a CV.
    db.batch(out["made"], [
        {"replaceAllText": {"containsText": {"text": "{{PLACE}}", "matchCase": True},
                            "replaceText": US_BASE.place}},
        {"replaceAllText": {"containsText": {"text": "{{PHONE}}", "matchCase": True},
                            "replaceText": US_BASE.phone_display}}])
    assert house.check(house.snapshot(db.get(out["made"])), kind="cv", base=US_BASE,
                       bases=BASES, page_count=2,
                       heading_sizes={h: 10.5 for h in ("PROFESSIONAL SUMMARY", "CAREER HIGHLIGHTS",
                                                        "CORE COMPETENCIES", "PROFESSIONAL EXPERIENCE",
                                                        "EDUCATION AND RECOGNITION")}) == []


def test_bold_survives_the_restyle():
    db, out = cv_made()
    paras = house.snapshot(db.get(out["made"]))
    bolds = sorted(house.norm(r.text) for p in paras for r in p.runs if r.bold and r.text.strip())
    assert "$0 to $5M" in bolds and "$2M to $8M" in bolds and "Invented Co. | Head of Things" in bolds


def test_the_banner_goes_before_the_cv_runs_over_two_pages():
    db, out = cv_made(pages=(3, 3, 3, 3, 3, 2))
    assert out["banner_kept"] is False and out["problems"] == []
    paras = house.snapshot(db.get(out["made"]))
    assert not any(p.images for p in paras)


def test_a_cv_that_never_fits_is_reported():
    db, out = cv_made(pages=(3,))
    assert "the CV runs over two pages even at the margin floor without the banner" in out["problems"]


def test_the_cv_command_prints_counts_and_the_new_id_only(monkeypatch, capsys):
    import hunter.config as config_mod
    import hunter.run as run_mod
    db = FakeDocBuild({"cv": cv_doc(), "spec": letter()})
    db.export_pages = [2]
    monkeypatch.setattr(config_mod, "CV_MASTER_ID", "cv")
    monkeypatch.setattr(run_mod, "load", lambda: type("C", (), {"raw": {}})())
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": "t"})())
    monkeypatch.setattr(run_mod, "DocBuild", lambda tok: db)
    assert run_mod.main(["cv-master", "--letter", "spec", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "5 headings" in out and "made copy_1_" in out
    for leak in ("Invented", "555", "example.com"):
        assert leak not in out, leak


def test_check_reads_a_made_master_back_and_makes_nothing(monkeypatch, capsys):
    import hunter.run as run_mod
    db, out = made()
    before = set(db.docs)
    monkeypatch.setattr(run_mod, "load", lambda: type("C", (), {"raw": {}})())
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": "t"})())
    monkeypatch.setattr(run_mod, "DocBuild", lambda tok: db)
    assert run_mod.main(["letter-master", "--check", out["made"]]) == 0
    assert "checked, 0 problem(s)" in capsys.readouterr().out
    assert set(db.docs) == before



def test_reordering_cv_highlights_keeps_every_font_size_and_indent():
    """The CV's highlights are reordered per role (canon 9.12). insertText
    takes the style of what sits beside it, here a heading, so a reorder that
    restored only bold would hand them the heading's font and size."""
    from hunter.docbuild import DocBuild
    db, out = cv_made()
    doc_id = out["made"]
    paras = db.paragraphs(db.get(doc_id))
    for p in paras:
        if p["bullet"]:
            el = p
            db.batch(doc_id, [{"updateParagraphStyle": {
                "range": {"startIndex": p["start"], "endIndex": p["end"]},
                "paragraphStyle": {"indentStart": {"magnitude": 18, "unit": "PT"},
                                   "indentFirstLine": {"magnitude": 4, "unit": "PT"}},
                "fields": "indentStart,indentFirstLine"}}])
    before = house.snapshot(db.get(doc_id))
    sig = lambda ps: sorted((house.norm(p.text), tuple((r.font, r.size, r.bold) for r in p.runs
                                                       if r.text.strip())) for p in ps if p.bullet)
    db.reorder_paragraphs(doc_id, ["Built an invented region", "Grew an invented line"], [1, 0])
    after = house.snapshot(db.get(doc_id))
    assert sig(after) == sig(before)
    texts = [house.norm(p.text) for p in after if p.bullet]
    assert texts[0].startswith("Grew") and texts[1].startswith("Built")
    raw = [el["paragraph"]["paragraphStyle"] for el in db.get(doc_id)["body"]["content"]
           if "paragraph" in el and "bullet" in el["paragraph"]]
    assert {(ps["indentStart"]["magnitude"], ps["indentFirstLine"]["magnitude"]) for ps in raw} == {(18, 4)}
