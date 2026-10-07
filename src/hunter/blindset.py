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
- The tab is a Pipeline tab: the same columns, widths, colours, dropdown and
  banding, copied from Pipeline itself each time it is written, so a change
  he makes to Pipeline's look carries over. His words, 2026-10-03: "the same
  column format and rich formatting as the Pipeline tab, always". Every row
  carries the JD Snippet, the link, and a Why It Fits that makes the strongest
  honest case from everything he has written (judge.make_the_case). The case
  writer is never told the judge's call, and the Score column stays empty,
  so nothing on the row gives the judgement away.
- Scoring reads his column A back and weights each stratum by its size in the
  judged sample, so the estimate is of the pool, not of the draw.
- A Yes is a real lead the old rules had hidden: `blind-eval --apply` puts it
  on Pipeline like any staged role, with the same row, sourced "blind set".
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import pathlib
import random

from . import judge, judgedata, spend
from .config import ALL_ROWS, Config, db_get, db_patch
from .sheet import HEADERS as PIPELINE_HEADERS

TAB = "Blind Set"
TAB_SRC = "Pipeline"
# The data starts where Pipeline's does (row 1 header, row 2 its banner), and
# the job id sits in one hidden column after Pipeline's last, so his verdict
# pairs back exactly.
FIRST_ROW = 3
REF_COL = len(PIPELINE_HEADERS)
GATES = ("G4", "G7", "G11", "G14")
STRATA = (("present", 15), ("near", 15), ("far", 10))
RECORD = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "judge_blind.json"
SOURCE = "blind set"


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
                          "source": row.get("source") or "",
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
        if j.usage:
            spend.record(cfg, "blind_set", j.model, j.usage, job_id=row["job_id"],
                         served_model=j.served_model)
        if j.verdict != "pending":
            db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]}, j.row_patch())
    picks, sizes = pick(judged, rng)
    postings = {row["job_id"]: row["jd_text"] for row, _ in judged}
    spent += add_cases(cfg, system, picks, postings, client=client)
    record = {"drawn_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "prompt_version": judge.PROMPT_VERSION, "seed": seed, "pool": len(rows),
              "judged": len(judged), "usd": round(spent, 4), "strata": sizes,
              "picks": picks}
    if write:
        RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        write_tab(sheet, picks)
    return record


def add_cases(cfg: Config, system: str, picks: list[dict], postings: dict[str, str], *,
              client=None) -> float:
    """Why It Fits and the JD Snippet for each pick that lacks them."""
    todo = [p for p in picks if not p.get("why")]

    def one(p):
        role = judge.Role(job_id=p["job_id"], company=p["company"], title=p["title"],
                          location=p.get("location", ""), comp=p.get("comp", ""),
                          url=p.get("url", ""), posting=postings.get(p["job_id"], ""),
                          source=p.get("source", ""))
        return p, judge.make_the_case(cfg, system, role, client=client)
    usd = 0.0
    with cf.ThreadPoolExecutor(max_workers=6) as pool_:
        for p, (why, snippet, problems, cost) in pool_.map(one, todo):
            p["why"], p["snippet"] = why, snippet
            if problems:
                p["case_problems"] = problems[:3]
            usd += cost
    return usd


def row_for(p: dict) -> list[str]:
    """Exactly the row Pipeline would carry, except Score: the old score points
    the wrong way and the judge's fit would give its call away."""
    from .sheet import COLS, make_row
    row = make_row(company=p["company"], role=p["title"], jd_url=p["url"], score=0,
                   why_it_fits=p.get("why", ""), location=p.get("location", ""),
                   comp=p.get("comp", ""), source=p.get("source", ""),
                   jd_snippet=p.get("snippet", ""), jd_verified=True)
    row[COLS["Score"]] = ""
    return row


