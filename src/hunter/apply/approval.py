"""One email per application, and the token that gates its submission.

Krish's brief 2026-09-14: one email with the application in the body, two
document links or a single merged attachment where the form has one upload slot,
and an approve-or-amend loop by reply. Profile row 60 already carried the rule
("never submit without approval, both gates"); this is its implementation.

The token is the whole safety mechanism. An application cannot be submitted
without an approval row in state APPROVED whose plan hash still matches what was
emailed, so what Krish approved is provably what gets sent. Change the answers
after he approves and the hash moves, which voids the approval rather than
quietly sending something he never read.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import secrets
import urllib.parse
from dataclasses import dataclass, field

from ..config import Config, db_get, db_insert, db_patch

TABLE = "hunter_application_approvals"

AWAITING = "awaiting"
APPROVED = "approved"
AMENDING = "amending"
SUBMITTED = "submitted"
QUEUED = "queued"
FAILED = "failed"
CANCELLED = "cancelled"
STATES = (AWAITING, APPROVED, AMENDING, SUBMITTED, QUEUED, FAILED, CANCELLED)

SUBJECT_MARK = "[hunter #"
APPROVE_WORD = "APPROVE"


def new_token(job_id: str) -> str:
    """Opaque enough that a token cannot be guessed from the job, short enough to
    survive a mail client mangling a subject line."""
    return f"{job_id[:40]}-{secrets.token_hex(4)}"


def subject_for(company: str, role: str, token: str) -> str:
    return f"Apply: {company} {role} {SUBJECT_MARK}{token}]"


def token_from_subject(subject: str) -> str:
    """Tolerant of Re:, Fwd: and whatever a phone prepends."""
    s = subject or ""
    if SUBJECT_MARK not in s:
        return ""
    tail = s.split(SUBJECT_MARK, 1)[1]
    return tail.split("]", 1)[0].strip() if "]" in tail else ""


def plan_hash(fill_plan: dict) -> str:
    """Stable over key order, so a reserialisation does not void an approval, but
    sensitive to any answer actually changing."""
    blob = json.dumps(fill_plan, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _mailto(to: str, subject: str, body: str) -> str:
    q = urllib.parse.urlencode({"subject": subject, "body": body},
                               quote_via=urllib.parse.quote)
    return f"mailto:{to}?{q}"


def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


@dataclass
class FieldLine:
    label: str
    value: str
    required: bool
    source: str = ""
    flagged: bool = False
    unresolved: bool = False


@dataclass
class ApprovalEmail:
    subject: str
    html: str
    text: str
    token: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


def render(*, company: str, role: str, jd_url: str, autonomy: str,
           token: str, to: str, lines: list[FieldLine],
           essays: dict[str, str] | None = None,
           summary: str = "", hook: str = "",
           cv_url: str = "", letter_url: str = "",
           cv_pdf_url: str = "", letter_pdf_url: str = "",
           merged_attachment: str = "",
           notes: list[str] | None = None) -> ApprovalEmail:
    """The whole application, reviewable without opening a document."""
    essays = essays or {}
    notes = notes or []
    subject = subject_for(company, role, token)

    needs_you = [l for l in lines if l.unresolved]
    flagged = [l for l in lines if l.flagged and not l.unresolved]

    approve_body = (f"{APPROVE_WORD}\n\n(Send as is to submit this application. "
                    f"Anything else in this reply is read as feedback instead.)")
    amend_body = ("Change the following, then resend for approval:\n\n"
                  "- \n\n(Write freely. Your words are passed through verbatim.)")
    approve_link = _mailto(to, subject, approve_body)
    amend_link = _mailto(to, subject, amend_body)

    H: list[str] = []
    A = H.append
    A("<div style=\"font:15px/1.55 -apple-system,BlinkMacSystemFont,"
      "'Segoe UI',system-ui,sans-serif;color:#111;max-width:680px\">")
    A("<div style='display:none'>[hunter-outbound]</div>")
    A(f"<h2 style='margin:0 0 2px;font-size:19px'>{_esc(role)}</h2>")
    A(f"<div style='color:#555;margin-bottom:18px'>{_esc(company)}"
      f"{' &middot; ' + _esc(autonomy) if autonomy else ''}</div>")

    if needs_you or flagged or notes:
        A("<div style='border-left:3px solid #c47f00;background:#fffbf0;"
          "padding:12px 14px;margin:0 0 18px'>")
        A("<strong>Read before approving</strong>")
        A("<ul style='margin:8px 0 0;padding-left:20px'>")
        for l in needs_you:
            A(f"<li><strong>{_esc(l.label)}</strong>: no answer. "
              f"{_esc(l.source)}</li>")
        for l in flagged:
            A(f"<li>{_esc(l.label)}: <strong>{_esc(l.value)}</strong> "
              f"<span style='color:#666'>({_esc(l.source)})</span></li>")
        for n in notes:
            A(f"<li>{_esc(n)}</li>")
        A("</ul></div>")

    A("<div style='margin:0 0 20px'>")
    A(f"<a href=\"{approve_link}\" style='display:inline-block;padding:11px 20px;"
      "background:#111;color:#fff;text-decoration:none;border-radius:6px;"
      "font-weight:600'>Approve and submit</a>")
    A(f"<a href=\"{amend_link}\" style='display:inline-block;padding:11px 20px;"
      "margin-left:10px;border:1px solid #bbb;color:#111;text-decoration:none;"
      "border-radius:6px'>Amend</a>")
    A("</div>")
    A("<p style='color:#666;font-size:13px;margin:0 0 22px'>Both buttons open a "
      "reply. Press send. Only the word "
      f"<code>{APPROVE_WORD}</code> on its own line submits; anything else is "
      "treated as feedback and comes back to you for approval again.</p>")

    A("<h3 style='font-size:14px;text-transform:uppercase;letter-spacing:.04em;"
      "color:#666;margin:22px 0 8px'>Documents</h3><ul style='margin:0;"
      "padding-left:20px'>")
    if merged_attachment:
        A(f"<li>Attached to this email: <strong>{_esc(merged_attachment)}</strong>"
          " (this form takes one upload, so the letter and CV are one file)</li>")
    for label, url in (("CV", cv_url), ("CV, PDF", cv_pdf_url),
                       ("Cover letter", letter_url),
                       ("Cover letter, PDF", letter_pdf_url)):
        if url:
            A(f"<li><a href=\"{_esc(url)}\">{label}</a></li>")
    if jd_url:
        A(f"<li><a href=\"{_esc(jd_url)}\">The job description</a></li>")
    A("</ul>")

    if summary or hook:
        A("<h3 style='font-size:14px;text-transform:uppercase;letter-spacing:"
          ".04em;color:#666;margin:22px 0 8px'>Written for this role</h3>")
        for label, body in (("CV summary", summary), ("Letter opening", hook)):
            if body:
                A(f"<div style='margin-bottom:12px'><div style='color:#666;"
                  f"font-size:13px'>{label}</div>"
                  f"<div style='background:#f6f6f6;padding:10px 12px;"
                  f"border-radius:4px;white-space:pre-wrap'>{_esc(body)}</div>"
                  f"</div>")

    if essays:
        A("<h3 style='font-size:14px;text-transform:uppercase;letter-spacing:"
          ".04em;color:#666;margin:22px 0 8px'>Long answers</h3>")
        for q, a in essays.items():
            A(f"<div style='margin-bottom:12px'><div style='color:#666;"
              f"font-size:13px'>{_esc(q)}</div><div style='background:#f6f6f6;"
              f"padding:10px 12px;border-radius:4px;white-space:pre-wrap'>"
              f"{_esc(a)}</div></div>")

    A("<h3 style='font-size:14px;text-transform:uppercase;letter-spacing:.04em;"
      "color:#666;margin:22px 0 8px'>The form, exactly as it will be "
      "submitted</h3>")
    A("<table style='border-collapse:collapse;width:100%;font-size:14px'>")
    for l in lines:
        mark = " *" if l.required else ""
        value = (l.value if l.value else
                 "<em style='color:#b00'>no answer</em>")
        A("<tr>"
          f"<td style='padding:6px 10px 6px 0;vertical-align:top;color:#555;"
          f"width:42%;border-bottom:1px solid #eee'>{_esc(l.label)}{mark}</td>"
          f"<td style='padding:6px 0;vertical-align:top;border-bottom:1px solid "
          f"#eee'>{value if not l.value else _esc(l.value)}"
          + (f"<div style='color:#888;font-size:12px'>{_esc(l.source)}</div>"
             if l.source else "")
          + "</td></tr>")
    A("</table>")
    A("<p style='color:#888;font-size:12px;margin-top:10px'>* required by the "
      "form</p>")
    A("</div>")
    html = "\n".join(H)

    # First line of the text part on purpose: inbox.is_our_own_email looks for it
    # in the lines Krish would have written, and hunter mails its own mailbox.
    T = ["[hunter-outbound]", f"{role} at {company}", f"autonomy: {autonomy}", ""]
    if needs_you:
        T.append("READ BEFORE APPROVING")
        T += [f"  {l.label}: no answer. {l.source}" for l in needs_you]
    if flagged:
        T += [f"  {l.label}: {l.value} ({l.source})" for l in flagged]
    T += ["", f"Reply {APPROVE_WORD} on its own line to submit.",
          "Reply with anything else and it is treated as feedback.", ""]
    if merged_attachment:
        T.append(f"Attached: {merged_attachment}")
    for label, url in (("CV", cv_url), ("Letter", letter_url),
                       ("JD", jd_url)):
        if url:
            T.append(f"{label}: {url}")
    if summary:
        T += ["", "CV SUMMARY", summary]
    if hook:
        T += ["", "LETTER OPENING", hook]
    for q, a in essays.items():
        T += ["", q.upper(), a]
    T += ["", "THE FORM"]
    for l in lines:
        T.append(f"  {l.label}{' *' if l.required else ''}: "
                 f"{l.value or '(no answer)'}"
                 + (f"  [{l.source}]" if l.source else ""))
    return ApprovalEmail(subject=subject, html=html, text="\n".join(T),
                         token=token)


# ---------- the approval ledger ----------

def record_sent(cfg: Config, *, token: str, job_id: str, company: str,
                role: str, fill_plan: dict,
                message_id: str = "") -> None:
    """message_id is hunter's OWN sent message. Recording it as already processed
    is what stops the next poll reading our approval email as a reply from Krish.
    """
    db_insert(cfg, TABLE, [{
        "token": token, "job_id": job_id, "company": company, "role": role,
        "state": AWAITING, "plan_hash": plan_hash(fill_plan),
        "fill_plan": json.dumps(fill_plan, sort_keys=True, default=str),
        "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "processed_message_ids": [message_id] if message_id else [],
    }], on_conflict="token")


def get_row(cfg: Config, token: str) -> dict | None:
    rows = db_get(cfg, TABLE, {"select": "*", "token": f"eq.{token}"})
    return rows[0] if rows else None


def set_state(cfg: Config, token: str, state: str, **extra) -> None:
    if state not in STATES:
        raise ValueError(f"unknown approval state {state!r}")
    values = {"state": state,
              "decided_at": datetime.datetime.now(
                  datetime.timezone.utc).isoformat()}
    values.update(extra)
    db_patch(cfg, TABLE, {"token": token}, values)


def mark_processed(cfg: Config, token: str, message_id: str,
                   existing: list[str] | None = None) -> None:
    ids = list(existing or [])
    if message_id not in ids:
        ids.append(message_id)
    db_patch(cfg, TABLE, {"token": token}, {"processed_message_ids": ids})


def supersede(cfg: Config, token: str) -> None:
    """An amend kills the old token so a stale APPROVE cannot land later."""
    set_state(cfg, token, CANCELLED)
