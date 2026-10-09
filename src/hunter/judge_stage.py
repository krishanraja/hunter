"""The judge, in the sourcing path. Off, shadow, or gate.

shadow (the default while it earns trust): every candidate is judged and the
judgement is written onto its database row, and NOTHING about what reaches him
changes. The run summary puts the two lists side by side, so he can see what the
old rules staged and what the judge would have.

gate: the judge decides. A role reaches his sheet only when the judge presents
it at fit 6 or more (the threshold judge-eval chose on the development window,
see tests/fixtures/judge_dev.json), at most twenty a run, plus up to two roles
the judge HELD,
labelled as an audit, so what the judge hides is measured continuously rather
than assumed. Switched on only on his word, after judge-eval and the blind set.

off: the old path, untouched.

Candidates are what clears the clear-cut rules only: not a duplicate (dedupe ran
before this), not dead, not on the never-apply list, not a city he cannot work
in, not a stated pay ceiling under the floor. Everything that needs judgement is
the judge's, including the roles the old regex gates would have blocked, so the
judge can find what those gates threw away.
"""
from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass, field

from . import judge, spend
from .config import Config, db_insert

# Failures of these gates are facts, not judgement, and stop a role before the
# judge: G0 never-apply, G1 dead posting, G2 stated ceiling under the floor,
# G6 a location he cannot work from.
HARD_GATES = frozenset({"G0", "G1", "G2", "G6"})
AUDIT_PREFIX = "AUDIT, the judge held this"


@dataclass
class Candidate:
    role: object            # ResolvedRole
    result: object         # score result, for ranking only
    row: dict              # the hunter_seen_roles row about to be written
    rank: float
    judgement: judge.Judgement | None = None


@dataclass
class Outcome:
    mode: str
    judged: int = 0
    counts: dict = field(default_factory=dict)
    usd: float = 0.0
    cache_reads: int = 0
    present: list = field(default_factory=list)     # Candidates, best first
    held: list = field(default_factory=list)
    audit: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    system: str = ""        # the cached context, reused to write the rows' case


def mode_of(cfg: Config) -> str:
    m = (cfg.optional("hunter_judge_mode", "shadow") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "gate") else "shadow"


def hard_failure(report) -> str | None:
    for g in report.failures():
        if g.gate in HARD_GATES:
            return f"{g.gate}: {g.reason}"
    return None


