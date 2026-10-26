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
import re

from ..config import Config
from ..package import his_view as his_view_mod
from ..package import voicegate

# Long enough to answer, short enough to read. Application boxes are not essays
# however the question is phrased, and a wall of text reads as a wall of text.
MIN_CHARS = 220
MAX_CHARS = 1400

# How many times to ask for it shorter before giving up. The first version threw
# a whole answer away for being 909 characters against a 900 cap, which left the
# OpenAI "Additional Information" box empty over nine characters. A cap is a
# shape to aim at, not a reason to send nothing.
TRIM_ATTEMPTS = 3

# Room to finish the sentence AND close the JSON. At 1200 one draft in three came
# back as '{"answer":"Three things the CV does not...' with no closing brace: the
# reply was cut mid-object and read as "could not find an answer", which is a
# blank box on his application for a reason that has nothing to do with the
# answer. A cap on the reply is not a cap on the answer; MAX_CHARS is.
MAX_TOKENS = 4000

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

- Write for the reader of this application. When a READER BRIEF is given, the \
answer builds one of the beliefs it names and feeds none of its doubts.
- Altitude: say what he did and what it changed for the business, as a senior \
operator and investor would. Never name a tool, a vendor or an internal (no \
platform names, no counts of agents or workflows) unless the question or the \
job description names it. His AI operating system is a credential, not the \
story: mention it only when the question asks what he has built, and then as an \
executive would say it, never as the person wiring the tools.

Return JSON only: {"answer": "..."}""" % (MIN_CHARS, MAX_CHARS)


# ---------- the writing routine (his ruling, 2026-10-26) ----------
#
# Asked whether form answers should move to the writing routine on his
# subscription, as letters did, he answered "Yes, move them": they cost about
# $7 a month on the API, Opus at medium with up to three tries a question. A
# form is one request with every open question in it, so the routine writes
# the form whole and form_problems holds it as the reader reads it. The Apply
# email waits for the routine, which runs daily at 12:47 London: at most a
# day. hunter_essay_writer = api puts the answers back on the API (draft_one).
KIND = "essay"
WRITER_KEY = "hunter_essay_writer"
WAITING = "waiting for the writing routine"
# A form whose answers the check refuses this many times is not asked again;
# the watchdog tells him (alerts.writer_problems).
FAILS = 3
SCHEMA = {"type": "object", "required": ["answers"], "additionalProperties": False,
          "properties": {"answers": {"type": "object",
                                     "additionalProperties": {"type": "string"}}}}
FORM_RULES = SYSTEM.split("Return JSON only:")[0] + (
    "Answer every QUESTION in the prompt, each on its own terms. One proof point once "
    "per form: never tell the same story or figure in two answers.\n\n"
    'Return JSON only: {"answers": {"<the question exactly as given>": "<the answer>"}}')


def writer(cfg: Config) -> str:
    m = (cfg.optional(WRITER_KEY, "routine") or "routine").strip().lower()
    return m if m in ("api", "routine") else "routine"


def form_prompt(questions: list[str], *, company: str, role: str, jd_text: str,
                view_section: str = "", reader: dict | None = None,
                banned_phrases: tuple[str, ...] = (), family: str = "") -> str:
    """What the routine is asked, with everything draft_one's prompt carries."""
    from ..package import proof
    budget = proof.ai_budget(family)
    rule = ("" if budget is None else
            f"\n\nHis AI operating system may appear in at most {budget} answer(s), and only "
            "in an answer to a question that asks what he has built.")
    forbidden = ("" if not banned_phrases else
                 "\n\nNEVER use any of these words or phrases, in any form:\n"
                 + "\n".join(f"- {b}" for b in sorted(set(banned_phrases))))
    brief = reader_section(reader)
    qs = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return (f"COMPANY: {company}\nROLE: {role}\n\n"
            + (f"{view_section.rstrip()}\n\n" if view_section else "")
            + (f"{brief}\n\n" if brief else "")
            + f"QUESTIONS:\n{qs}{rule}\n\nJOB DESCRIPTION:\n{jd_text[:6000]}{forbidden}")


