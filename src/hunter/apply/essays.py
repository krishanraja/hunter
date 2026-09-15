"""Draft an answer to an application's open question, in Krish's own words.

Krish: "the two questions have no answer. I thought you had enough of a bank of
information to draft these answers." He is right that the material exists and
right that nothing was using it. FillPlan has carried an `essays` slot since it
was written and nothing ever filled it, so every open question on every form has
come back blank since the first application.

Three rules, all of them borrowed from the letter rather than invented here:

  1. Grounded. The same evidence haystack the cover letter is traced against, so
     a number or a company name that is not his cannot appear in an answer.
  2. Gated. The same voice gate, so a drafted answer that reaches for a claim it
     cannot support is rejected and the field comes back blank rather than
     confident and wrong.
  3. Flagged. Every drafted answer is marked in the approval email. These are
     the sentences most likely to need his judgement, and he reads them before
     anything opens.
"""
from __future__ import annotations

import json

from ..config import Config
from ..package import voicegate

# Long enough to answer, short enough to read. Application boxes are not essays
# however the question is phrased, and a wall of text reads as a wall of text.
MIN_CHARS = 220
MAX_CHARS = 900

SYSTEM = """You are drafting one answer to one question on a job application, \
in the applicant's own voice.

Rules, all of them hard:
- Every fact, number, company name and claim must appear in the EVIDENCE. If the \
evidence does not support a claim, do not make it.
- Never invent a number, a date, a client, a title or a result.
- Write as the applicant, first person, no salutation and no sign-off.
- British-Australian register. Plain words. No em dashes. No exclamation marks.
- No "I am passionate about", no "I am excited to", no "leverage", no \
"synergy", no "thrilled".
- Answer the question that was asked, specifically, with something only this \
applicant could write.
- Between %d and %d characters.

Return JSON only: {"answer": "..."}""" % (MIN_CHARS, MAX_CHARS)


def _text(resp) -> str:
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "text":
            return block.text or ""
    return ""


def draft_one(cfg: Config, *, question: str, company: str, role: str,
              jd_text: str, evidence: str,
              banned_phrases: tuple[str, ...] = ()) -> tuple[str, str]:
    """(answer, why_not). An empty answer always carries a reason."""
    if not evidence.strip():
        return "", "no evidence loaded, so nothing could be traced"
    try:
        import anthropic
    except ImportError:
        return "", "anthropic sdk missing"
    # The same key every other generated word in this system uses.
    key = cfg.optional("hunter_anthropic_api_key")
    if not key:
        return "", "no hunter_anthropic_api_key in system_config"
    model = cfg.optional("hunter_anthropic_model", "claude-opus-5")
    prompt = (f"COMPANY: {company}\nROLE: {role}\n\n"
              f"QUESTION:\n{question}\n\n"
              f"JOB DESCRIPTION:\n{jd_text[:6000]}\n\n"
              f"EVIDENCE (everything you may draw on):\n{evidence[:120000]}")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model, max_tokens=1200, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        return "", f"{e.__class__.__name__}: {str(e)[:120]}"

    raw = _text(resp).strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        answer = (json.loads(raw).get("answer") or "").strip()
    except Exception:
        return "", "the model did not return the JSON it was asked for"
    if not answer:
        return "", "the model returned an empty answer"
    if len(answer) > MAX_CHARS:
        return "", f"the draft ran to {len(answer)} characters, over {MAX_CHARS}"

    verdict = voicegate.check(answer, evidence=evidence,
                              banned_phrases=banned_phrases)
    if not verdict.ok:
        return "", "the voice gate rejected it: " + "; ".join(verdict.failures[:3])
    return answer, ""


def draft_all(cfg: Config, questions: list[str], *, company: str, role: str,
              jd_text: str, evidence: str,
              banned_phrases: tuple[str, ...] = (),
              notes: list[str] | None = None) -> dict[str, str]:
    """Every question that could be answered. A failure is loud, never silent."""
    note = notes if notes is not None else []
    out: dict[str, str] = {}
    for q in questions:
        answer, why = draft_one(cfg, question=q, company=company, role=role,
                                jd_text=jd_text, evidence=evidence,
                                banned_phrases=banned_phrases)
        if answer:
            out[q] = answer
            note.append(f"drafted an answer to {q[:60]!r}")
        else:
            note.append(f"NOT drafted, {q[:60]!r}: {why}")
    return out
