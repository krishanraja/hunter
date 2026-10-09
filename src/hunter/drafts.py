"""Prose written on Krish's subscription, checked by hunter before it is used.

His words, 2026-10-07: "we absolutely need to get hunter's API costs down by
about 3X". The judge has to run unattended on the API, because it reads a
hundred roles a run. The prose does not: the case for each row he reads and the
opening line on each door-in card can wait for a weekly Claude Code routine on
his plan, which loads his voice skills as it writes.

How it works, and who writes what (one writer per column):

  hunter    queues a request (`request`): the exact prompt the API path would
            send, the JSON schema of the answer, and the evidence the answer
            will be checked against. Shared context is stored once by hash.
  routine   answers queued requests: output, drafted_at, drafted_by, and
            status queued -> drafted. Nothing else. docs/ROUTINE_WRITER.md.
  hunter    every hour (`settle`): checks each drafted answer with the same
            validator and voice gate the API path uses, then uses it or marks
            it failed. A request nobody answered in WAIT_HOURS falls back to
            the API under the monthly ceiling, so nothing waits forever.

`hunter_writer` in system_config chooses the writer: "api" (the default, and
what ran before this module) or "routine". Switching is his decision.

The checks are pure functions (`problems_for`), so the routine runs the very
same code on its own answer before it writes it back, without a database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from .config import ALL_ROWS, Config, db_get, db_insert, db_patch, db_patch_where

TABLE = "hunter_drafts"
CONTEXTS = "hunter_draft_contexts"
KINDS = ("case", "door_observation")
WAIT_HOURS = 48
MODES = ("api", "routine")


def writer(cfg: Config) -> str:
    mode = (cfg.optional("hunter_writer", "api") or "api").strip().lower()
    return mode if mode in MODES else "api"


def context_key(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------- the checks: one standard, whoever wrote the words ----------

def case_text(output) -> tuple[str, str, dict]:
    """(Why It Fits, JD Snippet, parts) from a case answer."""
    from .package import rationale
    parts = output if isinstance(output, dict) else json.loads(output or "{}")
    return rationale.assemble(parts), " ".join((parts.get("snippet") or "").split()), parts


def problems_for(kind: str, output, *, prompt: str, context: str = "",
                 evidence: str = "", meta: dict | None = None) -> list[str]:
    """Why this answer may not be used, or [] when it may. Exactly the checks
    the API path applies, plus the voice gate on the finished text."""
    from .package import rationale, voicegate
    meta = meta or {}
    if kind == "case":
        try:
            why, snippet, parts = case_text(output)
        except (ValueError, TypeError, KeyError) as e:
            return [f"the answer is not the case schema: {e.__class__.__name__}"]
        hay = prompt + "\n" + context
        out = list(rationale.validate(parts, hay))
        # The words themselves, without the FIT and RISK labels the cell adds,
        # which the gate would otherwise read as names.
        words = " ".join(str(parts.get(k) or "") for k in ("mandate", "fit", "risk", "snippet"))
        gate = voicegate.check(words, evidence=voicegate.build_evidence(hay),
                               banned_phrases=tuple(b for b in rationale.BANNED if len(b) > 1),
                               allow_names=frozenset({meta.get("company") or ""}))
        return out + [f"voice gate: {f}" for f in gate.failures]
    if kind == "door_observation":
        from . import doorin
        text = output.get("text") if isinstance(output, dict) else output
        text = " ".join(str(text or "").split()).strip().strip('"')
        return doorin.check_observation(text, evidence, frozenset(meta.get("names") or []))
    return [f"unknown kind {kind!r}"]


# ---------- hunter's side: request, then settle ----------

def save_context(cfg: Config, text: str) -> str:
    key = context_key(text)
    db_insert(cfg, CONTEXTS, [{"key": key, "text": text}],
              on_conflict="key", ignore_duplicates=True)
    return key


def request(cfg: Config, kind: str, ref: str, prompt: str, *, context: str = "",
            schema: dict | None = None, evidence: str = "", meta: dict | None = None) -> bool:
    """Queue one request. False when one is already open for this kind and ref."""
    if kind not in KINDS:
        raise ValueError(f"unknown draft kind {kind!r}")
    open_ = db_get(cfg, TABLE, {"select": "id", "kind": f"eq.{kind}", "ref": f"eq.{ref}",
                                "status": "in.(queued,drafted)", "limit": "1"})
    if open_:
        return False
    db_insert(cfg, TABLE, [{
        "kind": kind, "ref": ref, "prompt": prompt, "schema": schema,
        "context_key": save_context(cfg, context) if context else None,
        "evidence": evidence, "meta": meta or {}, "status": "queued"}])
    return True


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _context(cfg: Config, key: str | None, cache: dict) -> str:
    if not key:
        return ""
    if key not in cache:
        rows = db_get(cfg, CONTEXTS, {"select": "text", "key": f"eq.{key}"})
        cache[key] = rows[0]["text"] if rows else ""
    return cache[key]


def settle(cfg: Config, *, apply: bool = False, sheet=None, canon_headers=None,
           wait_hours: int = WAIT_HOURS, use_case=None, use_observation=None,
           api_case=None, api_observation=None) -> list[str]:
    """Check every drafted answer and use the good ones; fall back to the API
    for requests the routine left unanswered. Returns summary lines.

    use_* write a passing answer where it belongs and return True only after
    reading it back; api_* produce an answer through the API. All four default
    to the real functions and are parameters so the tests run offline."""
    use_case = use_case or (lambda r, why, snip: _use_case(cfg, r, why, snip, sheet, canon_headers))
    use_observation = use_observation or (lambda r, text: _use_observation(cfg, r, text))
    api_case = api_case or (lambda r, ctx: _api_case(cfg, r, ctx))
    api_observation = api_observation or (lambda r, ctx: _api_observation(cfg, r))
    rows = db_get(cfg, TABLE, {"select": "*", "status": "in.(queued,drafted)",
                               "order": "requested_at.asc", "limit": ALL_ROWS})
    stale_before = (dt.datetime.now(dt.timezone.utc)
                    - dt.timedelta(hours=wait_hours)).isoformat()
    cache: dict = {}
    lines: list[str] = []
    counts = {"used": 0, "failed": 0, "api": 0, "waiting": 0}
    for r in rows:
        ctx = _context(cfg, r.get("context_key"), cache)
        output, by = r.get("output"), r.get("drafted_by") or "routine"
        if r["status"] == "queued":
            if (r.get("requested_at") or "") >= stale_before:
                counts["waiting"] += 1
                continue
            if not apply:
                counts["api"] += 1
                continue
            output = (api_case if r["kind"] == "case" else api_observation)(r, ctx)
            by = "api"
            counts["api"] += 1
            if output is None:
                _close(cfg, r, "failed", problems=["the API fallback produced nothing"],
                       by=by, apply=apply)
                counts["failed"] += 1
                continue
        problems = problems_for(r["kind"], output, prompt=r["prompt"], context=ctx,
                                evidence=r.get("evidence") or "", meta=r.get("meta") or {})
        if problems and by == "routine" and apply:
            # The routine's answer failed the API path's checks: the API writes
            # it instead, once, under the ceiling, and is held to the same checks.
            lines.append(f"  {r['kind']} {r['ref']} from the routine failed: "
                         f"{'; '.join(problems[:2])}; asking the API")
            output = (api_case if r["kind"] == "case" else api_observation)(r, ctx)
            by = "api"
            counts["api"] += 1
            problems = (["the API fallback produced nothing"] if output is None else
                        problems_for(r["kind"], output, prompt=r["prompt"], context=ctx,
                                     evidence=r.get("evidence") or "", meta=r.get("meta") or {}))
        if problems:
            _close(cfg, r, "failed", problems=problems, by=by, output=output, apply=apply)
            counts["failed"] += 1
            lines.append(f"  {r['kind']} {r['ref']} from the {by} failed: {'; '.join(problems[:2])}")
            continue
        if not apply:
            counts["used"] += 1
            continue
        if r["kind"] == "case":
            why, snippet, _ = case_text(output)
            ok = use_case(r, why, snippet)
        else:
            text = output.get("text") if isinstance(output, dict) else output
            ok = use_observation(r, " ".join(str(text).split()).strip().strip('"'))
        _close(cfg, r, "used" if ok else "failed", by=by, output=output, apply=apply,
               problems=[] if ok else ["written, but the read-back did not find it"])
        counts["used" if ok else "failed"] += 1
    lines.insert(0, f"drafts: {counts['used']} used, {counts['failed']} failed, "
                    f"{counts['api']} from the API fallback, {counts['waiting']} waiting "
                    f"for the routine" + ("" if apply else " (dry run)"))
    return lines


def _close(cfg, r, status, *, problems=(), by=None, output=None, apply=True):
    if not apply:
        return
    body = {"status": status, "problems": list(problems)[:6]}
    if status == "used":
        body["used_at"] = _now()
    if by == "api":
        body["drafted_by"], body["drafted_at"], body["output"] = "api", _now(), output
    db_patch(cfg, TABLE, {"id": r["id"]}, body)


def _use_case(cfg, r, why, snippet, sheet, canon_headers) -> bool:
    """Why It Fits on the database row and on his Pipeline row, read back."""
    meta = r.get("meta") or {}
    if meta.get("audit_prefix"):
        why = f"{meta['audit_prefix']} {why}"
    if meta.get("note"):
        why = f"{why} {meta['note']}"
    why = why[:900]
    db_patch(cfg, "hunter_seen_roles", {"job_id": r["ref"]}, {"why_it_fits": why})
    back = db_get(cfg, "hunter_seen_roles", {"select": "why_it_fits,score,url,job_url,company,title",
                                             "job_id": f"eq.{r['ref']}"})
    if not back or back[0].get("why_it_fits") != why:
        return False
    if sheet is not None and canon_headers:
        from .actions import pipeline_row
        b = back[0]
        found = pipeline_row(sheet.read_pipeline(canon_headers), b)
        hit = [found] if found else []
        score = int(b.get("score") or meta.get("score") or 0)
        if hit and 1 <= score <= 10:
            sheet.update_assessment(hit[0].row_number, score=score, why_it_fits=why)
    return True


def _use_observation(cfg, r, text) -> bool:
    """The opening line on his door-in card, only while the card is still
    hunter's (listed, tagged). A card he has drafted or moved is his."""
    from . import doorin
    n = db_patch_where(cfg, "pilot_deals", {
        "contact_id": f"eq.{r['ref']}", "state": "eq.listed",
        "notes": f"ilike.{doorin.NOTES_TAG}*"}, {"draft_body": text})
    if not n:
        return False
    back = db_get(cfg, "pilot_deals", {"select": "draft_body", "contact_id": f"eq.{r['ref']}"})
    return bool(back) and back[0].get("draft_body") == text


