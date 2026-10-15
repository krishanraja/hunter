"""His house style, read back from a built document (gates A6, A7, A9 to A12).

On 2026-10-15 Krish rewrote the Sonos letter hunter had built and ruled that
every letter and CV carries what he set in it. Measured from his edit (the
Docs HTML export of that letter, read only):

  one font            Arial, every run
  name                23pt bold, navy
  title               11pt, navy
  everything else     9pt; the header lines grey, the body near black
  header              place and phone (no email), then the link row
                      "Bio | LinkedIn | Media channel" with its arrow, then
                      his banner image, then the rights line
  sign-off            his closing line, then his name; no footer contact line
  pages               the letter one, the CV two at most

Everything here reads the finished document, never the requests that made
it (CLAUDE.md 1): the style of each run is resolved the way Docs renders it
(the run's own style, then its paragraph's named style, then the document's
normal text), and a value that cannot be resolved fails rather than passing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FONT = "Arial"
NAME = "KRISH RAJA"
TITLE = "AI-Native Commercial Strategy Leader"
RIGHTS_PREFIX = "Full working rights"
SIGN_OFF = "I look forward to an introductory conversation to see if there is a mutual alignment."
SIGNATURE = "Krish Raja"
# The link row as he wrote it, and where each label goes.
LINK_ROW = "Bio | LinkedIn | Media channel ⬇️"
LINKS = {"Bio": "krishraja.com", "LinkedIn": "linkedin.com/in/krish-raja",
         "Media channel": "makeyourmindup.ai"}
# Sizes by section, in points. The CV's own headings keep the CV master's
# sizes, passed in from its exemplar (check's heading_sizes).
NAME_PT, TITLE_PT, BODY_PT = 23.0, 11.0, 9.0
# The header's colours on his letter: navy for the name and title, grey for
# the contact, link row separators and rights line.
NAVY, GREY = "#14213d", "#5a6472"
LETTER_PAGES, CV_PAGES = 1, 2
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def norm(text: str) -> str:
    """Text as compared: non-breaking spaces as spaces, runs of space as one."""
    return " ".join((text or "").replace(" ", " ").split())


def _url(u: str) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


@dataclass
class Run:
    text: str
    font: str | None
    size: float | None
    bold: bool
    color: str | None
    link: str = ""


@dataclass
class Image:
    object_id: str
    width_pt: float | None
    height_pt: float | None
    link: str = ""


@dataclass
class Para:
    text: str
    runs: list[Run] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    bullet: bool = False


def _hex(color: dict | None) -> str | None:
    rgb = ((color or {}).get("color") or {}).get("rgbColor")
    if rgb is None:
        return None
    return "#" + "".join(f"{round(float(rgb.get(k, 0)) * 255):02x}"
                         for k in ("red", "green", "blue"))


def _pt(dim: dict | None) -> float | None:
    if not dim or "magnitude" not in dim:
        return None
    return round(float(dim["magnitude"]), 1) if dim.get("unit", "PT") == "PT" else None


def _font(ts: dict) -> str | None:
    wff = (ts.get("weightedFontFamily") or {}).get("fontFamily")
    return wff or ts.get("fontFamily")


def _named(doc: dict) -> dict[str, dict]:
    styles = ((doc.get("namedStyles") or {}).get("styles")) or []
    return {s.get("namedStyleType"): (s.get("textStyle") or {}) for s in styles}


def _effective(ts: dict, para_named: dict, normal: dict) -> dict:
    """The run's own style over its paragraph's named style over normal text,
    field by field, as Docs renders it."""
    out = {}
    for layer in (normal, para_named, ts):
        for k, v in (layer or {}).items():
            out[k] = v
    return out


def snapshot(doc: dict) -> list[Para]:
    """Every paragraph of a documents.get body, with each run's effective
    font, size, weight, colour and link, and each inline image with its size
    and link. Section breaks and tables are skipped; his documents have none
    in the body."""
    named = _named(doc)
    normal = named.get("NORMAL_TEXT", {})
    objects = doc.get("inlineObjects") or {}
    out: list[Para] = []
    for el in (doc.get("body") or {}).get("content", []):
        p = el.get("paragraph")
        if not p:
            continue
        pstyle = named.get((p.get("paragraphStyle") or {}).get("namedStyleType", "NORMAL_TEXT"), {})
        para = Para(text="", bullet="bullet" in p)
        for e in p.get("elements", []):
            if "textRun" in e:
                tr = e["textRun"]
                eff = _effective(tr.get("textStyle") or {}, pstyle, normal)
                para.runs.append(Run(
                    text=tr.get("content", ""), font=_font(eff),
                    size=_pt(eff.get("fontSize")), bold=bool(eff.get("bold")),
                    color=_hex(eff.get("foregroundColor")),
                    link=((eff.get("link") or {}).get("url") or "")))
                para.text += tr.get("content", "")
            elif "inlineObjectElement" in e:
                ioe = e["inlineObjectElement"]
                oid = ioe.get("inlineObjectId", "")
                emb = (((objects.get(oid) or {}).get("inlineObjectProperties") or {})
                       .get("embeddedObject") or {})
                size = emb.get("size") or {}
                para.images.append(Image(
                    object_id=oid, width_pt=_pt(size.get("width")),
                    height_pt=_pt(size.get("height")),
                    link=(((ioe.get("textStyle") or {}).get("link") or {}).get("url") or "")))
        out.append(para)
    return out


# ---------- where each part of the header is ----------

@dataclass
class Layout:
    name: int | None = None
    title: int | None = None
    contact: int | None = None
    link_row: int | None = None
    banner: int | None = None
    rights: int | None = None

    @property
    def header_end(self) -> int:
        """The index of the last header paragraph (the rights line), or -1."""
        return self.rights if self.rights is not None else -1


def layout(paras: list[Para]) -> Layout:
    """Find the header's parts by what they say, never by position alone."""
    lay = Layout()
    for i, p in enumerate(paras):
        t = norm(p.text)
        if lay.name is None and t == NAME:
            lay.name = i
        elif lay.title is None and t == TITLE:
            lay.title = i
            if i + 1 < len(paras):
                lay.contact = i + 1
        elif lay.link_row is None and t == norm(LINK_ROW):
            lay.link_row = i
        elif lay.banner is None and p.images and lay.link_row is not None:
            lay.banner = i
        elif lay.rights is None and t.startswith(RIGHTS_PREFIX):
            lay.rights = i
            break
    return lay