def run(cfg: Config, canon, sheet, candidates: list[Candidate], *, mode: str,
        system: str | None = None, client=None) -> Outcome:
    out = Outcome(mode=mode)
    if mode == "off" or not candidates:
        return out
    limit = int(cfg.optional("hunter_judge_max_per_run", "60"))
    max_usd = float(cfg.optional("hunter_judge_max_usd_per_run", "8"))
    min_fit = int(cfg.optional("hunter_judge_min_fit", str(judge.MIN_FIT)))
    # He reads a long list easily, and on the holdout the judge presented 29
    # percent of what it judged; ten a run would cut roles it had shown.
    cap = int(cfg.optional("hunter_judge_max_present", "20"))
    workers = int(cfg.optional("hunter_judge_workers", "6"))

    ranked = sorted(candidates, key=lambda c: c.rank)
    todo, over = ranked[:limit], ranked[limit:]
    stop = spend.over_budget(cfg)
    if stop:
        # The month's ceiling is reached: nothing is judged, and in gate mode
        # nothing is staged on a judge that did not run.
        todo, over = [], ranked
        out.lines.append(f"judge did not run: {stop}")
    # A role judged in the last day under the same prompt and model is not
    # judged again. On 4 October the first scheduled attempt judged 100 roles,
    # failed a minute later, and the retry paid for the same 100 again.
    model = cfg.optional("hunter_judge_model", judge.DEFAULT_MODEL)
    earlier = earlier_judgements(cfg, [c.role.job_id for c in todo], model)
    for c in todo:
        if c.role.job_id in earlier:
            c.judgement = earlier[c.role.job_id]
    reused = sum(1 for c in todo if c.judgement is not None)
    todo = [c for c in todo if c.judgement is None]
    if system is None:
        system = live_context(cfg, sheet, canon)
    out.system = system
    client = client or judge._client(cfg)

    from . import universe
    try:
        notes = universe.top_notes(cfg)
    except Exception:
        notes = {}

    def one(c: Candidate) -> judge.Judgement:
        r = c.role
        return judge.judge_role(cfg, system, judge.Role(
            job_id=r.job_id, company=r.company, title=r.title, location=r.location,
            comp=r.comp, url=r.jd_url or r.url, posting=r.jd_text or "",
            source=r.source, company_note=universe.note_for(notes, r.company)),
            client=client)

    # The first call writes the cache; the rest read it. Running it alone first
    # is what makes the other calls cheap.
    spent = 0.0
    if todo:
        todo[0].judgement = one(todo[0])
        spent += todo[0].judgement.usd
    rest = todo[1:]
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        i = 0
        while i < len(rest):
            if spent >= max_usd:
                break
            chunk = rest[i:i + workers]
            for c, j in zip(chunk, pool.map(one, chunk)):
                c.judgement = j
                spent += j.usd
            i += workers
    unjudged = [c for c in todo if c.judgement is None] + \
        [c for c in over if c.judgement is None]
    for c in unjudged:
        c.judgement = judge.Judgement(
            job_id=c.role.job_id, verdict="pending", fit=None, confidence="", answers={},
            red_flags=[], likely_decline_code="none", why_it_fits="", snippet="",
            model="", served_model="", problems=[stop or "over this run's judging budget"])

    calls = []
    for c in candidates:
        j = c.judgement
        c.row.update(j.row_patch())
        if j.usage:
            calls.append({"job_id": j.job_id, "model": j.model,
                          "served_model": j.served_model or None,
                          "input_tokens": j.usage.get("input_tokens"),
                          "cache_read_tokens": j.usage.get("cache_read_input_tokens"),
                          "cache_write_tokens": j.usage.get("cache_creation_input_tokens"),
                          "output_tokens": j.usage.get("output_tokens"), "usd": j.usd,
                          "prompt_version": j.prompt_version,
                          # Kept so a retried run can reuse it (earlier_judgements).
                          "judgement": None if j.verdict == "pending" else stored(j)})
            out.cache_reads += j.usage.get("cache_read_input_tokens", 0)
    if calls:
        try:
            db_insert(cfg, "hunter_judge_calls", calls)
        except Exception as e:
            out.lines.append(f"judge: cost ledger not written ({e.__class__.__name__})")
    spend.count("judge", spent, len(calls))
    out.judged = sum(1 for c in candidates if c.judgement.verdict != "pending")
    out.usd = round(spent, 4)

    disp = {c.role.job_id: judge.disposition(c.judgement, min_fit=min_fit) for c in candidates}
    for k in ("staging", "held", "blocked", "judge_pending"):
        out.counts[k] = sum(1 for v in disp.values() if v == k)
    present = [c for c in candidates if disp[c.role.job_id] == "staging"]
    present.sort(key=lambda c: (-(c.judgement.fit or 0), c.rank))
    out.present = present[:cap]
    held = [c for c in candidates if disp[c.role.job_id] == "held"] + present[cap:]
    held.sort(key=lambda c: (-(c.judgement.fit or 0), c.rank))
    out.held = held
    out.audit = held[:int(cfg.optional("hunter_judge_audit_per_run", "2"))]

    out.lines.append(
        f"judge ({mode}): {len(candidates)} candidate(s), {out.judged} judged for "
        f"${out.usd:.2f} ({out.cache_reads:,} cached tokens read): "
        f"{out.counts['staging']} present, {out.counts['held']} held, "
        f"{out.counts['blocked']} rejected, {out.counts['judge_pending']} pending"
        + (f"; {reused} reused from a judgement made in the last day" if reused else ""))
    for c in out.present:
        out.lines.append(f"  PRESENT fit {c.judgement.fit}: {c.role.company} / {c.role.title}")
    for c in held[:5]:
        out.lines.append(f"  held fit {c.judgement.fit}: {c.role.company} / {c.role.title}: "
                         f"{judge.reason_line(c.judgement)[:140]}")
    return out


# A rejection stands for a week; anything else for a day. Measured 2026-10-08:
# 13 of the 100 roles judged on 8 October had been judged, and rejected, on
# 4 October under the same prompt and model, and were paid for again. A week
# covers the next batch. Only a rejection is kept that long, because his newest
# rulings feed the prompt and a role the judge would present deserves a fresh
# read against them.
REUSE_HOURS = 24
REUSE_REJECT_HOURS = 24 * 7
STORED = ("job_id", "verdict", "fit", "confidence", "answers", "red_flags",
          "likely_decline_code", "why_it_fits", "snippet", "model", "served_model",
          "prompt_version", "problems")


def stored(j: judge.Judgement) -> dict:
    return {k: getattr(j, k) for k in STORED}