def _api_case(cfg, r, ctx):
    from . import judge
    m = (r.get("meta") or {}).get("role") or {}
    role = judge.Role(job_id=r["ref"], company=m.get("company", ""), title=m.get("title", ""),
                      location=m.get("location", ""), comp=m.get("comp", ""),
                      url=m.get("url", ""), posting=m.get("posting", ""),
                      source=m.get("source", ""))
    why, snippet, problems, _ = judge.make_the_case(cfg, ctx, role)
    if problems:
        return None
    # make_the_case returns assembled text; hand it back in the schema's shape.
    mandate, _, rest = why.partition(" FIT: ")
    fit, _, risk = rest.partition(" RISK: ")
    return {"mandate": mandate, "fit": fit, "risk": risk, "archetype": "", "snippet": snippet}


def _api_observation(cfg, r):
    from . import llm, doorin
    text, _ = llm.complete(cfg, r["prompt"], max_tokens=300, system=doorin.OBSERVATION_SYSTEM,
                           purpose="door_observation", cache=True)
    return " ".join((text or "").split()).strip().strip('"') or None


def queue_case(cfg, system: str, role, *, meta: dict) -> bool:
    """A case request for one row about to reach his sheet."""
    from . import judge
    from .package.rationale import RATIONALE_SCHEMA
    m = dict(meta)
    m["company"] = role.company
    m["role"] = {"company": role.company, "title": role.title, "location": role.location,
                 "comp": role.comp, "url": role.url, "posting": role.posting,
                 "source": role.source}
    return request(cfg, "case", role.job_id,
                   judge.user_prompt(role) + "\n\n" + judge.CASE_PROMPT,
                   context=system, schema=RATIONALE_SCHEMA, meta=m)


