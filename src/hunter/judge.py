"""The judge: would Krish want this role in front of him?

Until 2026-10-03 nothing with judgement decided what reached him. Fourteen
regex gates and two arithmetic scores did, and the one model call in the path
ran AFTER a role was staged, to write Why It Fits. That model wrote "KBRA is a
credit ratings agency" and "a 501(c)(3) advocacy nonprofit", and neither role
could be stopped, because the writer had no veto. Each failure was answered
with another hand-written rule, and no rule generalises to the next jeweller.

This replaces the decision, not the facts. Clear-cut facts (a duplicate, a
dead posting, a stated pay ceiling under the floor, a city he cannot work in)
stay as rules before it. Everything that needs judgement comes here, to a
strong model that reads everything Krish has written about himself and every
ruling he has made, answers the questions he actually asks of a role, and
quotes the posting for each answer.

The rules it keeps (CLAUDE.md section 1):
- Every quote is checked against the posting. A quote that is not in the
  posting gets one corrective retry and is then refused: the role is marked
  judge_pending and never presented on an unread claim.
- Why It Fits and the snippet pass the same validator the rationale writer
  used (rationale.validate): no figure that is not in the posting, no banned
  word, no em dash.
- The model that actually answered is recorded. An answer from any model but
  the one measured by judge-eval is held, never presented.
- No answer is an answer of its own: judge_pending, never a default.
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field

from .config import Config
from .package import rationale

PROMPT_VERSION = "2026-10-03.1"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
MAX_TOKENS = 16000

# Published per-million-token prices for the default model, used only to turn
# a call's usage into dollars for hunter_judge_calls. Cache writes are 1.25x
# input, cache reads are the published cache price; the Batches API is half.
PRICES = {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20,
                              "cache_write": 5.0}}

FAMILIES = ["country_regional_gm", "chief_commercial_strategy",
            "corp_dev_strategy", "partnerships_alliances",
            "ai_chief_of_staff_transformation", "none"]
EMPLOYER_TYPES = ["startup", "scaleup", "public_company", "pe_owned",
                  "agency_or_holding_company", "consultancy",
                  "recruiter_or_staffing", "nonprofit", "bank_insurer_asset_manager",
                  "other"]
DECLINE_CODES = ["none", "domain_expertise", "function_wrong", "business_uninteresting",
                 "seniority_below", "seniority_above", "requirements_mismatch",
                 "geo_language", "comp_below_bar", "stage_wrong", "too_much_travel"]


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
SCHEMA = _obj({
    "business": _obj({
        "what_it_sells": STR, "to_whom": STR,
        "category": {"type": "string", "enum": ["his_category", "adjacent", "outside", "unclear"]},
        "employer_type": {"type": "string", "enum": EMPLOYER_TYPES},
        "rulings_that_bear": {"type": "array", "items": STR},
        "quote": STR}),
    "function": _obj({
        "family": {"type": "string", "enum": FAMILIES},
        "signals": {"type": "array", "items": STR},
        "quote": STR}),
    "level_scope": _obj({"reports_to": STR, "team": STR, "mandate": STR, "quote": STR}),
    "requirements_he_lacks": _obj({"items": {"type": "array", "items": STR}, "quote": STR}),
    "logistics": _obj({
        "location": STR,
        "location_ok": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "pay": STR,
        "pay_meets_floor": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "quote": STR}),
    "verdict": {"type": "string", "enum": ["present", "hold", "reject"]},
    "fit": {"type": "integer"},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "likely_decline_code": {"type": "string", "enum": DECLINE_CODES},
    "most_likely_reason_he_says_no": STR,
    "red_flags": {"type": "array", "items": STR},
    # The rationale parts, validated exactly as rationale.py validates them.
    "mandate": STR, "fit_text": STR, "risk": STR, "archetype": STR, "snippet": STR,
})

INSTRUCTIONS = """You decide whether one job posting should be put in front of Krish Raja.

He reviews a short list twice a week. A role you present costs him attention he
would rather spend selling and writing; a great role you hide may cost him the
job. Present a role only when, having read everything below, you believe he
would say Yes. Hold it when it could be right but something material is
unclear. Reject it when his own rulings or stated policy rule it out. An empty
week is a correct outcome; filling the list is not your job.

How he decides, in this order, as his rulings show:
1. The business. Would he join this company at all: what it sells, to whom, how
   it is backed, whether it is in or near his five categories. Most of his
   declines are about the business, not the seat.
2. The function. Is the seat one of his five role families, or something else
   wearing a senior title (data governance, revenue cycle, a quota-carrying
   sales seat with no build mandate, an individual contributor role).
