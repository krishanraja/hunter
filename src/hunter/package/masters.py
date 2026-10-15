"""Letter master v5: his edited Sonos letter, with slots where the body was.

His ruling, 2026-10-15: the letter is his Sonos letter's layout, typography
and header, with every word but the header and the sign-off written for the
posting. So the master is not rebuilt from a description of his style. It is
a copy of the letter he edited, and only its body changes:

  kept as he wrote them   name, title, link row, banner, rights line, his
                          closing line, his name, the blank spacers
  slots                   {{PLACE}}  ·  {{PHONE}}, {{DATE}}, {{HIRING_LEAD}},
                          {{COMPANY}}, Re: {{ROLE}}, Dear {{HIRING_LEAD}},
                          {{HOOK}}, {{IDENTITY}}, {{BULLET_1}} to {{BULLET_4}},
                          {{WHY_NOW}}, {{CLOSE}}

Each slot is written with an explicit style ("*" mask), measured from his
letter, never inherited from whatever character sat beside it. The margins
come down from his 52pt and 54pt, a step at a time, until his own letter
measures one page (his words: "Cover Letter always needs to be one page. You
can narrow the margins if you want"), and never below MARGIN_FLOOR.

Nothing here runs on its own: `python -m hunter.run letter-master --from <id>`
is a dry run that reads and plans, and --apply makes the copy. Making it is a
write to his Drive, so it runs on his yes.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import house

# Colours and sizes from his letter (package/house.py has the gates).
NAVY = {"red": 20 / 255, "green": 33 / 255, "blue": 61 / 255}
GREY = {"red": 90 / 255, "green": 100 / 255, "blue": 114 / 255}
INK = {"red": 26 / 255, "green": 26 / 255, "blue": 26 / 255}

# His letter's margins, and how far they may come down. The plan's floor.
MARGINS = {"marginTop": 52.0, "marginBottom": 52.0, "marginLeft": 54.0, "marginRight": 54.0}
MARGIN_STEPS = (0.0, 4.0, 8.0, 12.0, 16.0)
MARGIN_FLOOR = 36.0

DATE, LEAD, COMPANY, ROLE = "{{DATE}}", "{{HIRING_LEAD}}", "{{COMPANY}}", "{{ROLE}}"
PLACE, PHONE = "{{PLACE}}", "{{PHONE}}"
HOOK, IDENTITY, WHY_NOW, CLOSE = "{{HOOK}}", "{{IDENTITY}}", "{{WHY_NOW}}", "{{CLOSE}}"
CONTACT_LINE = f"{PLACE}  ·  {PHONE}"


def bullet_slot(n: int) -> str:
    return f"{{{{BULLET_{n}}}}}"


class MasterError(RuntimeError):
    """The letter given is not shaped like his Sonos letter. Plain words, no
    text of the letter: the run log is public."""


@dataclass(frozen=True)
class Slot:
    index: int      # paragraph index in the letter
    text: str       # the slot text that replaces the paragraph's words
    bold: bool
    color: dict


def text_style(slot: Slot) -> dict:
    style = {"weightedFontFamily": {"fontFamily": house.FONT, "weight": 700 if slot.bold else 400},
             "fontSize": {"magnitude": house.BODY_PT, "unit": "PT"},
             "foregroundColor": {"color": {"rgbColor": slot.color}}}
    if slot.bold:
        style["bold"] = True
    return style


def plan_slots(paras: list[house.Para]) -> list[Slot]:
    """Where each slot goes in a letter shaped like his, found by what each
    paragraph is, never by counting from the top alone. Refuses a letter
    whose shape differs, naming what is missing."""
    lay = house.layout(paras)
    missing = [n for n in ("name", "title", "contact", "link_row", "banner", "rights")
               if getattr(lay, n) is None]
    if missing:
        raise MasterError(f"the letter's header has no {', '.join(missing)}")
    texts = [house.norm(p.text) for p in paras]
    after = range(lay.rights + 1, len(paras))
    nonempty = [i for i in after if texts[i]]
    re_line = next((i for i in nonempty if texts[i].startswith("Re:")), None)
    salutation = next((i for i in nonempty if texts[i].startswith("Dear ")), None)
    sign_off = next((i for i in nonempty if texts[i] == house.SIGN_OFF), None)
    if None in (re_line, salutation, sign_off):
        raise MasterError("the letter has no Re: line, salutation or closing line of his")
    if not nonempty or texts[nonempty[-1]] != house.SIGNATURE:
        raise MasterError("the letter does not end with his name")
    head = [i for i in nonempty if i < re_line]
    if len(head) != 3:
        raise MasterError(f"expected a date and two recipient lines before Re:, found {len(head)}")
    body = [i for i in nonempty if salutation < i < sign_off]
    bullets = [i for i in body if paras[i].bullet]
    prose = [i for i in body if not paras[i].bullet]
    if len(bullets) != 4 or bullets != list(range(bullets[0], bullets[0] + 4)):
        raise MasterError(f"expected four bullets in one block, found {len(bullets)}")
    before = [i for i in prose if i < bullets[0]]
    later = [i for i in prose if i > bullets[-1]]
    if len(before) != 2 or len(later) != 2:
        raise MasterError("expected a hook and an identity paragraph before the bullets, "
                          "and why-now and a close after them")
    slots = [Slot(lay.contact, CONTACT_LINE, False, GREY),
             Slot(head[0], DATE, True, INK), Slot(head[1], LEAD, True, INK),
             Slot(head[2], COMPANY, True, INK), Slot(re_line, f"Re: {ROLE}", True, NAVY),
             Slot(salutation, f"Dear {LEAD},", False, INK),
             Slot(before[0], HOOK, False, INK), Slot(before[1], IDENTITY, False, INK)]
    slots += [Slot(i, bullet_slot(n + 1), False, INK) for n, i in enumerate(bullets)]
    slots += [Slot(later[0], WHY_NOW, False, INK), Slot(later[1], CLOSE, False, INK)]
    return slots


def slot_requests(doc: dict, slots: list[Slot]) -> list[dict]:
    """The batchUpdate that writes every slot, from the end of the letter
    backwards so no index moves under a later request: each paragraph's words
    are deleted (its mark kept), the slot inserted, and the slot's style set
    whole."""
    body = [el for el in doc["body"]["content"] if "paragraph" in el]
    reqs: list[dict] = []
    for slot in sorted(slots, key=lambda s: -s.index):
        el = body[slot.index]
        start, end = el["startIndex"], el["endIndex"] - 1
        if end > start:
            reqs.append({"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}})
        reqs.append({"insertText": {"location": {"index": start}, "text": slot.text}})
        reqs.append({"updateTextStyle": {
            "range": {"startIndex": start, "endIndex": start + len(slot.text)},
            "textStyle": text_style(slot), "fields": "*"}})
    return reqs


def margin_request(step: float) -> dict:
    style = {k: {"magnitude": max(MARGIN_FLOOR, v - step), "unit": "PT"}
             for k, v in MARGINS.items()}
    return {"updateDocumentStyle": {"documentStyle": style, "fields": ",".join(MARGINS)}}


def template_problems(paras: list[house.Para]) -> list[str]:
    """What is wrong with a finished v5 master: a slot missing or twice, his
    header or sign-off changed, a run off his font or size."""
    text = "\n".join(p.text for p in paras)
    want = {CONTACT_LINE: 1, DATE: 1, LEAD: 2, COMPANY: 1, ROLE: 1, HOOK: 1, IDENTITY: 1,
            WHY_NOW: 1, CLOSE: 1}
    want.update({bullet_slot(n): 1 for n in range(1, 5)})
    out = [f"{slot} appears {text.count(slot)} times, not {n}"
           for slot, n in want.items() if text.count(slot) != n]
    lay = house.layout(paras)
    for name in ("name", "title", "link_row", "banner", "rights"):
        if getattr(lay, name) is None:
            out.append(f"the master lost his {name.replace('_', ' ')}")
    texts = [house.norm(p.text) for p in paras]
    if house.SIGN_OFF not in texts:
        out.append("the master lost his closing line")
    if not [t for t in texts if t] or [t for t in texts if t][-1] != house.SIGNATURE:
        out.append("the master does not end with his name")
    for i, p in enumerate(paras):
        want_pt = (house.NAME_PT if i == lay.name else house.TITLE_PT if i == lay.title
                   else house.BODY_PT)
        for r in p.runs:
            if r.text.strip() and (r.font != house.FONT or r.size != want_pt):
                out.append(f"paragraph {i + 1} is not {house.FONT} {want_pt:g}pt")
                break
    return out


def build_v5(db, specimen_id: str, *, folder_id: str, page_count, apply: bool) -> dict:
    """Plan, and with apply make, the v5 master. Returns counts and, when
    made, the new document's id. page_count(pdf_bytes) measures a render.

    The margins are measured on a copy that still carries his words: the
    master is right only if his own letter fits one page with them."""
    paras = house.snapshot(db.get(specimen_id))
    slots = plan_slots(paras)
    out = {"slots": len(slots), "bullets": sum(1 for s in slots if s.text.startswith("{{BULLET_")),
           "made": "", "margin_step": None, "problems": []}
    if not apply:
        return out
    doc_id = db.copy_master(specimen_id, "Cover Letter master v5", parent_id=folder_id)
    for step in MARGIN_STEPS:
        db.batch(doc_id, [margin_request(step)])
        if page_count(db.export_pdf(doc_id)) <= house.LETTER_PAGES:
            out["margin_step"] = step
            break
    if out["margin_step"] is None:
        out["problems"].append("his letter does not fit one page even at the margin floor")
        out["made"] = doc_id
        return out
    db.batch(doc_id, slot_requests(db.get(doc_id), slots))
    out["problems"] = template_problems(house.snapshot(db.get(doc_id)))
    out["made"] = doc_id
    return out


# ---------- the CV master: his letter's header on his CV ----------
#
# His answers, 2026-10-15: the CV takes the letter's font, header (place and
# phone, no email), link row and banner, with a 9pt body; its headings keep
# their own sizes, in Arial; two pages at most, the margins narrowing first,
# then the banner going, and his CV content never cut. The link row and the
# banner are copied from his letter at run time, so they are his bytes and
# never a second description of them.

CV_MARGIN_FLOOR = MARGIN_FLOOR


def letter_header_parts(letter_doc: dict) -> dict:
    """His link row (each run's text and link) and his banner (its image uri,
    size and link), read from his letter."""
    paras = house.snapshot(letter_doc)
    lay = house.layout(paras)
    if lay.link_row is None or lay.banner is None:
        raise MasterError("his letter has no link row or no banner to copy")
    row = [(r.text.replace("\n", ""), r.link) for r in paras[lay.link_row].runs]
    row = [(t, u) for t, u in row if t]
    img = paras[lay.banner].images[0]
    if not img.uri:
        raise MasterError("his banner has no readable image to copy")
    return {"row": row, "banner": {"uri": img.uri, "width": img.width_pt or 150.0,
                                   "height": img.height_pt or 23.0, "link": img.link}}


def _cv_heading_sizes(paras: list[house.Para]) -> dict[str, float]:
    from .assertions import CV_HEADINGS
    out = {}
    for p in paras:
        t = house.norm(p.text)
        if t in CV_HEADINGS and p.runs:
            out[t] = p.runs[0].size or house.BODY_PT
    return out


def cv_restyle_requests(paras: list[house.Para]) -> list[dict]:
    """Every run to Arial, bold kept: the name and title in his letter's
    styles, the headings at their own sizes, everything else 9pt. No index
    moves, so order does not matter."""
    lay = house.layout(paras)
    headings = _cv_heading_sizes(paras)
    reqs = []
    for i, p in enumerate(paras):
        t = house.norm(p.text)
        for r in p.runs:
            if r.end <= r.start or r.text == "\n":
                continue
            bold = r.bold
            size = house.BODY_PT
            style: dict = {}
            fields = "weightedFontFamily,fontSize,bold"
            if i == lay.name:
                size, bold = house.NAME_PT, True
                style["foregroundColor"] = {"color": {"rgbColor": NAVY}}
                fields += ",foregroundColor"
            elif i == lay.title:
                size, bold = house.TITLE_PT, False
                style["foregroundColor"] = {"color": {"rgbColor": NAVY}}
                fields += ",foregroundColor"
            elif t in headings:
                size = headings[t]
            style.update({"weightedFontFamily": {"fontFamily": house.FONT,
                                                 "weight": 700 if bold else 400},
                          "fontSize": {"magnitude": size, "unit": "PT"}})
            if bold:
                style["bold"] = True
            reqs.append({"updateTextStyle": {"range": {"startIndex": r.start, "endIndex": r.end},
                                             "textStyle": style, "fields": fields}})
    return reqs


def cv_header_requests(paras: list[house.Para], parts: dict) -> list[dict]:
    """The contact line becomes his place and phone slot, and his link row
    and banner go in under it, as two paragraphs of their own."""
    lay = house.layout(paras)
    if lay.contact is None:
        raise MasterError("the CV has no contact line under its title")
    contact = paras[lay.contact]
    s, mark = contact.start, contact.end - 1
    reqs: list[dict] = []
    if mark > s:
        reqs.append({"deleteContentRange": {"range": {"startIndex": s, "endIndex": mark}}})
    reqs.append({"insertText": {"location": {"index": s}, "text": CONTACT_LINE}})
    grey = {"weightedFontFamily": {"fontFamily": house.FONT, "weight": 400},
            "fontSize": {"magnitude": house.BODY_PT, "unit": "PT"},
            "foregroundColor": {"color": {"rgbColor": GREY}}}
    reqs.append({"updateTextStyle": {"range": {"startIndex": s, "endIndex": s + len(CONTACT_LINE)},
                                     "textStyle": grey, "fields": "*"}})
    row_text = "".join(t for t, _ in parts["row"])
    at = s + len(CONTACT_LINE)
    reqs.append({"insertText": {"location": {"index": at}, "text": "\n" + row_text + "\n"}})
    row_start = at + 1
    reqs.append({"updateTextStyle": {"range": {"startIndex": row_start,
                                               "endIndex": row_start + len(row_text)},
                                     "textStyle": grey, "fields": "*"}})
    cursor = row_start
    blue = {"red": 17 / 255, "green": 85 / 255, "blue": 204 / 255}
    for text, url in parts["row"]:
        if url:
            reqs.append({"updateTextStyle": {
                "range": {"startIndex": cursor, "endIndex": cursor + len(text)},
                "textStyle": {"link": {"url": url}, "underline": True,
                              "foregroundColor": {"color": {"rgbColor": blue}}},
                "fields": "link,underline,foregroundColor"}})
        cursor += len(text)
    banner_at = row_start + len(row_text) + 1
    b = parts["banner"]
    reqs.append({"insertInlineImage": {"location": {"index": banner_at}, "uri": b["uri"],
                                       "objectSize": {"width": {"magnitude": b["width"], "unit": "PT"},
                                                      "height": {"magnitude": b["height"], "unit": "PT"}}}})
    if b.get("link"):
        reqs.append({"updateTextStyle": {"range": {"startIndex": banner_at, "endIndex": banner_at + 1},
                                         "textStyle": {"link": {"url": b["link"]}},
                                         "fields": "link"}})
    return reqs


def cv_template_problems(paras: list[house.Para], heading_sizes: dict[str, float], *,
                         banner: bool = True) -> list[str]:
    from .assertions import CV_HEADINGS
    out = []
    text = "\n".join(p.text for p in paras)
    if text.count(CONTACT_LINE.replace("  ", "  ")) != 1:
        out.append("the CV's place and phone slot is missing or twice")
    lay = house.layout(paras)
    for name in ("name", "title", "link_row") + (("banner",) if banner else ()):
        if getattr(lay, name) is None:
            out.append(f"the CV lost his {name.replace('_', ' ')}")
    for h in CV_HEADINGS:
        if h not in text:
            out.append(f"the CV lost its heading {h}")
    header_end = lay.rights if lay.rights is not None else 4
    if house._EMAIL.search(" ".join(p.text for p in paras[:header_end + 1])):
        out.append("an email address is still in the CV header")
    for i, p in enumerate(paras):
        t = house.norm(p.text)
        want = (house.NAME_PT if i == lay.name else house.TITLE_PT if i == lay.title
                else heading_sizes.get(t, house.BODY_PT))
        for r in p.runs:
            if r.text.strip() and (r.font != house.FONT or r.size != want):
                out.append(f"CV paragraph {i + 1} is not {house.FONT} {want:g}pt")
                break
    return out


def build_cv_master(db, cv_id: str, letter_id: str, *, folder_id: str, page_count,
                    apply: bool) -> dict:
    """Plan, and with apply make, the CV master in his house style: a copy of
    the CV master, restyled, his letter's header put on it, then fitted to
    two pages (margins first, then the banner goes)."""
    source = house.snapshot(db.get(cv_id))
    parts = letter_header_parts(db.get(letter_id))
    headings = _cv_heading_sizes(source)
    restyle = cv_restyle_requests(source)
    out = {"runs": len(restyle), "headings": len(headings), "made": "",
           "margin_step": None, "banner_kept": True, "problems": []}
    if not apply:
        return out
    doc_id = db.copy_master(cv_id, "CV master, house style", parent_id=folder_id)
    db.batch(doc_id, restyle)
    db.batch(doc_id, cv_header_requests(house.snapshot(db.get(doc_id)), parts))
    for step in MARGIN_STEPS:
        db.batch(doc_id, [margin_request(step)])
        if page_count(db.export_pdf(doc_id)) <= house.CV_PAGES:
            out["margin_step"] = step
            break
    if out["margin_step"] is None:
        lay = house.layout(house.snapshot(db.get(doc_id)))
        banner = house.snapshot(db.get(doc_id))[lay.banner] if lay.banner is not None else None
        if banner is not None:
            db.batch(doc_id, [{"deleteContentRange": {"range": {"startIndex": banner.start,
                                                                "endIndex": banner.end}}}])
            out["banner_kept"] = False
        if page_count(db.export_pdf(doc_id)) > house.CV_PAGES:
            out["problems"].append("the CV runs over two pages even at the margin floor "
                                   "without the banner")
    out["problems"] += cv_template_problems(house.snapshot(db.get(doc_id)), headings,
                                            banner=out["banner_kept"])
    out["made"] = doc_id
    return out
