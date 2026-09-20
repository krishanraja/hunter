"""When hunter is allowed to interrupt Krish, and what it says when it does.

Krish 2026-09-19: "I get an email alert when a new batch of roles is ready for
my review in the pipeline".

Until now the run summary went to stdout, which meant an Actions log nobody
reads. The loop he asked for has exactly one human step in it, and a human step
nobody is told about is not a loop at all: roles sat unreviewed not because he
declined to review them but because nothing said they were there.

Two alerts, and deliberately only two:

  REVIEW READY, after a sourcing run that staged something. It says how many,
  shows the strongest, links straight at the tab, and states what happens once
  he has set column A. This is the one that closes the loop.

  SOMETHING IS WRONG, when the system is not doing its job: no successful run
  in eight days, an approval sitting unanswered, a sourcing run that found
  nothing twice running, an invariant that could not be repaired. This is the
  one that stops a silent stall lasting a fortnight.

Every alert is deduplicated through hunter_alerts on a fingerprint that
describes the condition, not the moment. The same stall reported hourly is
noise, and noise is how an inbox alert stops being read, so the second copy is
never sent. A condition that clears and returns is a new fingerprint and is
sent again.

This module composes and decides. notify.py is still the only thing that can
put a message on the wire, and it can still only reach Krish.
"""
from __future__ import annotations

import datetime
import html as html_mod

from . import config as config_mod
from . import notify, verdicts
from .config import Config, db_get, db_insert

TABLE = "hunter_alerts"

REVIEW_READY = "review_ready"
TROUBLE = "trouble"

