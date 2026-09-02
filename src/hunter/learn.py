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

FILTER_KEY = "hunter_learned_filters"

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


# ---------- taste codes: proposals, never silent rules ----------

MIN_CLUSTER = 2


def clusters(events: list[dict]) -> list[dict]:
    """Candidate rules. An attribute Krish named in his own words counts on
    the first occurrence, because that is him stating a rule rather than
    hunter inferring one. An attribute inferred from role metadata needs
    MIN_CLUSTER occurrences before it is worth his time."""
    named: dict[tuple, dict] = {}
    inferred: dict[tuple, dict] = {}
    for e in events:
        if e.get("verdict") != "rejection":
            continue
        code = e.get("reason_code")
        if not code or verdicts.is_system_code(code):
            continue
        inf = infer(e.get("reason_text") or "")
        for kind, value in inf.attributes:
            acode = ATTRIBUTE_CODE.get(kind, code)
            c = named.setdefault((acode, kind, value),
                                 {"code": acode, "kind": kind, "value": value,
                                  "evidence": "his words", "job_ids": [],
                                  "quotes": []})
            c["job_ids"].append(e.get("job_id"))
            c["quotes"].append(e.get("reason_text"))
        key = (code, "company", slugify(e.get("company") or ""))
        if key[2]:
            c = inferred.setdefault(key, {"code": code, "kind": "company",
                                          "value": key[2],
                                          "evidence": "repeat occurrence",
                                          "job_ids": [], "quotes": []})
            c["job_ids"].append(e.get("job_id"))
            c["quotes"].append(e.get("reason_text"))
    out = list(named.values())
    out += [c for c in inferred.values() if len(set(c["job_ids"])) >= MIN_CLUSTER]
    for c in out:
        c["job_ids"] = sorted(set(j for j in c["job_ids"] if j))
        c["quotes"] = sorted(set(q for q in c["quotes"] if q))
    return sorted(out, key=lambda c: (-len(c["job_ids"]), c["code"], c["value"]))


# Which code an attribute belongs to. He often names two things in one
# sentence ("not interested in Salesforce as a business nor sales analytics
# as what I do all day"); each half is filed under the code it actually is,
# not under whichever matched first.
ATTRIBUTE_CODE = {"business": "business_uninteresting", "domain": "domain_expertise",
                  "function": "function_wrong", "geo": "geo_language",
                  "language": "geo_language", "company": "business_uninteresting"}

SCOPE = {"company": "company", "business": "company_or_jd", "domain": "jd_or_title",
         "function": "jd_or_title", "geo": "location_or_jd", "language": "jd"}

SCOPE_ENGLISH = {
    "company": "company name",
    "company_or_jd": "company name or job description",
    "jd_or_title": "role title or job description",
    "location_or_jd": "location or job description",
    "jd": "job description",
}


def impact(rule: dict, roles: list[dict]) -> list[str]:
    """Which already-recorded roles this rule would have dropped. He needs
    this before approving: a rule that would have cost him a role he wants
    is a rule to refuse."""
    hit = []
    for r in roles:
        m = check([rule], company=r.get("company") or "", title=r.get("title") or "",
                  location=r.get("location") or "", jd=r.get("why_it_fits") or "")
        if m:
            hit.append(f"{r.get('company')} / {r.get('title')}")
    return hit


def rule_from_cluster(c: dict) -> dict:
    return {"code": c["code"], "kind": c["kind"], "value": c["value"],
            "scope": SCOPE.get(c["kind"], "jd_or_title"),
            "action": "drop", "job_ids": c["job_ids"],
            "quotes": c["quotes"][:3]}


def rule_id(rule: dict) -> str:
    return f"{rule['code']}:{rule['kind']}:{rule['value']}"


