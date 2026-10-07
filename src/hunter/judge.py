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

from . import spend
from .config import Config
from .package import rationale

PROMPT_VERSION = "2026-10-03.3"
DEFAULT_MODEL = "claude-opus-5-5"
# Low, measured 2026-10-03 on the development window: AUC 0.93 against 0.92
# at high, the same Yes roles shown, 24 percent cheaper. Medium measured worse
# on those 45 roles (0.88), which at that size is mostly noise. He asked for
# cheaper; the reasoning a call writes is about 70 percent of its cost.
DEFAULT_EFFORT = "low"
# The case for a role on his sheet: medium since 2026-10-07, when he asked for
# the bill to come down three times. It had been high, at about 14 cents a row
# on the 4 October run, because it is what persuades him; every figure in it
# is still checked against the posting and his record, and a case that fails
# its checks is still replaced by an honest stub. hunter_case_effort=high puts
# it back without a deploy.
CASE_EFFORT = "medium"
MAX_TOKENS = 16000
# A posting this long is the posting; shorter is a snippet the sheet kept.
FULL_POSTING_CHARS = 400
# The presenting threshold, chosen by judge-eval on the development window
# (examples before 7 September, scored on 7 to 16 September) as the strictest
# that keeps the most of his Yes roles, then scored once on the holdout. Fit 8,
# the first guess, would have shown none of his 13 held-out Yes roles.
MIN_FIT = 6

# Published per-million-token prices, one table for every module (spend.py).
# A model missing from it is costed at the dearest price there, never at zero,
# so the judge's dollar cap cannot be switched off by changing the model.
PRICES = spend.PRICES

FAMILIES = ["country_regional_gm", "chief_commercial_strategy",
            "corp_dev_strategy", "partnerships_alliances",
            "ai_chief_of_staff_transformation", "none"]
EMPLOYER_TYPES = ["startup", "scaleup", "public_company", "pe_owned",
                  "agency_or_holding_company", "consultancy",
                  "recruiter_or_staffing", "nonprofit", "bank_insurer_asset_manager",
                  "measurement_research_or_services", "other"]
# His cut, decided 2026-10-03, enforced on the judge's own reading of the
# employer whatever verdict it gives: "Boring old businesses like comscore and
# obscure consultancies and finance roles should be auto eliminated". The one
# exception he kept is an AI transformation seat at a household name, so the
# AI transformation family is spared and the judge's instructions say when.
# A recruiter's posting is not here: the client is judged, not the recruiter.
CUT_EMPLOYERS = frozenset({"agency_or_holding_company", "consultancy",
                           "bank_insurer_asset_manager",
                           "measurement_research_or_services"})
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
    # The case for and the case against are written before the verdict, so the
    # verdict weighs both. v1 asked only for the case against, and rejected
    # every one of his 13 held-out Yes roles.
    "strongest_reason_he_says_yes": STR,
    "most_likely_reason_he_says_no": STR,
    "red_flags": {"type": "array", "items": STR},
    "verdict": {"type": "string", "enum": ["present", "hold", "reject"]},
    "fit": {"type": "integer"},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "likely_decline_code": {"type": "string", "enum": DECLINE_CODES},
    # The rationale parts, validated exactly as rationale.py validates them.
    "mandate": STR, "fit_text": STR, "risk": STR, "archetype": STR, "snippet": STR,
})