def layout_requests(src: dict, dst_id: int, n_rows: int) -> list[dict]:
    """batchUpdate requests that give the tab Pipeline's look, read from
    Pipeline's own metadata: frozen panes, widths, hidden columns, row heights,
    header and banner, data-row formats with the column A dropdown, the
    conditional colours and the banding. Idempotent on a tab this module
    owns: its old rules and banding are removed first."""
    from .sheet import HEADERS
    n_cols = len(HEADERS)
    sp = src["properties"]
    src_id = sp["sheetId"]
    grid = sp.get("gridProperties", {})
    last = FIRST_ROW - 1 + n_rows                      # 0-based end row
    reqs: list[dict] = [{"updateSheetProperties": {
        "properties": {"sheetId": dst_id, "gridProperties": {
            "rowCount": last + 5, "columnCount": n_cols + 1,
            "frozenRowCount": grid.get("frozenRowCount", 2),
            "frozenColumnCount": grid.get("frozenColumnCount", 0)}},
        "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)"}}]

    def rng(sheet_id, r0, r1, c0=0, c1=n_cols):
        return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1}
    reqs.append({"copyPaste": {"source": rng(src_id, 0, FIRST_ROW - 1),
                               "destination": rng(dst_id, 0, FIRST_ROW - 1),
                               "pasteType": "PASTE_NORMAL"}})
    if n_rows:
        reqs.append({"copyPaste": {"source": rng(src_id, FIRST_ROW - 1, FIRST_ROW),
                                   "destination": rng(dst_id, FIRST_ROW - 1, last),
                                   "pasteType": "PASTE_FORMAT"}})
    data = (src.get("data") or [{}])[0]
    for i, c in enumerate(data.get("columnMetadata", [])[:n_cols]):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": dst_id, "dimension": "COLUMNS", "startIndex": i,
                      "endIndex": i + 1},
            "properties": {"pixelSize": c.get("pixelSize", 100),
                           "hiddenByUser": bool(c.get("hiddenByUser"))},
            "fields": "pixelSize,hiddenByUser"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": dst_id, "dimension": "COLUMNS", "startIndex": n_cols,
                  "endIndex": n_cols + 1},
        "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
    heights = [r.get("pixelSize") for r in data.get("rowMetadata", [])]
    for i, h in enumerate(heights[:FIRST_ROW]):
        if h:
            end = last if i == FIRST_ROW - 1 else i + 1
            reqs.append({"updateDimensionProperties": {
                "range": {"sheetId": dst_id, "dimension": "ROWS", "startIndex": i,
                          "endIndex": max(end, i + 1)},
                "properties": {"pixelSize": h}, "fields": "pixelSize"}})
    return reqs


