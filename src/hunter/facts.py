"""A personal line he has not written yet: asked of him by email, and his
answer written to canon 9.14 exactly as he wrote it.

His ruling, 2026-10-15: a letter to a business in one of his categories
carries one line of his own; when canon 9.14 has none that fits, the package
holds, hunter asks him, and his answer goes into canon verbatim. One open ask
per category (the table's unique index), so ten voice AI roles waiting on him
are one email, and his one answer releases all ten.

  request   a role needs a line in a category: one open ask, never two
  ask       queued asks are mailed; marked asked only once Gmail returns an id
  read      his reply is written to 9.14 (canonwrite) and confirmed by email,
            quoting his words back; UNDO takes it back; a later reply replaces

His words are never printed, logged or put in a note: the row holds them for
the write, the confirmation goes only to him, and every line this module
prints is counts.
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import re
from dataclasses import dataclass, field

from . import canonwrite
from .apply import inbox
from .config import Config, db_get, db_insert, db_patch_where
from .package import personal

TABLE = "hunter_fact_requests"
SUBJECT_MARK = "[hunter fact #"
QUERY = 'subject:"hunter fact" newer_than:30d'
UNDO = "UNDO"
MAX_CHARS = 600
HOLD = "held: needs one line from you about {label} companies"

OPEN = ("queued", "asked")

# Words that say nothing about a business, so never narrow where a line fits.
GENERIC = frozenset("""
about after again against also always among another anyone anything around
because been before being better between both bring bringing build building
business businesses company companies could daily does doing during each
ever every everyone first from great have having help helps into just know
like love made make makes making many more most much need never only other
over people platform platforms product products really since some still such
than that their them then there these they thing things this those through
today together use used uses using very want week well were what when where
which while will with work works world would year years your life like
""".split())


def hold_reason(category: str) -> str:
    return HOLD.format(label=personal.LABEL.get(category, category))


def fact_id_from_subject(subject: str) -> str:
    s = subject or ""
    if SUBJECT_MARK not in s:
        return ""
    tail = s.split(SUBJECT_MARK, 1)[1]
    got = tail.split("]", 1)[0].strip() if "]" in tail else ""
    return got if got.isdigit() else ""


def terms_for(words: str, sentences: list[str]) -> list[str]:
    """The words his line and the business share, so the line goes to
    businesses that say them. None shared means his line fits the whole
    category."""
    def toks(text: str) -> list[str]:
        return [t.strip("'") for t in re.findall(r"[a-z0-9']+", personal._clean(text))]
    business = set(toks(" ".join(sentences)))
    out: list[str] = []
    for t in toks(words):
        if len(t) >= 4 and t not in GENERIC and t in business and t not in out:
            out.append(t)
    return out[:6]


def his_line(body: str) -> str:
    """His reply, as he wrote it: the lines above the quoted original, joined
    on one line. One pair of quotes around the whole line is taken off,
    because a quoted line is how people mark what they mean, not part of it."""
    text = " ".join(" ".join(inbox.krishs_own_lines(body)).split())
    if len(text) >= 2 and text[0] in "\"“" and text[-1] in "\"”" \
            and text.count('"') + text.count("“") + text.count("”") == 2:
        text = text[1:-1].strip()
    return text


# ---------- the request ----------

def open_for(cfg: Config, category: str) -> dict | None:
    rows = db_get(cfg, TABLE, {"select": "id,status,category,job_id",
                               "category": f"eq.{category}",
                               "status": f"in.({','.join(OPEN)})"})
    return rows[0] if rows else None


def request(cfg: Config, category: str, job_id: str) -> dict:
    """The open ask for this category, made if there is none."""
    if category not in personal.CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    got = open_for(cfg, category)
    if got:
        return got
    try:
        db_insert(cfg, TABLE, [{"category": category, "job_id": job_id, "status": "queued"}])
    except Exception:
        # Another pass made it first; the unique index kept it to one.
        pass
    got = open_for(cfg, category)
    if not got:
        raise RuntimeError("the fact request did not read back after it was made")
    return got


# ---------- the ask ----------

def _role(cfg: Config, job_id: str) -> dict:
    if not job_id:
        return {}
    rows = db_get(cfg, "hunter_seen_roles", {"select": "job_id,company,title,judge_answers",
                                             "job_id": f"eq.{job_id}", "limit": "1"})
    return rows[0] if rows else {}


def ask_email(req: dict, role: dict) -> tuple[str, str]:
    label = personal.LABEL.get(req["category"], req["category"])
    company = role.get("company") or "a company"
    title = role.get("title") or "a role"
    subject = f"One line from you for {label} companies {SUBJECT_MARK}{req['id']}]"
    e = html_mod.escape
    html = (
        f"<p>A letter is waiting on one line from you.</p>"
        f"<p><b>{e(company)}</b>, {e(title)}, is a {e(label)} business. Your letters to "
        f"businesses like this carry one personal line of yours, and canon 9.14 has none "
        f"that fits this one.</p>"
        f"<p><b>Reply with the line</b>, as you want it to read in the letter. It is "
        f"written to canon 9.14 exactly as you write it and used in your letters to "
        f"{e(label)} businesses it fits. Every letter waiting on it is built once it is "
        f"written.</p>"
        f"<p>Reply UNDO at any time to take it back. A later reply replaces it.</p>"
        f"<p style='color:#888'>{inbox.OUTBOUND_MARKER}</p>")
    return subject, html


def confirm_email(req: dict, words: str, terms: list[str], entry: int) -> tuple[str, str]:
    label = personal.LABEL.get(req["category"], req["category"])
    fits = (f"{label} businesses that say {', '.join(terms)}" if terms
            else f"every {label} business")
    e = html_mod.escape
    subject = f"Written: your line for {label} companies {SUBJECT_MARK}{req['id']}]"
    html = (f"<p>Written to canon 9.14 as entry {entry}, for {e(fits)}:</p>"
            f"<blockquote>{e(words)}</blockquote>"
            f"<p>Reply UNDO to take it back.</p>"
            f"<p style='color:#888'>{inbox.OUTBOUND_MARKER}</p>")
    return subject, html


def undone_email(req: dict, ok: bool) -> tuple[str, str]:
    label = personal.LABEL.get(req["category"], req["category"])
    subject = f"Taken back: your line for {label} companies {SUBJECT_MARK}{req['id']}]"
    body = ("Taken out of canon 9.14. Letters waiting on a line for these businesses "
            "hold until you send another." if ok else
            "Not taken back: the entry in canon 9.14 has changed since it was written, "
            "so it is left as you have it.")
    return subject, f"<p>{body}</p><p style='color:#888'>{inbox.OUTBOUND_MARKER}</p>"


def _patch(cfg: Config, req_id: int, expect: str, values: dict) -> bool:
    n = db_patch_where(cfg, TABLE, {"id": f"eq.{req_id}", "status": f"eq.{expect}"}, values)
    return n == 1


def open_asks(cfg: Config) -> list[dict]:
    """The asks still waiting on his line, oldest first, with the role each
    was made for. His ruling 2026-10-20: "stop asking me those by email". A
    Claude session reads these and asks him in chat (`run questions`)."""
    rows = db_get(cfg, TABLE, {"select": "id,category,job_id,status",
                               "status": "in.(queued,asked)", "order": "id.asc"})
    for r in rows:
        r["role"] = _role(cfg, r.get("job_id") or "")
    return rows


def answer(cfg: Config, req_id: int, words: str) -> int:
    """His line, given in chat, written to canon 9.14 exactly as he gave it and
    read back, as a reply by email once was. Returns the canon entry. Nothing
    is mailed."""
    words = (words or "").strip()
    if not words:
        raise ValueError("an answer needs his words")
    if len(words) > MAX_CHARS:
        raise ValueError(f"one line, up to {MAX_CHARS} characters")
    rows = db_get(cfg, TABLE, {"select": "id,category,job_id,status", "id": f"eq.{req_id}"})
    if not rows or rows[0]["status"] not in ("queued", "asked"):
        raise ValueError("no open ask with that id")
    req = rows[0]
    role = _role(cfg, req.get("job_id") or "")
    terms = terms_for(words, personal.business_sentences(role.get("judge_answers")))
    # The record first: a record that refuses must leave canon untouched, as
    # the first run of this did not (its source was not yet an allowed value).
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if not _patch(cfg, req["id"], req["status"], {
            "status": "answered", "answer": words, "answer_source": "chat",
            "answered_at": now}):
        raise RuntimeError("the ask changed before his answer was recorded")
    return _write(cfg, req, words, terms)


def _write(cfg: Config, req: dict, words: str, terms) -> int:
    """An answer already recorded, written to canon 9.14 and read back."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    w = canonwrite.add_entry(cfg, categories=[req["category"]], terms=terms, words=words)
    _patch(cfg, req["id"], "answered", {
        "status": "written", "written_at": now, "canon_version_before": w.version_before,
        "canon_version_after": w.version_after, "result": f"entry {w.entry} written"})
    back = db_get(cfg, TABLE, {"select": "status,answer", "id": f"eq.{req['id']}"})
    if not (back and back[0]["status"] == "written" and back[0]["answer"] == words):
        raise RuntimeError("the answer did not read back")
    return w.entry


