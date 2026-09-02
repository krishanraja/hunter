"""The learning loop: Krish's verdicts, read back as instructions.

Every verdict is appended to hunter_verdict_events and then split two ways,
because the two halves earn very different trust:

  SYSTEM codes are hunter failing. A dead posting is a liveness miss, an
  already-applied or duplicate row is a dedupe miss. Those are bugs, so they
  are fixed in code and pinned by a regression test named for the job_id
  that produced them. Nothing waits for approval to stop a bug recurring.

  TASTE codes are Krish's judgement, and hunter never infers a standing rule
  from them on its own. They raise a workflow_proposals row and stay inert
  until he approves it. Approved rules live in system_config under
  hunter_learned_filters, each carrying the job_ids and the verbatim words
  that produced it, so every suppression can be traced back to something he
  actually said and reversed with one command.

The inference below reads his own words. It never invents an attribute: the
value it stores is lifted verbatim out of the sentence he wrote.
"""
from __future__ import annotations

import json
import re

from .config import Config, db_get, db_insert, db_patch
from .sources import distinctive_tokens, norm_title, slugify
from . import verdicts

# What hunter writes into verdict_source when the coded verdict on the sheet
# is its own re-gate decision rather than something Krish typed. The loop must
# never learn from its own output: on 2026-09-02 forty auto verdicts had been
# recorded as his, and clustering them would have proposed blocklisting
# Sierra, Decagon, Cloudflare and Synthesia on no input from him at all.
AUTO_SOURCE = "hunter regate"


def is_auto(row: dict) -> bool:
    return (row.get("verdict_source") or "").lower().startswith("hunter")

# Ordered. The first pattern that matches names the primary code, and the
# order encodes which half of a two-part sentence is the real objection:
# "not interested in Salesforce as a business nor sales analytics as what I
# do all day" is a verdict on the business first.
INFERENCE: list[tuple[str, str]] = [
    ("dead_posting", r"role\s+does\s?n.?t\s+exist|no longer (?:live|posted|open|exists)"
                     r"|posting (?:is )?(?:gone|dead|closed)"),
    ("already_applied", r"already applied"),
    ("duplicate_row", r"\bduplicate\b|same role as|listed (?:twice|above)"),
    ("geo_language", r"\bnot based in\b|native (?:spanish|french|german|portuguese)"
                     r"|\brelocat|\bvisa\b|residen(?:cy|t) requirement"),
    ("business_uninteresting", r"no interest in|not interested in"),
    ("function_wrong", r"i do\s?n.?t want to do|what i do all day|not what i do"
                       r"|do\s?n.?t want to run"),
    ("domain_expertise", r"domain expert|no expertise in|not my domain|no experience in"),
    ("requirements_mismatch", r"do\s?n.?t fit|do\s?n.?t meet|precise jd|jd requirements"),
    ("seniority_below", r"too junior|below my level|not senior enough|\bic role\b"),
    ("seniority_above", r"too senior|above my level|\bceo role\b"),
    ("comp_below", r"comp(?:ensation)? (?:is )?(?:too )?low|below (?:my |the )?bar"
                   r"|salary is"),
    ("stage_wrong", r"too early|too late stage|pre.?seed|\bseed stage\b"),
    ("travel", r"travel"),
]

# Attribute extractors. Each returns the value verbatim from his sentence,
# so a proposal can quote the words that produced it.
ATTRIBUTES: list[tuple[str, str]] = [
    ("domain", r"no (?:expertise|experience) in ([a-z][\w &/-]{2,30}?)(?:[.,]|$| but | and )"),
    ("business", r"not interested in ([A-Za-z][\w &/-]{2,30}?) as a business"),
    ("business", r"no interest in ([a-z][\w &/-]{2,30}?)(?:[.,]|$| or | and )"),
    ("function", r"do\s?n.?t want to (?:do|run) ([a-z][\w &/-]{2,30}?)(?:[.,]|$| but | and )"),
    ("function", r"(?:^|\bnor |\band |\bor |, )([a-z][\w &/-]{2,30}?) as what i do all day"),
    ("geo", r"not based in ([A-Za-z][\w ]{1,20}?)(?:[.,]|$| or | and )"),
    ("language", r"native (\w+)"),
]


class Inference:
    def __init__(self, primary: str | None, codes: list[str],
                 attributes: list[tuple[str, str]]):
        self.primary = primary
        self.codes = codes
        self.attributes = attributes

    def __repr__(self) -> str:
        return f"Inference({self.primary!r}, {self.codes}, {self.attributes})"


def infer(text: str) -> Inference:
    """Read a free-text verdict. Krish types sentences, not codes; this maps
    them onto the vocabulary without discarding what else he said."""
    low = (text or "").lower()
    codes = [code for code, pat in INFERENCE if re.search(pat, low)]
    attrs: list[tuple[str, str]] = []
    for kind, pat in ATTRIBUTES:
        for m in re.finditer(pat, text, re.I):
            value = m.group(1).strip().lower()
            if value and (kind, value) not in attrs:
                attrs.append((kind, value))
    return Inference(codes[0] if codes else None, codes, attrs)


def classify(verdict_text: str) -> tuple[str, str | None, Inference]:
    """(kind, code, inference). The dropdown wins when he used it; his own
    words are read only when he did not."""
    kind, code = verdicts.parse(verdict_text)
    inf = infer(verdict_text)
    if kind == "rejection" and not code:
        code = inf.primary
    return kind, code, inf


# ---------- events ----------

