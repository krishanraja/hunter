"""In-memory emulator for the four Docs API request types docbuild emits,
plus the Drive calls build.py needs. Maintains Docs index arithmetic over a
captured documents.get fixture so the bold-run retention tests run offline.

Scope is deliberately narrow: replaceAllText, deleteContentRange, insertText,
updateTextStyle, updateParagraphStyle and updateDocumentStyle (each with a
field mask, "*" included), sequential batch semantics. Anything else raises.

Since 2026-10-15 every character carries its whole textStyle, every
paragraph its whole paragraphStyle and bullet, and an inline image is one
index, as in Docs: his house style (package/house.py) is read back from
fonts, sizes, colours, links and his banner, so an emulator that kept only
bold would pass a document that dropped all of them. It refuses what the API
refuses: an empty range, an unknown field in a mask, an output-only key. The letter master is ASCII plus BMP punctuation, so Python string
positions equal Docs UTF-16 indexes for this corpus; a guard rejects
astral-plane characters rather than miscounting them.
"""
from __future__ import annotations

import copy
import io
import json
from hunter.docbuild import DocBuild


# One index in Docs, and never matched as text.
OBJECT = "\ufffc"
TEXT_FIELDS = {"bold", "italic", "underline", "strikethrough", "smallCaps",
               "backgroundColor", "foregroundColor", "fontSize", "weightedFontFamily",
               "baselineOffset", "link"}
PARAGRAPH_FIELDS = {"namedStyleType", "alignment", "lineSpacing", "direction",
                    "spacingMode", "spaceAbove", "spaceBelow", "indentFirstLine",
                    "indentStart", "indentEnd", "keepLinesTogether", "keepWithNext",
                    "avoidWidowAndOrphan", "shading", "borderTop", "borderBottom",
                    "borderLeft", "borderRight", "borderBetween"}
# Set by Docs, refused in a request.
OUTPUT_ONLY = {"headingId"}
DOCUMENT_FIELDS = {"marginTop", "marginBottom", "marginLeft", "marginRight",
                   "pageSize", "background", "useFirstPageHeaderFooter"}
# What Docs gives a link when the request sets no colour or underline of its own.
LINK_BLUE = {"color": {"rgbColor": {"red": 0.06666667, "green": 0.33333334,
                                    "blue": 0.8}}}


def _mask(fields: str, known: set) -> set:
    names = {f.strip() for f in (fields or "").split(",") if f.strip()}
    if not names:
        raise ValueError("an update needs a field mask")
    if names == {"*"}:
        return set(known)
    if names & OUTPUT_ONLY:
        raise ValueError(f"output-only field in the mask: {sorted(names & OUTPUT_ONLY)}")
    unknown = names - known
    if unknown:
        raise NotImplementedError(f"emulator does not know the field(s) {sorted(unknown)}")
    return names


class Char:
    __slots__ = ("ch", "style", "meta", "obj")

    def __init__(self, ch, bold=False, bg=None, meta=None, style=None, obj=None):
        if ord(ch) > 0xFFFF:
            raise ValueError("astral-plane character; emulator index math would drift")
        self.ch = ch
        self.style = copy.deepcopy(style) if style is not None else {}
        if bold:
            self.style["bold"] = True
        if bg:
            self.style["backgroundColor"] = copy.deepcopy(bg)
        self.meta = meta  # paragraph properties, only on '\n'
        self.obj = obj    # inline object id, only on OBJECT

    @property
    def bold(self):
        return bool(self.style.get("bold"))

    @bold.setter
    def bold(self, value):
        if value:
            self.style["bold"] = True
        else:
            self.style.pop("bold", None)

    @property
    def bg(self):
        return self.style.get("backgroundColor")

    @bg.setter
    def bg(self, value):
        if value:
            self.style["backgroundColor"] = copy.deepcopy(value)
        else:
            self.style.pop("backgroundColor", None)

    def style_key(self):
        return (json.dumps(self.style, sort_keys=True), self.obj)


