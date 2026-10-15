"""A package in his house style, from hold to build (L5).

What build_one does for a role once the composer is live, in the order that
spends least:

  1. plan     from the record alone, before the posting is fetched: which
              header the role takes, whether the business needs a line of his
              (canon 9.14) and has one, and whether the writer has answered
              for these inputs. A role that waits on him, or on the writer,
              stops here, costs no fetch, and is reported as SKIPPED, which
              no build cap counts.
  2. queue    the posting fetched and gated, the letter is asked of the
              writing routine (drafts.py, kind "letter"), never the API
  3. build    a drafted letter that passes compose.problems is built from
              the v5 master and the house CV master, fitted and read back

Every line it returns for a log is codes and counts: the run log is public.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import drafts, facts
from .canon import CanonError
from .config import Config, db_get, db_patch_where
from .package import compose, contact, personal
from .package import style as style_mod

# A new brief asks the writer again: 15 October b, every paragraph written
# for the role (his words: "not much of this is actually written personally
# for the role").
# 15 October c: the CV summary is written for the role too (his words: "are
# you sure its ok that the CV's professional summary has close to zero
# personalization?").
# 15 October d: his CV's highlights are put in the role's order again, as
# canon 9.12 asks and the builder before this one did.
PROMPT_VERSION = "letter-2026-10-15d"
WAITING = "waiting on the writer"
HELD_BASE = f"held: {contact.HEADER_ASK}"
RECORD_FIELDS = "job_id,company,title,location,jd_text,judge_answers,header_base,package_status"


@dataclass
class Plan:
    job_id: str
    base_key: str = ""
    base_why: str = ""
    needs: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)
    bank: list[str] = field(default_factory=list)
    personal_key: str = ""
    fingerprint: str = ""
    draft: dict | None = None
    judge_answers: object = None
    reason: str = ""            # set when the build waits


def bank_of(canon) -> list[personal.Entry]:
    """Canon 9.14's entries; none while canon has no 9.14."""
    try:
        return personal.parse_bank(canon.section_text("9.14"))
    except (CanonError, AttributeError):
        return []


def view_hash(view_text: str) -> str:
    return hashlib.sha256((view_text or "").encode()).hexdigest()[:12]


def fingerprint(job_id: str, base_key: str, personal_key: str, view_text: str) -> str:
    """What a letter is written from, so his newer score, reaction or line, or
    another header, asks the writer again; and nothing else does."""
    blob = "|".join([PROMPT_VERSION, style_mod.HOUSE_STYLE, job_id, base_key, personal_key,
                     view_hash(view_text)])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def context_of(cfg: Config, draft: dict | None) -> str:
    """The shared context a letter was written from."""
    key = (draft or {}).get("context_key")
    if not key:
        return ""
    rows = db_get(cfg, drafts.CONTEXTS, {"select": "text", "key": f"eq.{key}"})
    return rows[0]["text"] if rows else ""


def latest_draft(cfg: Config, job_id: str) -> dict | None:
    rows = db_get(cfg, drafts.TABLE, {"select": "id,status,meta,output,prompt,evidence,context_key",
                                      "kind": f"eq.{compose.KIND}", "ref": f"eq.{job_id}",
                                      "status": "in.(queued,drafted,used)",
                                      "order": "id.desc", "limit": "1"})
    return rows[0] if rows else None


def plan(cfg: Config, canon, job_id: str, view_text: str, *, request_fact=None) -> Plan:
    """What the build needs and whether it must wait, from the record alone."""
    request_fact = request_fact or (lambda cat: facts.request(cfg, cat, job_id))
    out = Plan(job_id=job_id)
    rows = db_get(cfg, "hunter_seen_roles", {"select": RECORD_FIELDS, "job_id": f"eq.{job_id}"})
    rec = rows[0] if rows else {}
    out.judge_answers = rec.get("judge_answers")
    key = rec.get("header_base") or ""
    if not key:
        key, out.base_why = contact.base_for(rec.get("location") or "", rec.get("jd_text") or "")
    out.base_key = key
    if not key:
        out.reason = HELD_BASE
        return out
    bank = bank_of(canon)
    out.bank = [e.words for e in bank]
    m = personal.match(personal.business_sentences(rec.get("judge_answers")), bank)
    out.needs = sorted(m.categories)
    out.entries = [e.words for e in m.entries]
    out.personal_key = m.fingerprint() if m.needs_line else ""
    if m.missing:
        for cat in m.missing:
            request_fact(cat)
        out.reason = facts.hold_reason(m.missing[0])
        return out
    out.fingerprint = fingerprint(job_id, key, out.personal_key, view_text)
    d = latest_draft(cfg, job_id)
    same = bool(d) and ((d.get("meta") or {}).get("fingerprint") == out.fingerprint)
    if same and d["status"] in ("drafted", "used"):
        out.draft = d
    elif same and d["status"] == "queued":
        out.reason = WAITING
    return out


