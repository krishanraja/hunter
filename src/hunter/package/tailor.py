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
]
FALLBACK_BLOCK = "commercial_strategy"

# Precedence order for hybrid titles; first match supplies the lead candidate.
FAMILY_PATTERNS: list[tuple[str, str]] = [
    ("partnerships_alliances",
     r"\bpartnership|\balliances?\b|\bpartner\b|\bchannel\b|\becosystem\b"),
    ("gm_market_builder",
     r"\bgeneral manager\b|\bcountry manager\b|\bmanaging director\b|\bregional gm\b|\bgm\b|\bmarket entry\b|\bcountry lead\b"),
    ("corp_dev_strategy",
     r"\bcorporate development\b|\bcorp dev\b|\bcorporate strategy\b|\bvp,? strategy\b|\bvp of strategy\b|\bhead of strategy\b|\bdirector of strategy\b|\bstrategy and corporate\b"),
    ("ai_transformation",
     r"\bai chief of staff\b|\bchief of staff\b|\bhead of ai\b|\bai operations\b|\bgtm ai\b|\bai transformation\b|\bai enablement\b"),
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

THIN_JD_CHARS = 200
JD_MIRROR_MAX_WORDS = 12
JD_OVERLAP_THRESHOLD = 0.5

# The master's PROFESSIONAL SUMMARY runs to three paragraphs, about 900
# characters. The generated replacement has to fit the same space or the CV spills
# to a second page, which canon 9.12 forbids.
SUMMARY_MIN_CHARS = 350
SUMMARY_MAX_CHARS = 1100
HOOK_MIN_CHARS = 150
# Calibrated from the five approved blocks, which run 271 to 330 characters and
# all fit on page one of the master letter. 650 did not: the first live package
# generated a 477-character hook and the letter spilled to a second page carrying
# nothing but the contact footer, against canon 9.12's "One page." build.py still
# measures the rendered PDF, because a character budget is only a proxy and the
# master letter can move again.
HOOK_MAX_CHARS = 380
# What the ladder in build.build_letter trims a long hook back to before giving
# up on generation and shipping the approved block.
HOOK_TRIM_CHARS = 300


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

def select_candidates(title: str, jd_text: str = "") -> tuple[list[str], list[str]]:
    """Returns (candidate block keys in precedence order, flags)."""
    hay = title.lower()
    candidates = [fam for fam, pat in FAMILY_PATTERNS if re.search(pat, hay)]
    flags: list[str] = []
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

def _prompt(canon, company: str, title: str, jd_text: str,
            master_competencies: list[str], candidates: list[str],
            letter_blocks: dict, *, highlights: list[str] | None = None,
            evidence_view: str = "", voice_rules: str = "") -> str:
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
            .replace("[[EVIDENCE]]", evidence_view[:12000])
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
           feedback: str = "") -> TailorResult:
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
    prompt = _prompt(canon, company, title, jd_text, master_competencies,
                     candidates, letter_blocks, highlights=highlights,
                     evidence_view=evidence if generate else "",
                     voice_rules="\n".join(f"- never write {p!r}"
                                           for p in banned_phrases))
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
