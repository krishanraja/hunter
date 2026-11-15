"""Camoufox, measured 2026-11-15 and not used.

The question: would a rendering browser that hides it is automated read
companies Hunter cannot? companyintel.site_facts, unchanged, was fed the page
HTML from three readers: plain HTTP (what runs today), Hunter's own Playwright
Chromium, and jo-inc's camofox-browser server. 43 of his named companies, two
passes each, robots.txt read first, one adapter at a time.

The bar was set before the run: on the companies Hunter has no homepage
description for, excluding any that refused an automated reader, Camoufox had
to describe at least 3 more AND 10 points more than Chromium, or match it at 30
percent less time and memory. It described none more, and cost more. Its one
edge, in the first trial, was getting past Cloudflare challenges that had
refused the other two, which section 9 of CLAUDE.md rules out.

The harness is docs/evals/browser_compare.py. Re-run it before adding any
browser to the reading path; a change that cannot beat this record is not one.
"""
import json
import pathlib

REC = json.loads((pathlib.Path(__file__).parent / "fixtures" / "browser_eval.json").read_text())
ROWS = REC["rows"]
SUMMARY = REC["summary"]


def _described(row, adapter):
    return bool(row[adapter]) and all(o == "described" for o in row[adapter])


def _refused(row):
    return any(o == "access restricted" for o in row["plain"] + row["chromium"])


def test_the_record_is_whole():
    assert REC["meta"]["passes"] == 2
    assert len(ROWS) == 43
    assert {r["group"] for r in ROWS} == {"gap", "control"}
    for r in ROWS:
        for a in ("plain", "chromium", "camoufox"):
            assert len(r[a]) == 2, (r["company"], a)


def test_camoufox_reads_nothing_chromium_cannot_where_reading_is_allowed():
    """On every company that did not refuse an automated reader, Camoufox
    described exactly what Chromium did, and Chromium exactly what plain HTTP
    did. A rendering browser closes no gap here at all."""
    allowed = [r for r in ROWS if not _refused(r)]
    assert len(allowed) == 38
    cam = {r["company"] for r in allowed if _described(r, "camoufox")}
    chromium = {r["company"] for r in allowed if _described(r, "chromium")}
    plain = {r["company"] for r in allowed if _described(r, "plain")}
    assert not cam - chromium
    assert chromium == plain
    margin = len(cam - chromium) - len(chromium - cam)
    assert margin < 3, "Camoufox now clears the bar: re-read the record before adopting it"


def test_its_one_edge_was_getting_past_a_refusal():
    """In the first trial Camoufox described two companies whose sites had
    answered plain HTTP and Chromium with a Cloudflare challenge (403,
    cf-mitigated). That is the fingerprint spoofing working, and it is exactly
    what Hunter must not do. In the full run the same sites refused it too."""
    smoke = REC["smoke"]
    passed = {c for c, o in smoke["camoufox"].items() if o == "described"}
    refused = {c for c, o in smoke["chromium"].items() if o == "access restricted"}
    assert passed & refused, "the bypass is the finding; keep it in the record"
    for r in ROWS:
        if r["company"] in passed & refused:
            assert "described" not in r["camoufox"]


def test_it_costs_more_and_fails_more():
    cam, chromium = SUMMARY["camoufox"], SUMMARY["chromium"]
    assert cam["peak_rss_mb"] > chromium["peak_rss_mb"]
    assert cam["cpu_s"] > chromium["cpu_s"]
    assert cam["errors"] > chromium["errors"] == 0
    assert cam["median_ms"] > 5 * SUMMARY["plain"]["median_ms"]


def test_rendered_html_degrades_the_extraction_rather_than_helping_it():
    """Read off the rendered page, 10 descriptions came back shorter than the
    plain read of the same site: a browser re-serialises &#39; as a literal
    apostrophe, and META stops at the first quote of either kind. Chromium's
    read of one more site put a random token where the description was. A
    browser is not a drop-in reader for site_facts."""
    for a in ("chromium", "camoufox"):
        d = SUMMARY[a]["desc_vs_plain"]
        assert d["shorter"] >= 10 and d["same"] < 73


def test_the_verdict_on_record_is_not_adopted():
    v = REC["verdict"]
    assert v["passes"] is False
    assert v["camoufox_only_where_nothing_was_refused"] == []
