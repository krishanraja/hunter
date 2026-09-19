"""Learning from what Krish CHANGED, not only from what he rejected.

learn.py reads column A: go, applied, or a rejection with a reason. That is one
bit of signal per role and it only fires when he says no. Everything else he
does to the sheet was invisible: a title hunter got wrong and he corrected, a
Why It Fits he rewrote because the argument was weak, a score he overrode, a
cover letter paragraph he replaced before sending. Those are the highest value
corrections in the system, because they are him showing the right answer rather
than rejecting the wrong one, and until now every one of them was thrown away
on the next run when hunter rewrote the cell.

Krish 2026-09-19: "the system ALWAYS learns from what I amended or Rejected".

How it works. Every time hunter writes to a row it also records what it wrote,
in hunter_row_state. On the next run the live cell is compared with that record.
A difference can only have come from him, so it is an amendment: before, after,
and the field, appended to hunter_amendments and quoted in the learning report.

Two rules keep this honest:

  hunter never learns from its own output. A cell hunter rewrote since the
  snapshot is re-snapshotted at the moment of the write, so its own edit can
  never present as his.

  one amendment is evidence, not a rule. A single correction is reported and
  quoted. A pattern across several roles is what earns a proposal, and a
  proposal still waits for his approval, exactly as taste codes do in learn.py.
"""
from __future__ import annotations

import hashlib
import re

from .config import Config, db_get, db_insert, db_patch

TABLE = "hunter_amendments"
STATE_TABLE = "hunter_row_state"

# The cells hunter writes that carry an argument or a fact he might correct.
# Verdict is not here: column A is his by definition and learn.py owns it.
# Package cells are not here either; they are links, not prose.
WATCHED = ("Role", "JD Snippet", "Score", "Why It Fits", "Location", "Comp")

# A difference that is formatting rather than a correction: a trailing space,
# a smart quote a paste turned straight, the dash style Google Docs puts in.
# These are normalised away before anything is compared, because otherwise a
# document round trip would present as him rewriting his own letter.
PUNCTUATION = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
               "\u2013": "-", "\u2014": "-", "\u00a0": " "}
WHITESPACE = re.compile(r"\s+")

# What each field's amendment means, in one plain sentence for the report.
MEANING = {
    "Role": "hunter had the job title wrong",
    "JD Snippet": "hunter's summary of the job was wrong or useless",
    "Score": "his score disagrees with the scorer",
    "Why It Fits": "he rewrote the argument, so the argument was weak",
    "Location": "hunter had the location wrong",
    "Comp": "hunter had the pay wrong",
    "cover letter": "he edited the letter before sending it",
    "cv": "he edited the CV before sending it",
}


def norm(text: str) -> str:
    """What counts as the same text. Whitespace and punctuation style do not."""
    t = str(text or "")
    for bad, good in PUNCTUATION.items():
        t = t.replace(bad, good)
    return WHITESPACE.sub(" ", t).strip()


def fields_of(row) -> dict:
    """The watched cells of a SheetRow, as hunter would store them."""
    return {name: norm(row.cell(name)) for name in WATCHED}


# ---------- what hunter wrote ----------

def save_fields(cfg: Config, job_id: str, fields: dict) -> None:
    """Record what hunter just wrote to a row. Call this at the moment of the
    write, never later: the gap between the write and the record is the window
    in which hunter's own edit could be read back as his."""
    if not job_id:
        return
    db_insert(cfg, STATE_TABLE,
              [{"job_id": job_id, "fields": fields, "written_at": _now()}],
              on_conflict="job_id")


def save_docs(cfg: Config, job_id: str, *, letter_text: str = "",
              cv_text: str = "") -> None:
    if not job_id:
        return
    patch = {"docs_read_at": _now()}
    if letter_text:
        patch["letter_text"] = letter_text[:60000]
    if cv_text:
        patch["cv_text"] = cv_text[:60000]
    rows = db_get(cfg, STATE_TABLE, {"select": "job_id", "job_id": f"eq.{job_id}"})
    if rows:
        db_patch(cfg, STATE_TABLE, {"job_id": job_id}, patch)
    else:
        db_insert(cfg, STATE_TABLE, [dict(patch, job_id=job_id, fields={})],
                  on_conflict="job_id")


def load_state(cfg: Config, job_ids: list[str] | None = None) -> dict[str, dict]:
    params = {"select": "job_id,fields,letter_text,cv_text,written_at",
              "limit": "5000"}
    rows = db_get(cfg, STATE_TABLE, params)
    out = {r["job_id"]: r for r in rows if r.get("job_id")}
    if job_ids is None:
        return out
    want = set(job_ids)
    return {k: v for k, v in out.items() if k in want}


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------- what he changed ----------

def detect(rows, paired: dict, state: dict[str, dict]) -> list[dict]:
    """Amendments visible on the sheet right now.

    rows are SheetRows, paired maps row number to the DB row so an amendment
    carries a job_id, and state is what hunter last wrote. A row hunter has no
    record for produces nothing: absence of a record is not evidence of an
    edit, and guessing there would file an amendment for every row the system
    inherited from the incumbent.
    """
    out: list[dict] = []
    for r in rows:
        d = paired.get(r.row_number) or {}
        job_id = d.get("job_id")
        if not job_id:
            continue
        rec = state.get(job_id)
        if not rec:
            continue
        was = rec.get("fields") or {}
        now = fields_of(r)
        for name in WATCHED:
            before, after = was.get(name), now.get(name)
            if before is None or after is None or before == after:
                continue
            if not after:
                continue  # a cleared cell is a deletion, not a correction
            out.append({"job_id": job_id, "company": r.company, "title": r.role,
                        "field": name, "before_text": before[:2000],
                        "after_text": after[:2000],
                        "lesson": MEANING.get(name, "")})
    return out