def form_answers(output) -> dict[str, str]:
    """The answers in a routine reply, question to answer."""
    try:
        data = output if isinstance(output, dict) else json.loads(output or "{}")
    except (TypeError, ValueError):
        return {}
    got = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(got, dict):
        return {}
    return {str(q): voicegate.plain_prose(str(a or "")).strip() for q, a in got.items()}


def form_check(output, *, prompt: str, evidence: str, meta: dict) -> list[str]:
    """Why a routine's answers may not go on the form, or []. The same rules
    the API path holds each answer to, then the form taken whole. Redacted as
    the API path's public reasons are: these are stored and printed."""
    from .. import drafts
    answers = form_answers(output)
    if not answers:
        return ["the answer is not the essay schema: no answers object"]
    reaction = drafts.reaction_in(prompt)
    view = his_view_mod.HisView(reaction=reaction) if reaction else None
    posting = meta.get("posting") or ""
    banned = tuple(meta.get("banned") or ())
    ruled_out = tuple(meta.get("ruled_out") or ())
    out = []
    for i, q in enumerate(meta.get("questions") or [], 1):
        a = answers.get(q, "")
        if not a:
            out.append(f"question {i} has no answer")
            continue
        problem = _problem(a, evidence, banned, his_view=view, public=True,
                           posting=posting + "\n" + q, ruled_out=ruled_out)
        if problem:
            out.append(f"answer {i}: {problem}")
    out += form_problems({q: answers[q] for q in meta.get("questions") or [] if answers.get(q)},
                         posting=posting, family=meta.get("family") or "", ruled_out=ruled_out)
    return out


def from_routine(cfg: Config, questions: list[str], *, ref: str, company: str, role: str,
                 jd_text: str, evidence: str, banned_phrases: tuple[str, ...] = (),
                 notes: list[str] | None = None, his_view=None, reader: dict | None = None,
                 family: str = "", ruled_out: tuple[str, ...] = ()) -> dict[str, str]:
    """The form's answers when the routine has written them and they pass;
    otherwise nothing, a request queued, and a reason for each question."""
    from .. import drafts
    from ..config import db_get, db_patch_where
    note = notes if notes is not None else []

    def waiting(why: str) -> dict:
        for q in questions:
            note.append(f"NOT drafted, {q[:60]!r}: {why}")
        return {}

    if not ref:
        return waiting("no posting to file the request under")
    if not evidence.strip():
        return waiting("no evidence loaded, so nothing could be traced")
    rows = db_get(cfg, drafts.TABLE, {
        "select": "id,status,prompt,evidence,meta,output", "kind": f"eq.{KIND}",
        "ref": f"eq.{ref}", "order": "id.desc", "limit": "20"})
    latest = rows[0] if rows else None
    asked = set((latest or {}).get("meta", {}).get("questions") or [])
    if latest and latest["status"] in ("drafted", "used") and set(questions) <= asked:
        problems = form_check(latest.get("output"), prompt=latest.get("prompt") or "",
                              evidence=latest.get("evidence") or "", meta=latest.get("meta") or {})
        if not problems:
            answers = form_answers(latest.get("output"))
            if latest["status"] == "drafted":
                db_patch_where(cfg, drafts.TABLE, {"id": f"eq.{latest['id']}",
                                                   "status": "eq.drafted"},
                               {"status": "used", "used_at": drafts._now()})
            for q in questions:
                note.append(f"drafted an answer to {q[:60]!r} (the writing routine)")
            return {q: answers[q] for q in questions}
        db_patch_where(cfg, drafts.TABLE, {"id": f"eq.{latest['id']}"},
                       {"status": "failed", "problems": problems[:6]})
        rows[0] = dict(latest, status="failed")
    elif latest and latest["status"] in ("queued", "drafted"):
        if set(questions) <= asked:
            return waiting(WAITING)
        # The form now asks something the request did not: ask again, whole.
        db_patch_where(cfg, drafts.TABLE, {"id": f"eq.{latest['id']}",
                                           "status": f"eq.{latest['status']}"},
                       {"status": "expired", "problems": ["the form's questions changed"]})
    if sum(1 for r in rows if r.get("status") == "failed") >= FAILS:
        return waiting(f"the writing routine's answers failed the check {FAILS} times")
    view = his_view_mod.section(his_view, company=company)
    prompt = form_prompt(questions, company=company, role=role, jd_text=jd_text,
                         view_section=view, reader=reader, banned_phrases=banned_phrases,
                         family=family)
    drafts.request(cfg, KIND, ref, prompt,
                   context=FORM_RULES + f"\n\nEVIDENCE (everything you may draw on):\n"
                                        f"{evidence[:120000]}",
                   schema=SCHEMA, evidence=evidence,
                   meta={"job_id": ref, "questions": list(questions), "company": company,
                         "role": role, "posting": (jd_text or "")[:6000],
                         "banned": sorted(set(banned_phrases)), "family": family,
                         "ruled_out": list(ruled_out)})
    return waiting(WAITING)