def record(cfg: Config, rows: list[dict]) -> int:
    """Append verdict events for rows carrying a verdict. Unique on
    (job_id, verdict, reason_code, reason_text), so a verdict re-read every
    run records once and the history stays honest."""
    events = []
    for r in rows:
        text = (r.get("krish_verdict") or "").strip()
        if not text or is_auto(r):
            continue
        kind, code, _ = classify(text)
        if kind == "none":
            continue
        events.append({
            "job_id": r.get("job_id"), "company": r.get("company"),
            "title": r.get("title"), "verdict": kind, "reason_code": code,
            "reason_text": text[:500],
            "source": r.get("verdict_source") or "sheet column A",
        })
    if events:
        db_insert(cfg, "hunter_verdict_events", events,
                  on_conflict="job_id,verdict,reason_code,reason_text",
                  ignore_duplicates=True)
    return len(events)


def load_events(cfg: Config) -> list[dict]:
    return db_get(cfg, "hunter_verdict_events",
                  {"select": "*", "order": "recorded_at.asc", "limit": "2000"})


# ---------- system codes: bugs, fixed without asking ----------

def system_findings(events: list[dict], roles: list[dict]) -> list[dict]:
    """What each system-code verdict says hunter got wrong, checked against
    the recorded roles rather than assumed. Every finding names the job_id
    that produced it so it can become a regression test."""
    by_id = {r.get("job_id"): r for r in roles}
    findings: list[dict] = []
    for e in events:
        code = e.get("reason_code")
        if not verdicts.is_system_code(code):
            continue
        role = by_id.get(e.get("job_id")) or {}
        f = {"job_id": e.get("job_id"), "code": code,
             "gate": verdicts.SYSTEM_CODE_GATE.get(code, "unknown"),
             "quote": e.get("reason_text"), "company": e.get("company"),
             "title": e.get("title")}
        if code == "dead_posting":
            verified = str(role.get("last_verified_at") or "")[:10]
            if verified:
                f["evidence"] = (f"hunter verified it live on {verified}; the "
                                 f"posting closed between then and his read")
                f["fix"] = ("re-verify liveness before a staged row is read "
                            "again, which is what regate does")
            else:
                f["evidence"] = ("never verified live; the row predates "
                                 "hunter's own gating")
                f["fix"] = ("G1 already blocks this class at sourcing; the "
                            "row needed re-gating, not a new gate")
        else:
            twin = _identity_twin(role, roles)
            if twin:
                f["evidence"] = (f"identity twin {twin['job_id']} was already "
                                 f"recorded; the dedupe key missed it")
                f["fix"] = "identity dedupe on company tokens plus title"
                f["twin"] = twin["job_id"]
            else:
                f["evidence"] = ("no identity twin; a different role at a "
                                 "company where an application is already open")
                f["fix"] = "open-application signal, not a dedupe miss"
        findings.append(f)
    return findings


def _identity_twin(role: dict, roles: list[dict]) -> dict | None:
    """Another recorded row that is the same application target: the same
    company tokens and a title close enough to be the same posting."""
    if not role.get("job_id"):
        return None
    toks = distinctive_tokens(role.get("company") or "", role.get("title") or "")
    nt = norm_title(role.get("title") or "")
    best = None
    for other in roles:
        if other.get("job_id") == role.get("job_id"):
            continue
        if not (toks & distinctive_tokens(other.get("company") or "",
                                          other.get("title") or "")):
            continue
        ont = norm_title(other.get("title") or "")
        if ont == nt or _jaccard(nt, ont) >= 0.65:
            # prefer a twin that already carries standing
            if best is None or other.get("krish_verdict"):
                best = other
    return best


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    sa -= {"the", "and", "of", "to", "for", "a", "an", "in", "at"}
    sb -= {"the", "and", "of", "to", "for", "a", "an", "in", "at"}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def open_applications(roles: list[dict]) -> dict[str, dict]:
    """Companies where Krish has an application open, keyed by company token.
    Not a suppression: a second role at the same company can be exactly what
    he wants. It is a line in the rationale so he is never surprised."""
    out: dict[str, dict] = {}
    for r in roles:
        kind, _ = verdicts.parse(r.get("krish_verdict") or "")
        if kind != "applied":
            continue
        for tok in distinctive_tokens(r.get("company") or "", r.get("title") or ""):
            out[tok] = {"job_id": r.get("job_id"), "title": r.get("title"),
                        "company": r.get("company"),
                        "at": str(r.get("verdict_at") or "")[:10]}
    return out


# ---------- taste codes ----------
#
# There is deliberately nothing here.
#
# Until 2026-09-02 this module clustered his rejections into candidate
# suppression rules and filed them for approval: no healthcare, not LATAM, no
# growth marketing. Krish's objection was that this is an infinite task, and
# he was right. The system had read his canon and his sheet and still could
# not tell a Chief of Staff role from an Enterprise Sales Director without
# being told, one exclusion at a time.
#
# The answer was inversion, not more rules. archetype.py asks whether a
# posting is one of the shapes canon section 5 says he is targeting, and
# anything that is not simply never reaches him. On the 419 roles then on
# record that gate plus the geography gate plus dedupe cut 419 to 54, and
# kept 16 of the 17 roles he had personally approved. None of the seven rules
# this section would have asked him to approve were needed.
#
# What survives here is the half that finds hunter's own bugs (dead postings
# it should have caught, duplicates it should have collapsed) and the event
# log, which is evidence. A rejection the gates did not catch is evidence
# about the archetype definition and goes to him as a canon question, never
# as a new entry on a blocklist.