INSTRUCTIONS = """You decide whether one job posting should be put in front of Krish Raja,
and where it sits in his list.

What each mistake costs. He reads his list on a spreadsheet, quickly, and
declining a role there costs him seconds. Hiding a role he would have wanted
may cost him the job, and nobody ever finds out. So the expensive mistake is
the hidden Yes. Present a role when there is a real chance he says Yes, about
one in five or better. Reject it only when his rulings or his canon clearly
rule it out. Hold it only when one missing fact would decide it either way.

How he decides, in this order, as his rulings show:
1. The business. Would he join this company at all: what it sells, to whom,
   how it is backed, whether it is in or near his five categories. Most of his
   declines are about the business, not the seat, and this is where a reject
   usually comes from.
2. The function. Is the seat one of his five role families, or something else
   wearing a senior title (data governance, revenue cycle, growth marketing,
   sales analytics, engineering leadership).
3. Level and scope. Who it reports to, what it owns, whether there is room to
   build. Titles mislead in both directions: he has applied for seats titled
   Lead and declined seats titled Head of.
4. Requirements he lacks.
5. Logistics. City and pay, under his rules below.

His rules, decided 2026-10-03. Each role says whether its company is one of
his TOP companies: his own list of AI companies, his Target Companies, a
company he has said Yes to, or a close lookalike of those.
- Where. New York, London, UK remote and US remote, anywhere. The San
  Francisco Bay Area only at a top company: he would move for the right one.
  Anywhere else is a reject unless the posting allows one of those.
- At a top company the bar bends, it never rises: a base below $200,000 is
  fine when the top of the range or the on-target earnings reach $250,000, and
  Director and Manager titles in commercial, partnerships, business
  development, GTM and strategy are in scope.
- Cut, whatever the seat: banks, insurers and asset managers; consultancies;
  agencies and holding companies; measurement and research firms; staffing
  and services firms hiring for themselves. The one exception is an explicit
  AI transformation mandate at a household name. A recruiter's posting for an
  unnamed client is judged on the client as the posting describes it.

What his Yes rulings show, which a careful reader of his declines tends to miss:
- He applies for stretch roles at businesses he wants. In August and early
  September he applied for enterprise sales leadership at Sierra and at
  Anthropic, business development at Harvey and enterprise GTM at a blockchain
  company, none of which matched his background line by line. At a business he
  wants, a requirement he lacks lowers fit by a point or so. It is a reason to
  reject mainly where the business is ordinary to him, or where the domain is
  regulated and deep (canon 5 names insurance, clinical and banking back-office)
  or a specialist channel such as hyperscaler alliances.
- Revenue-carrying seats are fine. Canon 5: engine-builder over quota-carrier
  is a scoring preference, not a cut, and he has applied for quota-carrying
  seats.
- Pay. A range meets the $200,000 floor when its top is at or above $200,000;
  never count the bottom of a range against a role. He has said Yes to
  $180,000 to $250,000 and to $165,000 to $280,000. Pay not stated is the
  normal case and is neutral, never a flag on its own. A stated ceiling under
  $200,000 is a real flag, and so is pay that is implausible as written (an
  hourly or monthly figure shown as a salary, a range that spans 100x, a
  currency mix-up), which you report as unknown, never as meeting the floor.
- Canon 5 names AI transformation and AI Chief of Staff as a target family. A
  seat whose mandate is AI transformation is in-family wherever it sits;
  whether he wants that employer is the business question, answered from his
  rulings and canon.

Weigh these and name them when you see them; only his rulings make one decisive:
- A posting by a recruiter or staffing agency, where the employer is unknown.
- Agency holding companies, consultancies, private equity firms and PE-owned
  businesses: he has said yes to some and no to most.
- Nonprofits, consumer goods, healthcare providers, and banks, insurers and
  asset managers (for those, his words: "I'd reject that company unless the
  role was ideal").

Quotes. Every answer section has a quote field. Copy the shortest phrase from the
posting, character for character, that supports the answer. If the posting is
silent on that point, leave the quote empty and say "not stated" in the answer.
Never paraphrase inside a quote. Quotes are checked against the posting, and an
answer whose quote is not in the posting is thrown away. When the posting text
is short or missing, judge from the company, the title and his rulings, leave
the quotes empty and set confidence low; never reject a role only because its
posting is thin.

His rulings are the best evidence of his taste, better than any policy line.
Where the documents below disagree with each other, his canon wins over the
sheet tabs, and a recent ruling wins over an old one. Some sheet lines are out
of date: the Profile tab's "Never present a role <9/10" was replaced on
2026-09-03 by canon 9.2, under which the score orders and does not block.

Before the verdict, write the strongest reason he would say Yes and the most
likely reason he would say No, each from the posting and his rulings. Then
decide.

Fit, 0 to 10, is the chance he says Yes, and it orders his list: 9 or 10, very
likely yes; 8, more likely yes than no; 7, a real chance, about one in three;
6, possible, about one in five; 5, unlikely, about one in ten; 0 to 4, his
rulings or canon rule it out. A role you present has fit 6 or more; a role you
reject has fit 4 or less. Use the whole range.

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
    company_note: str = ""  # whether it is one of his top companies, and why


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
            f"Where it was found: {role.source or 'unknown'}\n"
            f"Top company: {role.company_note or 'no, not on his top list'}\n\n"
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


def quote_problems(answers: dict, posting: str, *, require: bool | None = None) -> list[str]:
    hay = fold(posting)
    bad = []
    for section in ("business", "function", "level_scope", "requirements_he_lacks", "logistics"):
        q = fold((answers.get(section) or {}).get("quote", "")).strip(" .\"'")
        if not q:
            continue
        if q not in hay:
            bad.append(f"the {section} quote is not in the posting: "
                       f"{(answers[section]['quote'] or '')[:80]!r}")
    # On a full posting the business and the function must be quoted. On a
    # snippet there may be nothing to quote, and a thin posting is never a
    # reason to refuse a judgement: v1 left ten roles pending for it.
    if (require if require is not None else len(posting or "") >= FULL_POSTING_CHARS):
        for section in ("business", "function"):
            if not fold((answers.get(section) or {}).get("quote", "")).strip():
                bad.append(f"the {section} answer has no quote from the posting")
    return bad


THIN_MARKER = "[The full posting is no longer available"


def quotable(role: Role) -> str:
    """What a quote may come from: the posting, and the role's own title,
    location and pay fields, which are the posting's too. On the holdout, nine
    roles went pending because the judge quoted a pay band from the pay field
    ("$206.8K - $310.2K") that the stored posting text did not repeat. A sheet
    snippet standing in for a lost posting is thin, whatever its length: the
    business and the function cannot be required to be quoted from it."""
    return (role.posting or "") + "\n" + f"{role.title}\n{role.location}\n{role.comp}"


def is_thin(role: Role) -> bool:
    p = role.posting or ""
    return p.startswith(THIN_MARKER) or len(p) < FULL_POSTING_CHARS


def check(answer: dict, posting: str, evidence: str = "", *,
          thin: bool | None = None) -> tuple[list[str], list[str]]:
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
        critical += quote_problems(answer, posting, require=not (
            thin if thin is not None else len(posting) < FULL_POSTING_CHARS))
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
    return spend.usd_of(model, usage, batch=batch)


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
    critical, text = check(answer, quotable(role), user_prompt(role) + "\n" + evidence,
                           thin=is_thin(role))
    if critical:
        pending.problems = critical + text
        pending.answers = answer
        return answer, pending
    j = Judgement(
        job_id=role.job_id, verdict=answer["verdict"], fit=int(answer["fit"]),
        confidence=answer.get("confidence", ""),
        answers={k: answer.get(k) for k in ("business", "function", "level_scope",
                                            "requirements_he_lacks", "logistics",
                                            "strongest_reason_he_says_yes",
                                            "most_likely_reason_he_says_no")},
        red_flags=list(answer.get("red_flags") or []),
        likely_decline_code=answer.get("likely_decline_code", "none"),
        why_it_fits="" if text else rationale.assemble(
            {"mandate": answer["mandate"], "fit": answer["fit_text"], "risk": answer["risk"]}),
        snippet="" if text else answer.get("snippet", ""), model=model, served_model=served,
        usage=usage, usd=usd_of(model, usage, batch=batch),
        problems=[f"sheet text not used: {t}" for t in text])
    etype = ((answer.get("business") or {}).get("employer_type") or "")
    family = ((answer.get("function") or {}).get("family") or "")
    if etype in CUT_EMPLOYERS and family != "ai_chief_of_staff_transformation" \
            and j.verdict != "reject":
        j.problems.append(f"his rule cuts a {etype.replace('_', ' ')}; rejected")
        j.verdict, j.fit = "reject", min(j.fit or 0, 3)
        j.likely_decline_code = "business_uninteresting"
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


# ---------- the case for a role, for his sheet ----------

CASE_PROMPT = """This time you are not deciding whether he should see this role: it is
already on his list. Write the text of its row, so he can decide in one read
whether to say Yes.

