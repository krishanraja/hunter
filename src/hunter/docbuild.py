"""docbuild.py - lossless tailoring of Krish's CV and cover letter masters.

Design rule: the master is copied, never rebuilt. Every operation is either a
placeholder text swap (preserves surrounding runs by construction), a formatting
clear on an existing run, or a whole-paragraph delete. Nothing reinserts styled
text, so no bold run can be lost.

reorder_paragraphs (added 2026-09-14) is the one operation that does rebuild
runs, and it is allowed only because it re-applies them deterministically and
then PROVES it: it reads the document back and asserts the multiset of bolded
substrings is identical to what it captured. Canon 9.12 requires career
highlights to be reordered per role, and refusing the operation meant that
requirement had never once been met. Verification is what earns the right to do
it, not the absence of the operation.

Verified against the live masters 2026-08-31, reorder added 2026-09-14.
"""
from __future__ import annotations

import json
from typing import Callable

import requests

DOCS = "https://docs.googleapis.com/v1/documents"
DRIVE = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def _coalesce(ranges):
    """Merge touching or overlapping (start, end) pairs, so a block of bullets is
    created as one list rather than several adjacent ones."""
    out = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


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

    def replace_paragraph_block(self, doc_id, first_anchor, count, new_text, *,
                                allow_styled=False, bold_substrings=()):
        """Replace `count` consecutive paragraphs, starting at the one matching
        first_anchor, with a single block of text.

        Used for the PROFESSIONAL SUMMARY, where swapping only the first of three
        paragraphs left the master's own two behind and shipped a CV in two
        registers.

        By default it REFUSES if any paragraph in the span carries a bold run, so
        it can never be pointed at the employer entries or the highlights by
        accident. Krish's master bolds "14-agent autonomous AI operating system"
        in summary paragraph 2, which tripped that guard on the first real build.
        Replacing the summary is deliberate, and canon 9.12 says numbers stay bold
        inside the prose, so the caller acknowledges the styled span with
        allow_styled and passes the substrings to bold in the NEW text. The old
        runs are gone because the old words are gone; the convention is carried
        over rather than lost.

        new_text may contain newlines; each becomes its own paragraph.
        """
        doc = self.get(doc_id)
        paras = self.paragraphs(doc)
        idx = next((i for i, p in enumerate(paras)
                    if first_anchor.lower() in p["text"].lower()), None)
        if idx is None:
            raise LookupError(f"paragraph not found: {first_anchor!r}")
        span = paras[idx:idx + count]
        if len(span) < count:
            raise RuntimeError(
                f"only {len(span)} paragraphs available from {first_anchor!r}, "
                f"needed {count}")
        for p in span:
            styled = [r for r in p["runs"] if r["bold"] and r["text"].strip()]
            if styled and not allow_styled:
                raise RuntimeError(
                    f"refusing to replace the block: paragraph "
                    f"{p['text'][:40]!r} carries {len(styled)} bold run(s). Pass "
                    f"allow_styled=True only where the replacement is deliberate, "
                    f"with bold_substrings for the new text.")
        start = span[0]["start"]
        end = span[-1]["end"] - 1  # keep the final paragraph mark
        self.batch(doc_id, [
            {"deleteContentRange": {"range": {"startIndex": start,
                                              "endIndex": end}}},
            {"insertText": {"location": {"index": start}, "text": new_text}},
        ])
        # Carry the master's convention onto the new prose, then prove it landed.
        # Drop any target contained in a longer one: bolding "$55M" and then
        # "$55M automated marketplace" merges them into a single run, after which
        # the narrower target no longer exists as a run and the check below would
        # report a loss that did not happen. Widest phrase wins.
        candidates = [b for b in bold_substrings if b and b in new_text]
        wanted = [b for b in candidates
                  if not any(b != other and b in other for other in candidates)]
        if wanted:
            reqs = []
            for frag in wanted:
                at = new_text.index(frag)
                reqs.append({"updateTextStyle": {
                    "range": {"startIndex": start + at,
                              "endIndex": start + at + len(frag)},
                    "textStyle": {"bold": True}, "fields": "bold"}})
            self.batch(doc_id, reqs)
            after = self.get(doc_id)
            # Containment, not equality: Docs may merge or split runs around the
            # applied ranges, so what matters is that the phrase IS bold, not that
            # it is exactly one run.
            bold_text = " \n ".join(
                r["text"] for p in self.paragraphs(after) for r in p["runs"]
                if r["bold"] and r["text"].strip())
            missing = [f for f in wanted if f.strip() not in bold_text]
            if missing:
                raise RuntimeError(
                    f"bold did not land on {missing!r} in the replaced block")
        return len(span)

    def reorder_paragraphs(self, doc_id, anchors, order):
        """Reorder a run of consecutive paragraphs, preserving bold runs.

        anchors identifies the block: a list of substrings, one per paragraph, in
        the document's current order. order is a permutation of range(len).

        The Docs API has no move-paragraph call, so this deletes the block and
        reinserts it, then re-applies every captured bold range at its new
        offset, then reads the document back and asserts the bolded substrings
        are IDENTICAL to what was captured. Not the count: the actual strings. A
        mismatch raises, because a CV with the bold on the wrong number is worse
        than one in the master's order.
        """
        if sorted(order) != list(range(len(anchors))):
            raise ValueError(f"order must be a permutation of "
                             f"0..{len(anchors) - 1}, got {order}")
        doc = self.get(doc_id)
        paras = self.paragraphs(doc)
        # Resolve each anchor AFTER the previous one, not from the top. The anchors
        # arrive in document order and the highlights are the last block that
        # carries them, but since the PROFESSIONAL SUMMARY became generated prose it
        # can legitimately repeat a highlight's opening words: a real build on
        # 2026-09-15 matched anchor 2 to the summary paragraph and failed here with
        # "paragraphs are not contiguous: [9, 7, ...]". A forward scan cannot match
        # backwards into text that only looks like the anchor.
        found, cursor = [], 0
        for a in anchors:
            hit = next((i for i in range(cursor, len(paras))
                        if a.lower() in paras[i]["text"].lower()), None)
            if hit is None:
                raise LookupError(f"paragraph not found at or after index "
                                  f"{cursor}: {a[:40]!r}")
            found.append(paras[hit])
            cursor = hit + 1
        # Must be contiguous, or a reorder would move unrelated content.
        positions = [paras.index(p) for p in found]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise RuntimeError(f"paragraphs are not contiguous: {positions}")

        # Capture each paragraph's text and its bold ranges as offsets INTO that
        # paragraph, so they survive being moved.
        captured = []
        for p in found:
            text = p["text"]
            # Bullets matter as much as bold. insertText creates PLAIN paragraphs,
            # so the first real Harvey build reordered the highlights correctly and
            # silently stripped the bullet from all seven; the package verifier
            # caught it as "expected 26 bullets, found 19". Capture the flag and
            # restore it, then assert it came back.
            bolds = []
            for r in p["runs"]:
                if r["bold"] and r["text"].strip():
                    rel_start = r["start"] - p["start"]
                    bolds.append((rel_start, rel_start + len(r["text"]),
                                  r["text"]))
            captured.append({"text": text, "bolds": bolds,
                             "bullet": bool(p["bullet"])})
        expected_bold = sorted(b[2].strip() for c in captured for b in c["bolds"])

        start = found[0]["start"]
        end = found[-1]["end"]
        new_texts = [captured[i]["text"] for i in order]
        payload = "".join(t if t.endswith("\n") else t + "\n" for t in new_texts)
        self.batch(doc_id, [
            {"deleteContentRange": {"range": {"startIndex": start,
                                              "endIndex": end}}},
            {"insertText": {"location": {"index": start}, "text": payload}},
        ])

        # Re-apply bold AND the bullet at the new offsets, computed from the
        # payload we built.
        reqs = []
        bullet_ranges = []
        cursor = start
        for i in order:
            cap = captured[i]
            body = cap["text"]
            length = len(body if body.endswith("\n") else body + "\n")
            for rel_start, rel_end, _ in cap["bolds"]:
                reqs.append({"updateTextStyle": {
                    "range": {"startIndex": cursor + rel_start,
                              "endIndex": cursor + rel_end},
                    "textStyle": {"bold": True},
                    "fields": "bold"}})
            if cap["bullet"]:
                bullet_ranges.append((cursor, cursor + length))
            cursor += length
        # One contiguous run of bullets is the normal case (a highlight block), so
        # coalesce rather than issuing one request per paragraph: createParagraphBullets
        # on adjacent ranges separately can renumber them into different lists.
        for rng_start, rng_end in _coalesce(bullet_ranges):
            reqs.append({"createParagraphBullets": {
                "range": {"startIndex": rng_start, "endIndex": rng_end},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}})
        if reqs:
            self.batch(doc_id, reqs)

        # The assertion that earns the operation. It must look ONLY at the
        # paragraphs that moved: a substring filter over the whole document also
        # picks up the generated summary's own bolded numbers, which share phrases
        # with the highlights, and reports a loss that did not happen. Found on the
        # first real Harvey build.
        after = self.get(doc_id)
        wanted_texts = {c["text"].strip() for c in captured}
        moved = [p for p in self.paragraphs(after)
                 if p["text"].strip() in wanted_texts]
        if len(moved) != len(captured):
            raise RuntimeError(
                f"after reorder, found {len(moved)} of {len(captured)} moved "
                f"paragraphs; refusing to assert against a partial block")
        expected_bullets = sum(1 for c in captured if c["bullet"])
        got_bullets = sum(1 for p in moved if p["bullet"])
        if got_bullets != expected_bullets:
            raise RuntimeError(
                f"reorder lost bullet formatting: expected {expected_bullets} "
                f"bulleted paragraphs, got {got_bullets}")
        got_bold = sorted(r["text"].strip()
                          for p in moved for r in p["runs"]
                          if r["bold"] and r["text"].strip())
        if got_bold != expected_bold:
            raise RuntimeError(
                "reorder lost or moved bold runs; expected "
                f"{expected_bold!r}, got {got_bold!r}")
        return len(order)

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