def earlier_judgements(cfg: Config, job_ids: list[str], model: str
                       ) -> dict[str, judge.Judgement]:
    """Judgements made in the last day under this prompt and model, by job.

    Read from hunter_judge_calls, where every judge call already lands with
    its cost. A ledger that cannot be read means every role is judged afresh,
    which is the cost this saves and never a wrong answer.
    """
    if not job_ids or not spend._live(cfg):
        return {}
    import datetime as dt
    from .config import ALL_ROWS, db_get
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=max(REUSE_HOURS, REUSE_REJECT_HOURS))).isoformat()
    day = (now - dt.timedelta(hours=REUSE_HOURS)).isoformat()
    found: dict[str, judge.Judgement] = {}
    try:
        for i in range(0, len(job_ids), 100):
            chunk = job_ids[i:i + 100]
            for r in db_get(cfg, "hunter_judge_calls", {
                    "select": "id,job_id,judgement,at",
                    "purpose": "eq.judge", "model": f"eq.{model}",
                    "prompt_version": f"eq.{judge.PROMPT_VERSION}",
                    "judgement": "not.is.null", "at": f"gte.{since}",
                    "job_id": "in.(" + ",".join(f'"{j}"' for j in chunk) + ")",
                    "order": "id.asc", "limit": ALL_ROWS}):
                d = r.get("judgement") or {}
                if d.get("verdict") in (None, "pending"):
                    continue
                if d.get("verdict") != "reject" and (r.get("at") or "") < day:
                    continue
                found[r["job_id"]] = judge.Judgement(
                    **{k: d.get(k) for k in STORED if k in d},
                    usage={}, usd=0.0)
    except Exception:
        return {}
    return found


def live_context(cfg: Config, sheet, canon) -> str:
    """The judge's fixed context for a live run: everything he has written, and
    every ruling he has made up to now."""
    from . import judgedata
    rows, _ = judgedata.collect(cfg, sheet, canon)
    dated = sorted(rows, key=lambda r: (str(r.presented_at or ""), r.company, r.title))
    rulings = [judge.ruling_line(str(r.when.date()) if r.when else "", r.label, r.company,
                                 r.title, r.words, r.comp, r.location) for r in dated]
    return judge.gather_context(cfg, sheet, canon, rulings=rulings)


def apply_gate(out: Outcome, candidates: list[Candidate]) -> None:
    """In gate mode the judge's disposition becomes the row's status."""
    by_id = {c.role.job_id: c for c in out.present}
    audit_ids = {c.role.job_id for c in out.audit}
    for c in candidates:
        j = c.judgement
        d = judge.disposition(j)
        if c.role.job_id in by_id:
            c.row["status"] = "staging"
        elif c.role.job_id in audit_ids:
            c.row["status"] = "staging"
            c.row["audit_sample"] = True
        elif d == "blocked":
            c.row["status"] = "blocked"
            c.row["rejection_reason"] = judge.reason_line(j)
        elif d == "judge_pending":
            c.row["status"] = "judge_pending"
            c.row["rejection_reason"] = "; ".join(j.problems)[:300]
        else:
            # Held, including a present that lost to the cap. Never 'staging':
            # reconcile appends any staging row missing from the sheet.
            c.row["status"] = "held"
            c.row["rejection_reason"] = judge.reason_line(j)
        if j.why_it_fits:
            c.row["why_it_fits"] = j.why_it_fits


def audit_why(j: judge.Judgement) -> str:
    return (f"{AUDIT_PREFIX} (fit {j.fit}). Your verdict tells hunter what it is "
            f"hiding. {j.why_it_fits or judge.reason_line(j)}")[:900]


def cases_for(cfg: Config, system: str, roles: list, *, client=None
              ) -> tuple[dict, float]:
    """{job_id: (Why It Fits, JD Snippet)} for the rows about to reach his
    sheet, from everything he has written, and what it cost. A role whose case
    failed its checks is left out, so the caller keeps the text it had. His
    words, 2026-10-03: "You need to convince me to say yes to these roles
    because of how well suited they are to what I want"."""
    if not roles or not system or spend.over_budget(cfg):
        return {}, 0.0
    client = client or judge._client(cfg)

    def one(r):
        return r.job_id, judge.make_the_case(cfg, system, judge.Role(
            job_id=r.job_id, company=r.company, title=r.title, location=r.location,
            comp=r.comp, url=r.jd_url or r.url, posting=r.jd_text or "",
            source=r.source), client=client)
    out, usd = {}, 0.0

    def take(jid, why, snippet, problems, cost):
        nonlocal usd
        usd += cost
        if not problems:
            out[jid] = (why, snippet)
    # The first call alone, so it writes the cache the rest read, as the
    # judge does. Started six at a time, each of the six paid to write it.
    jid, res = one(roles[0])
    take(jid, *res)
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for jid, res in pool.map(one, roles[1:]):
            take(jid, *res)
    return out, round(usd, 4)
