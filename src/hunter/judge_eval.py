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

Results are written to tests/fixtures/judge_eval.json, with no posting text, and
tests/test_judge_eval.py reads that record offline.
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import time
from collections import Counter

from . import judge, judgedata
from .sources import company_key
from .config import Config

CUTOFF = datetime.datetime(2026, 9, 17, tzinfo=datetime.timezone.utc)
FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "judge_eval.json"
THRESHOLDS = (7, 8, 9)
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
             f"cost: ${m['usd']:.2f} through the Batches API", "",
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
    lines.append(f"agrees with itself (fit 8): {pct(result['retest'])}")
    lines.append(f"predicts his decline reason: {pct(result['decline_codes'])}")
    for name, sl in result["slices"].items():
        x = sl
        lines.append(f"slice {name}: n={x['judged']}, precision {pct(x['precision'])}, "
                     f"Yes kept {pct(x['yes_recall'])}, declines blocked {pct(x['declines_blocked'])}")
    return lines


def run(cfg: Config, sheet, canon, *, replicas: int = 2, poll_seconds: int = 30,
        max_usd: float = MAX_USD, write: bool = True) -> dict:
    rows, skipped = judgedata.collect(cfg, sheet, canon)
    before, after, undated = judgedata.split(rows, CUTOFF)
    after = [judgedata.fill_jd(r) for r in after]
    rulings = [judge.ruling_line(str(r.when.date()), r.label, r.company, r.title, r.words,
                                 r.comp, r.location)
               for r in sorted(before, key=lambda r: (r.when, r.company, r.title))]
    system = judge.gather_context(cfg, sheet, canon, rulings=rulings)
    model = cfg.optional("hunter_judge_model", judge.DEFAULT_MODEL)
    effort = cfg.optional("hunter_judge_effort", judge.DEFAULT_EFFORT)

    roles = {r.job_id: judge.Role(job_id=r.job_id, company=r.company, title=r.title,
                                  location=r.location, comp=r.comp, url=r.url,
                                  posting=judgedata.posting_for(r), source=r.source)
             for r in after}
    # A ceiling before anything is sent: the fixed context at full price once,
    # each call's posting and a generous answer, all at the batch discount.
    # About 2.4 characters per token on this text, measured on the first live call.
    est = (len(system) / 2.4 * judge.PRICES[model]["cache_write"]
           + replicas * len(after) * (len(system) / 2.4 * judge.PRICES[model]["cache_read"] * 5
                                      + 4000 * judge.PRICES[model]["in"]
                                      + 5000 * judge.PRICES[model]["out"])) / 1e6 / 2
    if est > max_usd:
        raise RuntimeError(f"estimated ${est:.2f} is over the ${max_usd:.2f} cap; not sent")

    client = judge._client(cfg)
    requests_ = [{"custom_id": f"{i}-{n}", "params": judge.request_params(
                    system, roles[jid], model=model, effort=effort)}
                 for i, jid in enumerate(roles) for n in range(1, replicas + 1)]
    index = {str(i): jid for i, jid in enumerate(roles)}
    batch = client.messages.batches.create(requests=requests_)
    print(f"batch {batch.id}: {len(requests_)} requests, estimated at most ${est:.2f}")
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
            j = judge.Judgement(job_id=jid, verdict="pending", fit=None, confidence="",
                                answers={}, red_flags=[], likely_decline_code="none",
                                why_it_fits="", snippet="", model=model, served_model="",
                                problems=[f"batch result {res.result.type}"])
        else:
            _, j = judge.interpret(roles[jid], res.result.message, model=model,
                                   batch=True, evidence=system)
        usd += j.usd
        answers.setdefault(jid, {})[int(n)] = j

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
            "reason": (j1.answers.get("most_likely_reason_he_says_no") if j1 else "") or "",
            "problems": (j1.problems if j1 else [])[:3],
            "verdict2": j2.verdict if j2 else None, "fit2": j2.fit if j2 else None,
        })

    result = {
        "meta": {"prompt_version": judge.PROMPT_VERSION, "model": model, "effort": effort,
                 "cutoff": CUTOFF.isoformat(),
                 "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "n_examples": len(before), "n_holdout": len(after),
                 "holdout_yes": sum(r.label == "yes" for r in after),
                 "holdout_no": sum(r.label == "no" for r in after),
                 "holdout_with_jd": sum(r.has_jd for r in after),
                 "undated_left_out": len(undated), "skipped": skipped,
                 "usd": round(usd, 4), "batch_id": batch.id},
        "baseline": baseline(out_rows),
        "metrics": {str(t): metrics(out_rows, t) for t in THRESHOLDS},
        "retest": agreement(out_rows, 8),
        "decline_codes": code_agreement(out_rows),
        "slices": {
            "full posting": metrics([r for r in out_rows if r["has_jd"]], 8),
            "sheet text only": metrics([r for r in out_rows if not r["has_jd"]], 8),
            "company he had ruled on": metrics([r for r in out_rows if r["seen_company"]], 8),
            "company new to him": metrics([r for r in out_rows if not r["seen_company"]], 8),
        },
        "rows": out_rows,
    }
    if write:
        FIXTURE.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return result
