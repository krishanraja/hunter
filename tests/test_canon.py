"""Canon parser tests against a synthetic body exercising both heading styles.
Deliberately NOT a cached copy of the real canon: canon 9.10 exists because
stale copies get mistaken for the real thing. The synthetic body mimics shape,
not content."""
import pytest

import hunter.canon as canon_mod
from hunter import config
from hunter.canon import CanonError, load_canon
from hunter.config import Config


# Canon v9, 2026-10-13: his two columns at K and M, on one line, with a blank
# line after it. The parser stops at the first blank line, so the sentence
# about his columns has to be its own paragraph or it is read as part of the
# last header name.
HEADER_SENTENCE = ("A Verdict, B Business, C Role, D JD Snippet, E Job Link, F CV Doc, "
                   "G Cover Letter Doc, H CV PDF, I CL PDF, J AI Score, K Krish Score, "
                   "L Why AI thinks it Fits, M Krish Reaction, N Sector, O Stage, "
                   "P Location, Q Comp, R Warm Path, S Path Evidence, T Package Status, "
                   "U Source, V Application Status, W Applied Date, X Next Action, "
                   "Y Application Format, Z Attachment Style, AA Additional Questions, "
                   "AB Form Complexity, AC Autonomy Score, AD Form Audit Date, "
                   "AE JD URL Verified, AF Materials Built")
HIS_PARAGRAPH = ("K Krish Score and M Krish Reaction are Krish's. Hunter never writes "
                 "them and gives them no default.")
# Canon v8, the 30 columns before he inserted his two.
V8_SENTENCE = ("A Verdict, B Business, C Role, D JD Snippet, E Job Link, F CV Doc, "
               "G Cover Letter Doc, H CV PDF, I CL PDF, J Score, K Why It Fits, "
               "L Sector, M Stage, N Location, O Comp, P Warm Path, Q Path Evidence, "
               "R Package Status, S Source, T Application Status, U Applied Date, "
               "V Next Action, W Application Format, X Attachment Style, "
               "Y Additional Questions, Z Form Complexity, AA Autonomy Score, "
               "AB Form Audit Date, AC JD URL Verified, AD Materials Built")


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
            + HEADER_SENTENCE + ".\n\n"
            + HIS_PARAGRAPH + "\n\n"
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
    from hunter.sheet import HEADERS
    assert len(c.sheet_headers) == 32
    assert c.sheet_headers == HEADERS
    assert c.sheet_headers[0] == "Verdict"
    assert c.sheet_headers[3] == "JD Snippet"
    assert c.sheet_headers[9:13] == ["AI Score", "Krish Score",
                                     "Why AI thinks it Fits", "Krish Reaction"]
    assert c.sheet_headers[17] == "Warm Path"
    assert c.sheet_headers[31] == "Materials Built"
    # The numbered list item inside section 4 must not have opened a section.
    assert "1" not in c.sections or "Header row" not in c.sections.get("1", ("", ""))[0]


def test_the_v8_30_column_body_is_refused_in_plain_words(cfg, monkeypatch):
    """After he inserted Krish Score and Krish Reaction on 2026-10-11, a canon
    body still listing 30 names means canon and the sheet disagree, and hunter
    must not run on either. The message used to say "run migrate-columns", a
    command that no longer existed."""
    patch_body(monkeypatch, synthetic_body().replace(HEADER_SENTENCE, V8_SENTENCE))
    with pytest.raises(CanonError, match="lists 30 headers, expected 32") as e:
        load_canon(cfg)
    assert "migrate-columns" not in str(e.value)
    assert "same columns in the same order" in str(e.value)


def test_his_paragraph_without_a_blank_line_is_swallowed_by_the_list(cfg, monkeypatch):
    """Why canon v9 puts a blank line between the header and the sentence
    about his columns: without it the sentence is read as the end of the last
    header name, and the list still counts 32."""
    body = synthetic_body().replace(HEADER_SENTENCE + ".\n\n" + HIS_PARAGRAPH,
                                    HEADER_SENTENCE + ".\n" + HIS_PARAGRAPH)
    patch_body(monkeypatch, body)
    c = load_canon(cfg)
    assert len(c.sheet_headers) == 32 and c.sheet_headers[-1] != "Materials Built"


def test_a_mislettered_header_list_is_refused(cfg, monkeypatch):
    patch_body(monkeypatch, synthetic_body().replace("D JD Snippet", "E JD Snippet"))
    with pytest.raises(CanonError, match="letters and the order"):
        load_canon(cfg)


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


def test_g11_in_canon_loads_and_g10_missing_still_fails(cfg, monkeypatch):
    """Canon 9.4 gained G11 on 2026-09-03. A body from either side of that
    edit loads; a body missing one of the original ten does not."""
    body = synthetic_body()
    with_g11 = body.replace("G10 POSITIONING.",
                            "G11 ARCHETYPE. Title matches a section 5 family.\nG10 POSITIONING.")
    patch_body(monkeypatch, with_g11)
    assert "G11" in load_canon(cfg).gates
    with_g12 = with_g11.replace("G10 POSITIONING.",
                                "G12 COMPANY. A company Krish declined is never staged again.\nG10 POSITIONING.")
    patch_body(monkeypatch, with_g12)
    assert "G12" in load_canon(cfg).gates
    patch_body(monkeypatch, body.replace("G10 POSITIONING.", "G13 POSITIONING."))
    with pytest.raises(CanonError):
        load_canon(cfg)