- mandate: what the seat actually is, in the posting's own terms, one sentence.
- fit: the strongest honest case that this role suits what he wants. Name the
  role family from canon 5, the specific proof from his record (canon 3, his
  Profile, his Interview Answers) that matches what the posting asks for, and
  what in the posting should make him want it: the scope, the mandate, the
  company, the pay, the place. Where one of his past rulings is a close match,
  say so. Two or three sentences. Persuade with facts; where the case is thin,
  say what it rests on rather than stretching it.
- risk: the one thing most likely to make him say no, in one sentence.
- archetype: the role family in plain words.
- snippet: what the company sells and what the seat is, under 240 characters.

mandate, fit and risk together must stay under 750 characters: it is one cell
he reads at a glance. Every number must appear in the posting or in his record.
No em dashes; never the words leverage, synergy, passionate, rockstar,
world-class."""


def case_params(system: str, role: Role, *, model: str, effort: str,
                problems: list[str] | None = None) -> dict:
    """Same cached context as the judge, so the case costs a cache read."""
    from .package.rationale import RATIONALE_SCHEMA
    content = user_prompt(role) + "\n\n" + CASE_PROMPT
    if problems:
        content += ("\n\nA previous answer was thrown away for these reasons. "
                    "Fix them:\n- " + "\n- ".join(problems))
    return {"model": model, "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "output_config": {"effort": effort,
                              "format": {"type": "json_schema", "schema": RATIONALE_SCHEMA}},
            "messages": [{"role": "user", "content": content}]}


def thin_case(role: Role) -> str:
    """The honest fallback: says what is known and that no case was written."""
    return (f"{role.title} at {role.company}. FIT: no grounded case could be "
            f"written for this role, so read the JD before you decide. "
            f"RISK: unknown until you have read it.")


def make_the_case(cfg: Config, system: str, role: Role, *, client=None,
                  model: str | None = None, effort: str | None = None
                  ) -> tuple[str, str, list[str], float]:
    """(Why It Fits, JD Snippet, problems, usd) for a row on his sheet, in the
    shape Pipeline uses (mandate, FIT, RISK), written from everything he has
    written rather than canon 5 alone. Never told what the judge decided, so
    a list can carry it without giving the judgement away. Every figure is
    checked against the posting and his record; a case that fails twice is
    replaced by an honest stub, never shipped."""
    model = model or cfg.optional("hunter_judge_model", DEFAULT_MODEL)
    effort = effort or cfg.optional("hunter_case_effort", CASE_EFFORT)
    client = client or _client(cfg)
    snippet_fallback = rationale.deterministic_snippet(role.company, role.title, role.posting)
    problems: list[str] | None = None
    usd = 0.0
    for _attempt in (1, 2):
        try:
            msg = client.messages.create(**case_params(system, role, model=model,
                                                       effort=effort, problems=problems))
        except Exception as e:
            return thin_case(role), snippet_fallback, [f"the call failed: {e.__class__.__name__}"], usd
        usd += spend.record(cfg, "case", model, usage_of(msg),
                            served_model=getattr(msg, "model", "") or "",
                            job_id=role.job_id)
        if getattr(msg, "stop_reason", "") in ("refusal", "max_tokens"):
            problems = [f"the answer stopped: {msg.stop_reason}"]
            continue
        try:
            parts = json.loads(text_of(msg))
        except (ValueError, TypeError):
            problems = ["the answer was not valid JSON"]
            continue
        # Figures may come from anything the writer was shown: the role's own
        # pay and location fields, the posting, his record. The pay field is
        # often all a stored posting has of the band, because the posting
        # text is kept to its first 6,000 characters.
        problems = rationale.validate(parts, user_prompt(role) + "\n" + system)
        if not problems:
            snippet = " ".join(parts["snippet"].split())
            return rationale.assemble(parts), snippet, [], round(usd, 6)
    return thin_case(role), snippet_fallback, problems or [], round(usd, 6)


# ---------- what a judgement means for staging ----------

def disposition(j: Judgement, *, min_fit: int = MIN_FIT) -> str:
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