def file_proposals(cfg: Config, cands: list[dict], *, apply: bool,
                   roles: list[dict] | None = None) -> list[str]:
    """One workflow_proposals row per candidate rule, skipping any already
    filed or already approved. Nothing is suppressed by filing one."""
    existing = {p.get("title") for p in db_get(
        cfg, "workflow_proposals",
        {"select": "title,status", "agent_id": "eq.hunter", "limit": "500"})}
    live = {rule_id(r) for r in load_filters(cfg)}
    filed: list[str] = []
    for c in cands:
        rule = rule_from_cluster(c)
        rid = rule_id(rule)
        if rid in live:
            continue
        title = f"hunter: stop presenting roles matching {rid}"
        if title in existing:
            continue
        quotes = "\n".join(f'  "{q}"' for q in rule["quotes"])
        would_drop = impact(rule, roles or [])
        preview = ("\n".join(f"  {h}" for h in would_drop[:12])
                   or "  none of the roles on record")
        desc = (
            f"Krish declined {len(rule['job_ids'])} role(s) with reason "
            f"{rule['code']}, naming {rule['kind']} {rule['value']!r}.\n\n"
            f"His words:\n{quotes}\n\n"
            f"Job ids: {', '.join(rule['job_ids'])}\n\n"
            f"If approved, hunter drops a sourced role when {rule['value']!r} "
            f"appears in its {SCOPE_ENGLISH.get(rule['scope'], rule['scope'])}, "
            f"records the rule id {rid} against the row, and names the "
            f"suppression in every run report.\n\n"
            f"Applied to the {len(roles or [])} roles already on record it "
            f"would have dropped {len(would_drop)}:\n{preview}\n\n"
            f"Reverse it with: python -m hunter.run learn --revoke {rid}")
        if apply:
            db_insert(cfg, "workflow_proposals", [{
                "agent_id": "hunter", "proposal_type": "quality_improve",
                "title": title, "description": desc, "status": "proposed",
                "priority": "medium", "quality_impact": "fewer unsuitable rows",
                "proposed_changes": {"rule": rule},
            }])
        filed.append(title)
    return filed


# ---------- approved rules ----------

def load_filters(cfg: Config) -> list[dict]:
    raw = cfg.optional(FILTER_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_filters(cfg: Config, rules: list[dict]) -> None:
    db_insert(cfg, "system_config",
              [{"key": FILTER_KEY, "value": json.dumps(rules, indent=2)}],
              on_conflict="key", merge=True)


def promote_approved(cfg: Config, *, apply: bool) -> list[str]:
    """Approved proposals become live rules. Approval is Krish's act in
    Control Center; hunter only carries the decision across."""
    rows = db_get(cfg, "workflow_proposals",
                  {"select": "id,title,status,proposed_changes,executed_at",
                   "agent_id": "eq.hunter", "status": "eq.approved",
                   "limit": "200"})
    rules = load_filters(cfg)
    live = {rule_id(r) for r in rules}
    promoted: list[str] = []
    for p in rows:
        rule = (p.get("proposed_changes") or {}).get("rule")
        if not rule or rule_id(rule) in live:
            continue
        rule = dict(rule)
        rule["approved_proposal_id"] = p.get("id")
        rules.append(rule)
        live.add(rule_id(rule))
        promoted.append(rule_id(rule))
        if apply:
            db_patch(cfg, "workflow_proposals", {"id": str(p["id"])},
                     {"executed_at": "now()", "status": "completed"})
    if promoted and apply:
        save_filters(cfg, rules)
    return promoted


def revoke(cfg: Config, rid: str) -> bool:
    rules = load_filters(cfg)
    kept = [r for r in rules if rule_id(r) != rid]
    if len(kept) == len(rules):
        return False
    save_filters(cfg, kept)
    return True


def check(rules: list[dict], *, company: str, title: str,
          location: str = "", jd: str = "") -> tuple[dict, str] | None:
    """The first approved rule this role trips, with the sentence to record.
    Matching is substring on lowercased text, because the values are Krish's
    own words and he writes them the way the postings do."""
    for rule in rules:
        value = (rule.get("value") or "").lower()
        if not value:
            continue
        scope = rule.get("scope") or "jd_or_title"
        hay = {
            "company": slugify(company),
            "company_or_jd": f"{company} {jd}".lower(),
            "jd_or_title": f"{title} {jd}".lower(),
            "location_or_jd": f"{location} {jd}".lower(),
            "jd": jd.lower(),
        }.get(scope, f"{title} {jd}".lower())
        needle = slugify(value) if scope == "company" else value
        if needle and needle in hay:
            return rule, (f"learned filter {rule_id(rule)}: Krish declined "
                          f"{len(rule.get('job_ids') or [])} role(s) naming "
                          f"{rule.get('kind')} {value!r}")
    return None