def hold(cfg: Config, job_id: str, reason: str, *, base_key: str = "") -> None:
    """The reason on the record, where Control Center shows it. A package
    already built keeps its status: only its reason would mislead."""
    patch = {"rejection_reason": reason}
    if base_key:
        patch["header_base"] = base_key
    db_patch_where(cfg, "hunter_seen_roles",
                   {"job_id": f"eq.{job_id}", "package_status": "neq.built"},
                   dict(patch, package_status="blocked"))


def queue(cfg: Config, p: Plan, *, company: str, title: str, location: str, jd_text: str,
          judge_answers, view_text: str, model_letter: str, evidence_text: str,
          evidence: str, banned: tuple[str, ...], examples: str = "",
          his_words: str = "", highlights: list[str] | None = None) -> bool:
    """Ask the routine for this role's letter. A request for older inputs is
    expired first, so the routine never writes for a letter nobody will use."""
    d = latest_draft(cfg, p.job_id)
    if d and d["status"] in ("queued", "drafted") and \
            (d.get("meta") or {}).get("fingerprint") != p.fingerprint:
        db_patch_where(cfg, drafts.TABLE, {"id": f"eq.{d['id']}", "status": f"eq.{d['status']}"},
                       {"status": "expired", "problems": ["its inputs changed"]})
    prompt = compose.role_prompt(company=company, title=title, location=location,
                                 base_key=p.base_key, jd_text=jd_text,
                                 judge_answers=judge_answers, his_view=view_text,
                                 entries=p.entries, needs=p.needs)
    context = compose.shared_context(model_letter=model_letter, evidence_text=evidence_text,
                                     bank_words=p.bank, examples=examples, his_words=his_words,
                                     highlights=highlights)
    # His own sentences are his facts: the gate traces them like the rest.
    if his_words:
        evidence = evidence + "\n" + his_words.lower()
    meta = {"company": company, "short_name": short_name(company), "role": title,
            "base": p.base_key, "needs": p.needs, "entries": p.entries, "bank": p.bank,
            "banned": list(banned), "fingerprint": p.fingerprint, "job_id": p.job_id,
            "highlights": len(highlights or []),
            "prompt_version": PROMPT_VERSION}
    return drafts.request(cfg, compose.KIND, p.job_id, prompt, context=context,
                          schema=compose.SCHEMA, evidence=evidence, meta=meta)


def short_name(company: str) -> str:
    import re
    s = re.sub(r",?\s+(inc|inc\.|ltd|ltd\.|llc|plc|gmbh|corp|corporation|co\.)$", "",
               (company or "").strip(), flags=re.I)
    return s.strip(" ,.")


def checked(p: Plan, context: str = "") -> tuple[compose.Letter | None, list[str]]:
    """The drafted letter, held to the same check the routine ran, with the
    context it was written from."""
    d = p.draft or {}
    probs = compose.problems(d.get("output"), prompt=d.get("prompt") or "",
                             evidence=d.get("evidence") or "", meta=d.get("meta") or {},
                             context=context)
    if probs:
        return None, probs
    return compose.parse(d.get("output")), []


def mark(cfg: Config, draft_id, status: str, problems=()) -> None:
    db_patch_where(cfg, drafts.TABLE, {"id": f"eq.{draft_id}"},
                   {"status": status, "problems": list(problems)[:6]})


def asks_summary(letter: compose.Letter) -> dict:
    gaps = sum(1 for a in letter.asks if str(a.get("answered_by") or "").strip().lower() == "gap")
    return {"asks": len(letter.asks), "answered": len(letter.asks) - gaps, "gaps": gaps}


def style_record(doc: dict) -> list[dict]:
    """The style snapshot kept for the doc-edits pass: per paragraph, its
    text and each run's effective font, size, weight, colour and link."""
    from .package import house
    out = []
    for p in house.snapshot(doc):
        out.append({"text": p.text, "bullet": p.bullet,
                    "runs": [[r.text, r.font, r.size, r.bold, r.color, r.link] for r in p.runs],
                    "images": [[i.width_pt, i.height_pt, i.link] for i in p.images]})
    return out


def jd_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def brief_row(p: Plan, letter: compose.Letter, jd_text: str) -> dict:
    return {"jd_hash": jd_hash(jd_text), "hints_hash": p.fingerprint, "job_id": p.job_id,
            "brief": json.loads(json.dumps(letter.asks)), "asks": len(letter.asks),
            "quotes_checked": True, "drafted_by": "routine"}
