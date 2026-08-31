"""tailor(): the only model call in the package path, and a deliberately
narrow one. The prose that ships comes from Krish-approved blocks stored in
system_config; the model only picks a block from deterministically matched
candidates, supplies a bounded JD mirror clause, cuts one letter bullet,
reorders the eleven competencies, and names the hiring lead. There is no
field for free document text, so fabrication is structurally impossible.

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
        "competency_order": {"type": "array", "items": {"type": "string"},
                             "minItems": 11, "maxItems": 11},
        "letter_bullet_to_cut": {"type": "integer", "minimum": 1, "maximum": 4},
        "block_key": {"type": "string"},
        "jd_mirror": {"type": "string"},
        "hiring_lead": {"type": "string"},
    },
    "required": ["competency_order", "letter_bullet_to_cut", "block_key",
                 "jd_mirror", "hiring_lead"],
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


def validate(data: dict, master_competencies: list[str], candidates: list[str],
             jd_text: str) -> list[str]:
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
    return fails


# ---------- the model call ----------

def _prompt(canon, company: str, title: str, jd_text: str,
            master_competencies: list[str], candidates: list[str],
            letter_blocks: dict) -> str:
    template = resources.files("hunter").joinpath("prompts/tailor.md").read_text()
    blocks_view = "\n\n".join(
        f"### {k}\n{letter_blocks[k]['text']}" for k in candidates)
    return (template
            .replace("[[COMPANY]]", company)
            .replace("[[ROLE]]", title)
            .replace("[[CANDIDATES]]", ", ".join(candidates))
            .replace("[[CANDIDATE_BLOCKS]]", blocks_view)
            .replace("[[COMPETENCIES]]", "\n".join(f"- {c}" for c in master_competencies))
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
           master_competencies: list[str], letter_blocks: dict) -> TailorResult:
    candidates, flags = select_candidates(title, jd_text)
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
                     candidates, letter_blocks)

    last_fails: list[str] = []
    for attempt in range(2):
        content = prompt if attempt == 0 else (
            prompt + "\n\nYour previous answer failed validation: "
            + "; ".join(last_fails) + ". Return a corrected JSON object.")
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2000,
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
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            last_fails = ["response was not valid JSON"]
            continue
        last_fails = validate(data, master_competencies, candidates, jd_text)
        if not last_fails:
            mirror = (data["jd_mirror"] or "").strip()
            if len(jd_text.strip()) < THIN_JD_CHARS:
                mirror = ""
            return TailorResult(
                competency_order=data["competency_order"],
                letter_bullet_to_cut=data["letter_bullet_to_cut"],
                block_key=data["block_key"],
                jd_mirror=mirror,
                hiring_lead=data["hiring_lead"].strip(),
                flags=flags,
            )
    return fallback_result(master_competencies, candidates,
                           "validation failed twice: " + "; ".join(last_fails))