# How long the system may be quiet before silence is itself the problem. The
# schedule is Sunday and Thursday, so eight days means a whole cycle was missed.
STALL_DAYS = 8
# An approval he has not answered. Two days is generous for a role that is live
# now and gone in a fortnight.
APPROVAL_DAYS = 2


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _age_days(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return (_now() - t).total_seconds() / 86400.0


def already_sent(cfg: Config, kind: str, fingerprint: str) -> bool:
    rows = db_get(cfg, TABLE, {"select": "id", "kind": f"eq.{kind}",
                               "fingerprint": f"eq.{fingerprint}", "limit": "1"})
    return bool(rows)


def mark_sent(cfg: Config, kind: str, fingerprint: str, detail: dict) -> None:
    db_insert(cfg, TABLE, [{"kind": kind, "fingerprint": fingerprint,
                            "detail": detail}],
              on_conflict="kind,fingerprint", ignore_duplicates=True)


def pipeline_url() -> str:
    return (f"https://docs.google.com/spreadsheets/d/{config_mod.WORKBOOK_ID}"
            f"/edit#gid={config_mod.PIPELINE_SHEET_ID}")


def _esc(text) -> str:
    return html_mod.escape(str(text or ""))


# ---------- the batch is ready ----------

def batch_state(rows) -> dict:
    """What the sheet holds right now, in his language."""
    new = approved = applied = dead_approved = 0
    for r in rows:
        kind = verdicts.parse(r.verdict or "")[0]
        if kind == "none":
            new += 1
        elif kind == "go":
            approved += 1
            from .sheet import PKG_DEAD
            if r.cell("Package Status").strip() == PKG_DEAD:
                dead_approved += 1
        elif kind == "applied":
            applied += 1
    return {"to_review": new, "approved": approved, "applied": applied,
            "dead_approved": dead_approved, "total": len(rows)}


def review_email(rows, staged: int, *, state: dict | None = None,
                 top: int = 12, notes: list[str] | None = None,
                 batches=None) -> tuple[str, str, str]:
    """(subject, html, text) for the batch he has to review."""
    st = state or batch_state(rows)
    # The funnel's own report card, in front of him every week. He should
    # never again be the monitoring system for his own accept rate.
    if batches:
        from . import batchstats
        notes = list(notes or []) + batchstats.lines(batches)
    fresh = [r for r in rows if verdicts.parse(r.verdict or "")[0] == "none"]

    def score_of(r):
        try:
            return int(float(r.cell("Score") or 0))
        except (TypeError, ValueError):
            return 0

    fresh.sort(key=score_of, reverse=True)
    subject = (f"{staged} new role{'s' if staged != 1 else ''} to review, "
               f"{st['to_review']} waiting in total")

    head = [
        f"<p style='margin:0 0 14px'>Open the Pipeline tab and set column A on "
        f"each row. That is the whole job: everything after it happens on its "
        f"own.</p>",
        f"<p style='margin:0 0 18px'><a href='{pipeline_url()}' "
        f"style='background:#111;color:#fff;padding:10px 16px;border-radius:6px;"
        f"text-decoration:none;display:inline-block'>Open the pipeline</a></p>",
        f"<p style='margin:0 0 18px;color:#555'>{st['to_review']} to review, "
        f"{st['approved']} approved and waiting on materials, "
        f"{st['applied']} applied.</p>",
    ]

    rowsx = []
    for r in fresh[:top]:
        rowsx.append(
            "<tr>"
            f"<td style='padding:6px 10px 6px 0;font-weight:600'>{score_of(r)}</td>"
            f"<td style='padding:6px 10px 6px 0'><b>{_esc(r.company)}</b><br>"
            f"<span style='color:#444'>{_esc(r.role)}</span></td>"
            f"<td style='padding:6px 10px 6px 0;color:#555'>{_esc(r.cell('Location'))}"
            f"<br>{_esc(r.cell('Comp'))}</td>"
            f"<td style='padding:6px 0;color:#444'>{_esc(r.cell('Why It Fits')[:220])}</td>"
            "</tr>")
    table = ("<table style='border-collapse:collapse;font:14px/1.45 -apple-system,"
             "Segoe UI,Helvetica,Arial,sans-serif'>"
             "<tr style='text-align:left;color:#888;font-size:12px'>"
             "<th style='padding-bottom:6px'>Score</th><th>Role</th>"
             "<th>Where and what</th><th>Why hunter staged it</th></tr>"
             + "".join(rowsx) + "</table>") if rowsx else ""

    tail = ["<p style='margin:18px 0 0;color:#555'>Set a row to Yes and hunter "
            "builds the CV, the cover letter and the filled application, then "
            "emails you the pack to press send on. Decline with a reason and it "
            "learns the reason. Anything you edit, it reads as a correction.</p>"]
    if st["dead_approved"]:
        tail.insert(0, f"<p style='margin:18px 0 0;color:#a00'>"
                       f"{st['dead_approved']} role(s) you approved have closed "
                       f"since. They stay on the sheet until you decide.</p>")
    for n in (notes or []):
        tail.append(f"<p style='margin:10px 0 0;color:#555'>{_esc(n)}</p>")

    body = ("<div style='font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,"
            "sans-serif;color:#111;max-width:760px'>"
            + "".join(head) + table + "".join(tail) + "</div>")

    text_lines = [f"{staged} new roles staged. {st['to_review']} waiting for you.",
                  pipeline_url(), ""]
    for r in fresh[:top]:
        text_lines.append(f"  {score_of(r)}  {r.company} - {r.role} "
                          f"({r.cell('Location')}, {r.cell('Comp')})")
    text_lines += ["", "Set column A. Yes builds the pack, a reason teaches it."]
    return subject, body, "\n".join(text_lines)


def send_review_ready(cfg: Config, rows, staged: int, *,
                      notes: list[str] | None = None, force: bool = False,
                      batches=None) -> dict:
    """Mail him the batch, once per batch.

    The fingerprint is the date plus the number waiting, so two runs on one day
    that both stage roles send one mail unless the count moved, and a rerun of
    the same run sends nothing at all.
    """
    st = batch_state(rows)
    if not staged and not force:
        return {"sent": False, "reason": "nothing new was staged"}
    fp = f"{_now():%Y-%m-%d}:{st['to_review']}"
    if not force and already_sent(cfg, REVIEW_READY, fp):
        return {"sent": False, "reason": "already told him about this batch"}
    subject, body, text = review_email(rows, staged, state=st, notes=notes,
                                       batches=batches)
    out = notify.send_email(cfg, subject, body, text=text)
    mark_sent(cfg, REVIEW_READY, fp, {"staged": staged, **st})
    return {"sent": bool(out.get("sent")), "subject": subject, **st}


# ---------- something is wrong ----------

def trouble_checks(cfg: Config) -> list[dict]:
    """Conditions that mean the loop has stopped turning. Read only."""
    out: list[dict] = []

    runs = db_get(cfg, "workflow_runs",
                  {"select": "run_at,status,outcome,workflow_name,error_message",
                   "agent_id": "eq.hunter", "order": "run_at.desc", "limit": "12"})
    ok_runs = [r for r in runs if str(r.get("status") or "").lower()
               in ("success", "ok", "completed")]
    age = _age_days(ok_runs[0]["run_at"]) if ok_runs else None
    if age is None:
        out.append({"kind": "no runs on record", "fingerprint": "no-runs",
                    "detail": "workflow_runs has no successful hunter run at all"})
    elif age > STALL_DAYS:
        out.append({"kind": "the schedule has stopped",
                    "fingerprint": f"stalled:{int(age)}d",
                    "detail": f"the last successful run was {age:.0f} days ago. "
                              f"Check the Actions schedule and the repository "
                              f"secrets."})
    fails = [r for r in runs[:3] if str(r.get("status") or "").lower()
             in ("failed", "error")]
    if len(fails) >= 2:
        why = (fails[0].get("error_message") or "")[:160]
        out.append({"kind": "runs are failing",
                    "fingerprint": f"failing:{str(fails[0].get('run_at'))[:10]}",
                    "detail": f"{len(fails)} of the last 3 runs failed. {why}"})

    awaiting = db_get(cfg, "hunter_application_approvals",
                      {"select": "token,company,role,created_at",
                       "state": "eq.awaiting", "order": "created_at.asc",
                       "limit": "50"})
    old = [a for a in awaiting if (_age_days(a.get("created_at")) or 0) > APPROVAL_DAYS]
    if old:
        names = ", ".join(f"{a.get('company')} {a.get('role')}" for a in old[:4])
        out.append({"kind": "applications waiting on you",
                    "fingerprint": f"awaiting:{len(old)}:{str(old[0].get('created_at'))[:10]}",
                    "detail": f"{len(old)} filled application(s) have been waiting "
                              f"more than {APPROVAL_DAYS} days: {names}"})

    recent = runs[:4]
    empty = [r for r in recent
             if isinstance(r.get("outcome"), dict)
             and (r["outcome"] or {}).get("staged") == 0
             and (r["outcome"] or {}).get("discovered", 0) > 0]
    if len(empty) >= 2:
        out.append({"kind": "sourcing is finding nothing",
                    "fingerprint": f"empty-sourcing:{str(recent[0].get('run_at'))[:10]}",
                    "detail": f"{len(empty)} recent run(s) read postings and staged "
                              f"none. Either the gates are too tight or a source "
                              f"changed shape."})
    return out


def trouble_email(problems: list[dict]) -> tuple[str, str, str]:
    subject = (f"hunter needs you: {problems[0]['kind']}" if len(problems) == 1
               else f"hunter needs you: {len(problems)} things")
    items = "".join(f"<li style='margin-bottom:10px'><b>{_esc(p['kind'])}</b><br>"
                    f"<span style='color:#444'>{_esc(p['detail'])}</span></li>"
                    for p in problems)
    body = ("<div style='font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,"
            "sans-serif;color:#111;max-width:760px'>"
            "<p style='margin:0 0 14px'>The pipeline is not turning on its own. "
            "Each of these stops roles reaching you.</p>"
            f"<ul style='padding-left:18px'>{items}</ul>"
            f"<p style='margin:14px 0 0'><a href='{pipeline_url()}'>Open the "
            f"pipeline</a></p></div>")
    text = "\n".join([f"- {p['kind']}: {p['detail']}" for p in problems])
    return subject, body, text


def send_trouble(cfg: Config, *, force: bool = False) -> dict:
    problems = trouble_checks(cfg)
    if not problems:
        return {"sent": False, "problems": 0}
    fresh = [p for p in problems
             if force or not already_sent(cfg, TROUBLE, p["fingerprint"])]
    if not fresh:
        return {"sent": False, "problems": len(problems),
                "reason": "every condition has already been reported"}
    subject, body, text = trouble_email(fresh)
    out = notify.send_email(cfg, subject, body, text=text)
    for p in fresh:
        mark_sent(cfg, TROUBLE, p["fingerprint"], {"kind": p["kind"]})
    return {"sent": bool(out.get("sent")), "problems": len(fresh),
            "kinds": [p["kind"] for p in fresh]}
