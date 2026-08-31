"""tailor(): the only model call in the package path. The model returns a
decision object against a fixed schema. There is no field for document text,
so fabrication is structurally impossible rather than discouraged.

This module writes no voice rules; prompts/tailor.md carries the instructions.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources

from ..config import Config, ConfigError

TAILOR_SCHEMA = {
    "type": "object",
    "properties": {
        "competency_order": {"type": "array", "items": {"type": "string"},
                             "minItems": 11, "maxItems": 11},
        "letter_bullet_to_cut": {"type": "integer", "minimum": 1, "maximum": 4},
        "hook": {"type": "string"},
        "hiring_lead": {"type": "string"},
    },
    "required": ["competency_order", "letter_bullet_to_cut", "hook", "hiring_lead"],
    "additionalProperties": False,
}

BANNED_IN_HOOK = [
    "\u2014",         # em dash, never
    "it is not just", "it's not just", "at the intersection of",
    "uniquely positioned", "what sets", "i am passionate", "i'm passionate",
    "leverage", "synergy", "cutting-edge", "best-in-class", "game-chang",
]


class TailorError(RuntimeError):
    pass


@dataclass
class TailorResult:
    competency_order: list[str]
    letter_bullet_to_cut: int
    hook: str
    hiring_lead: str


def validate(data: dict, master_competencies: list[str]) -> list[str]:
    """Return a list of failure strings; empty means valid."""
    fails: list[str] = []
    order = data.get("competency_order") or []
    if sorted(order) != sorted(master_competencies):
        fails.append("competency_order is not an exact permutation of the master's eleven items")
    cut = data.get("letter_bullet_to_cut")
    if not isinstance(cut, int) or not 1 <= cut <= 4:
        fails.append("letter_bullet_to_cut must be an integer 1..4")
    hook = (data.get("hook") or "").strip()
    if not hook:
        fails.append("hook is empty")
    # Sentence split that ignores decimal points ($1.5M) and digit-adjacent dots.
    sentences = [s for s in re.split(r"(?<!\d)[.!?]+(?!\d)", hook) if s.strip()]
    if len(sentences) > 2:
        fails.append("hook exceeds two sentences")
    if "{{" in hook:
        fails.append("hook contains a placeholder")
    lowered = hook.lower()
    for banned in BANNED_IN_HOOK:
        if banned in lowered:
            fails.append(f"hook contains banned language: {banned!r}")
            break
    if not (data.get("hiring_lead") or "").strip():
        fails.append("hiring_lead is empty; the model must return 'Hiring Team' when no name is found")
    return fails


def _prompt(canon, role_company: str, role_title: str, jd_text: str,
            master_competencies: list[str]) -> str:
    template = resources.files("hunter").joinpath("prompts/tailor.md").read_text()
    return (template
            .replace("[[COMPANY]]", role_company)
            .replace("[[ROLE]]", role_title)
            .replace("[[COMPETENCIES]]", "\n".join(f"- {c}" for c in master_competencies))
            .replace("[[CANON_POSITIONING]]", canon.section_text("4"))
            .replace("[[CANON_PROOF]]", canon.section_text("3"))
            .replace("[[JD]]", jd_text[:20000]))


def tailor(cfg: Config, canon, *, company: str, title: str, jd_text: str,
           master_competencies: list[str]) -> TailorResult:
    try:
        import anthropic
    except ImportError as e:
        raise TailorError(f"anthropic SDK not installed: {e}") from e
    api_key = cfg.require("hunter_anthropic_api_key")
    model = cfg.optional("hunter_anthropic_model", "claude-opus-5")
    client = anthropic.Anthropic(api_key=api_key)
    prompt = _prompt(canon, company, title, jd_text, master_competencies)

    last_fails: list[str] = []
    for attempt in range(2):
        content = prompt if attempt == 0 else (
            prompt + "\n\nYour previous answer failed validation: "
            + "; ".join(last_fails) + ". Return a corrected JSON object.")
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": TAILOR_SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            last_fails = ["model refused the request"]
            continue
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            last_fails = ["response was not valid JSON"]
            continue
        last_fails = validate(data, master_competencies)
        if not last_fails:
            return TailorResult(
                competency_order=data["competency_order"],
                letter_bullet_to_cut=data["letter_bullet_to_cut"],
                hook=data["hook"].strip(),
                hiring_lead=data["hiring_lead"].strip(),
            )
    raise TailorError("tailor() failed validation twice: " + "; ".join(last_fails))