def queue_observation(cfg, card, evidence: str):
    """observe_with for doorin.run_cards in routine mode: queue, never call."""
    from . import doorin
    prompt, haystack, names = doorin.observation_request(card, evidence)
    if card.leader.contact_id:
        request(cfg, "door_observation", str(card.leader.contact_id), prompt,
                context=doorin.OBSERVATION_SYSTEM, evidence=haystack,
                meta={"names": sorted(names), "company": card.company})
        card.observation_note = "queued for the writing routine"
    return card


def main(argv: list[str]) -> int:
    """`python -m hunter.drafts check FILE`: the routine's own check, offline.

    FILE is a JSON list of {kind, prompt, context, evidence, meta, output}. It
    prints each answer's problems and exits 1 if any has one, so the routine
    rewrites before it writes back. No database, no network, no key."""
    if len(argv) != 2 or argv[0] != "check":
        print("usage: python -m hunter.drafts check FILE")
        return 2
    items = json.load(open(argv[1], encoding="utf-8"))
    bad = 0
    for i, it in enumerate(items):
        problems = problems_for(it["kind"], it.get("output"), prompt=it.get("prompt", ""),
                                context=it.get("context", ""), evidence=it.get("evidence", ""),
                                meta=it.get("meta") or {})
        bad += bool(problems)
        print(f"{i} {it.get('ref', '')}: " + ("ok" if not problems else "; ".join(problems)))
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
