"""docbuild.py - lossless tailoring of Krish's CV and cover letter masters.

Design rule: the master is copied, never rebuilt. Every operation is either a
placeholder text swap (preserves surrounding runs by construction), a formatting
clear on an existing run, or a whole-paragraph delete. Nothing reinserts styled
text, so no bold run can be lost.

Operations that are DELIBERATELY not implemented:
  - reordering CAREER HIGHLIGHTS. Each highlight carries multiple bold runs and
    the Docs API has no move-paragraph call. Delete plus insertText drops the
    runs. Do not add it without a run-preserving implementation.

Verified against the live masters 2026-08-31.
"""
from __future__ import annotations

import json
from typing import Callable

import requests

DOCS = "https://docs.googleapis.com/v1/documents"
DRIVE = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class DocBuild:
    def __init__(self, access_token: str | Callable[[], str]):
        self._token = access_token

    @property
    def h(self) -> dict[str, str]:
        tok = self._token() if callable(self._token) else self._token
        return {"Authorization": "Bearer " + tok}

    # ---------- primitives ----------

    def get(self, doc_id):
        r = requests.get(f"{DOCS}/{doc_id}", headers=self.h, timeout=60)
        r.raise_for_status()
        return r.json()

    def batch(self, doc_id, requests_list):
        if not requests_list:
            return
        r = requests.post(f"{DOCS}/{doc_id}:batchUpdate", headers=self.h,
                          json={"requests": requests_list}, timeout=60)
        r.raise_for_status()
        return r.json()

    def copy_master(self, master_id, title, parent_id=None):
        body = {"name": title}
        if parent_id:
            body["parents"] = [parent_id]
        r = requests.post(f"{DRIVE}/{master_id}/copy", headers=self.h,
                          params={"supportsAllDrives": "true"}, json=body, timeout=60)
        r.raise_for_status()
        return r.json()["id"]

    # ---------- structure ----------

    @staticmethod
    def paragraphs(doc):
        """Every paragraph with its range, text, bullet flag and styled runs."""
        out = []
        for el in doc["body"]["content"]:
            p = el.get("paragraph")
            if not p:
                continue
            runs = []
            for e in p.get("elements", []):
                tr = e.get("textRun")
                if tr:
                    runs.append({
                        "start": e["startIndex"], "end": e["endIndex"],
                        "text": tr["content"],
                        "bold": bool(tr.get("textStyle", {}).get("bold")),
                        "bg": tr.get("textStyle", {}).get("backgroundColor"),
                    })
            out.append({
                "start": el["startIndex"], "end": el["endIndex"],
                "text": "".join(r["text"] for r in runs),
                "bullet": "bullet" in p,
                "style": p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT"),
                "runs": runs,
            })
        return out

    @staticmethod
    def bold_runs(doc):
        return [r["text"].strip()
                for p in DocBuild.paragraphs(doc)
                for r in p["runs"] if r["bold"] and r["text"].strip()]

    # ---------- lossless operations ----------

    def replace_placeholders(self, doc_id, mapping):
        """{{TOKEN}} to value. Preserves the run's own styling by construction."""
        reqs = [{"replaceAllText": {
                    "containsText": {"text": k, "matchCase": True},
                    "replaceText": v}}
                for k, v in mapping.items()]
        self.batch(doc_id, reqs)

    def clear_highlighting(self, doc_id):
        """Strip backgroundColor from every run that carries one.

        Required: the masters highlight placeholders, and replaceAllText keeps
        the highlight on the replacement text. Without this the letter ships
        with coloured blocks in it.
        """
        doc = self.get(doc_id)
        reqs = []
        for p in self.paragraphs(doc):
            for r in p["runs"]:
                if r["bg"]:
                    reqs.append({"updateTextStyle": {
                        "range": {"startIndex": r["start"], "endIndex": r["end"]},
                        "textStyle": {},
                        "fields": "backgroundColor"}})
        self.batch(doc_id, reqs)
        return len(reqs)

    def set_unstyled_paragraph(self, doc_id, anchor, new_text):
        """Replace a paragraph's text. ONLY safe where the paragraph has no
        styled runs. Refuses otherwise, because insertText would drop them."""
        doc = self.get(doc_id)
        target = next((p for p in self.paragraphs(doc)
                       if anchor.lower() in p["text"].lower()), None)
        if not target:
            raise LookupError(f"paragraph not found: {anchor!r}")
        styled = [r for r in target["runs"] if r["bold"] and r["text"].strip()]
        if styled:
            raise RuntimeError(
                f"refusing: paragraph carries {len(styled)} bold run(s), "
                f"rewriting it would lose them: {styled[0]['text'][:40]!r}")
        end = target["end"] - 1  # keep the paragraph mark
        self.batch(doc_id, [
            {"deleteContentRange": {"range": {"startIndex": target["start"], "endIndex": end}}},
            {"insertText": {"location": {"index": target["start"]}, "text": new_text}},
        ])

    def delete_paragraph(self, doc_id, anchor):
        """Remove a whole paragraph, including its mark. Lossless: nothing is
        reinserted, so no run is rebuilt. Returns the bold runs removed, so
        verification can account for them."""
        doc = self.get(doc_id)
        target = next((p for p in self.paragraphs(doc)
                       if anchor.lower() in p["text"].lower()), None)
        if not target:
            raise LookupError(f"paragraph not found: {anchor!r}")
        removed = [r["text"].strip() for r in target["runs"]
                   if r["bold"] and r["text"].strip()]
        self.batch(doc_id, [{"deleteContentRange": {
            "range": {"startIndex": target["start"], "endIndex": target["end"]}}}])
        return removed

    def delete_block(self, doc_id, anchor):
        """Alias for delete_paragraph, used for the DELETE THIS BLOCK note."""
        return self.delete_paragraph(doc_id, anchor)

    # ---------- Drive helpers (P1 additions, still lossless) ----------

    def find_by_name(self, name: str, parent_id: str) -> list[dict]:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{escaped}' and '{parent_id}' in parents and trashed = false"
        r = requests.get(DRIVE, headers=self.h, timeout=60, params={
            "q": q, "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true", "fields": "files(id,name)"})
        r.raise_for_status()
        return r.json().get("files", [])

    def export_pdf(self, doc_id: str) -> bytes:
        r = requests.get(f"{DRIVE}/{doc_id}/export", headers=self.h,
                         params={"mimeType": "application/pdf"}, timeout=120)
        r.raise_for_status()
        return r.content

    def upload_pdf(self, name: str, parent_id: str, data: bytes) -> str:
        meta = json.dumps({"name": name, "parents": [parent_id],
                           "mimeType": "application/pdf"})
        boundary = "hunter_pdf_boundary_7f3a"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{meta}\r\n--{boundary}\r\nContent-Type: application/pdf\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        headers = dict(self.h)
        headers["Content-Type"] = f"multipart/related; boundary={boundary}"
        r = requests.post(DRIVE_UPLOAD, headers=headers, timeout=120,
                          params={"uploadType": "multipart", "supportsAllDrives": "true"},
                          data=body)
        r.raise_for_status()
        return r.json()["id"]