# ---------- the gates ----------

def check(paras: list[Para], *, kind: str, base, bases: dict, page_count: int = 0,
          heading_sizes: dict[str, float] | None = None) -> list[str]:
    """Failures, each a gate code and plain words with no digits from either
    phone. kind is "letter" or "cv"; base the role's contact.Base; bases every
    base system_config holds; heading_sizes the CV master's heading sizes by
    heading text. page_count 0 means the document was not rendered."""
    fails: list[str] = []
    lay = layout(paras)
    missing = [n for n in ("name", "title", "contact", "link_row", "banner", "rights")
               if getattr(lay, n) is None]
    if missing:
        fails.append(f"A12 header incomplete: no {', '.join(missing)}")
    header = paras[:lay.header_end + 1] if lay.rights is not None else paras[:6]
    header_text = " ".join(norm(p.text) for p in header)
    full_digits = re.sub(r"\D", "", " ".join(p.text for p in paras))

    # A10: the role's base, and only it.
    if lay.contact is not None and norm(paras[lay.contact].text) != norm(base.header_line()):
        fails.append(f"A10 the contact line is not the {base.key.upper()} header line")
    for k, other in bases.items():
        if k == base.key:
            continue
        for ph in (other.phone_display, other.phone_international):
            d = re.sub(r"\D", "", ph)
            if len(d) >= 7 and d in full_digits:
                fails.append(f"A10 the {k.upper()} number appears in the {kind}")
                break
        # Places in the body may be his history ("I built the team in
        # London"); only the header claims where he is.
        if re.search(rf"\b{re.escape(other.place)}\b", header_text):
            fails.append(f"A10 the header names the {k.upper()} place")
    if _EMAIL.search(header_text):
        fails.append("A10 an email address is in the header")

    # A11: one font, his sizes, his colours, and no bold where he has none.
    for i, p in enumerate(paras):
        for r in p.runs:
            if not r.text.strip():
                continue
            if r.font != FONT:
                fails.append(f"A11 a run is not {FONT}" + (" (unknown)" if r.font is None else ""))
                break
        want = (NAME_PT if i == lay.name else TITLE_PT if i == lay.title
                else (heading_sizes or {}).get(norm(p.text)) if kind == "cv"
                and norm(p.text) in (heading_sizes or {}) else BODY_PT)
        for r in p.runs:
            if r.text.strip() and r.size != want:
                fails.append(f"A11 paragraph {i + 1} is not {want:g}pt")
                break
    for i, colour in ((lay.name, NAVY), (lay.title, NAVY), (lay.contact, GREY),
                      (lay.rights, GREY)):
        if i is not None and any(r.text.strip() and r.color != colour for r in paras[i].runs):
            fails.append(f"A11 header paragraph {i + 1} is not in his colour")
    if kind == "letter":
        sal = next((i for i, p in enumerate(paras)
                    if i > lay.header_end and norm(p.text).startswith("Dear ")), None)
        if sal is None:
            fails.append("A7 no salutation")
        else:
            if any(r.bold for r in paras[sal].runs if r.text.strip()):
                fails.append("A11 the salutation is bold")
            hook = next((j for j in range(sal + 1, len(paras)) if norm(paras[j].text)), None)
            if hook is not None and any(r.bold for r in paras[hook].runs if r.text.strip()):
                fails.append("A11 the opening paragraph is bold")

    # A12: the link row and banner, exactly his.
    if lay.link_row is not None:
        got = {}
        for r in paras[lay.link_row].runs:
            label = norm(r.text)
            if label in LINKS:
                got[label] = _url(r.link)
        for label, where in LINKS.items():
            if got.get(label) != where:
                fails.append(f"A12 the {label} link is missing or wrong")
    if lay.banner is not None and not paras[lay.banner].images:
        fails.append("A12 the banner is missing")

    # A7: his sign-off, his name, and no footer contact line.
    if kind == "letter":
        texts = [norm(p.text) for p in paras]
        if SIGN_OFF not in texts:
            fails.append("A7 his closing line is missing")
        tail = [t for t in texts if t]
        if not tail or tail[-1] != SIGNATURE:
            fails.append("A7 the letter does not end with his name")
        body = " ".join(texts[lay.header_end + 1:])
        if _EMAIL.search(body) or "krishraja.com" in body.lower():
            fails.append("A7 a footer contact line is in the letter")
        bullets = sum(1 for p in paras if p.bullet)
        if bullets not in (3, 4):
            fails.append(f"A6 the letter has {bullets} bullets, not 3 or 4")

    # A9: pages.
    limit = LETTER_PAGES if kind == "letter" else CV_PAGES
    if page_count > limit:
        fails.append(f"A9 the {kind} renders {page_count} pages; the limit is {limit}")
    return fails
