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
SNIPPET_MAX = 240

RATIONALE_SCHEMA = {
    "type": "object",
    "properties": {
        "mandate": {"type": "string"},
        "fit": {"type": "string"},
        "risk": {"type": "string"},
        "archetype": {"type": "string"},
        "snippet": {"type": "string"},
    },
    "required": ["mandate", "fit", "risk", "archetype", "snippet"],
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

Write four short pieces, as JSON:
- mandate: what the job actually is, one sentence, in the posting's own terms.
- fit: why it fits Krish specifically. Name the archetype and the evidence.
  Be concrete about what he has done that maps to this mandate. One or two
  sentences. Never generic praise.
- risk: the one thing that might make him decline, stated plainly. One
  sentence. If there is no real risk, say what would need to be true.
- archetype: one of gm_market_builder, commercial_strategy, corp_dev_strategy,
  ai_transformation, partnerships_alliances.
- snippet: two short sentences for the sheet's JD Snippet column, under
  {snippet_chars} characters: first what the business does and sells, then what
  this role is for. Written so Krish understands the business in one glance.

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
    snippet = (parts.get("snippet") or "").strip()
    if not snippet:
        fails.append("snippet is empty")
    elif len(snippet) > SNIPPET_MAX:
        fails.append(f"snippet too long at {len(snippet)} chars")
    else:
        for bad in BANNED:
            if bad.lower() in snippet.lower():
                fails.append(f"banned language in snippet: {bad!r}")
        if not digits_grounded(snippet, jd):
            fails.append("a figure in the snippet does not appear in the JD")
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


def deterministic_snippet(company: str, title: str, jd: str) -> str:
    """The JD's own opening, or an honest stub. Never a guess about the
    business."""
    text = " ".join((jd or "").split()).replace("\u2014", " - ").replace("\u2013", " - ")
    if len(text) >= 200:
        cut = text[:SNIPPET_MAX]
        return cut[:cut.rfind(" ")] if " " in cut else cut
    return f"{title} at {company}. JD not captured."


def _text(resp) -> str:
    """The model may emit a thinking block first, so content[0] is not
    reliably the JSON. Take the first text block, or fail loudly."""
    for block in resp.content:
        if getattr(block, "type", "") == "text":
            return block.text
    raise ValueError("no text block in the model response")


def write_rationale(cfg: Config, canon, *, company: str, title: str, jd: str,
                    score: int, score_reason: str, location: str = "",
                    comp: str = "") -> tuple[str, list[str]]:
    """(Why It Fits text, flags). Never raises: a role always gets a
    rationale, even if it is the honest fallback."""
    text, _snippet, flags = write_rationale_and_snippet(
        cfg, canon, company=company, title=title, jd=jd, score=score,
        score_reason=score_reason, location=location, comp=comp)
    return text, flags


def write_rationale_and_snippet(cfg: Config, canon, *, company: str, title: str,
                                jd: str, score: int, score_reason: str,
                                location: str = "", comp: str = ""
                                ) -> tuple[str, str, list[str]]:
    """(Why It Fits text, JD Snippet text, flags). One model call writes
    both, so the snippet Krish reads in column D and the rationale in column
    K come from the same reading of the same JD."""
    flags: list[str] = []
    fallback_snippet = deterministic_snippet(company, title, jd)
    if len(jd or "") < 200:
        return deterministic(company, title, score, score_reason), fallback_snippet, ["thin JD"]
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.require("hunter_anthropic_api_key"))
        prompt = PROMPT.format(
            canon_profile=canon.section_text("5")[:2500],
            company=company, title=title, location=location or "not stated",
            comp=comp or "not disclosed", score=score, score_reason=score_reason,
            jd=jd[:6000], max_chars=MAX_CHARS, snippet_chars=SNIPPET_MAX)
        resp = client.messages.create(
            model=cfg.optional("hunter_anthropic_model", "claude-opus-5"),
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema",
                                      "schema": RATIONALE_SCHEMA}})
        if getattr(resp, "stop_reason", "") == "refusal":
            return (deterministic(company, title, score, score_reason),
                    fallback_snippet, ["model refused"])
        if getattr(resp, "stop_reason", "") == "max_tokens":
            # a truncated JSON body is not partially usable
            return (deterministic(company, title, score, score_reason),
                    fallback_snippet, ["rationale truncated at the token limit"])
        parts = json.loads(_text(resp))
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
                return (deterministic(company, title, score, score_reason),
                        fallback_snippet, fails)
            parts = json.loads(_text(retry))
            fails = validate(parts, jd)
            if fails:
                flags.append("rationale rejected twice: " + "; ".join(fails))
                return (deterministic(company, title, score, score_reason),
                        fallback_snippet, flags)
            flags.append("rationale needed one retry")
        snippet = " ".join(parts["snippet"].split()).replace("\u2014", " - ")
        return assemble(parts), snippet, flags
    except Exception as e:
        return (deterministic(company, title, score, score_reason), fallback_snippet,
                [f"rationale generation failed: {e.__class__.__name__}"])
