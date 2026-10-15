"""His house style, read back from the document (package/house.py).

The letter here has the shape of his edited Sonos letter, 2026-10-15: the
same paragraphs, fonts, sizes, colours, link row, banner and sign-off. Every
word of the body and every number is invented."""
import copy
import re

import pytest

from contact_bases import BASES, UK_BASE, US_BASE
from hunter.package import house

NAVY = {"red": 20 / 255, "green": 33 / 255, "blue": 61 / 255}
GREY = {"red": 90 / 255, "green": 100 / 255, "blue": 114 / 255}
INK = {"red": 26 / 255, "green": 26 / 255, "blue": 26 / 255}
BLUE = {"red": 17 / 255, "green": 85 / 255, "blue": 204 / 255}


def ts(size=9, bold=False, color=INK, font=None, link=None):
    out = {"fontSize": {"magnitude": size, "unit": "PT"},
           "foregroundColor": {"color": {"rgbColor": color}}}
    if bold:
        out["bold"] = True
    if font:
        out["weightedFontFamily"] = {"fontFamily": font, "weight": 400}
    if link:
        out["link"] = {"url": link}
        out["underline"] = True
    return out


def letter(base=US_BASE, *, bullets=4):
    """[(runs, bullet, image)] where runs is [(text, textStyle)]."""
    paras = [
        ([("KRISH RAJA", ts(23, True, NAVY))], False, False),
        ([("AI-Native Commercial Strategy Leader", ts(11, False, NAVY))], False, False),
        ([(base.header_line(), ts(9, False, GREY))], False, False),
        ([("Bio", ts(9, color=BLUE, link="http://www.krishraja.com")),
          ("   |   ", ts(9, color=GREY)),
          ("LinkedIn", ts(9, color=BLUE, link="http://linkedin.com/in/krish-raja")),
          ("    |   ", ts(9, color=GREY)),
          ("Media channel", ts(9, color=BLUE, link="http://makeyourmindup.ai")),
          (" ⬇️", ts(9, color=GREY))], False, False),
        ([], False, True),
        ([("Full working rights in the USA, UK and Australia.", ts(9, color=GREY))], False, False),
        ([], False, False),
        ([("October 14, 2026", ts(9, True))], False, False),
        ([("Hiring Team", ts(9, True))], False, False),
        ([("Zentrox, Inc.", ts(9, True))], False, False),
        ([("Re: Vice President, Partnerships", ts(9, True, NAVY))], False, False),
        ([("Dear Hiring Team,", ts(9))], False, False),
        ([("Zentrox's pipeline is busy and the gap is signed partners.", ts(9))], False, False),
        ([("I'm a commercial leader with technical roots.", ts(9))], False, False),
    ]
    for n in range(bullets):
        paras.append(([(f"Quorra {n}.", ts(9, True)), (" Built a partner engine.", ts(9))], True, False))
    paras += [
        ([], False, False),
        ([("Worth saying why I am looking.", ts(9))], False, False),
        ([("I'd rather show you the system than describe it.", ts(9))], False, False),
        ([(house.SIGN_OFF, ts(9))], False, False),
        ([], False, False),
        ([("Krish Raja", ts(9))], False, False),
    ]
    content, idx = [], 1
    for runs, bullet, image in paras:
        els = []
        for text, style in runs:
            els.append({"startIndex": idx, "endIndex": idx + len(text),
                        "textRun": {"content": text, "textStyle": style}})
            idx += len(text)
        if image:
            els.append({"startIndex": idx, "endIndex": idx + 1,
                        "inlineObjectElement": {"inlineObjectId": "kix.banner",
                                                "textStyle": {}}})
            idx += 1
        els.append({"startIndex": idx, "endIndex": idx + 1,
                    "textRun": {"content": "\n", "textStyle": ts(9)}})
        idx += 1
        para = {"elements": els, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}
        if bullet:
            para["bullet"] = {"listId": "kix.list"}
        content.append({"paragraph": para})
    return {"namedStyles": {"styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {
                "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                "fontSize": {"magnitude": 11, "unit": "PT"}}}]},
            "inlineObjects": {"kix.banner": {"inlineObjectProperties": {"embeddedObject": {
                "size": {"width": {"magnitude": 150, "unit": "PT"},
                         "height": {"magnitude": 23, "unit": "PT"}},
                "imageProperties": {"contentUri": "https://lh7.example.invalid/banner"}}}}},
            "body": {"content": content}}


def fails_of(doc, base=US_BASE, **kw):
    return house.check(house.snapshot(doc), kind="letter", base=base, bases=BASES, **kw)


def runs_of(doc, i):
    return doc["body"]["content"][i]["paragraph"]["elements"]


def test_his_layout_passes_clean():
    assert fails_of(letter()) == []
    assert fails_of(letter(UK_BASE), base=UK_BASE) == []
    assert fails_of(letter(bullets=3)) == []


def test_the_snapshot_resolves_style_through_the_named_style():
    snap = house.snapshot(letter())
    assert snap[0].runs[0].font == "Arial" and snap[0].runs[0].size == 23.0
    assert snap[0].runs[0].color == "#14213d" and snap[0].runs[0].bold
    assert [i.width_pt for i in snap[4].images] == [150.0]
    lay = house.layout(snap)
    assert (lay.name, lay.title, lay.contact, lay.link_row, lay.banner, lay.rights) == (0, 1, 2, 3, 4, 5)


def _break(mutate, **kw):
    doc = letter()
    mutate(doc)
    return fails_of(doc, **kw)


@pytest.mark.parametrize("name,mutate,code", [
    ("another font", lambda d: runs_of(d, 12)[0]["textRun"]["textStyle"].update(
        weightedFontFamily={"fontFamily": "Calibri"}), "A11 a run is not Arial"),
    ("another font set on a run", lambda d: runs_of(d, 13)[0]["textRun"]["textStyle"].update(
        weightedFontFamily={"fontFamily": "Times New Roman"}), "A11 a run is not Arial"),
    ("body at 10pt", lambda d: runs_of(d, 13)[0]["textRun"]["textStyle"].update(
        fontSize={"magnitude": 10, "unit": "PT"}), "A11 paragraph 14 is not 9pt"),
    ("name at 25pt", lambda d: runs_of(d, 0)[0]["textRun"]["textStyle"].update(
        fontSize={"magnitude": 25, "unit": "PT"}), "A11 paragraph 1 is not 23pt"),
    ("a bold hook", lambda d: runs_of(d, 12)[0]["textRun"]["textStyle"].update(bold=True),
     "A11 the opening paragraph is bold"),
    ("a bold salutation", lambda d: runs_of(d, 11)[0]["textRun"]["textStyle"].update(bold=True),
     "A11 the salutation is bold"),
    ("a title not navy", lambda d: runs_of(d, 1)[0]["textRun"]["textStyle"].update(
        foregroundColor={"color": {"rgbColor": INK}}), "A11 header paragraph 2 is not in his colour"),
    ("the banner gone", lambda d: runs_of(d, 4).pop(0), "A12 header incomplete: no banner"),
    ("LinkedIn to the wrong place", lambda d: runs_of(d, 3)[2]["textRun"]["textStyle"].update(
        link={"url": "https://example.com"}), "A12 the LinkedIn link is missing or wrong"),
    ("his closing line gone", lambda d: runs_of(d, len(d["body"]["content"]) - 3)[0]
     ["textRun"].update(content="Thanks."), "A7 his closing line is missing"),
    ("a footer contact line", lambda d: runs_of(d, len(d["body"]["content"]) - 1)[0]
     ["textRun"].update(content="hello@krishraja.com"), "A7 a footer contact line is in the letter"),
    ("an email in the header", lambda d: runs_of(d, 2)[0]["textRun"].update(
        content=US_BASE.header_line() + " · hello@krishraja.com"), "A10 an email address is in the header"),
])
def test_each_rule_refuses_its_own_break(name, mutate, code):
    got = _break(mutate)
    assert code in got, (name, got)


def test_a_us_role_with_the_london_header_is_refused_without_a_digit():
    got = fails_of(letter(UK_BASE), base=US_BASE)
    assert "A10 the contact line is not the US header line" in got
    assert "A10 the UK number appears in the letter" in got
    assert "A10 the header names the UK place" in got
    for f in got:
        assert not re.search(r"\d{3}", f), f


def test_the_body_may_name_london_for_a_us_role():
    doc = letter()
    runs_of(doc, 13)[0]["textRun"]["content"] = "I built the first team in London."
    assert fails_of(doc) == []


def test_bullets_pages_and_the_last_line():
    assert "A6 the letter has 2 bullets, not 3 or 4" in fails_of(letter(bullets=2))
    assert "A9 the letter renders 2 pages; the limit is 1" in fails_of(letter(), page_count=2)
    doc = letter()
    doc["body"]["content"].pop()
    assert "A7 the letter does not end with his name" in fails_of(doc)


def test_a_cv_keeps_its_heading_sizes_and_may_run_two_pages():
    doc = letter()
    runs_of(doc, 13)[0]["textRun"].update(content="PROFESSIONAL SUMMARY")
    runs_of(doc, 13)[0]["textRun"]["textStyle"]["fontSize"] = {"magnitude": 10.5, "unit": "PT"}
    snap = house.snapshot(doc)
    got = house.check(snap, kind="cv", base=US_BASE, bases=BASES, page_count=2,
                      heading_sizes={"PROFESSIONAL SUMMARY": 10.5})
    assert got == []
    assert "A9 the cv renders 3 pages; the limit is 2" in house.check(
        snap, kind="cv", base=US_BASE, bases=BASES, page_count=3,
        heading_sizes={"PROFESSIONAL SUMMARY": 10.5})


def test_a_run_with_no_font_anywhere_renders_in_docs_default_arial():
    """As his letter does: no family on any run, none on normal text."""
    doc = letter()
    doc["namedStyles"] = {"styles": []}
    assert fails_of(doc) == []
    r = house.snapshot(doc)[2].runs[0]
    assert (r.font, r.size) == ("Arial", 9.0)
