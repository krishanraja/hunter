"""Column J: why this role is a fit for Krish specifically.

Krish's complaint, 2026-09-02: rows 31-40 do this well and nothing else
does. Measured, he is right. Of the 70 rows below 41, 42 said literally
"Not assessed" and 17 carried the generic "Engine-Builder signals N,
mandate absent" stub. Those are scoring artefacts, not rationale.

The shape is fixed so every pass reads the same:
  1. what the mandate actually is, in the posting's own terms
  2. why it fits Krish against canon section 5, naming the archetype
  3. the one risk worth knowing before he spends a verdict on it

Fabrication guard: every digit-bearing token must appear verbatim in the
JD, reusing the jd_mirror rule from tailor.py. A model that cannot ground
its numbers gets replaced by a deterministic sentence and a flag, never a
plausible invention.
"""
from __future__ import annotations

import json
import re

from ..config import Config

MAX_CHARS = 800
MIN_CHARS = 120

RATIONALE_SCHEMA = {
    "type": "object",
    "properties": {
        "mandate": {"type": "string"},
        "fit": {"type": "string"},
        "risk": {"type": "string"},
        "archetype": {"type": "string"},
    },
    "required": ["mandate", "fit", "risk", "archetype"],
    # the structured-output subset requires this explicitly on every object
    "additionalProperties": False,
}

BANNED = ("\u2014", "leverage", "synergy", "passionate", "rockstar", "world-class")

PROMPT = """You are writing one field of a job pipeline row for Krish Raja.

KRISH, from his canon:
{canon_profile}

THE ROLE
Company: {company}
Title: {title}
Location: {location}
Comp: {comp}
Score this system gave it: {score} out of 10, because: {score_reason}

JOB DESCRIPTION (verbatim, the only source of fact about the role):
{jd}

Write three short pieces, as JSON:
- mandate: what the job actually is, one sentence, in the posting's own terms.
- fit: why it fits Krish specifically. Name the archetype and the evidence.
  Be concrete about what he has done that maps to this mandate. One or two
  sentences. Never generic praise.
- risk: the one thing that might make him decline, stated plainly. One
  sentence. If there is no real risk, say what would need to be true.
- archetype: one of gm_market_builder, commercial_strategy, corp_dev_strategy,
  ai_transformation, partnerships_alliances.

Rules: plain English, no em dashes, no marketing adjectives. Every number or
figure you use must appear verbatim in the job description above. Do not
invent funding, headcount, revenue or customer facts.

Length is a hard limit: mandate, fit and risk together must come to UNDER
{max_chars} characters. Aim for about 350. Going over fails the whole field
and it gets thrown away, so be short and specific."""


def _digits(s: str) -> str:
    return re.sub(r"[^0-9]", "", s)


def digits_grounded(text: str, jd: str) -> bool:
    """Every digit-bearing token must be traceable to the JD.

    Compared on digits alone, because a model writing $250,000 where the
    posting says $250,000 was being rejected over comma and currency
    formatting. Fabrication is still caught: 900 is not a substring of a JD
    whose only figure is 500.
    """
    jd_digits = _digits(jd)
    for token in re.findall(r"\S*\d\S*", text):
        d = _digits(token)
        if d and d not in jd_digits:
            return False
    return True


def validate(parts: dict, jd: str) -> list[str]:
    fails = []
    text = " ".join(parts.get(k, "") for k in ("mandate", "fit", "risk"))
    if len(text) < MIN_CHARS:
        fails.append(f"too thin at {len(text)} chars")
    if len(text) > MAX_CHARS:
        fails.append(f"too long at {len(text)} chars")
    for bad in BANNED:
        if bad.lower() in text.lower():
            fails.append(f"banned language: {bad!r}")
    if not digits_grounded(text, jd):
        fails.append("a figure does not appear in the JD")
    for key in ("mandate", "fit", "risk"):
        if not parts.get(key, "").strip():
            fails.append(f"{key} is empty")
    return fails


def assemble(parts: dict) -> str:
    return (f"{parts['mandate'].strip()} "
            f"FIT: {parts['fit'].strip()} "
            f"RISK: {parts['risk'].strip()}")


def deterministic(company: str, title: str, score: int, score_reason: str) -> str:
    """The honest fallback. Says what is known and admits what is not,
    rather than inventing a rationale the JD does not support."""
    return (f"{title} at {company}. "
            f"FIT: scored {score} of 10 on the canon rubric ({score_reason}). "
            f"RISK: no grounded rationale was generated for this role, so read "
            f"the JD before spending a verdict on it.")


def write_rationale(cfg: Config, canon, *, company: str, title: str, jd: str,
                    score: int, score_reason: str, location: str = "",
                    comp: str = "") -> tuple[str, list[str]]:
    """(column J text, flags). Never raises: a role always gets a rationale,
    even if it is the honest fallback."""
    flags: list[str] = []
    if len(jd or "") < 200:
        return deterministic(company, title, score, score_reason), ["thin JD"]
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.require("hunter_anthropic_api_key"))
        prompt = PROMPT.format(
            canon_profile=canon.section_text("5")[:2500],
            company=company, title=title, location=location or "not stated",
            comp=comp or "not disclosed", score=score, score_reason=score_reason,
            jd=jd[:6000], max_chars=MAX_CHARS)
        resp = client.messages.create(
            model=cfg.optional("hunter_anthropic_model", "claude-opus-5"),
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema",
                                      "schema": RATIONALE_SCHEMA}})
        if getattr(resp, "stop_reason", "") == "refusal":
            return deterministic(company, title, score, score_reason), ["model refused"]
        if getattr(resp, "stop_reason", "") == "max_tokens":
            # a truncated JSON body is not partially usable
            return (deterministic(company, title, score, score_reason),
                    ["rationale truncated at the token limit"])
        parts = json.loads(resp.content[0].text)
        fails = validate(parts, jd)
        if fails:
            # one corrective retry naming the exact failure, then the honest
            # fallback. Usually the model only needs to be told the limit.
            retry = client.messages.create(
                model=cfg.optional("hunter_anthropic_model", "claude-opus-5"),
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt},
                          {"role": "assistant", "content": json.dumps(parts)},
                          {"role": "user", "content":
                           "That failed validation: " + "; ".join(fails)
                           + ". Rewrite it shorter and use only figures that "
                             "appear in the job description."}],
                output_config={"format": {"type": "json_schema",
                                          "schema": RATIONALE_SCHEMA}})
            if getattr(retry, "stop_reason", "") in ("refusal", "max_tokens"):
                return deterministic(company, title, score, score_reason), fails
            parts = json.loads(retry.content[0].text)
            fails = validate(parts, jd)
            if fails:
                flags.append("rationale rejected twice: " + "; ".join(fails))
                return deterministic(company, title, score, score_reason), flags
            flags.append("rationale needed one retry")
        return assemble(parts), flags
    except Exception as e:
        return (deterministic(company, title, score, score_reason),
                [f"rationale generation failed: {e.__class__.__name__}"])