def finishing_requests(src: dict, after: dict, dst_id: int, n_rows: int) -> list[dict]:
    """What the format paste did not bring, judged from a read of the tab
    after it. Measured on 2026-10-03: the paste brings Pipeline's colour rules
    and its banding (adding either again duplicates the rules or is refused),
    and does NOT bring the column A dropdown, whatever the API reference says.
    So each is added only when the read shows it missing, and the dropdown is
    set from Pipeline's own rule every time."""
    last = FIRST_ROW - 1 + n_rows
    out: list[dict] = []
    if not after.get("conditionalFormats"):
        for i, rule in enumerate(src.get("conditionalFormats", [])):
            new = {k: v for k, v in rule.items() if k != "ranges"}
            new["ranges"] = [dict(r, sheetId=dst_id, endRowIndex=last) for r in rule["ranges"]]
            out.append({"addConditionalFormatRule": {"rule": new, "index": i}})
    if not after.get("bandedRanges"):
        for band in src.get("bandedRanges", []):
            body = {k: v for k, v in band.items() if k not in ("bandedRangeId", "range")}
            body["range"] = dict(band["range"], sheetId=dst_id, endRowIndex=last)
            out.append({"addBanding": {"bandedRange": body}})
    rule = pipeline_dropdown(src)
    if rule and n_rows:
        out.append({"setDataValidation": {
            "range": {"sheetId": dst_id, "startRowIndex": FIRST_ROW - 1, "endRowIndex": last,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "rule": rule}})
    return out


def pipeline_dropdown(src: dict) -> dict | None:
    """Pipeline's column A rule, read from its first data row."""
    try:
        rows = src["data"][0]["rowData"]
        return rows[FIRST_ROW - 1]["values"][0].get("dataValidation")
    except (KeyError, IndexError, TypeError):
        return None


def write_tab(sheet, picks: list[dict]) -> None:
    """Write the tab as a Pipeline tab, then read it back: the job ids in the
    hidden column, and the verdict dropdown on every row."""
    from .sheet import HEADERS, col_letter
    meta = sheet._get("", {"ranges": f"{TAB_SRC}!A1:{col_letter(len(HEADERS) - 1)}{FIRST_ROW}",
                           "fields": "sheets(properties(sheetId,title,gridProperties),"
                                     "conditionalFormats,bandedRanges,"
                                     "data(columnMetadata(pixelSize,hiddenByUser),"
                                     "rowMetadata(pixelSize),rowData(values(dataValidation))))"})
    # A request with ranges answers for the sheets those ranges touch and no
    # others, so Pipeline's look and this tab's state are two lookups. The
    # first version asked once, never saw this tab, and tried to add it again.
    src = {s["properties"]["title"]: s for s in meta.get("sheets", [])}[TAB_SRC]

    def tabs():
        m = sheet._get("", {"fields": "sheets(properties(sheetId,title),"
                                      "conditionalFormats,bandedRanges)"})
        return {s["properties"]["title"]: s for s in m.get("sheets", [])}
    sheets = tabs()
    if TAB not in sheets:
        sheet._post(":batchUpdate", {"requests": [{"addSheet": {"properties": {"title": TAB}}}]})
        sheets = tabs()
    dst = sheets[TAB]
    dst_id = dst["properties"]["sheetId"]
    clear: list[dict] = []
    for i in range(len(dst.get("conditionalFormats", [])) - 1, -1, -1):
        clear.append({"deleteConditionalFormatRule": {"sheetId": dst_id, "index": i}})
    for band in dst.get("bandedRanges", []):
        clear.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})
    sheet._post("/values:batchClear", {"ranges": [f"'{TAB}'!A1:ZZ"]})
    if clear:
        sheet._post(":batchUpdate", {"requests": clear})
    sheet._post(":batchUpdate", {"requests": layout_requests(src, dst_id, len(picks))})
    finish = finishing_requests(src, tabs()[TAB], dst_id, len(picks))
    if finish:
        sheet._post(":batchUpdate", {"requests": finish})
    after = tabs()[TAB]
    if picks and (len(after.get("conditionalFormats", [])) != len(src.get("conditionalFormats", []))
                  or bool(after.get("bandedRanges")) != bool(src.get("bandedRanges"))):
        raise RuntimeError(f"the {TAB} tab's colours do not read back as Pipeline's")
    ref = col_letter(REF_COL)
    last = FIRST_ROW - 1 + len(picks)
    if picks:
        sheet._write([(f"'{TAB}'!A{FIRST_ROW}:{col_letter(REF_COL - 1)}{last}",
                       [row_for(p) for p in picks])])
        sheet._write([(f"'{TAB}'!{ref}{FIRST_ROW}:{ref}{last}",
                       [[p["job_id"]] for p in picks])], raw=True)
    back = sheet.read_tab_values(f"'{TAB}'!{ref}{FIRST_ROW}:{ref}{max(last, FIRST_ROW)}")
    got = [r[0] if r else "" for r in back]
    if got != [p["job_id"] for p in picks]:
        raise RuntimeError(f"the {TAB} tab did not read back as written")
    head = sheet.read_tab_values(f"'{TAB}'!A1:{col_letter(len(HEADERS) - 1)}1")
    if not head or [str(h).strip() for h in head[0]] != HEADERS:
        raise RuntimeError(f"the {TAB} tab's header is not Pipeline's")
    if picks and pipeline_dropdown(src):
        m = sheet._get("", {"ranges": f"'{TAB}'!A{last}",
                            "fields": "sheets(data(rowData(values(dataValidation))))"})
        try:
            got_rule = m["sheets"][0]["data"][0]["rowData"][0]["values"][0].get("dataValidation")
        except (KeyError, IndexError):
            got_rule = None
        if got_rule != pipeline_dropdown(src):
            raise RuntimeError(f"the {TAB} tab's verdict dropdown is not Pipeline's")


