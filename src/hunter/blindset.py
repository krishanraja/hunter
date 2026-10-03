"""The blind set: the one test of what the judge hides.

Every number judge-eval reports is measured on roles the old gates let through,
because those are the only roles he has ever ruled on. Nobody has measured what
the old gates threw away, or what the judge would throw away in their place.
This does, the only way it can be done: he rules on roles he has never seen,
with the judge's call hidden.

- The pool is what the old judgement gates blocked in the last fortnight with
  the full posting stored (G4, G7, G11, G14). The clear-cut gates (location,
  dead, never-apply, a stated ceiling under the floor) stay out: they are
  facts, and the judge never overrides them either.
- A random sample is judged with the live context, and drawn in three strata:
  what the judge would present, what it hides narrowly (fit 4 or 5, or held),
  what it hides plainly (fit 3 or less). Each pick is checked live, so he is
  never asked about a posting that has closed.
- The tab shows the facts only, shuffled: company, title, pay, location, link.
  No score, no reasoning, nothing that would tell him what the judge thought.
- Scoring reads his column A back and weights each stratum by its size in the
  judged sample, so the estimate is of the pool, not of the draw.
- A Yes is a real lead the old rules had hidden: `blind-eval --apply` puts it
  on Pipeline like any staged role, said to be from the blind set.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import pathlib
import random

from . import judge, judgedata
from .config import ALL_ROWS, Config, db_get, db_patch

TAB = "Blind Set"
HEADER = ["Your verdict (Yes or No)", "Company", "Title", "Pay", "Location", "Link", "Ref"]
GATES = ("G4", "G7", "G11", "G14")
STRATA = (("present", 15), ("near", 15), ("far", 10))
RECORD = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "judge_blind.json"
FROM_BLIND = "From the blind set: you said Yes to a role the old rules had blocked"


def stratum(j: judge.Judgement) -> str | None:
    """Where a judged role falls. Pending is no stratum: it was not judged."""
    d = judge.disposition(j)
    if d == "staging":
        return "present"
    if d == "judge_pending":
        return None
    return "near" if (d == "held" or (j.fit or 0) >= 4) else "far"


def pool(cfg: Config, days: int = 14) -> list[dict]:
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).isoformat()
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,comp,location,url,job_url,source,jd_text,"
                  "rejection_reason",
        "status": "eq.blocked", "scanned_at": f"gte.{since}", "limit": ALL_ROWS})
    return [r for r in rows
            if (r.get("rejection_reason") or "").split(":", 1)[0] in GATES
            and len(r.get("jd_text") or "") >= judge.FULL_POSTING_CHARS]


def is_live(row: dict) -> bool:
    from .run import _resolve_for_build, ats_key, fetch_jd_plain
    url = row.get("job_url") or row.get("url") or ""
    try:
        if ats_key(url):
            return bool(_resolve_for_build({"url": url, "title": row["title"],
                                            "company": row["company"]}).live)
        live, _ = fetch_jd_plain(url)
        return live is not False
    except Exception:
        return False


def pick(judged: list[tuple[dict, judge.Judgement]], rng: random.Random,
         live=is_live) -> tuple[list[dict], dict]:
    """Up to the stratum's quota from each stratum, live postings only, in a
    random order. Returns (picks, the size of each stratum in the sample)."""
    by: dict[str, list] = {s: [] for s, _ in STRATA}
    for row, j in judged:
        s = stratum(j)
        if s:
            by[s].append((row, j))
    sizes = {s: len(v) for s, v in by.items()}
    picks = []
    for s, quota in STRATA:
        cands = by[s][:]
        rng.shuffle(cands)
        taken = 0
        for row, j in cands:
            if taken >= quota:
                break
            if not live(row):
                continue
            picks.append({"job_id": row["job_id"], "stratum": s, "verdict": j.verdict,
                          "fit": j.fit, "company": row["company"], "title": row["title"],
                          "comp": row.get("comp") or "", "location": row.get("location") or "",
                          "url": row.get("job_url") or row.get("url") or "",
                          "old_gate": (row.get("rejection_reason") or "").split(":", 1)[0]})
            taken += 1
    rng.shuffle(picks)
    return picks, sizes


def draw(cfg: Config, sheet, canon, *, n_judge: int = 120, max_usd: float = 10.0,
         client=None, seed: int | None = None, write: bool = True) -> dict:
    from .judge_stage import live_context
    seed = seed if seed is not None else int(datetime.date.today().strftime("%Y%m%d"))
    rng = random.Random(seed)
    rows = pool(cfg)
    sample = rng.sample(rows, min(n_judge, len(rows)))
    system = live_context(cfg, sheet, canon)
    client = client or judge._client(cfg)

    def one(row):
        return row, judge.judge_role(cfg, system, judge.Role(
            job_id=row["job_id"], company=row["company"], title=row["title"],
            location=row.get("location") or "", comp=row.get("comp") or "",
            url=row.get("job_url") or row.get("url") or "", posting=row["jd_text"],
            source=row.get("source") or ""), client=client)

    judged, spent = [], 0.0
    if sample:
        judged.append(one(sample[0]))
        spent += judged[0][1].usd
    with cf.ThreadPoolExecutor(max_workers=6) as pool_:
        rest = sample[1:]
        for i in range(0, len(rest), 6):
            if spent >= max_usd:
                break
            for row, j in pool_.map(one, rest[i:i + 6]):
                judged.append((row, j))
                spent += j.usd
    for row, j in judged:
        if j.verdict != "pending":
            db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, j.row_patch())
    picks, sizes = pick(judged, rng)
    record = {"drawn_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "prompt_version": judge.PROMPT_VERSION, "seed": seed, "pool": len(rows),
              "judged": len(judged), "usd": round(spent, 4), "strata": sizes,
              "picks": picks}
    if write:
        write_tab(sheet, picks)
        RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def write_tab(sheet, picks: list[dict]) -> None:
    """A new tab, facts only, and read back."""
    meta = sheet._get("", {"fields": "sheets.properties.title"})
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB not in titles:
        sheet._post(":batchUpdate", {"requests": [{"addSheet": {"properties": {
            "title": TAB, "gridProperties": {"frozenRowCount": 1}}}}]})
    grid = [HEADER] + [["", p["company"], p["title"], p["comp"], p["location"],
                        p["url"], p["job_id"]] for p in picks]
    sheet._write([(f"'{TAB}'!A1:G{len(grid)}", grid)], raw=True)
    back = sheet.read_tab_values(f"'{TAB}'!G2:G{len(grid)}")
    got = [r[0] if r else "" for r in back]
    if got != [p["job_id"] for p in picks]:
        raise RuntimeError(f"the {TAB} tab did not read back as written")


def read(sheet) -> dict[str, str]:
    """Ref to his label (yes or no), for the rows he has ruled on."""
    out = {}
    for r in sheet.read_tab_values(f"'{TAB}'!A2:G80"):
        r = list(r) + [""] * (7 - len(r))
        label, _ = judgedata.label_of(r[0])
        if label and r[6]:
            out[r[6]] = label
    return out


def evaluate(record: dict, labels: dict[str, str]) -> dict:
    """Per stratum, the share he said Yes to; and, weighting each stratum by its
    size in the judged sample, the estimated share of his Yes roles the judge
    would present, and of his declines it would hide."""
    from .judge_eval import wilson
    per = {}
    for s, _ in STRATA:
        ruled = [p for p in record["picks"] if p["stratum"] == s and p["job_id"] in labels]
        yes = sum(labels[p["job_id"]] == "yes" for p in ruled)
        per[s] = {"ruled": len(ruled), "yes": yes, "yes_rate": wilson(yes, len(ruled)),
                  "weight": record["strata"].get(s, 0)}
    est_yes = {s: (v["yes"] / v["ruled"]) * v["weight"] if v["ruled"] else 0.0
               for s, v in per.items()}
    est_no = {s: ((v["ruled"] - v["yes"]) / v["ruled"]) * v["weight"] if v["ruled"] else 0.0
              for s, v in per.items()}
    total_yes, total_no = sum(est_yes.values()), sum(est_no.values())
    return {
        "ruled": sum(v["ruled"] for v in per.values()),
        "strata": per,
        "yes_shown_est": round(est_yes["present"] / total_yes, 4) if total_yes else None,
        "declines_hidden_est": round((est_no["near"] + est_no["far"]) / total_no, 4)
        if total_no else None,
    }


def report(record: dict, result: dict) -> list[str]:
    def pct(x):
        return f"{x[0]*100:.0f}% (95%: {x[1]*100:.0f} to {x[2]*100:.0f})" if x[0] == x[0] else "n/a"
    names = {"present": "the judge would show", "near": "it hides narrowly (fit 4 or 5)",
             "far": "it hides plainly (fit 3 or less)"}
    lines = [f"blind set, prompt {record['prompt_version']}: {result['ruled']} of "
             f"{len(record['picks'])} ruled, drawn from {record['judged']} judged out of "
             f"{record['pool']} the old gates blocked"]
    for s, _ in STRATA:
        v = result["strata"][s]
        lines.append(f"  {names[s]}: he said Yes to {v['yes']} of {v['ruled']}, "
                     f"{pct(v['yes_rate'])}")
    if result["yes_shown_est"] is not None:
        lines.append(f"estimated share of his Yes roles in the pool the judge would show: "
                     f"{result['yes_shown_est']*100:.0f}%")
    if result["declines_hidden_est"] is not None:
        lines.append(f"estimated share of the pool he would decline that it hides: "
                     f"{result['declines_hidden_est']*100:.0f}%")
    return lines


def apply_yes(cfg: Config, sheet, record: dict, labels: dict[str, str]) -> list[str]:
    """Each Yes goes onto Pipeline as a staged role with his Yes already in
    column A, so the ordinary path builds its package. A role already staged
    or on the sheet is left alone."""
    from .sheet import COLS, make_row
    from .run import NOW
    on_sheet = {r["job_id"] for r in db_get(cfg, "hunter_seen_roles", {
        "select": "job_id", "presented_at": "not.is.null", "limit": ALL_ROWS})}
    moved, rows = [], []
    for p in record["picks"]:
        if labels.get(p["job_id"]) != "yes" or p["job_id"] in on_sheet:
            continue
        row = make_row(company=p["company"], role=p["title"], jd_url=p["url"], score=0,
                       why_it_fits=f"{FROM_BLIND} ({p['old_gate']}).",
                       location=p["location"], comp=p["comp"], source="blind set")
        row[COLS["Verdict"]] = "Yes"
        rows.append(row)
        moved.append(p["job_id"])
    if rows:
        sheet.append_rows(rows)
        for jid in moved:
            # His verdict is recorded the one way verdicts are: reconcile reads
            # the Yes in column A. Writing it here too would be a second path.
            db_patch(cfg, "hunter_seen_roles", {"job_id": jid},
                     {"status": "staging", "presented_at": NOW()})
    return moved