3. Level and scope. Who it reports to, what it owns, whether there is a
   mandate to build.
4. Requirements he lacks. A language, a domain credential, years inside an
   industry he has not worked in.
5. Logistics. City (NYC, London, US-remote, UK-remote) and pay.

Name these explicitly when you see them:
- A posting by a recruiter or staffing agency, where the employer is unknown.
- Agency holding companies and consultancies: he has said yes to some and no to
  most; weigh his rulings, not a rule.
- Nonprofits, consumer goods, healthcare providers, and banks, insurers and asset
  managers (for those, his words: "I'd reject that company unless the role was
  ideal").
- Pay that is implausible as written (an hourly or monthly figure presented as
  a salary, a range that spans 100x, a currency mix-up). Report it as unknown,
  never as meeting the floor.
- Pay whose stated ceiling is under $200,000.

Quotes. Every answer section has a quote field. Copy the shortest phrase from the
posting, character for character, that supports the answer. If the posting is
silent on that point, leave the quote empty and say "not stated" in the answer.
Never paraphrase inside a quote. Quotes are checked against the posting, and an
answer whose quote is not in the posting is thrown away.

His rulings are the best evidence of his taste, better than any policy line.
Where the documents below disagree with each other, his canon wins over the
sheet tabs, and a recent ruling wins over an old one. Some sheet lines are out
of date: the Profile tab's "Never present a role <9/10" was replaced on
2026-09-03 by canon 9.2, under which the score orders and does not block.

Fit, 0 to 10: 9 or 10 he would be excited; 8 a clear yes; 7 plausible but he
may well decline; 5 or 6 probably no; 0 to 4 no.

The written parts (mandate, fit_text, risk, archetype, snippet) go on his sheet:
- mandate: what the seat actually is, in the posting's own terms, one sentence.
- fit_text: why it fits him specifically, naming the role family and the proof
  from his record that matches, one or two sentences.
- risk: the one thing most likely to make him decline.
- archetype: the role family in plain words.
- snippet: what the company sells and what the seat is, under 240 characters.
Rules for those: every number must appear in the posting; no em dashes; never
the words leverage, synergy, passionate, rockstar, world-class."""


@dataclass
class Role:
    job_id: str
    company: str
    title: str
    location: str = ""
    comp: str = ""
    url: str = ""
    posting: str = ""      # the text the judge reads and quotes are checked against
    source: str = ""


@dataclass
class Judgement:
    job_id: str
    verdict: str            # present | hold | reject | pending
    fit: int | None
    confidence: str
    answers: dict
    red_flags: list
    likely_decline_code: str
    why_it_fits: str
    snippet: str
    model: str
    served_model: str
    prompt_version: str = PROMPT_VERSION
    problems: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    usd: float = 0.0

    def row_patch(self) -> dict:
        """The judge's record on hunter_seen_roles."""
        return {
            "judge_verdict": None if self.verdict == "pending" else self.verdict,
            "judge_fit": self.fit, "judge_confidence": self.confidence,
            "judge_answers": self.answers, "judge_red_flags": self.red_flags,
            "judge_model": self.model, "judge_served_model": self.served_model,
            "judge_prompt_version": self.prompt_version,
            "judge_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# ---------- the prompt ----------

def _tab_text(grid: list[list]) -> str:
    lines = []
    for row in grid or []:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def ruling_line(when: str, label: str, company: str, title: str, words: str,
                comp: str = "", location: str = "") -> str:
    bits = [f"[{when or 'undated'}]", "YES" if label == "yes" else "NO",
            company, title]
    if comp:
        bits.append(comp)
    if location:
        bits.append(location)
    return " | ".join(bits) + f" | his words: {words}"


def build_system(*, canon_sections: dict[str, str], tabs: dict[str, str],
                 targets_text: str, rulings: list[str]) -> str:
    """The fixed context, identical for every role in a run so it is cached.
    Deterministic: same inputs, same bytes."""
    parts = [INSTRUCTIONS, "\n\n=== HIS CANON ==="]
    for n in sorted(canon_sections):
        parts.append(f"\n--- canon {n} ---\n{canon_sections[n]}")
    for name in sorted(tabs):
        parts.append(f"\n\n=== HIS SHEET: {name} ===\n{tabs[name]}")
    parts.append(f"\n\n=== HIS TARGET COMPANIES ===\n{targets_text}")
    parts.append("\n\n=== HIS RULINGS, oldest first ===\n" + "\n".join(rulings))
    return "".join(parts)


def gather_context(cfg: Config, sheet, canon, *, rulings: list[str]) -> str:
    """Everything he has written, read live: canon 2 to 6 and the tabs that hold
    his profile, his own answers, his rubric and his companies."""
    sections = {}
    for n in ("2", "3", "4", "5", "6"):
        try:
            sections[n] = canon.section_text(n)
        except Exception:
            continue
    tabs = {}
    for name, rng in (("Profile", "Profile!A1:C80"),
                      ("Interview Answers", "Interview Answers!A1:B60"),
                      ("Scoring Reference", "Scoring Reference!A1:C100")):
        try:
            tabs[name] = _tab_text(sheet.read_tab_values(rng))
        except Exception:
            continue
    targets_text = ""
    try:
        from . import targets
        lines = []
        for t in targets.read(sheet):
            lines.append(" | ".join(x for x in (
                t.name, t.category, f"tier {t.tier}" if t.tier else "", t.stage,
                t.location, t.why, t.note) if x))
        targets_text = "\n".join(lines)
    except Exception:
        pass
    return build_system(canon_sections=sections, tabs=tabs,
                        targets_text=targets_text, rulings=rulings)


def user_prompt(role: Role, problems: list[str] | None = None) -> str:
    head = (f"THE ROLE\nCompany: {role.company}\nTitle: {role.title}\n"
            f"Location: {role.location or 'not stated'}\nPay: {role.comp or 'not stated'}\n"
            f"Where it was found: {role.source or 'unknown'}\n\n"
            f"THE POSTING (verbatim, the only source of fact about the role):\n"
            f"{role.posting or '(no posting text)'}")
    if problems:
        head += ("\n\nA previous answer for this role was thrown away for these "
                 "reasons. Answer again and fix them:\n- " + "\n- ".join(problems))
    return head


# ---------- checking an answer ----------

_FOLD = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
         "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2026": "..."}


def fold(text: str) -> str:
    text = (text or "").translate(str.maketrans(_FOLD)).lower()
    return " ".join(text.split())


def quote_problems(answers: dict, posting: str) -> list[str]:
    hay = fold(posting)
    bad = []
    for section in ("business", "function", "level_scope", "requirements_he_lacks", "logistics"):
        q = fold((answers.get(section) or {}).get("quote", "")).strip(" .\"'")
        if not q:
            continue
        if q not in hay:
            bad.append(f"the {section} quote is not in the posting: "
                       f"{(answers[section]['quote'] or '')[:80]!r}")
    for section in ("business", "function"):
        if not fold((answers.get(section) or {}).get("quote", "")).strip():
            bad.append(f"the {section} answer has no quote from the posting")
    return bad


def check(answer: dict, posting: str, evidence: str = "") -> tuple[list[str], list[str]]:
    """(critical problems, text problems).

    Critical problems void the judgement: a verdict or fit outside its range,
    or a quote that is not in the posting. Text problems only void the words
    written for his sheet; the verdict stands and the caller writes those words
    another way, because a sound judgement should not be thrown away over a
    sentence.
    """
    critical = []
    if answer.get("verdict") not in ("present", "hold", "reject"):
        critical.append(f"verdict {answer.get('verdict')!r} is not present, hold or reject")
    fit = answer.get("fit")
    if not isinstance(fit, int) or not 0 <= fit <= 10:
        critical.append(f"fit {fit!r} is not a whole number from 0 to 10")
    if posting.strip():
        critical += quote_problems(answer, posting)
    parts = {"mandate": answer.get("mandate", ""), "fit": answer.get("fit_text", ""),
             "risk": answer.get("risk", ""), "snippet": answer.get("snippet", "")}
    # A figure may come from the posting or from his own recorded proof points
    # (canon 3: "$0 to $12M ARR"), both of which are evidence; never from
    # nowhere. Quotes, above, are checked against the posting alone.
    return critical, rationale.validate(parts, posting + "\n" + evidence)


# ---------- one call ----------

def _client(cfg: Config):
    import anthropic
    key = cfg.optional("hunter_anthropic_api_key")
    if not key:
        raise RuntimeError("no hunter_anthropic_api_key in system_config")
    return anthropic.Anthropic(api_key=key)


def request_params(system: str, role: Role, *, model: str, effort: str,
                   problems: list[str] | None = None) -> dict:
    """The exact request, shared by the live call and the Batches API."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system,
                    "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": effort,
                          "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": user_prompt(role, problems)}],
    }


def usd_of(model: str, usage: dict, *, batch: bool = False) -> float:
    p = PRICES.get(model)
    if not p:
        return 0.0
    usd = (usage.get("input_tokens", 0) * p["in"]
           + usage.get("output_tokens", 0) * p["out"]
           + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
           + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]) / 1e6
    return round(usd / 2 if batch else usd, 6)


def usage_of(message) -> dict:
    u = getattr(message, "usage", None)
    return {k: int(getattr(u, k, 0) or 0) for k in
            ("input_tokens", "output_tokens", "cache_read_input_tokens",
             "cache_creation_input_tokens")}


def text_of(message) -> str:
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text")


def interpret(role: Role, message, *, model: str, batch: bool = False,
              evidence: str = "") -> tuple[dict | None, Judgement]:
    """A model response turned into a judgement, or a pending one saying why."""
    usage = usage_of(message)
    served = getattr(message, "model", "") or ""
    stop = getattr(message, "stop_reason", "")
    pending = Judgement(job_id=role.job_id, verdict="pending", fit=None, confidence="",
                        answers={}, red_flags=[], likely_decline_code="none",
                        why_it_fits="", snippet="", model=model, served_model=served,
                        usage=usage, usd=usd_of(model, usage, batch=batch))
    if stop == "refusal":
        pending.problems = ["the model declined to answer"]
        return None, pending
    if stop == "max_tokens":
        pending.problems = ["the answer was cut off at the token limit"]
        return None, pending
    try:
        answer = json.loads(text_of(message))
    except (ValueError, TypeError):
        pending.problems = ["the answer was not valid JSON"]
        return None, pending
    critical, text = check(answer, role.posting, evidence)
    if critical:
        pending.problems = critical + text
        pending.answers = answer
        return answer, pending
    j = Judgement(
        job_id=role.job_id, verdict=answer["verdict"], fit=int(answer["fit"]),
        confidence=answer.get("confidence", ""),
        answers={k: answer.get(k) for k in ("business", "function", "level_scope",
                                            "requirements_he_lacks", "logistics",
                                            "most_likely_reason_he_says_no")},
        red_flags=list(answer.get("red_flags") or []),
        likely_decline_code=answer.get("likely_decline_code", "none"),
        why_it_fits="" if text else rationale.assemble(
            {"mandate": answer["mandate"], "fit": answer["fit_text"], "risk": answer["risk"]}),
        snippet="" if text else answer.get("snippet", ""), model=model, served_model=served,
        usage=usage, usd=usd_of(model, usage, batch=batch),
        problems=[f"sheet text not used: {t}" for t in text])
    if served and not served.startswith(model):
        # Not the judge that was measured. Its reasoning is kept; its say is not.
        j.problems.append(f"answered by {served}, not {model}; held")
        if j.verdict == "present":
            j.verdict = "hold"
    return answer, j


def judge_role(cfg: Config, system: str, role: Role, *, client=None,
               model: str | None = None, effort: str | None = None) -> Judgement:
    """One judgement, with one corrective retry when the answer fails its checks."""
    model = model or cfg.optional("hunter_judge_model", DEFAULT_MODEL)
    effort = effort or cfg.optional("hunter_judge_effort", DEFAULT_EFFORT)
    client = client or _client(cfg)
    problems: list[str] | None = None
    spent, total_usage = 0.0, {}
    j = None
    for _attempt in (1, 2):
        try:
            message = client.messages.create(
                **request_params(system, role, model=model, effort=effort, problems=problems))
        except Exception as e:
            j = Judgement(job_id=role.job_id, verdict="pending", fit=None, confidence="",
                          answers={}, red_flags=[], likely_decline_code="none",
                          why_it_fits="", snippet="", model=model, served_model="",
                          problems=[f"the call failed: {e.__class__.__name__}"])
            break
        _, j = interpret(role, message, model=model, evidence=system)
        spent += j.usd
        for k, v in j.usage.items():
            total_usage[k] = total_usage.get(k, 0) + v
        if j.verdict != "pending" or "declined" in (j.problems[0] if j.problems else ""):
            break
        problems = j.problems
    j.usd, j.usage = round(spent, 6), total_usage or j.usage
    return j


# ---------- what a judgement means for staging ----------

def disposition(j: Judgement, *, min_fit: int = 8) -> str:
    """staging | held | blocked | judge_pending."""
    if j.verdict == "pending":
        return "judge_pending"
    if j.verdict == "reject":
        return "blocked"
    if j.verdict == "present" and (j.fit or 0) >= min_fit:
        return "staging"
    return "held"


def reason_line(j: Judgement) -> str:
    """One line for rejection_reason and the run summary."""
    why = (j.answers.get("most_likely_reason_he_says_no") or "").strip()
    flags = "; ".join(j.red_flags[:2])
    core = flags or why or "no reason given"
    return re.sub(r"\s+", " ", f"JUDGE {j.verdict} fit {j.fit}: {core}")[:300]