def detect_docs(cfg: Config, read_doc, rows, paired: dict,
                state: dict[str, dict], *, limit: int = 40) -> list[dict]:
    """Amendments inside the CV and cover letter documents.

    read_doc(url) returns the document's plain text or "" when it cannot be
    read. Only rows that have a package and a stored text are compared, and a
    document that cannot be read is skipped rather than reported as changed:
    a permissions blip must never present as Krish rewriting his letter.
    """
    out: list[dict] = []
    looked = 0
    for r in rows:
        if looked >= limit:
            break
        d = paired.get(r.row_number) or {}
        job_id = d.get("job_id")
        rec = state.get(job_id or "")
        if not rec:
            continue
        for field, cell, stored in (("cover letter", "Cover Letter Doc", "letter_text"),
                                    ("cv", "CV Doc", "cv_text")):
            was = rec.get(stored)
            if not was:
                continue
            url = _url_of(r.cell(cell))
            if not url:
                continue
            looked += 1
            text = read_doc(url)
            if not text:
                continue
            if norm(text) == norm(was):
                continue
            out.append({"job_id": job_id, "company": r.company, "title": r.role,
                        "field": field, "before_text": was[:6000],
                        "after_text": text[:6000],
                        "lesson": MEANING.get(field, "")})
    return out


def _url_of(cell: str) -> str:
    from .sheet import parse_hyperlink
    hit = parse_hyperlink(str(cell or ""))
    if hit:
        return hit[0]
    c = str(cell or "").strip()
    return c if c.startswith("http") else ""


def record(cfg: Config, amendments: list[dict]) -> int:
    """Append, unique on (job_id, field, the text he ended up with). An
    amendment re-read on every run records once, and changing his mind back
    records the second change as its own event.

    after_hash is stored rather than derived in the index because PostgREST's
    on_conflict names columns and cannot name an expression.
    """
    if not amendments:
        return 0
    payload = [dict(a, after_hash=hashlib.md5(
        (a.get("after_text") or "").encode("utf-8")).hexdigest())
        for a in amendments]
    db_insert(cfg, TABLE, payload, on_conflict="job_id,field,after_hash",
              ignore_duplicates=True)
    return len(payload)


def sync_after(cfg: Config, rows, paired: dict) -> int:
    """Re-snapshot every row from what the sheet says now.

    Runs at the END of a pass, after amendments have been recorded and after
    hunter's own writes. From here on, any difference is his again.
    """
    payload = []
    for r in rows:
        d = paired.get(r.row_number) or {}
        if not d.get("job_id"):
            continue
        payload.append({"job_id": d["job_id"], "fields": fields_of(r),
                        "written_at": _now()})
    if not payload:
        return 0
    for i in range(0, len(payload), 500):
        db_insert(cfg, STATE_TABLE, payload[i:i + 500], on_conflict="job_id")
    return len(payload)


# ---------- reading it back ----------

def score_calibration(amendments: list[dict]) -> str:
    """Does he score higher or lower than the scorer, and by how much."""
    deltas = []
    for a in amendments:
        if a.get("field") != "Score":
            continue
        try:
            deltas.append(int(float(a["after_text"])) - int(float(a["before_text"])))
        except (TypeError, ValueError):
            continue
    if len(deltas) < 3:
        return ""
    avg = sum(deltas) / len(deltas)
    if abs(avg) < 0.75:
        return ""
    direction = "higher" if avg > 0 else "lower"
    return (f"you score {abs(avg):.1f} points {direction} than the scorer across "
            f"{len(deltas)} corrections; the scorer needs recalibrating, not you")


def report_lines(cfg: Config, *, limit: int = 12) -> list[str]:
    """What he changed, quoted back, most recent first."""
    rows = db_get(cfg, TABLE, {"select": "*", "order": "noticed_at.desc",
                               "limit": str(max(limit, 60))})
    if not rows:
        return ["amendments: none on record yet"]
    lines = [f"amendments on record: {len(rows)}"]
    cal = score_calibration(rows)
    if cal:
        lines.append(f"  calibration: {cal}")
    by_field: dict[str, int] = {}
    for r in rows:
        by_field[r.get("field") or "?"] = by_field.get(r.get("field") or "?", 0) + 1
    lines.append("  by field: " + ", ".join(
        f"{k} {v}" for k, v in sorted(by_field.items(), key=lambda x: -x[1])))
    for r in rows[:limit]:
        before = (r.get("before_text") or "")[:70]
        after = (r.get("after_text") or "")[:70]
        lines.append(f"  {r.get('company')} {r.get('field')}: {before!r} "
                     f"became {after!r}")
    return lines


def proposals(amendments: list[dict], *, floor: int = 3) -> list[dict]:
    """A pattern worth asking him about. One correction is evidence; this is
    what repeats. Nothing here is applied; it becomes a workflow_proposals row
    and waits, the same contract taste codes have."""
    by_field: dict[str, list[dict]] = {}
    for a in amendments:
        by_field.setdefault(a.get("field") or "?", []).append(a)
    out = []
    for field, group in by_field.items():
        if len(group) < floor:
            continue
        out.append({
            "kind": "amendment pattern",
            "field": field,
            "count": len(group),
            "question": f"You corrected {field} on {len(group)} roles. "
                        f"{MEANING.get(field, 'Something upstream is wrong.')} "
                        f"Should hunter change how it writes that cell?",
            "examples": [f"{a.get('company')}: {(a.get('before_text') or '')[:60]!r} "
                         f"became {(a.get('after_text') or '')[:60]!r}"
                         for a in group[:4]],
        })
    return out
