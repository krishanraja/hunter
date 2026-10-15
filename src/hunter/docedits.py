"""Every edit he makes to a letter or CV hunter built, words and formatting (L6).

His ruling, 2026-10-15: every edit is learned from, the words and the
formatting both. Before this, amend.detect_docs kept his whole document as
plain text, cut at 6,000 characters, read only in the twice-weekly batch and
only on Pipeline rows, so a role he applied to (which leaves Pipeline within
the hour) had its edits never read, and a formatting edit was invisible.

Hourly, for every package built in the last WINDOW_DAYS (scratch previews
too): a document is read only when Drive says it changed since the last
look. What it says now is compared with what hunter wrote into it (the style
snapshot kept at build, letters.style_record), and a difference is stored in
hunter_doc_edits: both versions, which paragraphs changed style and how,
whether the words changed, how many characters, whether an Apply email was
open at the time, and whether it was a preview.

An edit after the Apply email went out means the employer would get the
version he has since replaced, so the waiting application is cancelled with
that reason, read back, and Prepare sends his version. His edit is his: no
gate overrules it.

His newest letter edits go to the writer as examples of what he changes
(edit_examples), with his personal lines taken out, never as evidence.
Counts only in every line it prints.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re

from .config import ALL_ROWS, Config, db_get, db_insert, db_patch_where

TABLE = "hunter_doc_edits"
STATE = "hunter_row_state"
WINDOW_DAYS = 30
APPROVALS = "hunter_application_approvals"
OPEN_STATES = ("awaiting", "approved", "amending")
EDITED_AFTER_SEND = ("he edited the documents after the Apply email; Prepare sends "
                     "his version")
KINDS = ("letter", "cv")


def _norm(text: str) -> str:
    return " ".join((text or "").replace(" ", " ").split())


def _style_key(p: dict) -> tuple:
    """A paragraph's formatting without its words: each visible run's font,
    size, weight, colour and link, and its images."""
    runs = tuple(tuple(r[1:]) for r in p.get("runs") or [] if (r[0] or "").strip())
    imgs = tuple(tuple(i) for i in p.get("images") or [])
    return runs, imgs, bool(p.get("bullet"))


def diff(before: list[dict], after: list[dict]) -> dict:
    """What changed between what hunter wrote and what the document holds now."""
    a = [_norm(p.get("text")) for p in before]
    b = [_norm(p.get("text")) for p in after]
    text_a, text_b = "\n".join(a), "\n".join(b)
    chars = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, text_a, text_b,
                                                      autojunk=False).get_opcodes():
        if op != "equal":
            chars += max(i2 - i1, j2 - j1)
    style = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            continue
        for k in range(i2 - i1):
            pb, pa = before[i1 + k], after[j1 + k]
            if _style_key(pb) != _style_key(pa):
                style.append({"paragraph": j1 + k,
                              "before": [list(r) for r in _style_key(pb)[0]],
                              "after": [list(r) for r in _style_key(pa)[0]],
                              "images": [len(pb.get("images") or []), len(pa.get("images") or [])]})
    return {"text_changed": text_a != text_b, "style_changed": bool(style),
            "edit_chars": chars, "style_diff": style}


def _ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_pass(cfg: Config, *, db, apply: bool, now: dt.datetime | None = None,
             snapshot=None) -> dict:
    """Read the documents that changed and store what he did to them.
    snapshot(doc) -> style record; defaults to letters.style_record."""
    from . import letters
    snapshot = snapshot or letters.style_record
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=WINDOW_DAYS)
    out = {"packages": 0, "read": 0, "unchanged": 0, "edited": 0, "words": 0,
           "formatting": 0, "after_send": 0, "cancelled": 0, "scratch": 0, "unreadable": 0}
    rows = db_get(cfg, STATE, {"select": "job_id,letter_doc_id,cv_doc_id,letter_style,cv_style,"
                                         "docs_modified_at,docs_read_at",
                               "letter_doc_id": "not.is.null", "limit": ALL_ROWS})
    rows = [r for r in rows if (_ts(r.get("docs_read_at")) or now) >= since]
    open_by_job: dict[str, list[str]] = {}
    try:
        for a in db_get(cfg, APPROVALS, {"select": "job_id,token,state",
                                         "state": f"in.({','.join(OPEN_STATES)})",
                                         "limit": ALL_ROWS}):
            open_by_job.setdefault(a.get("job_id") or "", []).append(a["token"])
    except Exception:
        open_by_job = {}
    for r in rows:
        out["packages"] += 1
        job = r["job_id"]
        last = _ts(r.get("docs_modified_at"))
        newest = last
        cancel = False
        for kind in KINDS:
            doc_id, base = r.get(f"{kind}_doc_id"), r.get(f"{kind}_style")
            if not doc_id or not base:
                continue
            try:
                modified = db.modified_time(doc_id)
            except Exception:
                out["unreadable"] += 1
                continue
            m = _ts(modified)
            if m is None or (last is not None and m <= last):
                out["unchanged"] += 1
                continue
            newest = max(newest, m) if newest else m
            try:
                after = snapshot(db.get(doc_id))
            except Exception:
                out["unreadable"] += 1
                continue
            out["read"] += 1
            d = diff(base, after)
            if not (d["text_changed"] or d["style_changed"]):
                out["unchanged"] += 1
                continue
            scratch = job.startswith("preview:")
            after_send = bool(open_by_job.get(job)) and not scratch
            out["edited"] += 1
            out["words"] += d["text_changed"]
            out["formatting"] += d["style_changed"]
            out["after_send"] += after_send
            out["scratch"] += scratch
            cancel = cancel or after_send
            if apply:
                db_insert(cfg, TABLE, [{
                    "job_id": job, "doc_kind": kind, "doc_id": doc_id, "modified_at": modified,
                    "before_paragraphs": base, "after_paragraphs": after,
                    "style_diff": d["style_diff"], "text_changed": d["text_changed"],
                    "style_changed": d["style_changed"], "edit_chars": d["edit_chars"],
                    "after_send": after_send, "scratch": scratch}],
                    on_conflict="doc_id,modified_at", ignore_duplicates=True)
        if apply and cancel:
            out["cancelled"] += _cancel(cfg, open_by_job.get(job) or [])
        if apply and newest and newest != last:
            db_patch_where(cfg, STATE, {"job_id": f"eq.{job}"},
                           {"docs_modified_at": newest.isoformat()})
    return out


def _cancel(cfg: Config, tokens: list[str]) -> int:
    """Cancel the waiting applications for a package he has edited since,
    and say how many read back cancelled."""
    from .apply import approval
    for t in tokens:
        approval.set_state(cfg, t, approval.CANCELLED, failure_reason=EDITED_AFTER_SEND)
    if not tokens:
        return 0
    back = db_get(cfg, approval.TABLE, {"select": "token,state",
                                        "token": f"in.({','.join(tokens)})", "limit": "500"})
    return sum(1 for b in back if b.get("state") == approval.CANCELLED)


def lines(counts: dict, *, apply: bool) -> list[str]:
    tail = "" if apply else " (dry run, nothing stored)"
    return [f"doc edits: {counts['packages']} package(s) watched, {counts['read']} document(s) "
            f"read, {counts['edited']} edited ({counts['words']} words, {counts['formatting']} "
            f"formatting, {counts['scratch']} preview), {counts['after_send']} after the Apply "
            f"email, {counts['cancelled']} waiting application(s) cancelled for his version, "
            f"{counts['unreadable']} unreadable{tail}"]


def edit_examples(cfg: Config, bank_words: list[str], *, limit: int = 3) -> str:
    """His newest letter edits as before and after pairs of the paragraphs he
    changed, his personal lines taken out: examples of what he changes,
    never evidence."""
    from .package import compose
    try:
        rows = db_get(cfg, TABLE, {"select": "before_paragraphs,after_paragraphs,noticed_at",
                                   "doc_kind": "eq.letter", "text_changed": "eq.true",
                                   "order": "noticed_at.desc", "limit": str(limit)})
    except Exception:
        return ""
    blocks = []
    for r in rows:
        a = [_norm(p.get("text")) for p in r.get("before_paragraphs") or []]
        b = [_norm(p.get("text")) for p in r.get("after_paragraphs") or []]
        pairs = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            was = compose.strip_his_lines(" ".join(a[i1:i2]), bank_words)
            now = compose.strip_his_lines(" ".join(b[j1:j2]), bank_words)
            if was or now:
                pairs.append(f"HUNTER WROTE: {was or '(nothing)'}\nHE MADE IT: {now or '(cut)'}")
        if pairs:
            blocks.append("\n".join(pairs[:8]))
    return "\n\n".join(blocks)
