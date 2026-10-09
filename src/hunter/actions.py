"""What Krish pressed in Control Center, applied by hunter.

The one place he acts is Control Center (docs/ONE_SYSTEM.md). Its buttons do
not write the sheet: only hunter holds Google credentials, and two writers on
column A is how rows disagree. A press inserts a row in hunter_actions; the
next drain applies it here and records what happened, and Control Center reads
that result back onto the card.

  verdict   payload {verdict: yes | declined | applied, reason: <label>}
            Written to column A as exactly what he would have typed there, so
            settle, reconcile and the learning loop treat it as his ruling.
  prepare   build and send the "Apply:" email for a role he said Yes to
            (approvals --apply for that job). It prepares; he presses Submit.
  outcome   payload {outcome: interview | offer | rejected | withdrew}
            What happened after he applied, on the role row.

Nothing here sends a message to anyone but him, and nothing presses Submit.
"""
from __future__ import annotations

import datetime as dt

from . import verdicts
from .config import ALL_ROWS, Config, db_get, db_patch

TABLE = "hunter_actions"
OUTCOMES = {"interview": "Interviewing", "offer": "Offer", "rejected": "Rejected by employer",
            "withdrew": "Withdrew"}


def column_a(payload: dict) -> str:
    """The column A text for a verdict press, or '' when it is not one he could type."""
    v = (payload.get("verdict") or "").strip().lower()
    if v == "yes":
        return verdicts.BUILD
    if v == "applied":
        return verdicts.APPLIED
    if v == "declined":
        label = (payload.get("reason") or "").strip()
        if label in verdicts.LABEL_TO_CODE:
            return f"{verdicts.DECLINE_PREFIX}{label}"
    return ""


def pipeline_row(rows: list, db_row: dict):
    """His Pipeline row for this role: the posting's link first, then company
    and title only when exactly one row carries them."""
    urls = {u for u in (db_row.get("url"), db_row.get("job_url")) if u}
    hit = [x for x in rows if x.jd_url and x.jd_url in urls]
    if hit:
        return hit[0]
    same = [x for x in rows if x.company.lower() == (db_row.get("company") or "").lower()
            and x.role.lower() == (db_row.get("title") or "").lower()]
    return same[0] if len(same) == 1 else None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def apply_queued(cfg: Config, sheet, canon_headers, *, apply: bool = False,
                 prepare=None) -> list[str]:
    """Apply every queued press, oldest first. Returns summary lines.

    prepare(job_id) -> (ok, note) builds and sends the approval email; it is a
    parameter so the tests run offline."""
    queued = db_get(cfg, TABLE, {"select": "*", "status": "eq.queued",
                                 "order": "requested_at.asc", "limit": ALL_ROWS})
    if not queued:
        return ["actions: none waiting"]
    lines = [f"actions: {len(queued)} waiting" + ("" if apply else " (dry run)")]
    rows = None
    for a in queued:
        jid = a["job_id"]
        role = db_get(cfg, "hunter_seen_roles", {
            "select": "job_id,company,title,url,job_url,application_state",
            "job_id": f"eq.{jid}", "limit": "1"})
        if not role:
            ok, note = False, "no such role"
        elif a["kind"] == "verdict":
            text = column_a(a.get("payload") or {})
            if not text:
                ok, note = False, f"not a verdict column A accepts: {a.get('payload')}"
            else:
                if rows is None:
                    rows = sheet.read_pipeline(canon_headers)
                row = pipeline_row(rows, role[0])
                if row is None:
                    ok, note = False, "not on Pipeline any more; it may have been archived"
                elif not apply:
                    ok, note = True, f"would write {text!r} to row {row.row_number}"
                else:
                    sheet.set_verdicts({row.row_number: text})  # reads column A back
                    ok, note = True, f"wrote {text!r} to row {row.row_number}"
        elif a["kind"] == "prepare":
            if not apply:
                ok, note = True, "would prepare the application"
            else:
                ok, note = (prepare or (lambda j: _prepare(j)))(jid)
        elif a["kind"] == "outcome":
            out = OUTCOMES.get(((a.get("payload") or {}).get("outcome") or "").lower())
            if not out:
                ok, note = False, f"unknown outcome {a.get('payload')}"
            elif not apply:
                ok, note = True, f"would record {out!r}"
            else:
                db_patch(cfg, "hunter_seen_roles", {"job_id": jid}, {"application_state": out})
                back = db_get(cfg, "hunter_seen_roles", {"select": "application_state",
                                                         "job_id": f"eq.{jid}"})
                ok = bool(back) and back[0].get("application_state") == out
                note = f"recorded {out!r}" if ok else "written, but the read-back did not find it"
        else:
            ok, note = False, f"unknown kind {a['kind']!r}"
        lines.append(f"  {a['kind']} {jid}: {note}")
        if apply:
            db_patch(cfg, TABLE, {"id": a["id"]}, {
                "status": "done" if ok else "failed", "result": note[:500],
                "processed_at": _now()})
    return lines


def _prepare(job_id: str) -> tuple[bool, str]:
    """approvals --apply for one job: the filled form, the screenshot and the
    "Apply:" email to him. Its exit code is the only honest signal it gives."""
    from .run import cmd_approvals
    code = cmd_approvals(apply=True, job_id=job_id)
    return (code == 0, "application prepared; the email is on its way to you" if code == 0
            else f"could not prepare it (approvals exited {code}); see the run log")
