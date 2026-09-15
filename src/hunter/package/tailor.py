"""tailor(): the only model call in the package path.

Until 2026-09-14 it returned a decision object only, with no field for free
document text, so fabrication was structurally impossible. That also meant the
per-role customisation amounted to one block of five plus a 12-word JD mirror,
and it produced a visible defect: build_cv swapped summary paragraph 1 for a
block while leaving the master's own paragraphs 2 and 3 in place, so the shipped
CV carried two registers inside one section.

Krish's ruling 2026-09-14: generate the PROFESSIONAL SUMMARY and the cover letter
hook per role, protect everything else. Those two fields are now generated, and
what makes that safe is voicegate.py, which rejects any number or company name
that is not traceable to his recorded evidence. Fabrication is no longer
structurally impossible, so it is made detectable instead, and a generated string
that fails the gate never reaches a document: it falls back to the block path.

Everything else stays a decision object: block choice from deterministically
matched candidates, the bullet to cut, competency order, highlight order, and the
hiring lead.

Block selection is deterministic FIRST (title patterns per canon section 5's
families plus partnerships); the model tie-breaks only among matched
candidates. A JD that matches nothing falls back to commercial_strategy with
a flag. Nothing here loses a package silently: total tailoring failure
degrades to the first candidate block with the master's own ordering and a
flag in the run report.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources

from ..config import Config
from . import voicegate

BLOCK_KEYS = [
    "gm_market_builder",
    "commercial_strategy",
    "corp_dev_strategy",
    "ai_transformation",
    "partnerships_alliances",
    # Krish's call 2026-09-15. Distinct from ai_transformation: that one is putting
    # AI into a company that sells something else, this one is selling for a
    # company whose product IS AI. See AI_NATIVE_BLOCK below.
    "ai_native_gtm",
]
FALLBACK_BLOCK = "commercial_strategy"

# Precedence order for hybrid titles; first match supplies the lead candidate.
AI_NATIVE_BLOCK = "ai_native_gtm"

# Titles that carry their own AI signal. Ranked above commercial_strategy, whose
# pattern catches a bare "gtm".
AI_NATIVE_TITLE = re.compile(
    r"\bai.native\b|\bai\b[^,]{0,20}\b(?:gtm|go.to.market|revenue|commercial)\b"
    r"|\b(?:gtm|go.to.market|revenue|commercial)\b[^,]{0,20}\bai\b")

# What the summary and the hook must carry when the employer's product IS AI.
# Krish, on the first real package for exactly such a role: it "does not talk about
# how I am one of the most experienced in designing AI native GTM through the
# customers I've worked with through mind/make ... with my signature 'Build your AI
# Brain' and 'Build your AI GTM' programs." Offering the right block was not enough:
# the model read a GTM operations JD and led on operating-model credentials, which
# is a fair reading of the JD and not what he asked for. So the instruction is
# explicit rather than left to inference.
AI_NATIVE_ANGLE = """\
**This employer's product is AI, so the AI-native GTM work is not optional here.**
Krish designs AI-native go-to-market models for a living, through his practice, and
that is the single most relevant thing about him for this role. The summary must say
so and the hook must build on it. Name the program he runs, Build your AI GTM, and
the four levers it works across: product, price, positioning and people. Ground it
in the client work in the evidence, the first-party identity repositioning and the
$254K POC at AdFixus, the publisher that went from 14 competing vendors to three
decisions, the advisory that turned expertise into products clients could buy.

