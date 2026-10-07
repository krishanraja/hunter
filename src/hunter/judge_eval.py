"""Measure the judge against Krish before it decides anything.

The honest version of the question: had the judge been running, which roles
would he have seen, and would he have wanted them?

- Chronological split. The judge is given only the rulings he made before the
  cutoff, and is scored on the batches presented after it. A judge that saw
  the answers would score itself.
- Twice. Every held-out role is judged twice, so the report can say how often
  the judge agrees with itself.
- Against today. The system that actually ran staged every one of these roles,
  so its precision is simply his accept rate on them. The judge has to beat that
  without hiding the roles he wanted.
- With margins. 13 Yes roles is a small sample, and every rate is reported with
  a 95 percent Wilson interval so nobody mistakes 12 of 13 for certainty.

- Tuned on one window, tested on another. The first version was scored on the
  holdout and failed it: it rejected all 13 of his Yes roles. Changing the
  prompt and re-scoring on the same roles would tune to them. So prompt work is
  measured on a development window (examples before 7 September, scored on 7 to
  16 September), the fit threshold is chosen there, and only the frozen prompt
  and threshold are scored on the holdout. The blind set he rules on fresh is
  the clean test after that.

Results are written to tests/fixtures/judge_eval.json (holdout) and
tests/fixtures/judge_dev.json (development), with no posting text, and
tests/test_judge_eval.py reads those records offline.
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import time
import concurrent.futures as cf
from collections import Counter

from . import judge, judgedata, spend
from .sources import company_key
from .config import Config

CUTOFF = datetime.datetime(2026, 9, 17, tzinfo=datetime.timezone.utc)
DEV_CUTOFF = datetime.datetime(2026, 9, 7, tzinfo=datetime.timezone.utc)
FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures"
FIXTURE = FIXTURES / "judge_eval.json"
DEV_FIXTURE = FIXTURES / "judge_dev.json"
THRESHOLDS = (5, 6, 7, 8, 9)
TARGET_YES_RECALL = 0.9
MAX_USD = 25.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """(rate, low, high) for k of n."""
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(p, 4), round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def presented(row: dict, t: int) -> bool:
    return row.get("verdict") == "present" and (row.get("fit") or 0) >= t


def metrics(rows: list[dict], t: int) -> dict:
    """Rows carry label (his) and verdict, fit (the judge's first answer)."""
    judged = [r for r in rows if r.get("verdict") not in (None, "pending")]
    yes = [r for r in judged if r["label"] == "yes"]
    no = [r for r in judged if r["label"] == "no"]
    shown = [r for r in judged if presented(r, t)]
    shown_yes = [r for r in shown if r["label"] == "yes"]
    return {
        "threshold": t,
        "judged": len(judged),
        "pending": len(rows) - len(judged),
        "presented": len(shown),
        "precision": wilson(len(shown_yes), len(shown)),
        "yes_recall": wilson(len(shown_yes), len(yes)),
        "yes_blocked": [r["company"] + " / " + r["title"] for r in yes if not presented(r, t)],
        "declines_blocked": wilson(len([r for r in no if not presented(r, t)]), len(no)),
        "declines_presented": [r["company"] + " / " + r["title"] for r in no if presented(r, t)],
    }


def baseline(rows: list[dict]) -> dict:
    """What actually happened: every one of these roles was staged."""
    yes = sum(1 for r in rows if r["label"] == "yes")
    return {"presented": len(rows), "precision": wilson(yes, len(rows)),
            "yes_recall": wilson(yes, yes), "declines_blocked": wilson(0, len(rows) - yes)}


def auc(rows: list[dict]) -> float:
    """The chance a role he said Yes to is ranked above one he declined, ties
    counted half. 0.5 is a coin; 1.0 is his own order. It measures the fit
    ranking with no threshold, so it cannot be gamed by moving one."""
    judged = [r for r in rows if r.get("fit") is not None
              and r.get("verdict") not in (None, "pending")]
    yes = [r["fit"] for r in judged if r["label"] == "yes"]
    no = [r["fit"] for r in judged if r["label"] == "no"]
    if not yes or not no:
        return float("nan")
    wins = sum((y > n) + 0.5 * (y == n) for y in yes for n in no)
    return round(wins / (len(yes) * len(no)), 4)


def choose_threshold(rows: list[dict], target: float = TARGET_YES_RECALL) -> int | None:
    """The strictest fit threshold that still presents the target share of his
    Yes roles, judged on the point estimate. When none reaches the target, the
    one that keeps the most of them, strictest on a tie: a hidden Yes is the
    expensive mistake. Chosen on the development window and then frozen;
    choosing it on the holdout would score itself."""
    rated = []
    for t in sorted(THRESHOLDS, reverse=True):
        rate = metrics(rows, t)["yes_recall"][0]
        if rate == rate:
            if rate >= target:
                return t
            rated.append((rate, t))
    if not rated:
        return None
    best = max(r for r, _ in rated)
    return max(t for r, t in rated if r == best)


def agreement(rows: list[dict], t: int) -> tuple[float, float, float]:
    pairs = [r for r in rows if r.get("verdict2") not in (None, "pending")
             and r.get("verdict") not in (None, "pending")]
    same = sum(1 for r in pairs
               if presented(r, t) == (r["verdict2"] == "present" and (r.get("fit2") or 0) >= t))
    return wilson(same, len(pairs))


def code_agreement(rows: list[dict]) -> tuple[float, float, float]:
    coded = [r for r in rows if r["label"] == "no" and r.get("his_code")
             and r.get("decline_code") not in (None, "")]
    hit = sum(1 for r in coded if r["decline_code"] == r["his_code"])
    return wilson(hit, len(coded))


def report(result: dict) -> list[str]:
    def pct(x):
        return "n/a" if x[0] != x[0] else f"{x[0]*100:.0f}% (95%: {x[1]*100:.0f} to {x[2]*100:.0f})"
    m = result["meta"]
    lines = [f"judge-eval {m['prompt_version']} on {m['model']} at {m['effort']}, "
             f"run {m['run_at'][:16]}",
             f"examples: {m['n_examples']} rulings before {m['cutoff'][:10]}; "
             f"held out: {m['n_holdout']} roles ({m['holdout_yes']} Yes, {m['holdout_no']} No), "
             f"{m['holdout_with_jd']} with the full posting",
             f"cost: ${m['usd']:.2f}" + (" through the Batches API" if m.get("batch", True) else ""),
             f"ranking (AUC, his Yes above his No): {result.get('auc', float('nan')):.2f}",
             f"threshold chosen for {TARGET_YES_RECALL:.0%} of his Yes: "
             f"{result.get('chosen_threshold')}", "",
             f"TODAY'S SYSTEM staged all {result['baseline']['presented']}: "
             f"precision {pct(result['baseline']['precision'])}", ""]
    for t in THRESHOLDS:
        x = result["metrics"][str(t)]
        lines += [f"JUDGE at fit {t} or more: presents {x['presented']} of {x['judged']} judged "
                  f"({x['pending']} pending)",
                  f"  precision (share of presented he said Yes to): {pct(x['precision'])}",
                  f"  his Yes roles it would present: {pct(x['yes_recall'])}",
                  f"  his declines it would block: {pct(x['declines_blocked'])}",
                  f"  Yes roles it would have hidden: {x['yes_blocked'] or 'none'}",
                  ""]
    if result["retest"][0] == result["retest"][0]:
        lines.append(f"agrees with itself (fit {result.get('chosen_threshold') or 8}): "
                     f"{pct(result['retest'])}")
    lines.append(f"predicts his decline reason: {pct(result['decline_codes'])}")
    for name, sl in result["slices"].items():
        x = sl
        lines.append(f"slice {name} (fit {x['threshold']}): n={x['judged']}, "
                     f"precision {pct(x['precision'])}, "
                     f"Yes kept {pct(x['yes_recall'])}, declines blocked {pct(x['declines_blocked'])}")
    return lines


def run(cfg: Config, sheet, canon, *, dev: bool = False, replicas: int | None = None,
        poll_seconds: int = 30, max_usd: float = MAX_USD, write: bool = True,
        threshold: int | None = None) -> dict:
    """dev: examples before DEV_CUTOFF, scored on DEV_CUTOFF to CUTOFF, one
    replica, direct calls (minutes, not a batch queue). Otherwise the holdout:
    examples before CUTOFF, scored after it, twice, through the Batches API."""
    rows, skipped = judgedata.collect(cfg, sheet, canon)
    # The blind set is a different population (roles the old gates threw
    # away) with its own measure (blindset.evaluate); mixing it in would make
    # this record incomparable with the last.
    rows = [r for r in rows if r.source != "blind set"]
    # His blind-set Yes roles move onto Pipeline, where they read like any
    # other row; the record of the draw is what marks them.
    from . import blindset
    if blindset.RECORD.exists():
        drawn = {p["job_id"] for p in json.loads(blindset.RECORD.read_text()).get("picks", [])}
        rows = [r for r in rows if r.job_id not in drawn]
    if dev:
        before, later, undated = judgedata.split(rows, DEV_CUTOFF)
        after = [r for r in later if r.when < CUTOFF]
        replicas = replicas or 1
    else:
        before, after, undated = judgedata.split(rows, CUTOFF)
        replicas = replicas or 2
    after = [judgedata.fill_jd(r) for r in after]
    rulings = [judge.ruling_line(str(r.when.date()), r.label, r.company, r.title, r.words,
                                 r.comp, r.location)
               for r in sorted(before, key=lambda r: (r.when, r.company, r.title))]
    system = judge.gather_context(cfg, sheet, canon, rulings=rulings)
    model = cfg.optional("hunter_judge_model", judge.DEFAULT_MODEL)
    effort = cfg.optional("hunter_judge_effort", judge.DEFAULT_EFFORT)

    from . import universe
    notes = universe.top_notes(cfg)
    roles = {r.job_id: judge.Role(job_id=r.job_id, company=r.company, title=r.title,
                                  location=r.location, comp=r.comp, url=r.url,
                                  posting=judgedata.posting_for(r), source=r.source,
                                  company_note=universe.note_for(notes, r.company))
             for r in after}
    # An estimate before anything is sent, refused over the cap. About 2.4
    # characters per token on this text, measured on the first live call. A
    # batch reads the cache only some of the time, so a batch is priced as if
    # it never did: the first holdout run cost $12.91 and the second $17.40
    # against an estimate of $15.97 that assumed the cache was read. This is an
    # estimate and is called one; nothing can stop a batch half way.
    #
    # Direct calls by default since 2026-10-07, the holdout included. The
    # three batched holdout runs cost 8 to 15 cents a role; the direct
    # development runs 4 to 6, on a context about half the size. A direct run
    # writes the cache once and every later call reads it at a twentieth of
    # the input price; a batch reads it only some of the time, and a miss is
    # the whole context at the batch's half price, ten times a cache read.
    # The live ledger measures the next holdout; hunter_judge_eval_batch=on
    # brings the batch back.
    use_batch = not dev and cfg.optional("hunter_judge_eval_batch", "") == "on"
    ctx = len(system) / 2.4
    price = spend.price_of(model)
    per_call = 4000 * price["in"] + 5000 * price["out"]
    if not use_batch:
        est = (ctx * price["cache_write"]
               + replicas * len(after) * (ctx * price["cache_read"] + per_call)) / 1e6
    else:
        est = replicas * len(after) * (ctx * price["in"] + per_call) / 1e6 / 2
    if est > max_usd:
        raise RuntimeError(f"estimated ${est:.2f} is over the ${max_usd:.2f} cap; not sent")

    client = judge._client(cfg)
    if not use_batch:
        answers, usd, batch_id = _direct(cfg, system, roles, replicas, client, model, effort)
    else:
        answers, usd, batch_id = _batch(system, roles, replicas, client, model, effort,
                                        poll_seconds, est)
    for jid, by_n in answers.items():
        for j in by_n.values():
            if j.usage:
                spend.record(cfg, "judge_eval", model, j.usage, job_id=jid,
                             served_model=j.served_model, batch=bool(batch_id))

    out_rows = []
    seen_companies = {company_key(r.company) for r in before}
    for r in after:
        a = answers.get(r.job_id, {})
        j1, j2 = a.get(1), a.get(2)
        out_rows.append({
            "batch": str(r.when.date()), "company": r.company, "title": r.title,
            "comp": r.comp, "label": r.label, "his_words": r.words, "his_code": r.code,
            "has_jd": r.has_jd, "seen_company": company_key(r.company) in seen_companies,
            "verdict": j1.verdict if j1 else None, "fit": j1.fit if j1 else None,
            "confidence": j1.confidence if j1 else None,
            "decline_code": j1.likely_decline_code if j1 else None,
            "red_flags": (j1.red_flags if j1 else [])[:4],
            "for": (j1.answers.get("strongest_reason_he_says_yes") if j1 else "") or "",
            "reason": (j1.answers.get("most_likely_reason_he_says_no") if j1 else "") or "",
            "problems": (j1.problems if j1 else [])[:3],
            "verdict2": j2.verdict if j2 else None, "fit2": j2.fit if j2 else None,
        })

    chosen = threshold if threshold is not None else choose_threshold(out_rows)
    at = chosen or 8
    result = {
        "meta": {"prompt_version": judge.PROMPT_VERSION, "model": model, "effort": effort,
                 "window": "development" if dev else "holdout",
                 "cutoff": (DEV_CUTOFF if dev else CUTOFF).isoformat(),
                 "scored_to": CUTOFF.isoformat() if dev else None,
                 "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "n_examples": len(before), "n_holdout": len(after),
                 "holdout_yes": sum(r.label == "yes" for r in after),
                 "holdout_no": sum(r.label == "no" for r in after),
                 "holdout_with_jd": sum(r.has_jd for r in after),
                 "undated_left_out": len(undated), "skipped": skipped,
                 "usd": round(usd, 4), "batch_id": batch_id, "batch": not dev,
                 "replicas": replicas},
        "baseline": baseline(out_rows),
        "auc": auc(out_rows),
        "chosen_threshold": chosen,
        "threshold_from": "given" if threshold is not None else "this window",
        "metrics": {str(t): metrics(out_rows, t) for t in THRESHOLDS},
        "retest": agreement(out_rows, at),
        "decline_codes": code_agreement(out_rows),
        "slices": {
            "full posting": metrics([r for r in out_rows if r["has_jd"]], at),
            "sheet text only": metrics([r for r in out_rows if not r["has_jd"]], at),
            "company he had ruled on": metrics([r for r in out_rows if r["seen_company"]], at),
            "company new to him": metrics([r for r in out_rows if not r["seen_company"]], at),
        },
        "rows": out_rows,
    }
    if write:
        (DEV_FIXTURE if dev else FIXTURE).write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n")
    return result


def _pending(jid: str, model: str, why: str) -> judge.Judgement:
    return judge.Judgement(job_id=jid, verdict="pending", fit=None, confidence="",
                           answers={}, red_flags=[], likely_decline_code="none",
                           why_it_fits="", snippet="", model=model, served_model="",
                           problems=[why])


def _direct(cfg, system, roles, replicas, client, model, effort):
    """Live calls, the first alone so the rest read its cache."""
    jobs = [(jid, n) for jid in roles for n in range(1, replicas + 1)]
    answers: dict[str, dict[int, judge.Judgement]] = {}

    def one(job):
        jid, n = job
        return job, judge.judge_role(cfg, system, roles[jid], client=client,
                                     model=model, effort=effort)
    usd = 0.0
    if jobs:
        (jid, n), j = one(jobs[0])
        answers.setdefault(jid, {})[n] = j
        usd += j.usd
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for (jid, n), j in pool.map(one, jobs[1:]):
            answers.setdefault(jid, {})[n] = j
            usd += j.usd
    print(f"direct: {len(jobs)} calls, ${usd:.2f}", flush=True)
    return answers, usd, None


def _batch(system, roles, replicas, client, model, effort, poll_seconds, est):
    requests_ = [{"custom_id": f"{i}-{n}", "params": judge.request_params(
                    system, roles[jid], model=model, effort=effort)}
                 for i, jid in enumerate(roles) for n in range(1, replicas + 1)]
    index = {str(i): jid for i, jid in enumerate(roles)}
    batch = client.messages.batches.create(requests=requests_)
    print(f"batch {batch.id}: {len(requests_)} requests, estimated ${est:.2f}")
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        print(f"  {b.processing_status}: {b.request_counts.processing} processing, "
              f"{b.request_counts.succeeded} done", flush=True)
        time.sleep(poll_seconds)

    answers: dict[str, dict[int, judge.Judgement]] = {}
    usd = 0.0
    for res in client.messages.batches.results(batch.id):
        i, n = res.custom_id.rsplit("-", 1)
        jid = index[i]
        if res.result.type != "succeeded":
            j = _pending(jid, model, f"batch result {res.result.type}")
        else:
            _, j = judge.interpret(roles[jid], res.result.message, model=model,
                                   batch=True, evidence=system)
        usd += j.usd
        answers.setdefault(jid, {})[int(n)] = j
    return answers, usd, batch.id
