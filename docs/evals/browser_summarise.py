"""Turn the three result files of docs/evals/browser_compare.py into the
fixture (tests/fixtures/browser_eval.json) and the verdict against the pass
bar declared before the run."""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import sys

S = os.path.abspath(os.environ.get("BROWSER_EVAL_DIR", "browser-eval"))
R = os.path.join(S, "results")
ADAPTERS = ("plain", "chromium", "camoufox")

data = {a: json.load(open(os.path.join(R, f"{a}.json"))) for a in ADAPTERS}
by = {a: {} for a in ADAPTERS}
for a in ADAPTERS:
    for r in data[a]["results"]:
        by[a].setdefault(r["company"], []).append(r)

rows = []
for company, base in by["plain"].items():
    row = {"company": company, "group": base[0]["group"], "url": base[0]["url"]}
    for a in ADAPTERS:
        rs = sorted(by[a].get(company, []), key=lambda x: x["pass_"])
        row[a] = [x["outcome"] for x in rs]
        reasons = sorted({w for x in rs for w in (x.get("restricted") or [])})
        row[f"{a}_restricted"] = reasons
        ms = [x["ms"] for x in rs if x["ms"]]
        row[f"{a}_ms"] = int(statistics.median(ms)) if ms else None
    rows.append(row)


def described(row, a):  # described on every pass: reliable
    return bool(row[a]) and all(o == "described" for o in row[a])


def ever(row, a):
    return any(o == "described" for o in row[a])


def refused_automation(row):
    """Plain HTTP or Hunter's Chromium was refused or challenged."""
    return any(o == "access restricted" for o in row["plain"] + row["chromium"])


gap = [r for r in rows if r["group"] == "gap"]
ctl = [r for r in rows if r["group"] == "control"]
allowed_gap = [r for r in gap if not refused_automation(r) and "robots disallow" not in r["plain"]]

summary = {}
for a in ADAPTERS:
    s = data[a]["summary"]
    ms = [x["ms"] for x in data[a]["results"] if x["ms"]]
    summary[a] = dict(
        gap_described=sum(described(r, a) for r in gap),
        gap_described_unrestricted=sum(described(r, a) for r in allowed_gap),
        control_described=sum(described(r, a) for r in ctl),
        flaky=sum(ever(r, a) and not described(r, a) for r in rows),
        restricted=sum(any(o == "access restricted" for o in r[a]) for r in rows),
        errors=sum(any(o == "error" for o in r[a]) for r in rows),
        median_ms=int(statistics.median(ms)) if ms else None,
        p90_ms=int(sorted(ms)[int(0.9 * (len(ms) - 1))]) if ms else None,
        wall_s=s["wall_s"], cpu_s=s["cpu_s"], peak_rss_mb=s["peak_rss_mb"],
        mean_rss_mb=s["mean_rss_mb"], cold_start_ms=s["cold_start_ms"],
        page_loads=s["loads"])

cam_only = [r for r in rows if described(r, "camoufox") and not described(r, "chromium")]
chr_only = [r for r in rows if described(r, "chromium") and not described(r, "camoufox")]
bypass = [r["company"] for r in cam_only if refused_automation(r)]
genuine = [r["company"] for r in cam_only if not refused_automation(r) and r["group"] == "gap"]
chr_genuine = [r["company"] for r in chr_only if not refused_automation(r) and r["group"] == "gap"]
margin = len(genuine) - len(chr_genuine)
points = 100.0 * margin / max(1, len(allowed_gap))
c, k = summary["camoufox"], summary["chromium"]
faster = c["median_ms"] <= 0.7 * k["median_ms"] and c["peak_rss_mb"] <= 0.7 * k["peak_rss_mb"]
equal = c["gap_described_unrestricted"] >= k["gap_described_unrestricted"]
passes = (margin >= 3 and points >= 10) or (equal and faster)

verdict = dict(
    bar="adopt only if, on the gap set excluding targets that refused automated "
        "access, Camoufox describes at least 3 more targets AND 10 points more "
        "than Chromium, or equal success at 30 percent less median time and "
        "30 percent less peak memory",
    allowed_gap_targets=len(allowed_gap),
    camoufox_only_where_automation_was_refused=bypass,
    camoufox_only_where_nothing_was_refused=genuine,
    chromium_only_where_nothing_was_refused=chr_genuine,
    margin_targets=margin, margin_points=round(points, 1),
    passes=passes)

# The extraction itself, compared: a description read off rendered HTML against
# the one read off the plain HTML of the same site on the same pass.
m = {a: {(x["company"], x["pass_"]): x for x in data[a]["results"]} for a in ADAPTERS}
for a in ("chromium", "camoufox"):
    same = shorter = other = 0
    for k, p in m["plain"].items():
        b = m[a].get(k)
        if b and p["outcome"] == b["outcome"] == "described":
            if p["desc"] == b["desc"]:
                same += 1
            elif len(b["desc"]) < len(p["desc"]):
                shorter += 1
            else:
                other += 1
    summary[a]["desc_vs_plain"] = dict(same=same, shorter=shorter, different=other)

# The first trial, three targets, one pass each, minutes before the full run.
smoke = {}
for a in ADAPTERS:
    p = os.path.join(R, "smoke", f"{a}.json")
    if os.path.exists(p):
        smoke[a] = {x["company"]: x["outcome"] for x in json.load(open(p))["results"]}

out = dict(
    meta=dict(
        run_at=dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(R, "camoufox.json")),
                                  dt.timezone.utc).isoformat(timespec="seconds"),
        extraction="hunter.companyintel.site_facts, unchanged; each adapter supplies "
                   "only the page HTML through companyintel._fetch",
        plain="requests, companyintel._fetch (polite UA, then the browser UA string)",
        chromium="playwright 1.63.0, chromium build 1243, launched headless as "
                 "apply/submit.py launches it; domcontentloaded then up to 5s network idle",
        camoufox="@askjo/camofox-browser 1.19.0 (camoufox-js 0.11.5, engine "
                 "152.0.4-beta.30), local REST, telemetry off, 127.0.0.1, access key; "
                 "domcontentloaded then up to 5s network idle",
        passes=2, pause_s=1.0, robots="robots.txt read first; a disallowed target is skipped",
        targets="his named companies (ai_universe.csv, a16z_consumer_top50.csv): gap is "
                "a company whose description Hunter did not take from its own homepage, "
                "control is one it did",
        machine="4 vCPU, 16 GB, sandbox egress through an inspecting proxy"),
    summary=summary, verdict=verdict, smoke=smoke, rows=rows)

dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, "browser_eval.json")
json.dump(out, open(dest, "w"), indent=1)
print(json.dumps(dict(summary=summary, verdict=verdict), indent=1))
for r in rows:
    print(f"{r['group']:7s} {r['company']:22s} plain={'/'.join(o[:4] for o in r['plain']):10s} "
          f"chromium={'/'.join(o[:4] for o in r['chromium']):10s} "
          f"camoufox={'/'.join(o[:4] for o in r['camoufox']):10s} "
          f"{','.join(r['plain_restricted'] + r['chromium_restricted'])}")
