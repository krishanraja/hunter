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
sent again; a sheet header change has no date in its fingerprint, so its mark
is deleted when the tab reads clean (forget_cleared), and the same change made
again is sent again.

This module composes and decides. notify.py is still the only thing that can
put a message on the wire, and it can still only reach Krish.
"""
from __future__ import annotations

import datetime
import hashlib
import html as html_mod
import json

from . import config as config_mod
from . import notify, verdicts
from .config import Config, db_delete, db_get, db_insert
from .sheet import AI_SCORE, AI_WHY

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


def hunt_url(cfg) -> str:
    """The Hunt lane in Control Center, where he rules on roles and opens
    applications (docs/ONE_SYSTEM.md, layer 4). '' when the address is not set,
    and then the email falls back to the sheet alone."""
    base = (cfg.optional("control_center_url") or "").rstrip("/") if cfg else ""
    return f"{base}/#/people?lane=bridges" if base else ""


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
                 batches=None, act_url: str = "") -> tuple[str, str, str]:
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
            return int(float(r.cell(AI_SCORE) or 0))
        except (TypeError, ValueError):
            return 0

    fresh.sort(key=score_of, reverse=True)
    subject = (f"{staged} new role{'s' if staged != 1 else ''} to review, "
               f"{st['to_review']} waiting in total")

    if act_url:
        # One place he acts: the Hunt lane, where each role carries its case and
        # Yes or No is one press. The sheet stays the record and is one link away.
        head = [
            "<p style='margin:0 0 14px'>Open the Hunt lane and say Yes or No on "
            "each role. That is the whole job: everything after it happens on its "
            "own, and the sheet is kept up to date for you.</p>",
            f"<p style='margin:0 0 18px'><a href='{act_url}' "
            f"style='background:#111;color:#fff;padding:10px 16px;border-radius:6px;"
            f"text-decoration:none;display:inline-block'>Rule on them in Control Center</a>"
            f"<span style='color:#555'> or <a href='{pipeline_url()}'>open the pipeline</a>"
            f"</span></p>",
        ]
    else:
        head = [
            f"<p style='margin:0 0 14px'>Open the Pipeline tab and set column A on "
            f"each row. That is the whole job: everything after it happens on its "
            f"own.</p>",
            f"<p style='margin:0 0 18px'><a href='{pipeline_url()}' "
            f"style='background:#111;color:#fff;padding:10px 16px;border-radius:6px;"
            f"text-decoration:none;display:inline-block'>Open the pipeline</a></p>",
        ]
    head.append(
        f"<p style='margin:0 0 18px;color:#555'>{st['to_review']} to review, "
        f"{st['approved']} approved and waiting on materials, "
        f"{st['applied']} applied.</p>")

    rowsx = []
    for r in fresh[:top]:
        rowsx.append(
            "<tr>"
            f"<td style='padding:6px 10px 6px 0;font-weight:600'>{score_of(r)}</td>"
            f"<td style='padding:6px 10px 6px 0'><b>{_esc(r.company)}</b><br>"
            f"<span style='color:#444'>{_esc(r.role)}</span></td>"
            f"<td style='padding:6px 10px 6px 0;color:#555'>{_esc(r.cell('Location'))}"
            f"<br>{_esc(r.cell('Comp'))}</td>"
            f"<td style='padding:6px 0;color:#444'>{_esc(r.cell(AI_WHY)[:220])}</td>"
            "</tr>")
    table = ("<table style='border-collapse:collapse;font:14px/1.45 -apple-system,"
             "Segoe UI,Helvetica,Arial,sans-serif'>"
             "<tr style='text-align:left;color:#888;font-size:12px'>"
             "<th style='padding-bottom:6px'>AI score</th><th>Role</th>"
             "<th>Where and what</th><th>Why hunter staged it</th></tr>"
             + "".join(rowsx) + "</table>") if rowsx else ""

    # The pack used to be promised by email on its own. Nothing scheduled
    # sends it: he asks for it, with Prepare on the role in Control Center.
    tail = ["<p style='margin:18px 0 0;color:#555'>Set a row to Yes and hunter "
            "builds the CV and the cover letter. When you want to apply, press "
            "Prepare on the role in Control Center and it fills the application "
            "for you to open and submit yourself. Decline with a reason and it "
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
                  act_url or pipeline_url(), ""]
    for r in fresh[:top]:
        text_lines.append(f"  {score_of(r)}  {r.company} - {r.role} "
                          f"({r.cell('Location')}, {r.cell('Comp')})")
    text_lines += ["", ("Say Yes or No on each in Control Center." if act_url else "Set column A.")
                   + " Yes builds the pack, a reason teaches it."]
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
                                       batches=batches, act_url=hunt_url(cfg))
    out = notify.send_email(cfg, subject, body, text=text)
    mark_sent(cfg, REVIEW_READY, fp, {"staged": staged, **st})
    return {"sent": bool(out.get("sent")), "subject": subject, **st}


# ---------- something is wrong ----------

COLUMNS_CHANGED = "Pipeline columns changed"
APPLIED_CHANGED = "Applied columns changed"
CANON_DISAGREES = "canon and the code disagree"
SHEET_UNOPENED = "the sheet could not be opened"
# What the watchdog writes into workflow_runs itself (send_trouble). Not a
# run, so never counted as a failed one: one header change otherwise came
# back an hour later as a second email saying runs were failing.
WATCHDOG_KINDS = frozenset({COLUMNS_CHANGED, APPLIED_CHANGED, CANON_DISAGREES})
# These conditions by fingerprint prefix. Each fingerprint is the change
# itself with no date in it, so it is forgotten when the thing reads clean
# (forget_cleared); otherwise the same change made again a month later was
# never reported, while every run failed on it.
SHEET_PREFIXES = {COLUMNS_CHANGED: "columns:", APPLIED_CHANGED: "applied-columns:"}
CANON_PREFIX = "canon:"


def _digest(diffs) -> str:
    return hashlib.sha1(json.dumps(diffs).encode("utf-8")).hexdigest()[:16]


def header_problem(sheet) -> dict | None:
    """The Pipeline header against the one hunter writes, read out past the
    last column it knows. Fingerprinted on the difference itself, so the same
    change is reported once however many hours it stands, and a further
    change is reported again.

    He inserted two columns on 2026-10-11 and hunter stopped, correctly, for
    56 hourly runs, with nothing recorded and nobody told. The guard was
    right and silent; this is the telling.
    """
    from .sheet import HEADERS, describe_header_change, header_diffs, header_shift
    live = sheet.read_header()
    diffs = header_diffs(live, HEADERS)
    if not diffs:
        return None
    return {"kind": COLUMNS_CHANGED,
            "fingerprint": SHEET_PREFIXES[COLUMNS_CHANGED] + _digest(diffs),
            "detail": describe_header_change(diffs, lead="",
                                             shift=header_shift(live, HEADERS)),
            "record_run": True}


def applied_header_problem(sheet) -> dict | None:
    """The Applied header, judged exactly as its readers and writers judge it
    (sheet.archive_layout). Applied's row 1 became load-bearing when
    read_archive started finding columns by name: a header there that is
    neither layout stops every archive and the next batch's reconcile, and
    before this nothing looked at it but the code that failed on it."""
    from .sheet import (APPLIED_STOPPED, archive_header_diffs, archive_layout,
                        describe_header_change)
    grid = sheet.read_archive_grid()
    if archive_layout(grid):
        return None
    diffs = archive_header_diffs(grid)
    return {"kind": APPLIED_CHANGED,
            "fingerprint": SHEET_PREFIXES[APPLIED_CHANGED] + _digest(diffs),
            "detail": describe_header_change(diffs, lead="",
                                             consequence=APPLIED_STOPPED),
            "record_run": True}


def canon_problem(cfg: Config) -> dict | None:
    """Canon 9.13 against sheet.HEADERS. Every hourly command loads canon
    first and stops on a mismatch before it can record a run, so a merge
    that lands before its canon write, or a hand edit that breaks a section,
    was silent until the eight-day stall check."""
    from .canon import load_canon
    from .sheet import HEADERS, header_diffs
    try:
        names = list(load_canon(cfg).sheet_headers)
    except Exception as e:
        why = f"canon will not load: {e.__class__.__name__}: {str(e)[:200]}"
    else:
        if names == list(HEADERS):
            return None
        why = (f"canon 9.13 names {len(names)} columns where the code names "
               f"{len(HEADERS)}, first differences {header_diffs(names, HEADERS)[:3]}")
    digest = hashlib.sha1(why.encode("utf-8")).hexdigest()[:16]
    return {"kind": CANON_DISAGREES, "fingerprint": CANON_PREFIX + digest,
            "detail": why + ". Every hourly command stops at its first step until "
                            "canon and the code agree.",
            "record_run": True}


def trouble_checks(cfg: Config, sheet=None, *, observed: dict | None = None,
                   sheet_error: Exception | None = None,
                   check_canon: bool = False) -> list[dict]:
    """Conditions that mean the loop has stopped turning. Read only. Given a
    sheet, the Pipeline and Applied headers are two of them; `sheet_error` is
    why there is no sheet to read. `check_canon` adds canon 9.13 against the
    code, one more read, for the scheduled watchdog.

    `observed`, when given, is filled with what the sheet checks saw:
    "clean", the fingerprint prefixes read and found clear, and "unread",
    the checks that could not read at all. A header that could not be read
    is not a header that is fine, and the watchdog says which it was."""
    out: list[dict] = []
    seen = observed if observed is not None else {}
    seen.setdefault("clean", [])
    seen.setdefault("unread", [])
    checks = ((COLUMNS_CHANGED, "the Pipeline header", header_problem),
              (APPLIED_CHANGED, "the Applied header", applied_header_problem))
    if sheet_error is not None:
        seen["unread"].extend(name for _, name, _ in checks)
        out.append({"kind": SHEET_UNOPENED,
                    "fingerprint": f"sheet-open:{sheet_error.__class__.__name__}",
                    "detail": f"{sheet_error.__class__.__name__}: "
                              f"{str(sheet_error)[:160]}. Every step that reads "
                              f"or writes the sheet is failing the same way."})
    elif sheet is not None:
        for kind, name, check in checks:
            try:
                found = check(sheet)
            except Exception as e:
                print(f"watchdog: {name} not read ({e.__class__.__name__})")
                seen["unread"].append(name)
                continue
            if found:
                out.append(found)
            else:
                seen["clean"].append(SHEET_PREFIXES[kind])
    if check_canon:
        found = canon_problem(cfg)
        if found:
            out.append(found)
        else:
            seen["clean"].append(CANON_PREFIX)

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
    real = [r for r in runs if not (isinstance(r.get("outcome"), str)
                                    and r["outcome"] in WATCHDOG_KINDS)]
    fails = [r for r in real[:3] if str(r.get("status") or "").lower()
             in ("failed", "error")]
    if len(fails) >= 2:
        why = (fails[0].get("error_message") or "")[:160]
        out.append({"kind": "runs are failing",
                    "fingerprint": f"failing:{str(fails[0].get('run_at'))[:10]}",
                    "detail": f"{len(fails)} of the last 3 runs failed. {why}"})

    awaiting = db_get(cfg, "hunter_application_approvals",
                      {"select": "token,company,role,created_at,opened_at",
                       "state": "eq.awaiting", "order": "created_at.asc",
                       "limit": "50"})
    # Opened, filled, and never reported. openrouter on 2026-09-25 is the
    # shape: he opened the form through the extension, sent it, and the report
    # never arrived, so the role sat at Yes with nothing saying why. A question
    # he can answer in one keystroke beats a silence he cannot see.
    opened = [a for a in awaiting if a.get("opened_at")
              and (_age_days(a.get("opened_at")) or 0) > 1]
    if opened:
        names = ", ".join(f"{a.get('company')} {a.get('role')}" for a in opened[:4])
        out.append({"kind": "did you send these?",
                    "fingerprint": "opened:" + ",".join(sorted(a["token"] for a in opened))[:180],
                    "detail": f"You opened the filled form for {names} more than a day "
                              f"ago and hunter never heard it was sent. If you sent it, "
                              f"type Applied in column A of its Pipeline row; hunter "
                              f"moves it within the hour."})
    old = [a for a in awaiting if not a.get("opened_at")
           and (_age_days(a.get("created_at")) or 0) > APPROVAL_DAYS]
    if old:
        names = ", ".join(f"{a.get('company')} {a.get('role')}" for a in old[:4])
        out.append({"kind": "applications waiting on you",
                    "fingerprint": f"awaiting:{len(old)}:{str(old[0].get('created_at'))[:10]}",
                    "detail": f"{len(old)} filled application(s) have been waiting "
                              f"more than {APPROVAL_DAYS} days: {names}"})

    # A batch the schedule could not get through. schedule.decide stops after
    # two attempts at one slot rather than paying again for a failure nobody
    # has looked at; this is where he hears about it.
    try:
        import datetime as _dt
        from . import schedule
        now = _dt.datetime.now(_dt.timezone.utc)
        since = (now - _dt.timedelta(days=8)).isoformat()
        cmds = db_get(cfg, "hunter_commands", {
            "select": "state,requested_at,started_at", "command": "eq.run",
            "requested_at": f"gte.{since}"})
        decision = schedule.decide(now, cmds, ok_runs)
        if decision.stuck:
            out.append({"kind": "a batch could not run",
                        "fingerprint": f"stuck:{decision.slot.isoformat()}",
                        "detail": f"{decision.why}. The run summary emails say why."})
    except Exception:
        pass

    recent = real[:4]
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


def forget_cleared(cfg: Config, observed: dict) -> None:
    """Delete the sent marks of the header and canon conditions just read and
    found clear. Only ever on a clean read: a header that could not be read
    is not evidence that the change was undone."""
    for prefix in observed.get("clean", []):
        try:
            db_delete(cfg, TABLE, {"kind": f"eq.{TROUBLE}",
                                   "fingerprint": f"like.{prefix}*"})
        except Exception as e:
            print(f"watchdog: could not clear {prefix}* ({e.__class__.__name__})")


def send_trouble(cfg: Config, *, force: bool = False, sheet=None,
                 sheet_error: Exception | None = None,
                 check_canon: bool = False) -> dict:
    observed: dict = {}
    problems = trouble_checks(cfg, sheet=sheet, observed=observed,
                              sheet_error=sheet_error, check_canon=check_canon)
    forget_cleared(cfg, observed)
    if not problems:
        return {"sent": False, "problems": 0}
    new = [p for p in problems if not already_sent(cfg, TROUBLE, p["fingerprint"])]
    fresh = problems if force else new
    if not fresh:
        return {"sent": False, "problems": len(problems),
                "reason": "every condition has already been reported"}
    # A condition that stops every run before the run can record itself
    # gets one failed workflow_runs row, the first time it is seen, so
    # Control Center's banner shows it too. Once, not hourly: report.py keeps
    # one row per run and never a heartbeat.
    for p in new:
        if p.get("record_run"):
            from .report import report_run
            report_run(cfg, started_at=_now(), ok=False, counts={}, spend_usd=0.0,
                       summary_line=p["kind"], error=p["detail"])
    subject, body, text = trouble_email(fresh)
    out = notify.send_email(cfg, subject, body, text=text)
    for p in fresh:
        mark_sent(cfg, TROUBLE, p["fingerprint"], {"kind": p["kind"]})
    return {"sent": bool(out.get("sent")), "problems": len(fresh),
            "kinds": [p["kind"] for p in fresh]}