def _text(resp) -> str:
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "text":
            return block.text or ""
    return ""


def reader_section(reader: dict | None) -> str:
    """The letter's reader brief, for the form answers: the same reader reads
    both (compose.reader_problems)."""
    r = reader or {}
    if not r:
        return ""
    lines = [f"READER BRIEF: {r.get('who') or ''}; accountable for "
             f"{r.get('accountable_for') or ''}; expects {r.get('register') or ''}."]
    for i, b in enumerate(r.get("must_believe") or []):
        if isinstance(b, dict):
            lines.append(f"Belief {i}: {b.get('belief') or ''}")
    for d in r.get("doubts") or []:
        if isinstance(d, dict):
            lines.append(f"Doubt: {d.get('doubt') or ''}")
    return "\n".join(lines)


# One proof point once per form (his ruling, 2026-10-19, on Replit's two
# answers, which both told the same publisher story). Two answers repeat when
# they share a run of this many words: a story retold, not a common phrase.
REPEAT_RUN = 6
_WORD = re.compile(r"[a-z0-9$%][a-z0-9$%'.-]*")


def _runs(text: str, n: int = REPEAT_RUN) -> set[tuple[str, ...]]:
    words = _WORD.findall((text or "").lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def repeats(answer: str, told: list[str]) -> bool:
    """Whether this answer retells something an earlier answer on the form told."""
    mine = _runs(answer)
    return any(mine & _runs(t) for t in told)


def asks_what_he_built(question: str) -> bool:
    import re
    return bool(re.search(r"\b(built|build|made|created|shipped|project)\b", question or "", re.I))


def draft_one(cfg: Config, *, question: str, company: str, role: str,
              jd_text: str, evidence: str,
              banned_phrases: tuple[str, ...] = (),
              his_view=None, reader: dict | None = None,
              ruled_out: tuple[str, ...] = (),
              ai_allowed: bool = True,
              told: list[str] | None = None) -> tuple[str, str]:
    """(answer, why_not). An empty answer always carries a reason.

    his_view is what he said about the role (package.his_view), held to the
    rules the package is: the same section of the tailor prompt goes into
    the system text, and the answer is gated against the evidence alone,
    never his note, so nothing only his note says reaches the form. With no
    score and no reaction the system text is what it was."""
    if not evidence.strip():
        return "", "no evidence loaded, so nothing could be traced"
    from .. import llm
    # His banned list, up front. Telling the model only after it has already
    # used one of them wastes a retry and sometimes all three: the OpenAI answer
    # died three times on the word "solutions" because nothing had ever said not
    # to use it.
    forbidden = ""
    if banned_phrases:
        forbidden = ("\n\nNEVER use any of these words or phrases, in any form:\n"
                     + "\n".join(f"- {b}" for b in sorted(set(banned_phrases))))
    # The evidence is the same for every question on every form in a run, so
    # it goes in the cached system text and the question comes after it.
    # Sent inside the prompt, all of it was paid for at the full input price
    # on every question and again on every retry.
    # His view before the evidence, in the cached text with it: the same for
    # every question on this form, different on the next one.
    view = his_view_mod.section(his_view, company=company)
    system = (SYSTEM + (f"\n\n{view.rstrip()}" if view else "")
              + f"\n\nEVIDENCE (everything you may draw on):\n{evidence[:120000]}")
    brief = reader_section(reader)
    rule = ("" if ai_allowed else
            "\n\nThis answer must not mention his AI operating system or his hands-on AI "
            "building: another answer on this form already does, or the question does not ask.")
    told = list(told or [])
    if told:
        rule += ("\n\nALREADY TOLD ON THIS FORM (never tell these stories or figures "
                 "again; use a different proof):\n" + "\n---\n".join(told))
    prompt = (f"COMPANY: {company}\nROLE: {role}\n\n"
              + (f"{brief}\n\n" if brief else "")
              + f"QUESTION:\n{question}{rule}\n\n"
              f"JOB DESCRIPTION:\n{jd_text[:6000]}"
              f"{forbidden}")
    raw, notes = llm.complete(cfg, prompt, max_tokens=MAX_TOKENS, system=system,
                              purpose="essay", cache=True)
    if not raw:
        return "", "; ".join(notes) or "no model answered"

    answer, why = _parse(raw)
    if not answer:
        return "", why

    # Ask for it shorter rather than discarding it. Same for a gate failure: the
    # model can be told what it got wrong and try again, which is how the cover
    # letter's hook already works.
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(TRIM_ATTEMPTS):
        problem = _problem(answer, evidence, banned_phrases, his_view=his_view,
                           public=False, posting=jd_text + "\n" + question,
                           ruled_out=ruled_out, ai_allowed=ai_allowed, told=told)
        if not problem:
            return answer, ""
        if attempt == TRIM_ATTEMPTS - 1:
            break
        messages = messages + [
            {"role": "assistant", "content": json.dumps({"answer": answer})},
            {"role": "user", "content":
             f"That answer will not do: {problem}. Keep everything that is "
             f"true and specific, cut what is not, and return the same JSON."}]
        raw, notes = llm.complete(cfg, messages[-1]["content"],
                                  max_tokens=MAX_TOKENS, system=system,
                                  history=messages[:-1], purpose="essay", cache=True)
        if not raw:
            return "", "; ".join(notes) or "no model answered"
        answer, why = _parse(raw)
        if not answer:
            return "", why
    return "", (_problem(answer, evidence, banned_phrases, his_view=his_view,
                         posting=jd_text + "\n" + question, ruled_out=ruled_out,
                         ai_allowed=ai_allowed, told=told)
                or "unknown")


def _parse(raw: str, truncated: bool = False) -> tuple[str, str]:
    """The answer, however the model chose to wrap it.

    A reply cut off at the token limit is named as that, not as a malformed one:
    the two need different fixes and only one of them is the model's fault.

    A strict json.loads of the whole reply threw away a good answer because the
    model put a sentence in front of it. The JSON asked for is a container, not
    the point, so this looks for the object anywhere in the reply and falls back
    to the plain text when there is no object at all.
    """
    if truncated:
        return "", "the reply was cut off at the token limit"
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            answer = voicegate.plain_prose(
                (json.loads(raw[start:end + 1]).get("answer") or "")).strip()
            if answer:
                return answer, ""
        except Exception:
            pass
    # No object, or an object without the key. If what came back reads as the
    # answer itself, use it: the alternative is a blank box on his application.
    if raw and "{" not in raw and "}" not in raw:
        return voicegate.plain_prose(raw).strip(), ""
    # Its length, never its words. The reply is the model's, and the model
    # has his note in its system text; a reason is printed in the approval
    # run, which on the Prepare path is a public Actions log.
    return "", (f"could not find an answer in the reply ({len(raw)} characters, "
                f"no answer field)")


def _problem(answer: str, evidence: str,
             banned_phrases: tuple[str, ...], *, his_view=None,
             public: bool = True, posting: str = "",
             ruled_out: tuple[str, ...] = (), ai_allowed: bool = True,
             told: list[str] | None = None) -> str:
    """What is wrong with this draft. public False is the retry message to
    the model, told exactly what failed so it can fix it; public True is the
    reason returned and printed in the approval run and plan.notes, which
    with a reaction of his quotes no refused figure or name
    (his_view.redact)."""
    if len(answer) > MAX_CHARS:
        return (f"it runs to {len(answer)} characters and must be under "
                f"{MAX_CHARS}")
    if len(answer) < MIN_CHARS:
        return (f"it is only {len(answer)} characters and must be at least "
                f"{MIN_CHARS}")
    verdict = voicegate.check(answer, evidence=evidence,
                              banned_phrases=banned_phrases)
    if not verdict.ok:
        failures = (his_view_mod.redact(verdict.failures, his_view) if public
                    else list(verdict.failures))
        return "the voice gate rejected it: " + "; ".join(failures[:3])
    from ..package import proof
    hits = proof.mechanics_in(answer, posting, ruled_out)
    if hits:
        return (f"it says how something is built ({', '.join(hits)}); say what it did "
                f"for the business instead, and name no tool")
    if told and repeats(answer, told):
        return ("it retells a story another answer on this form already tells; "
                "use a different proof")
    if not ai_allowed and proof.says_ai_system(answer):
        return ("it mentions his AI operating system, which this answer may not; "
                "use another proof")
    if his_view_mod.quotes_note(answer, his_view, evidence):
        return ("it copies his note word for word; say it in the answer's own "
                "words")
    return ""


def form_problems(answers: dict[str, str], *, posting: str, family: str = "",
                  ruled_out: tuple[str, ...] = ()) -> list[str]:
    """What is wrong with a form's answers taken together, as the reader reads
    them: a tool or an internal in any answer, and his AI system said in more
    answers than the seat allows, or in one that does not ask what he built.
    Labels only, never his words."""
    from ..package import proof
    out = []
    budget = proof.ai_budget(family)
    says = []
    for i, (q, a) in enumerate(answers.items(), 1):
        hits = proof.mechanics_in(a, posting + "\n" + q, ruled_out)
        if hits:
            out.append(f"answer {i} says how something is built ({', '.join(hits)})")
        if proof.says_ai_system(a):
            says.append(i)
            if budget is not None and not asks_what_he_built(q):
                out.append(f"answer {i} speaks of his AI system to a question that does not ask")
    if budget is not None and len(says) > budget:
        out.append(f"{len(says)} answers speak of his AI system; this seat gets at most {budget}")
    texts = list(answers.values())
    for i in range(1, len(texts)):
        if repeats(texts[i], texts[:i]):
            out.append(f"answer {i + 1} retells a story an earlier answer tells")
    return out


def draft_all(cfg: Config, questions: list[str], *, company: str, role: str,
              jd_text: str, evidence: str,
              banned_phrases: tuple[str, ...] = (),
              notes: list[str] | None = None,
              his_view=None, reader: dict | None = None,
              family: str = "", ruled_out: tuple[str, ...] = (),
              ref: str = "") -> dict[str, str]:
    """Every question that could be answered. A failure is loud, never silent.

    Written by the routine unless hunter_essay_writer says api (from_routine).

    His AI system is said once per form at most outside an AI transformation
    seat (his ruling 2026-10-19), and only in an answer to a question about
    what he has built; the Replit form said it in both of its answers."""
    if writer(cfg) == "routine":
        return from_routine(cfg, questions, ref=ref, company=company, role=role,
                            jd_text=jd_text, evidence=evidence, banned_phrases=banned_phrases,
                            notes=notes, his_view=his_view, reader=reader, family=family,
                            ruled_out=ruled_out)
    from ..package import proof
    note = notes if notes is not None else []
    out: dict[str, str] = {}
    budget = proof.ai_budget(family)
    used = 0
    order = sorted(questions, key=lambda q: not asks_what_he_built(q))
    for q in order:
        allowed = budget is None or (used < budget and asks_what_he_built(q))
        answer, why = draft_one(cfg, question=q, company=company, role=role,
                                jd_text=jd_text, evidence=evidence,
                                banned_phrases=banned_phrases,
                                his_view=his_view, reader=reader,
                                ruled_out=ruled_out, ai_allowed=allowed,
                                told=list(out.values()))
        if answer and proof.says_ai_system(answer):
            used += 1
        if answer:
            out[q] = answer
            note.append(f"drafted an answer to {q[:60]!r}")
        else:
            note.append(f"NOT drafted, {q[:60]!r}: {why}")
    return out
