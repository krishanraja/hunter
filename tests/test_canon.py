"""Canon parser tests against a synthetic body exercising both heading styles.
Deliberately NOT a cached copy of the real canon: canon 9.10 exists because
stale copies get mistaken for the real thing. The synthetic body mimics shape,
not content."""
import pytest

import hunter.canon as canon_mod
from hunter import config
from hunter.canon import CanonError, load_canon
from hunter.config import Config


def synthetic_body(cv_id=config.CV_MASTER_ID, letter_id=config.LETTER_MASTER_ID,
                   workbook_id=config.WORKBOOK_ID, drop_section=None):
    parts = {
        "2": "## 2. EMPLOYMENT HISTORY (VERIFIED, EXHAUSTIVE)\nCaptify. Nine.\n",
        "3": "## 3. PROOF POINTS (NUMBERS ONLY, ALL VERIFIED)\n$0 to $12M ARR.\n",
        "4": "## 4. POSITIONING (USE VERBATIM)\nThe hook.\n1. Header row columns 10 to 14 hold data values, a list item that must not parse as a heading.\n",
        "6": "## 6. COMPENSATION\nFloor $200,000 base.\n",
        "9.1": (
            "9.1 SOURCING UNIVERSE. Two parts, one rule.\n"
            "(a) The 52 named target companies below.\n"
            "Content and Data Licensing / Open Web: ProRata.ai, TollBit, Reddit\n"
            "AI-Native Enterprise and Agentic: Glean, Clay, Harvey\n"
            "RETIRED. The old universe is out.\n"
            "ATS SLUG CORRECTIONS. Glean slug is gleanwork.\n"
        ),
        "9.2": "9.2 PRESENTATION BAR. 8 out of 10, lowered from 9 on 2026-08-24.\n",
        "9.3": (
            "9.3 AUTO-REJECTS. Exactly two.\n"
            "HARD REJECT: (1) band bottom below $200,000. (2) pure quota carrying.\n"
        ),
        "9.4": "9.4 VERIFICATION GATES. Ten gates.\n" + "".join(
            f"G{i} {name}. Rule text for gate {i}.\n" for i, name in enumerate(
                ["LIVE", "COMP", "SENIORITY", "YEARS", "MANDATE",
                 "GEOGRAPHY", "DOMAIN", "CANON", "FORM", "POSITIONING"], start=1)),
        "9.9": (
            "## 9.9 CURRENT ARTIFACT REGISTRY. Single pointer list.\n"
            f"| Master CV | {cv_id} (v14) | superseded |\n"
            f"| Cover letter template | {letter_id} (v4) | superseded |\n"
            f"| Pipeline surface, CANONICAL | {workbook_id}, Krish_Job_Search_OS_v2 | none |\n"
        ),
        "9.12": "## 9.12 TEMPLATE CONTRACT, locked 2026-08-29.\nMasters edited in place.\n",
        "9.13": (
            "## 9.13 PIPELINE TAB SPEC, repaired and verified 2026-08-31.\n"
            "Fifteen tabs, in order:\n"
            "00 START HERE, Profile, Pipeline, Application Info Bank, Headhunters.\n"
            "Pipeline tab: sheetId 708873267.\n"
            "Header, exact, in order:\n"
            "A Verdict, B Business, C Role, D Job Link, E CV Doc, F Cover Letter Doc, "
            "G CV PDF, H CL PDF, I Score, J Why It Fits, K Sector, L Stage, M Location, "
            "N Comp, O Package Status, P Source, Q Application Status, R Applied Date, "
            "S Next Action, T Application Format, U Attachment Style, V Additional Questions, "
            "W Form Complexity, X Autonomy Score, Y Form Audit Date, Z JD URL Verified, "
            "AA JD Snippet, AB Materials Built.\n\n"
            "LINK COLUMNS ARE HYPERLINK FORMULAS, NOT URLS.\n"
        ),
    }
    if drop_section:
        parts.pop(drop_section)
    return "\n".join(parts.values())


@pytest.fixture
def cfg():
    return Config(supabase_url="http://offline.invalid", supabase_key="offline", raw={})


def patch_body(monkeypatch, body):
    monkeypatch.setattr(canon_mod, "db_get",
                        lambda cfg, table, params: [{"slug": "krish-canon",
                                                     "body": body, "version": 2}])


def test_parses_both_heading_styles_and_all_guards_pass(cfg, monkeypatch):
    patch_body(monkeypatch, synthetic_body())
    c = load_canon(cfg)
    assert c.bar == 8
    assert sorted(c.gates) == [f"G{i}" for i in range(1, 11)] or len(c.gates) == 10
    assert "TollBit" in c.universe and "Glean" in c.universe
    assert len(c.sheet_headers) == 28
    assert c.sheet_headers[0] == "Verdict"
    assert c.sheet_headers[3] == "Job Link"
    assert c.sheet_headers[27] == "Materials Built"
    # The numbered list item inside section 4 must not have opened a section.
    assert "1" not in c.sections or "Header row" not in c.sections.get("1", ("", ""))[0]


def test_missing_required_section_fails(cfg, monkeypatch):
    patch_body(monkeypatch, synthetic_body(drop_section="9.4"))
    with pytest.raises(CanonError, match="missing required sections"):
        load_canon(cfg)


def test_registry_mismatch_fails_without_choosing(cfg, monkeypatch):
    patch_body(monkeypatch, synthetic_body(cv_id="1WRONGwrongWRONGwrongWRONGwrong"))
    with pytest.raises(CanonError, match="9.9"):
        load_canon(cfg)


def test_missing_canon_row_fails(cfg, monkeypatch):
    monkeypatch.setattr(canon_mod, "db_get", lambda cfg, table, params: [])
    with pytest.raises(CanonError, match="missing"):
        load_canon(cfg)