His operating-model record still matters and belongs in the summary. It is the
second thing, not the first.
"""

# The seats where an AI-native company changes which block is right. A partnerships
# or corp dev seat has its own block that already fits an AI company; a GTM or
# market-building seat does not.
AI_NATIVE_PROMOTES = frozenset({"commercial_strategy", "gm_market_builder"})

# What makes a posting an AI-native COMPANY rather than a company that mentions AI.
#
# Counting distinct terms across the whole JD was the first attempt and it failed on
# the posting that prompted all of this: Harvey's 4325-character GTM operations JD
# carries exactly one, "agentic", because a GTM job description describes the JOB.
# The company appears once, in a sentence of its own boilerplate: "By combining
# frontier agentic AI, an enterprise-grade platform, and deep domain expertise,
# we're reshaping how critical knowledge work gets done."
#
# So the test is a phrase only a company whose product IS AI puts in its own
# description. One of those is enough. The weaker terms stay, counted, for the JDs
# that do describe an AI product at length, because a single weak term proves
# nothing: a publisher saying "AI in the newsroom" and "AI tooling for our sales
# team" is not an AI company.
AI_NATIVE_JD_STRONG = (
    "ai-native", "ai native", "frontier ai", "frontier model",
    "frontier agentic", "agentic ai", "foundation model",
    "large language model", "ai platform", "ai product", "ai research",
    "ai lab", "ai company", "ai assistant", "our models", "ai-powered platform",
    "generative ai platform", "ai for legal", "ai copilot",
)
AI_NATIVE_JD_TERMS = (
    "llm", "generative ai", "genai", "ai agents", "agentic", "ai adoption",
    "our model", "inference", "prompt", "fine-tun", "ai-powered", "ai powered",
    "copilot", "model training", "evals",
)
AI_NATIVE_JD_THRESHOLD = 4

FAMILY_PATTERNS: list[tuple[str, str]] = [
    ("partnerships_alliances",
     r"\bpartnership|\balliances?\b|\bpartner\b|\bchannel\b|\becosystem\b"),
    ("gm_market_builder",
     r"\bgeneral manager\b|\bcountry manager\b|\bmanaging director\b|\bregional gm\b|\bgm\b|\bmarket entry\b|\bcountry lead\b"),
    ("corp_dev_strategy",
     r"\bcorporate development\b|\bcorp dev\b|\bcorporate strategy\b|\bvp,? strategy\b|\bvp of strategy\b|\bhead of strategy\b|\bdirector of strategy\b|\bstrategy and corporate\b"),
    ("ai_transformation",
     r"\bai chief of staff\b|\bchief of staff\b|\bhead of ai\b|\bai operations\b|\bgtm ai\b|\bai transformation\b|\bai enablement\b"),
    (AI_NATIVE_BLOCK, AI_NATIVE_TITLE.pattern),
    ("commercial_strategy",
     r"\bchief commercial\b|\bcco\b|\bchief strategy officer\b|\bhead of commercial\b|\brevenue strategy\b|\bcommercial strategy\b|\bhead of gtm\b|\bgtm\b|\bcustomer success\b|\brevenue\b|\bsales\b"),
]

TAILOR_SCHEMA = {
    "type": "object",
    "properties": {
        # exactly-eleven is enforced by validate(); structured outputs only
        # supports minItems 0 or 1, so the schema stays permissive here
        "competency_order": {"type": "array", "items": {"type": "string"}},
        "letter_bullet_to_cut": {"type": "integer"},
        "block_key": {"type": "string"},
        "jd_mirror": {"type": "string"},
        "hiring_lead": {"type": "string"},
        "highlight_order": {"type": "array", "items": {"type": "integer"}},
        "summary": {"type": "string"},
        "hook": {"type": "string"},
    },
    "required": ["competency_order", "letter_bullet_to_cut", "block_key",
                 "jd_mirror", "hiring_lead", "highlight_order", "summary",
                 "hook"],
    "additionalProperties": False,
}

BANNED_LANGUAGE = [
    "\u2014",   # em dash, never
    "it is not just", "it's not just", "at the intersection of",
    "uniquely positioned", "what sets", "i am passionate", "i'm passionate",
    "leverage", "synergy", "cutting-edge", "best-in-class", "game-chang",
]

# The whole haystack reaches the prompt. It was capped at 12000 characters until
# 2026-09-15, by which point the real haystack ran to 30743: the master CV, the
# Profile and Interview Answers tabs, the stored form answers and the mindmake
# engagement records. Everything past the cap was a claim voicegate would happily
# TRACE, because the gate reads the full string, and the model could never SOURCE,
# because it never saw it. A truncated evidence bank is the one failure mode this
# design cannot detect on its own, so there is no silent truncation: over the
# ceiling raises a flag rather than quietly cutting.
EVIDENCE_MAX_CHARS = 120000

THIN_JD_CHARS = 200
JD_MIRROR_MAX_WORDS = 12
JD_OVERLAP_THRESHOLD = 0.5

# The master's PROFESSIONAL SUMMARY runs to three paragraphs, about 900
# characters. The generated replacement has to fit the same space or the CV spills
# to a second page, which canon 9.12 forbids.
SUMMARY_MIN_CHARS = 350
SUMMARY_MAX_CHARS = 1100
HOOK_MIN_CHARS = 110
# Measured, not inferred, and re-measured every time the master changes. Rendering
# the live master letter with three proof bullets and hooks of increasing length:
# 321 characters was one page and 331 was two, until Krish's feedback of 2026-09-15
# put his own answer to "why are you looking" into the master and the closing block
# grew by 100 characters. Re-measured on the new master: 201 is one page, 221 is two.
# So 190, leaving a little for a longer hiring lead or a fourth line of address.
#
# This number is not a style preference, it is the space his letter has left. If it
# gets uncomfortably small, the master is too long for one page and that is his call
# to make, not something to fix by shrinking the hook forever.
#
# This was 650 when the first live package spilled onto a second page with a
# 477-character hook, then 380, which was still over: the five approved blocks run
# 271 to 330 BEFORE their [[JD_MIRROR]] slot is filled, and a twelve-word mirror can
# add 70. build.py still measures the rendered PDF, because a character budget is a
# proxy and the master letter can move again.
HOOK_MAX_CHARS = 190


class TailorError(RuntimeError):
    pass


@dataclass
class TailorResult:
    competency_order: list[str]
    letter_bullet_to_cut: int
    block_key: str
    jd_mirror: str            # empty string means: use the block's default clause
    hiring_lead: str
    flags: list[str] = field(default_factory=list)
    # Generated, gated prose. An empty string means the gate rejected it or the
    # call failed, and the caller must fall back to the approved block.
    summary: str = ""
    hook: str = ""
    # Permutation of the master's highlights, by original index. Empty means keep
    # the master's own order.
    highlight_order: list[int] = field(default_factory=list)


# ---------- approved blocks, from system_config ----------

def load_blocks(cfg: Config) -> tuple[dict, dict]:
    """Returns (letter_blocks, cv_blocks). Each is {block_key: {text, default_mirror?,
    approved_at}}. Missing keys or malformed blocks fail loudly: the blocks are
    Krish-approved copy and the build never improvises around them."""
    letter = cfg.require_json("hunter_letter_blocks")
    cv = cfg.require_json("hunter_cv_summary_blocks")
    for name, blocks, need_slots in (("hunter_letter_blocks", letter, True),
                                     ("hunter_cv_summary_blocks", cv, False)):
        missing = [k for k in BLOCK_KEYS if k not in blocks]
        if missing:
            raise TailorError(f"{name} is missing blocks: {missing}")
        for key, b in blocks.items():
            text = b.get("text", "")
            if not text:
                raise TailorError(f"{name}[{key}] has no text")
            if "\u2014" in text:
                raise TailorError(f"{name}[{key}] contains an em dash")
            if need_slots:
                if "[[COMPANY]]" not in text or "[[JD_MIRROR]]" not in text:
                    raise TailorError(
                        f"{name}[{key}] must carry [[COMPANY]] and [[JD_MIRROR]] slots")
                if not b.get("default_mirror"):
                    raise TailorError(f"{name}[{key}] needs a default_mirror clause")
    return letter, cv


def assemble_hook(letter_blocks: dict, block_key: str, company: str,
                  jd_mirror: str) -> str:
    b = letter_blocks[block_key]
    mirror = jd_mirror.strip() or b["default_mirror"]
    text = b["text"].replace("[[COMPANY]]", company).replace("[[JD_MIRROR]]", mirror)
    if "[[" in text:
        raise TailorError(f"unresolved slot in assembled hook for {block_key}")
    return text


# ---------- deterministic family selection ----------

def ai_native_signals(jd_text: str) -> tuple[list[str], list[str]]:
    """(strong signals, weak signals) the JD carries. One strong is enough; weak
    ones only count together."""
    low = (jd_text or "").lower()
    strong = sorted({t for t in AI_NATIVE_JD_STRONG if t in low})
    weak = sorted({t for t in AI_NATIVE_JD_TERMS if t in low})
    return strong, weak


def is_ai_native_jd(jd_text: str) -> tuple[bool, str]:
    """(verdict, why). The why goes in the run report, so a promotion is never a
    silent reclassification of the company."""
    strong, weak = ai_native_signals(jd_text)
    if strong:
        return True, f"its own description says {', '.join(strong[:3])}"
    if len(weak) >= AI_NATIVE_JD_THRESHOLD:
        return True, f"{len(weak)} AI signals ({', '.join(weak[:4])})"
    return False, ""


def select_candidates(title: str, jd_text: str = "") -> tuple[list[str], list[str]]:
    """Returns (candidate block keys in precedence order, flags).

    jd_text was accepted and ignored until 2026-09-15. It has to be read, because
    the signal that decides the ai_native_gtm family is usually the COMPANY rather
    than the title: Harvey's "Head of GTM Strategy & Operations" carries no AI word
    at all, matched commercial_strategy on its bare "gtm", and got a letter opening
    on Captify's pricing and forecasting model. Right for a media business, wrong
    for an AI company.
    """
    hay = title.lower()
    candidates = [fam for fam, pat in FAMILY_PATTERNS if re.search(pat, hay)]
    flags: list[str] = []
    if candidates and AI_NATIVE_BLOCK not in candidates \
            and set(candidates) & AI_NATIVE_PROMOTES:
        native, why = is_ai_native_jd(jd_text)
        if native:
            candidates = [AI_NATIVE_BLOCK] + candidates
            flags.append(f"AI-native company, {why}; ai_native_gtm offered first")
    if not candidates:
        candidates = [FALLBACK_BLOCK]
        flags.append("weak archetype match, review the hook before sending")
    return candidates, flags


# ---------- validation ----------

def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())}


def validate_jd_mirror(mirror: str, jd_text: str) -> list[str]:
    """Empty mirror is always valid (the block's default clause is used)."""
    mirror = mirror.strip()
    if not mirror:
        return []
    fails: list[str] = []
    if len(mirror.split()) > JD_MIRROR_MAX_WORDS:
        fails.append(f"jd_mirror exceeds {JD_MIRROR_MAX_WORDS} words")
    if "{{" in mirror or "[[" in mirror:
        fails.append("jd_mirror contains a placeholder")
    lowered = mirror.lower()
    for banned in BANNED_LANGUAGE:
        if banned in lowered:
            fails.append(f"jd_mirror contains banned language: {banned!r}")
            break
    for token in re.findall(r"\S*\d\S*", mirror):
        if token not in jd_text:
            fails.append(f"jd_mirror carries a number not in the JD verbatim: {token!r}")
            break
    words = _content_words(mirror)
    if words:
        jd_words = _content_words(jd_text)
        overlap = len(words & jd_words) / len(words)
        if overlap < JD_OVERLAP_THRESHOLD:
            fails.append("jd_mirror does not mirror the JD's own language")
    return fails


def validate_generated(data: dict, *, evidence: str,
                       banned_phrases: tuple[str, ...],
                       company: str) -> tuple[list[str], list[str]]:
    """(hard failures that void the whole result, soft failures that only drop
    the generated prose back to the block path).

    A gate failure is SOFT on purpose. A model that invents a number must not
    lose Krish the package; it must lose the generated prose and say so, leaving
    the approved block in place.
    """
    soft: list[str] = []
    allow = frozenset({company} | set(company.split()))
    for name, key, lo, hi in (("summary", "summary", SUMMARY_MIN_CHARS,
                               SUMMARY_MAX_CHARS),
                              ("hook", "hook", HOOK_MIN_CHARS, HOOK_MAX_CHARS)):
        text = (data.get(key) or "").strip()
        if not text:
            soft.append(f"{name} was not generated")
            continue
        verdict = voicegate.check(text, evidence=evidence,
                                  banned_phrases=banned_phrases,
                                  min_chars=lo, max_chars=hi, allow_names=allow)
        if not verdict.ok:
            soft.append(f"{name} rejected: " + "; ".join(verdict.failures))
    return [], soft


def validate_highlight_order(order, count: int) -> list[str]:
    """Must be an exact permutation of the master's indices. Canon 9.12: reorder
    them, never add one, never drop one."""
    if not order:
        return []
    if not isinstance(order, list) or any(not isinstance(i, int) for i in order):
        return ["highlight_order must be a list of integers"]
    if sorted(order) != list(range(count)):
        return [f"highlight_order must be an exact permutation of 0..{count - 1}, "
                f"got {order}"]
    return []


def validate(data: dict, master_competencies: list[str], candidates: list[str],
             jd_text: str, highlight_count: int = 0) -> list[str]:
    fails: list[str] = []
    order = data.get("competency_order") or []
    if sorted(order) != sorted(master_competencies):
        fails.append("competency_order is not an exact permutation of the master's eleven items")
    cut = data.get("letter_bullet_to_cut")
    if not isinstance(cut, int) or not 1 <= cut <= 4:
        fails.append("letter_bullet_to_cut must be an integer 1..4")
    if data.get("block_key") not in candidates:
        fails.append(f"block_key must be one of the matched candidates {candidates}")
    fails.extend(validate_jd_mirror(data.get("jd_mirror") or "", jd_text))
    if not (data.get("hiring_lead") or "").strip():
        fails.append("hiring_lead is empty; return 'Hiring Team' when no name is found")
    if highlight_count:
        fails.extend(validate_highlight_order(data.get("highlight_order"),
                                              highlight_count))
    return fails


# ---------- the model call ----------

def _keep_verbatim_block(phrases: tuple[str, ...]) -> str:
    """Krish bolds these phrases in his own summary. A paraphrase loses the bold with
    the words, which the package verifier reports as a dropped run: it is allowed,
    since the summary is generated, but his own phrasing is better than a rewrite of
    it. Asking is cheaper than excusing."""
    keep = [p.strip() for p in phrases if p and p.strip()]
    if not keep:
        return ""
    lines = "\n".join(f'- "{p}"' for p in keep)
    return ("**Keep these phrases word for word if you use the fact behind them.** "
            "Krish bolds them in his own summary, and a paraphrase loses the "
            "emphasis along with his wording:\n" + lines + "\n")


def _prompt(canon, company: str, title: str, jd_text: str,
            master_competencies: list[str], candidates: list[str],
            letter_blocks: dict, *, highlights: list[str] | None = None,
            evidence_view: str = "", voice_rules: str = "",
            angle: str = "", keep_verbatim: tuple[str, ...] = ()) -> str:
    template = resources.files("hunter").joinpath("prompts/tailor.md").read_text()
    blocks_view = "\n\n".join(
        f"### {k}\n{letter_blocks[k]['text']}" for k in candidates)
    hl = highlights or []
    return (template
            .replace("[[COMPANY]]", company)
            .replace("[[ROLE]]", title)
            .replace("[[CANDIDATES]]", ", ".join(candidates))
            .replace("[[CANDIDATE_BLOCKS]]", blocks_view)
            .replace("[[COMPETENCIES]]", "\n".join(f"- {c}" for c in master_competencies))
            .replace("[[HIGHLIGHTS]]",
                     "\n".join(f"{i}. {h}" for i, h in enumerate(hl)))
            .replace("[[HIGHLIGHT_COUNT]]", str(len(hl)))
            .replace("[[ANGLE]]", angle)
            .replace("[[KEEP_VERBATIM]]", _keep_verbatim_block(keep_verbatim))
            .replace("[[EVIDENCE]]", evidence_view)
            .replace("[[VOICE_RULES]]", voice_rules)
            .replace("[[JD]]", jd_text[:20000]))


def fallback_result(master_competencies: list[str], candidates: list[str],
                    reason: str) -> TailorResult:
    return TailorResult(
        competency_order=list(master_competencies),
        letter_bullet_to_cut=3,
        block_key=candidates[0],
        jd_mirror="",
        hiring_lead="Hiring Team",
        flags=[f"tailor fallback: {reason}"],
    )


def tailor(cfg: Config, canon, *, company: str, title: str, jd_text: str,
           master_competencies: list[str], letter_blocks: dict,
           highlights: list[str] | None = None, evidence: str = "",
           banned_phrases: tuple[str, ...] = (),
           feedback: str = "",
           keep_verbatim: tuple[str, ...] = ()) -> TailorResult:
    """evidence is the haystack voicegate traces every generated number and name
    against: the master's own text plus Krish's recorded proof points. Passing it
    empty disables generation rather than allowing ungated prose, because an
    empty haystack would reject everything anyway and silently.

    feedback is Krish's own words from an amend reply, passed through verbatim.
    """
    candidates, flags = select_candidates(title, jd_text)
    highlights = highlights or []
    generate = bool(evidence.strip())
    if not generate:
        flags.append("no evidence supplied, generated prose disabled")
    if len(evidence) > EVIDENCE_MAX_CHARS:
        # Truncating here would let the gate permit claims the model cannot see.
        flags.append(f"evidence is {len(evidence)} chars, over the "
                     f"{EVIDENCE_MAX_CHARS} ceiling; it was sent whole, review "
                     f"the prompt size")
    if len(jd_text.strip()) < THIN_JD_CHARS:
        flags.append("thin JD, block default clause used")

    try:
        import anthropic
    except ImportError:
        return fallback_result(master_competencies, candidates,
                               "anthropic SDK not installed")
    api_key = cfg.optional("hunter_anthropic_api_key")
    if not api_key:
        return fallback_result(master_competencies, candidates,
                               "hunter_anthropic_api_key missing from system_config")
    model = cfg.optional("hunter_anthropic_model", "claude-opus-5")
    client = anthropic.Anthropic(api_key=api_key)
    native, _why = is_ai_native_jd(jd_text)
    prompt = _prompt(canon, company, title, jd_text, master_competencies,
                     candidates, letter_blocks, highlights=highlights,
                     evidence_view=evidence if generate else "",
                     voice_rules="\n".join(f"- never write {p!r}"
                                           for p in banned_phrases),
                     angle=AI_NATIVE_ANGLE if native else "",
                     keep_verbatim=keep_verbatim)
    if feedback:
        prompt += ("\n\n## Krish's feedback on the previous draft\n\n"
                   "Apply this exactly. It overrides your earlier choices.\n\n"
                   + feedback.strip())

    last_fails: list[str] = []
    for attempt in range(2):
        content = prompt if attempt == 0 else (
            prompt + "\n\nYour previous answer failed validation: "
            + "; ".join(last_fails) + ". Return a corrected JSON object.")
        try:
            resp = client.messages.create(
                model=model,
                # 2000 was enough when this returned a decision object only.
                # Generating the summary and the hook pushed the response past it,
                # and the truncated JSON surfaced as "response was not valid JSON",
                # which sent the first live Harvey build silently to the block
                # fallback. rationale.py already checks stop_reason for this; the
                # check below now does too, so the cause is reported rather than
                # guessed at.
                max_tokens=16000,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema",
                                          "schema": TAILOR_SCHEMA}},
            )
        except anthropic.APIError as e:
            last_fails = [f"API error: {e.__class__.__name__}"]
            continue
        if resp.stop_reason == "refusal":
            last_fails = ["model refused the request"]
            continue
        if resp.stop_reason == "max_tokens":
            last_fails = [f"response hit the output cap ({resp.usage.output_tokens} "
                          f"tokens) and is truncated; raise max_tokens"]
            continue
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            last_fails = ["response was not valid JSON"]
            continue
        last_fails = validate(data, master_competencies, candidates, jd_text,
                              highlight_count=len(highlights))
        if not last_fails:
            mirror = (data["jd_mirror"] or "").strip()
            if len(jd_text.strip()) < THIN_JD_CHARS:
                mirror = ""
            summary, hook = "", ""
            if generate:
                _, soft = validate_generated(
                    data, evidence=evidence, banned_phrases=banned_phrases,
                    company=company)
                if soft and attempt == 0:
                    # One regeneration, addressing every gate failure at once.
                    last_fails = soft
                    continue
                if soft:
                    flags.extend(soft)
                    flags.append("fell back to the approved block for the "
                                 "rejected piece")
                else:
                    summary = (data.get("summary") or "").strip()
                    hook = (data.get("hook") or "").strip()
            return TailorResult(
                competency_order=data["competency_order"],
                letter_bullet_to_cut=data["letter_bullet_to_cut"],
                block_key=data["block_key"],
                jd_mirror=mirror,
                hiring_lead=data["hiring_lead"].strip(),
                flags=flags,
                summary=summary,
                hook=hook,
                highlight_order=list(data.get("highlight_order") or []),
            )
    return fallback_result(master_competencies, candidates,
                           "validation failed twice: " + "; ".join(last_fails))