def read(sheet) -> dict[str, str]:
    """Ref to his label (yes or no), for the rows he has ruled on. Column A
    carries Pipeline's dropdown, so a decline code counts as a No."""
    from .sheet import col_letter
    out = {}
    for r in sheet.read_tab_values(f"'{TAB}'!A{FIRST_ROW}:{col_letter(REF_COL)}200"):
        r = list(r) + [""] * (REF_COL + 1 - len(r))
        label, _ = judgedata.label_of(r[0])
        if label and r[REF_COL]:
            out[r[REF_COL]] = label
    return out


def refresh(cfg: Config, sheet, canon, *, client=None) -> dict:
    """Rewrite the drawn set as a Pipeline tab, writing the case for every pick
    that lacks one. Same roles, same order; nothing is judged again. A verdict
    he has already given is carried over."""
    from .judge_stage import live_context
    record = json.loads(RECORD.read_text())
    given = {}
    try:
        given = _raw_verdicts(sheet)
    except Exception:
        given = {}
    ids = [p["job_id"] for p in record["picks"]]
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,jd_text,source", "job_id": f"in.({','.join(ids)})",
        "limit": ALL_ROWS})
    postings = {r["job_id"]: r.get("jd_text") or "" for r in rows}
    for r in rows:
        for p in record["picks"]:
            if p["job_id"] == r["job_id"] and not p.get("source"):
                p["source"] = r.get("source") or ""
    system = live_context(cfg, sheet, canon)
    record["usd"] = round(record.get("usd", 0.0) + add_cases(
        cfg, system, record["picks"], postings, client=client), 4)
    # Saved before the tab is touched: the cases are paid for, and a tab that
    # fails to write must not throw them away.
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    write_tab(sheet, record["picks"])
    if given:
        cells = [(f"'{TAB}'!A{FIRST_ROW + i}", [[given[p["job_id"]]]])
                 for i, p in enumerate(record["picks"]) if p["job_id"] in given]
        sheet._write(cells, raw=True)
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def _raw_verdicts(sheet) -> dict[str, str]:
    """Whatever is in column A against each Ref, in either layout this tab has
    had (the first, facts-only version kept the Ref in column G)."""
    from .sheet import col_letter
    out = {}
    for r in sheet.read_tab_values(f"'{TAB}'!A2:{col_letter(REF_COL)}200"):
        r = [str(x) for x in r]
        verdict = r[0].strip() if r else ""
        ref = (r[REF_COL] if len(r) > REF_COL else "") or (r[6] if len(r) > 6 else "")
        if verdict and ref and not verdict.lower().startswith("your verdict"):
            out[ref] = verdict
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
    """Each Yes goes onto Pipeline as the same row he said Yes to, sourced
    "blind set", so the ordinary path builds its package. Pipeline takes a new
    row only as "New" with a whole-number score (sheet.validate_row), so the
    row is appended that way, with the score hunter holds for it, and then his
    Yes is written into column A and read back. A role already on the sheet is
    left alone."""
    import re
    from .sheet import COLS
    from .run import NOW
    on_sheet = {r["job_id"] for r in db_get(cfg, "hunter_seen_roles", {
        "select": "job_id", "presented_at": "not.is.null", "limit": ALL_ROWS})}
    todo = [p for p in record["picks"]
            if labels.get(p["job_id"]) == "yes" and p["job_id"] not in on_sheet]
    if not todo:
        return []
    held = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,score", "job_id": f"in.({','.join(p['job_id'] for p in todo)})",
        "limit": ALL_ROWS})
    scores = {r["job_id"]: int(r.get("score") or 0) for r in held}
    rows = []
    for p in todo:
        row = row_for(dict(p, source=SOURCE))
        row[COLS["Score"]] = str(scores.get(p["job_id"], 0))
        rows.append(row)
    rng = sheet.append_rows(rows)
    first = int(re.search(r"!A(\d+):", rng).group(1))
    # His own word, typed on the Blind Set: reconcile reading it back as his
    # verdict is exactly right here.
    sheet.set_verdicts({first + i: "Yes" for i in range(len(todo))})
    for p in todo:
        db_patch(cfg, "hunter_seen_roles", {"job_id": p["job_id"]},
                 {"status": "staging", "presented_at": NOW()})
    return [p["job_id"] for p in todo]
