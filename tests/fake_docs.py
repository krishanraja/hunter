"""In-memory emulator for the four Docs API request types docbuild emits,
plus the Drive calls build.py needs. Maintains Docs index arithmetic over a
captured documents.get fixture so the bold-run retention tests run offline.

Scope is deliberately narrow: replaceAllText, deleteContentRange, insertText,
updateTextStyle (backgroundColor only), sequential batch semantics. Anything
else raises. The letter master is ASCII plus BMP punctuation, so Python string
positions equal Docs UTF-16 indexes for this corpus; a guard rejects
astral-plane characters rather than miscounting them.
"""
from __future__ import annotations

import copy
import json
from hunter.docbuild import DocBuild


class Char:
    __slots__ = ("ch", "bold", "bg", "meta")

    def __init__(self, ch, bold=False, bg=None, meta=None):
        if ord(ch) > 0xFFFF:
            raise ValueError("astral-plane character; emulator index math would drift")
        self.ch = ch
        self.bold = bold
        self.bg = bg
        self.meta = meta  # paragraph properties, only on '\n'

    def style_key(self):
        return (self.bold, json.dumps(self.bg, sort_keys=True) if self.bg else None)


class FakeDoc:
    def __init__(self, chars, base):
        self.chars = chars
        self.base = base

    @classmethod
    def from_fixture(cls, doc_json):
        chars = []
        base = None
        for el in doc_json["body"]["content"]:
            p = el.get("paragraph")
            if not p:
                continue
            if base is None:
                base = el["startIndex"]
            meta = {
                "bullet": copy.deepcopy(p.get("bullet")),
                "style": p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT"),
            }
            for e in p.get("elements", []):
                tr = e.get("textRun")
                if not tr:
                    continue
                ts = tr.get("textStyle", {})
                for ch in tr["content"]:
                    chars.append(Char(ch, bold=bool(ts.get("bold")),
                                      bg=copy.deepcopy(ts.get("backgroundColor"))))
            if chars and chars[-1].ch == "\n":
                chars[-1].meta = meta
        return cls(chars, base if base is not None else 1)

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
        return {"body": {"content": content}}

    def _render_para(self, start, last):
        meta = self.chars[last].meta or {"bullet": None, "style": "NORMAL_TEXT"}
        elements = []
        run_start = start
        i = start
        while i <= last:
            if (i > run_start
                    and self.chars[i].style_key() != self.chars[run_start].style_key()):
                elements.append(self._render_run(run_start, i))
                run_start = i
            i += 1
        elements.append(self._render_run(run_start, last + 1))
        para = {"elements": elements,
                "paragraphStyle": {"namedStyleType": meta.get("style", "NORMAL_TEXT")}}
        if meta.get("bullet"):
            para["bullet"] = meta["bullet"]
        return {"startIndex": self.base + start, "endIndex": self.base + last + 1,
                "paragraph": para}

    def _render_run(self, start, end):
        c = self.chars[start]
        style = {}
        if c.bold:
            style["bold"] = True
        if c.bg:
            style["backgroundColor"] = c.bg
        return {"startIndex": self.base + start, "endIndex": self.base + end,
                "textRun": {"content": "".join(x.ch for x in self.chars[start:end]),
                            "textStyle": style}}

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
            new = [Char(ch, bold=style_src.bold, bg=copy.deepcopy(style_src.bg))
                   for ch in replacement]
            self.chars[idx:idx + len(needle)] = new
            pos = idx + len(replacement)

    def _delete(self, start, end):
        s, e = start - self.base, end - self.base
        if not (0 <= s < e <= len(self.chars)):
            raise IndexError(f"deleteContentRange out of bounds: {start}..{end}")
        del self.chars[s:e]

    def _insert(self, index, text):
        pos = index - self.base
        if not (0 <= pos <= len(self.chars)):
            raise IndexError(f"insertText out of bounds: {index}")
        ref = self.chars[pos] if pos < len(self.chars) else (
            self.chars[pos - 1] if pos else None)
        bold = ref.bold if (ref and ref.ch != "\n") else False
        bg = copy.deepcopy(ref.bg) if (ref and ref.ch != "\n") else None
        self.chars[pos:pos] = [Char(ch, bold=bold, bg=bg) for ch in text]

    def _update_style(self, body):
        fields = body.get("fields", "")
        if "backgroundColor" not in fields:
            raise NotImplementedError("emulator only supports backgroundColor updates")
        r = body["range"]
        s, e = r["startIndex"] - self.base, r["endIndex"] - self.base
        new_bg = body.get("textStyle", {}).get("backgroundColor")
        for c in self.chars[s:e]:
            c.bg = copy.deepcopy(new_bg)


class FakeDocBuild(DocBuild):
    """DocBuild with the HTTP layer swapped for the emulator."""

    def __init__(self, fixtures: dict[str, dict]):
        super().__init__(access_token="offline")
        self.docs: dict[str, FakeDoc] = {
            doc_id: FakeDoc.from_fixture(j) for doc_id, j in fixtures.items()}
        self._copies = 0
        self.uploaded_pdfs: list[str] = []

    def get(self, doc_id):
        return self.docs[doc_id].to_json()

    def batch(self, doc_id, requests_list):
        if not requests_list:
            return
        for req in requests_list:
            self.docs[doc_id].apply(req)
        return {"replies": [{} for _ in requests_list]}

    def copy_master(self, master_id, title, parent_id=None):
        self._copies += 1
        new_id = f"copy_{self._copies}_{title[:24]}"
        src = self.docs[master_id]
        self.docs[new_id] = FakeDoc(copy.deepcopy(src.chars), src.base)
        return new_id

    def find_by_name(self, name, parent_id):
        return []

    def export_pdf(self, doc_id):
        return b"%PDF-1.4 fake export for tests"

    def upload_pdf(self, name, parent_id, data):
        self.uploaded_pdfs.append(name)
        return f"pdf_{len(self.uploaded_pdfs)}"