def write_recorded(cfg: Config) -> dict:
    """Answers a session recorded in chat but could not write: a session with
    no keys sets the ask to answered, answer_source chat, with his words, and
    this writes them to canon 9.14 on the next drain (2026-10-23, Higgsfield).
    Counts only: his words never reach a log."""
    counts = {"written": 0, "refused": 0}
    for req in db_get(cfg, TABLE, {"select": "id,category,job_id,status,answer,answer_source",
                                   "status": "eq.answered", "answer_source": "eq.chat",
                                   "order": "id.asc"}):
        words = (req.get("answer") or "").strip()
        if not words or len(words) > MAX_CHARS:
            counts["refused"] += 1
            continue
        role = _role(cfg, req.get("job_id") or "")
        terms = terms_for(words, personal.business_sentences(role.get("judge_answers")))
        try:
            _write(cfg, req, words, terms)
            counts["written"] += 1
        except Exception:
            counts["refused"] += 1
    return counts


def ask(cfg: Config, send) -> dict:
    """Mail every queued ask. Marked asked only when Gmail gave an id back
    and the row reads back as asked."""
    counts = {"asked": 0, "not_sent": 0}
    for req in db_get(cfg, TABLE, {"select": "id,category,job_id,status",
                                   "status": "eq.queued", "order": "id.asc"}):
        subject, html = ask_email(req, _role(cfg, req.get("job_id") or ""))
        out = send(cfg, subject, html) or {}
        if not (out.get("sent") and out.get("id")):
            counts["not_sent"] += 1
            continue
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        ok = _patch(cfg, req["id"], "queued", {
            "status": "asked", "gmail_message_id": out["id"],
            "gmail_thread_id": out.get("thread_id") or None, "asked_at": now,
            "processed_message_ids": [out["id"]]})
        back = db_get(cfg, TABLE, {"select": "status,gmail_message_id", "id": f"eq.{req['id']}"})
        if ok and back and back[0]["status"] == "asked" and back[0]["gmail_message_id"] == out["id"]:
            counts["asked"] += 1
        else:
            counts["not_sent"] += 1
    return counts