class FakeDoc:
    def __init__(self, chars, base, extra=None):
        self.chars = chars
        self.base = base
        # namedStyles, inlineObjects, documentStyle: carried through to_json.
        self.extra = copy.deepcopy(extra) if extra else {}

    @classmethod
    def from_fixture(cls, doc_json):
        chars = []
        base = None
        for el in doc_json["body"]["content"]:
            p = el.get("paragraph")
            if not p:
                continue
            if base is None:
                base = el.get("startIndex", 1)
            pstyle = copy.deepcopy(p.get("paragraphStyle") or {})
            pstyle.setdefault("namedStyleType", "NORMAL_TEXT")
            meta = {"bullet": copy.deepcopy(p.get("bullet")), "paragraphStyle": pstyle}
            for e in p.get("elements", []):
                if "inlineObjectElement" in e:
                    ioe = e["inlineObjectElement"]
                    chars.append(Char(OBJECT, style=ioe.get("textStyle") or {},
                                      obj=ioe.get("inlineObjectId")))
                    continue
                tr = e.get("textRun")
                if not tr:
                    continue
                ts = tr.get("textStyle", {})
                for ch in tr["content"]:
                    chars.append(Char(ch, style=ts))
            if chars and chars[-1].ch == "\n":
                chars[-1].meta = meta
        extra = {k: doc_json[k] for k in ("namedStyles", "inlineObjects", "documentStyle")
                 if k in doc_json}
        return cls(chars, base if base is not None else 1, extra)

    # ---------- rendering back to Docs API shape ----------

    def text(self):
        return "".join(c.ch for c in self.chars)

    def to_json(self):
        content = []
        if self.base > 0:
            content.append({"endIndex": self.base, "sectionBreak": {}})
        para_start = 0
        i = 0
        while i < len(self.chars):
            if self.chars[i].ch == "\n":
                content.append(self._render_para(para_start, i))
                para_start = i + 1
            i += 1
        if para_start < len(self.chars):  # trailing paragraph without newline
            content.append(self._render_para(para_start, len(self.chars) - 1))
        out = {"body": {"content": content}}
        out.update(copy.deepcopy(self.extra))
        return out

    def _render_para(self, start, last):
        meta = self.chars[last].meta or {"bullet": None,
                                         "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}
        elements = []
        run_start = start
        i = start
        while i <= last:
            if i > run_start and (
                    self.chars[i].obj or self.chars[run_start].obj
                    or self.chars[i].style_key() != self.chars[run_start].style_key()):
                elements.append(self._render_run(run_start, i))
                run_start = i
            i += 1
        elements.append(self._render_run(run_start, last + 1))
        para = {"elements": elements, "paragraphStyle": copy.deepcopy(meta["paragraphStyle"])}
        if meta.get("bullet"):
            para["bullet"] = meta["bullet"]
        return {"startIndex": self.base + start, "endIndex": self.base + last + 1,
                "paragraph": para}

    def _render_run(self, start, end):
        c = self.chars[start]
        if c.obj:
            return {"startIndex": self.base + start, "endIndex": self.base + start + 1,
                    "inlineObjectElement": {"inlineObjectId": c.obj,
                                            "textStyle": copy.deepcopy(c.style)}}
        return {"startIndex": self.base + start, "endIndex": self.base + end,
                "textRun": {"content": "".join(x.ch for x in self.chars[start:end]),
                            "textStyle": copy.deepcopy(c.style)}}

    # ---------- the four operations ----------

    def apply(self, req):
        if "replaceAllText" in req:
            self._replace_all(req["replaceAllText"])
        elif "deleteContentRange" in req:
            r = req["deleteContentRange"]["range"]
            self._delete(r["startIndex"], r["endIndex"])
        elif "insertText" in req:
            self._insert(req["insertText"]["location"]["index"], req["insertText"]["text"])
        elif "updateTextStyle" in req:
            self._update_style(req["updateTextStyle"])
        elif "updateParagraphStyle" in req:
            self._update_paragraph(req["updateParagraphStyle"])
        elif "createParagraphBullets" in req:
            body = req["createParagraphBullets"]
            s, e = self._range(body["range"])
            for i in range(s, e):
                j = next((k for k in range(i, len(self.chars)) if self.chars[k].ch == "\n"), None)
                if j is not None:
                    c = self.chars[j]
                    c.meta = c.meta or {"bullet": None,
                                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}
                    c.meta["bullet"] = {"listId": "kix.created"}
                    # A new list brings its own indents, as in Docs.
                    c.meta["paragraphStyle"]["indentStart"] = {"magnitude": 36, "unit": "PT"}
                    c.meta["paragraphStyle"]["indentFirstLine"] = {"magnitude": 18, "unit": "PT"}
        elif "insertInlineImage" in req:
            body = req["insertInlineImage"]
            if not str(body.get("uri", "")).startswith("https://"):
                raise ValueError("insertInlineImage needs an https uri")
            pos = body["location"]["index"] - self.base
            if not (0 <= pos <= len(self.chars)):
                raise IndexError(f"insertInlineImage out of bounds: {body['location']}")
            objects = self.extra.setdefault("inlineObjects", {})
            oid = f"kix.inserted{len(objects) + 1}"
            objects[oid] = {"objectId": oid, "inlineObjectProperties": {"embeddedObject": {
                "size": copy.deepcopy(body.get("objectSize") or {}),
                "imageProperties": {"contentUri": body["uri"]}}}}
            self.chars[pos:pos] = [Char(OBJECT, obj=oid)]
        elif "updateDocumentStyle" in req:
            body = req["updateDocumentStyle"]
            names = _mask(body.get("fields"), DOCUMENT_FIELDS)
            style = self.extra.setdefault("documentStyle", {})
            for f in names:
                if f in body.get("documentStyle", {}):
                    style[f] = copy.deepcopy(body["documentStyle"][f])
                else:
                    style.pop(f, None)
        else:
            raise NotImplementedError(f"emulator does not support: {list(req)}")

    def _replace_all(self, body):
        needle = body["containsText"]["text"]
        replacement = body["replaceText"]
        if not body["containsText"].get("matchCase", False):
            raise NotImplementedError("emulator only supports matchCase=true")
        pos = 0
        while True:
            idx = self.text().find(needle, pos)
            if idx < 0:
                break
            style_src = self.chars[idx]
            new = [Char(ch, style=style_src.style) for ch in replacement]
            self.chars[idx:idx + len(needle)] = new
            pos = idx + len(replacement)

    def _range(self, r):
        s, e = r["startIndex"] - self.base, r["endIndex"] - self.base
        if s >= e:
            raise ValueError(f"the range should not be empty: {r}")
        if not (0 <= s < e <= len(self.chars)):
            raise IndexError(f"range out of bounds: {r}")
        return s, e

    def _delete(self, start, end):
        s, e = start - self.base, end - self.base
        if s == e:
            raise ValueError("the range should not be empty")
        if not (0 <= s < e <= len(self.chars)):
            raise IndexError(f"deleteContentRange out of bounds: {start}..{end}")
        del self.chars[s:e]

    def _insert(self, index, text):
        pos = index - self.base
        if not (0 <= pos <= len(self.chars)):
            raise IndexError(f"insertText out of bounds: {index}")
        ref = self.chars[pos] if pos < len(self.chars) else (
            self.chars[pos - 1] if pos else None)
        style = ref.style if (ref and ref.ch != "\n" and not ref.obj) else (
            {k: v for k, v in ref.style.items() if k not in ("bold", "backgroundColor")}
            if ref else {})
        # A newline splits its paragraph, and both halves keep its style and
        # bullet: the paragraph mark is the next newline at or after pos.
        end = next((c for c in self.chars[pos:] if c.ch == "\n"), None)
        para_meta = copy.deepcopy(end.meta) if end is not None and end.meta else None
        self.chars[pos:pos] = [Char(ch, style=style,
                                    meta=copy.deepcopy(para_meta) if ch == "\n" else None)
                               for ch in text]

    def _update_style(self, body):
        """backgroundColor for clear_highlighting, bold for the bold_substrings
        that replace_paragraph_block reapplies after a replacement. Bold was
        NotImplementedError here until 2026-09-15, which meant the bold-carryover
        path of replace_paragraph_block could not be exercised offline at all: the
        CV fixture happens to have no money phrases, so `wanted` was always empty
        and the emulator was never asked. The hook swap in build_letter asks.
        """
        names = _mask(body.get("fields", ""), TEXT_FIELDS)
        style = body.get("textStyle", {})
        s, e = self._range(body["range"])
        default_link = ("link" in names and style.get("link")
                        and not ({"foregroundColor", "underline"} & names))
        for c in self.chars[s:e]:
            for f in names:
                if f in style and style[f] not in (None, False):
                    c.style[f] = copy.deepcopy(style[f])
                else:
                    c.style.pop(f, None)
            if default_link:
                c.style["foregroundColor"] = copy.deepcopy(LINK_BLUE)
                c.style["underline"] = True

    def _update_paragraph(self, body):
        names = _mask(body.get("fields", ""), PARAGRAPH_FIELDS)
        style = body.get("paragraphStyle", {})
        if set(style) & OUTPUT_ONLY:
            raise ValueError(f"output-only key in the style: {sorted(set(style) & OUTPUT_ONLY)}")
        s, e = self._range(body["range"])
        # Every paragraph the range touches: its mark is the next newline at
        # or after each position.
        marks = set()
        for i in range(s, e):
            j = next((k for k in range(i, len(self.chars)) if self.chars[k].ch == "\n"), None)
            if j is not None:
                marks.add(j)
        for j in marks:
            c = self.chars[j]
            c.meta = c.meta or {"bullet": None, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}
            ps = c.meta["paragraphStyle"]
            for f in names:
                if f in style:
                    ps[f] = copy.deepcopy(style[f])
                else:
                    ps.pop(f, None)


class FakeDocBuild(DocBuild):
    """DocBuild with the HTTP layer swapped for the emulator."""

    def __init__(self, fixtures: dict[str, dict], allowed=("f",)):
        super().__init__(access_token="offline")
        self.docs: dict[str, FakeDoc] = {
            doc_id: FakeDoc.from_fixture(j) for doc_id, j in fixtures.items()}
        self._copies = 0
        self.uploaded_pdfs: list[str] = []
        # A fake registered folder "f" under a fake root, so a test that builds
        # into "f" passes the Drive guard and one that names no folder does not.
        self.allowed = set(allowed) if allowed is not None else None
        self.parent_of: dict[str, list[str]] = {
            "f": ["home"], "apps": ["home"], "masters": ["home"], "prev": ["home"],
            "home": ["cur"], "inbox": ["cur"], "cur": ["root"]}
        self.folders: dict[tuple[str, str], str] = {}
        self.moves: list[tuple[str, str]] = []
        # What export_pdf claims the rendered page count is. See export_pdf.
        self.export_pages: int | list[int] = 1
        self.exports: list[str] = []

    def get(self, doc_id):
        return self.docs[doc_id].to_json()

    def batch(self, doc_id, requests_list):
        if not requests_list:
            return
        for req in requests_list:
            self.docs[doc_id].apply(req)
        return {"replies": [{} for _ in requests_list]}

    def _copy(self, master_id, title, parent_id):
        self._copies += 1
        new_id = f"copy_{self._copies}_{title[:24]}"
        src = self.docs[master_id]
        self.docs[new_id] = FakeDoc(copy.deepcopy(src.chars), src.base, src.extra)
        self.parent_of[new_id] = [parent_id]
        return new_id

    def find_by_name(self, name, parent_id, mime=""):
        fid = self.folders.get((parent_id, name))
        return [{"id": fid, "name": name}] if fid else []

    def _make_folder(self, name, parent_id):
        fid = f"F{len(self.folders) + 1}"
        self.folders[(parent_id, name)] = fid
        self.parent_of[fid] = [parent_id]
        return fid

    def parents_of(self, file_id):
        return list(self.parent_of.get(file_id, []))

    def children(self, folder_id):
        meta = getattr(self, "meta", {})
        return [{"id": fid, **meta.get(fid, {"name": fid})}
                for fid, ps in self.parent_of.items() if folder_id in ps]

    def _reparent(self, file_id, to_parent, old):
        self.moves.append((file_id, to_parent))
        self.parent_of[file_id] = [to_parent]

    def export_pdf(self, doc_id):
        """A real, parseable PDF, because build_letter now measures its pages to
        hold canon 9.12's one-page rule and a fake blob cannot be measured.

        `export_pages` controls what the renderer claims: an int for every call,
        or a list consumed one entry per call with the last value repeating, which
        is how a test drives the hook shrink ladder down a rung.
        """
        from pypdf import PdfWriter

        plan = self.export_pages
        if isinstance(plan, list):
            n = plan.pop(0) if len(plan) > 1 else (plan[0] if plan else 1)
        else:
            n = plan
        writer = PdfWriter()
        for _ in range(max(1, int(n))):
            writer.add_blank_page(width=612, height=792)
        out = io.BytesIO()
        writer.write(out)
        self.exports.append(doc_id)
        return out.getvalue()

    def _upload(self, name, parent_id, data):
        self.uploaded_pdfs.append(name)
        fid = f"pdf_{len(self.uploaded_pdfs)}"
        self.parent_of[fid] = [parent_id]
        return fid
