"""The emulator holds what his house style is read from (tests/fake_docs.py):
every run's whole style, every paragraph's style and bullet, an inline image
as one index, and the API's refusals. Without it, a document that dropped his
fonts or banner would pass offline."""
import pytest

from fake_docs import FakeDocBuild, LINK_BLUE
from hunter.package import house
from test_house import letter
from contact_bases import BASES, US_BASE


def fdb():
    return FakeDocBuild({"m": letter()})


def paras(doc):
    return [el for el in doc["body"]["content"] if "paragraph" in el]


def test_a_round_trip_keeps_every_style_and_the_banner():
    db = fdb()
    snap = house.snapshot(db.get("m"))
    assert house.check(snap, kind="letter", base=US_BASE, bases=BASES) == []
    assert snap[4].images and snap[4].images[0].width_pt == 150.0


def test_a_copy_carries_the_named_styles_and_images():
    db = fdb()
    new = db.copy_master("m", "copy")
    assert house.check(house.snapshot(db.get(new)), kind="letter", base=US_BASE,
                       bases=BASES) == []


def test_replace_all_text_keeps_the_run_style():
    db = fdb()
    db.batch("m", [{"replaceAllText": {"containsText": {"text": "Zentrox's pipeline",
                                                        "matchCase": True},
                                       "replaceText": "Quillfield's book"}}])
    p = house.snapshot(db.get("m"))[12]
    assert p.text.startswith("Quillfield's book") and {r.size for r in p.runs} == {9.0}
    assert not any(r.bold for r in p.runs)


def test_an_inserted_newline_keeps_the_paragraph_style_and_bullet():
    db = fdb()
    doc = db.get("m")
    bullet = next(el for el in paras(doc) if "bullet" in el["paragraph"])
    at = bullet["startIndex"] + 3
    db.batch("m", [{"insertText": {"location": {"index": at}, "text": "\n"}}])
    after = [el["paragraph"] for el in paras(db.get("m"))]
    assert sum(1 for p in after if "bullet" in p) == 5


def test_a_link_set_alone_gets_the_default_link_style():
    db = fdb()
    db.batch("m", [{"updateTextStyle": {"range": {"startIndex": 1, "endIndex": 5},
                                        "textStyle": {"link": {"url": "https://x.example"}},
                                        "fields": "link"}}])
    run = paras(db.get("m"))[0]["paragraph"]["elements"][0]["textRun"]["textStyle"]
    assert run["link"]["url"] == "https://x.example"
    assert run["underline"] is True and run["foregroundColor"] == LINK_BLUE


def test_a_star_mask_replaces_the_whole_style():
    db = fdb()
    db.batch("m", [{"updateTextStyle": {"range": {"startIndex": 1, "endIndex": 11},
                                        "textStyle": {"fontSize": {"magnitude": 9, "unit": "PT"}},
                                        "fields": "*"}}])
    r = house.snapshot(db.get("m"))[0].runs[0]
    assert r.size == 9.0 and not r.bold and r.color is None


@pytest.mark.parametrize("req,err", [
    ({"updateTextStyle": {"range": {"startIndex": 3, "endIndex": 3}, "textStyle": {},
                          "fields": "bold"}}, ValueError),
    ({"updateTextStyle": {"range": {"startIndex": 1, "endIndex": 3}, "textStyle": {},
                          "fields": "fontWeight"}}, NotImplementedError),
    ({"updateTextStyle": {"range": {"startIndex": 1, "endIndex": 3}, "textStyle": {},
                          "fields": ""}}, ValueError),
    ({"updateParagraphStyle": {"range": {"startIndex": 1, "endIndex": 3},
                               "paragraphStyle": {"headingId": "h.x"}, "fields": "headingId"}},
     ValueError),
    ({"deleteContentRange": {"range": {"startIndex": 4, "endIndex": 4}}}, ValueError),
])
def test_what_the_api_refuses_is_refused(req, err):
    with pytest.raises(err):
        fdb().batch("m", [req])


def test_paragraph_and_document_styles_are_set_by_mask():
    db = fdb()
    db.batch("m", [{"updateParagraphStyle": {"range": {"startIndex": 1, "endIndex": 4},
                                             "paragraphStyle": {"spaceBelow": {"magnitude": 2,
                                                                               "unit": "PT"}},
                                             "fields": "spaceBelow"}},
                   {"updateDocumentStyle": {"documentStyle": {"marginLeft": {"magnitude": 40,
                                                                             "unit": "PT"}},
                                            "fields": "marginLeft"}}])
    doc = db.get("m")
    assert paras(doc)[0]["paragraph"]["paragraphStyle"]["spaceBelow"]["magnitude"] == 2
    assert doc["documentStyle"]["marginLeft"]["magnitude"] == 40


def test_docbuild_paragraphs_see_the_banner():
    db = fdb()
    ps = db.paragraphs(db.get("m"))
    assert ps[4]["images"] == ["kix.banner"] and ps[4]["text"].strip() in ("", "\ufffc")
    assert all(p["images"] == [] for i, p in enumerate(ps) if i != 4)