# ---------- his reply ----------

@dataclass
class Outcome:
    counts: dict = field(default_factory=lambda: {
        "written": 0, "replaced": 0, "undone": 0, "not_undone": 0, "empty": 0,
        "too_long": 0, "not_his": 0, "failed": 0, "skipped": 0})


def _mark_processed(cfg: Config, req: dict, message_ids: list[str]) -> None:
    seen = list(req.get("processed_message_ids") or [])
    for m in message_ids:
        if m and m not in seen:
            seen.append(m)
    db_patch_where(cfg, TABLE, {"id": f"eq.{req['id']}"}, {"processed_message_ids": seen})
    req["processed_message_ids"] = seen


def _entry_of(req: dict) -> int | None:
    m = re.match(r"entry (\d+)\b", req.get("result") or "")
    return int(m.group(1)) if m else None


def read(cfg: Config, *, fetch, send) -> Outcome:
    """His replies to asks, oldest first, each acted on once."""
    out = Outcome()
    c = out.counts
    rows = {r["id"]: r for r in db_get(cfg, TABLE, {
        "select": "*", "status": "in.(asked,written,failed,undone)"})}
    if not rows:
        return out      # nothing was ever asked: no mailbox read
    replies = sorted(fetch(cfg), key=lambda r: (int(r.get("internal_date") or 0), r["message_id"]))
    for rep in replies:
        req = rows.get(int(rep["token"])) if str(rep.get("token", "")).isdigit() else None
        if req is None or rep["message_id"] in (req.get("processed_message_ids") or []):
            c["skipped"] += 1
            continue
        if inbox.is_our_own_email(rep.get("body", "")):
            _mark_processed(cfg, req, [rep["message_id"]])
            c["skipped"] += 1
            continue
        if not inbox.is_from_krish(rep.get("from", ""), cfg):
            _mark_processed(cfg, req, [rep["message_id"]])
            c["not_his"] += 1
            continue
        words = his_line(rep.get("body", ""))
        sent_ids: list[str] = []
        if words.strip(".!").upper() == UNDO:
            entry = _entry_of(req)
            if req["status"] != "written" or entry is None:
                c["skipped"] += 1
            else:
                try:
                    w = canonwrite.remove_entry(cfg, number=entry, words=req.get("answer") or "")
                    _patch(cfg, req["id"], "written", {
                        "status": "undone", "result": f"entry {entry} taken back",
                        "canon_version_before": w.version_before,
                        "canon_version_after": w.version_after})
                    req["status"] = "undone"
                    c["undone"] += 1
                    ok = True
                except canonwrite.CanonWriteError:
                    c["not_undone"] += 1
                    ok = False
                s = send(cfg, *undone_email(req, ok)) or {}
                sent_ids.append(s.get("id", ""))
            _mark_processed(cfg, req, [rep["message_id"]] + sent_ids)
            continue
        if not words:
            c["empty"] += 1
            _mark_processed(cfg, req, [rep["message_id"]])
            continue
        if len(words) > MAX_CHARS:
            c["too_long"] += 1
            label = personal.LABEL.get(req["category"], req["category"])
            s = send(cfg, f"One line, please: {label} companies {SUBJECT_MARK}{req['id']}]",
                     f"<p>That reply is {len(words)} characters; a letter carries one or two "
                     f"sentences, up to {MAX_CHARS}. Nothing was written. Reply with the line "
                     f"alone.</p><p style='color:#888'>{inbox.OUTBOUND_MARKER}</p>") or {}
            _mark_processed(cfg, req, [rep["message_id"], s.get("id", "")])
            continue
        role = _role(cfg, req.get("job_id") or "")
        terms = terms_for(words, personal.business_sentences(role.get("judge_answers")))
        replacing = req["status"] == "written" and _entry_of(req) is not None
        try:
            before = None
            if replacing:
                try:
                    r = canonwrite.remove_entry(cfg, number=_entry_of(req),
                                                words=req.get("answer") or "")
                    before = r.version_before
                except canonwrite.CanonWriteError:
                    pass   # he changed it since: his, and it stays beside the new one
            w = canonwrite.add_entry(cfg, categories=[req["category"]], terms=terms, words=words)
        except (canonwrite.CanonWriteError, ValueError) as exc:
            c["failed"] += 1
            db_patch_where(cfg, TABLE, {"id": f"eq.{req['id']}"}, {
                "status": "failed", "result": f"not written: {type(exc).__name__}"})
            req["status"] = "failed"
            _mark_processed(cfg, req, [rep["message_id"]])
            continue
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        db_patch_where(cfg, TABLE, {"id": f"eq.{req['id']}"}, {
            "status": "written", "answer": words, "answer_source": "email",
            "answered_at": now, "written_at": now,
            "canon_version_before": before if before is not None else w.version_before,
            "canon_version_after": w.version_after,
            "result": f"entry {w.entry} written"})
        back = db_get(cfg, TABLE, {"select": "status,answer,result", "id": f"eq.{req['id']}"})
        if not (back and back[0]["status"] == "written" and back[0]["answer"] == words):
            c["failed"] += 1
            _mark_processed(cfg, req, [rep["message_id"]])
            continue
        req.update(status="written", answer=words, result=f"entry {w.entry} written")
        c["replaced" if replacing else "written"] += 1
        s = send(cfg, *confirm_email(req, words, terms, w.entry)) or {}
        _mark_processed(cfg, req, [rep["message_id"], s.get("id", "")])
    return out


def fetch_replies(cfg: Config) -> list[dict]:
    return inbox.fetch_replies(cfg, query=QUERY, token_of=fact_id_from_subject)
