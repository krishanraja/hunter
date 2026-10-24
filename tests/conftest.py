import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))  # for fake_docs

from hunter.package.tailor import BLOCK_KEYS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "house_style_paused: run against the real house-style pause "
                   "(package/style.py) instead of the composer being live")
    config.addinivalue_line(
        "markers", "house_builder: build_one runs the house builder (letters.py), "
                   "not the tailor builder the older tests describe")


@pytest.fixture(autouse=True)
def _house_style_live(request, monkeypatch):
    """Every test written before 2026-10-15 exercises building and sending as
    they work once a composer writes his house style: builds run, and the
    package it built carries the stamp. So they run with the composer live
    and every record stamped. The pause itself, which is what production
    runs until the composer lands, is tested by the tests marked
    house_style_paused (tests/test_house_style.py), which get the real
    package/style.py."""
    if request.node.get_closest_marker("house_style_paused"):
        return
    from hunter.package import style
    monkeypatch.setattr(style, "COMPOSER_LIVE", True)
    monkeypatch.setattr(style, "stamped", lambda row: True)
    # The builder those tests describe; the house builder's own tests set
    # "house" (tests/test_letters.py).
    if not request.node.get_closest_marker("house_builder"):
        monkeypatch.setattr(style, "BUILDER", "tailor")


@pytest.fixture(scope="session")
def letter_fixture():
    path = FIXTURES / "letter_master.json"
    if not path.exists():
        pytest.fail(
            "tests/fixtures/letter_master.json is missing. Capture it with one "
            "authorized documents.get of the live letter master (see the plan, P1)."
        )
    return json.loads(path.read_text())


def _para(segments, *, bullet=False, style="NORMAL_TEXT", start):
    """Build one Docs paragraph element from (text, textStyle) segments."""
    elements = []
    pos = start
    for text, ts in segments:
        elements.append({
            "startIndex": pos, "endIndex": pos + len(text),
            "textRun": {"content": text, "textStyle": ts},
        })
        pos += len(text)
    para = {"elements": elements, "paragraphStyle": {"namedStyleType": style}}
    if bullet:
        para["bullet"] = {"listId": "kix.synthetic"}
    return {"startIndex": start, "endIndex": pos, "paragraph": para}, pos


TEST_LETTER_BLOCKS = {
    key: {
        "text": (
            f"[[COMPANY]] is [[JD_MIRROR]] and the {key.replace('_', ' ')} mandate "
            "is one I have run before: Captify APAC went from $0 to $12M ARR."
        ),
        "default_mirror": "building its commercial engine",
        "approved_at": "2026-08-31",
    }
    # Driven off BLOCK_KEYS, not a second copy of the list. When ai_native_gtm was
    # added as the sixth family the hardcoded five here failed load_blocks with
    # "missing blocks", which is the right error in the wrong place.
    for key in BLOCK_KEYS
}
TEST_CV_BLOCKS = {
    key: {
        "text": (
            f"Synthetic {key.replace('_', ' ')} summary paragraph used by the "
            "offline suite, sixteen years across four commercial builds."
        ),
        "approved_at": "2026-08-31",
    }
    for key in TEST_LETTER_BLOCKS
}

COMPETENCIES_11 = [
    "Commercial Architecture", "Zero-to-One GTM and Operating Model",
    "P&L Ownership", "Strategic Partnerships and Corporate Development",
    "Pipeline Generation and Enterprise Closing",
    "Sales Playbook and Enablement Design", "AI-Native Commercial Operations",
    "Generative AI and Agentic Systems",
    "Cross-Functional Execution (Product, Engineering, Marketing)",
    "Country and Regional Market Entry", "Team Building",
]


def make_synthetic_cv():
    """A CV-shaped fixture: headline, five headings, seven bold-bearing
    highlight bullets, an unstyled 11-item competencies paragraph, and a few
    experience and education bullets. Used where the live CV master is not
    needed; the live P1 gate exercises the real document."""
    bold = {"bold": True}
    plain = {}
    rows = [
        ([("KRISH RAJA\n", bold)], {}),
        ([("AI-Native Commercial Strategy Leader\n", bold)], {}),
        ([("PROFESSIONAL SUMMARY\n", bold)], {}),
        ([("Sixteen years commercializing data and tech.\n", plain)], {}),
        ([("CAREER HIGHLIGHTS\n", bold)], {}),
    ]
    highlights = [
        ("Built APAC from ", "$0 to $12M ARR", " at ", "22% EBITDA", "."),
        ("Grew revenue ", "$9M to $61M", " and launched ", "70+ commercial products", "."),
        ("Scaled ", "$4M to $38M", " across ", "12 fragmented markets", "."),
        ("Built a ", "$55M automated marketplace", " from scratch", "", "."),
        ("Closed a ", "$1.5M Microsoft partnership", " and ", "30% of regional revenue", "."),
        ("Contracted a ", "$254K POC", " at AdFixus", "", "."),
        ("Runs a ", "14-agent autonomous AI operating system", " in production", "", "."),
    ]
    for h in highlights:
        segs = []
        for i, chunk in enumerate(h):
            if not chunk:
                continue
            segs.append((chunk, bold if i % 2 == 1 else plain))
        segs[-1] = (segs[-1][0] + "\n", segs[-1][1])
        rows.append((segs, {"bullet": True}))
    rows.append(([("CORE COMPETENCIES\n", bold)], {}))
    rows.append(([(" · ".join(COMPETENCIES_11) + "\n", plain)], {}))
    rows.append(([("PROFESSIONAL EXPERIENCE\n", bold)], {}))
    rows.append(([("Captify\n", bold)], {}))
    rows.append(([("Built APAC from ", plain), ("$0 to $12M ARR", bold),
                  (" in three years.\n", plain)], {"bullet": True}))
    rows.append(([("EDUCATION AND RECOGNITION\n", bold)], {}))
    rows.append(([("Harvard Business School", bold), (", Executive Programs.\n", plain)],
                 {"bullet": True}))

    content = [{"endIndex": 1, "sectionBreak": {}}]
    pos = 1
    for segs, opts in rows:
        el, pos = _para(segs, start=pos, **opts)
        content.append(el)
    return {"body": {"content": content}}


@pytest.fixture(autouse=True)
def _reset_model_providers():
    """A provider disabled by one test must not silence the next one.

    llm.complete remembers a provider that answered "out of budget until the
    first of the month", because asking it 150 more times in one run buys
    nothing. Tests share a process, so that memory has to be cleared between
    them or the suite reports failures that belong to whichever test ran
    first.
    """
    from hunter import llm
    llm.reset()
    yield
    llm.reset()


@pytest.fixture(autouse=True)
def _drive_map(monkeypatch):
    """A registry for the fake Drive (tests/fake_docs.py): every build files
    into its application folder under a fake Hunter folder inside a fake
    registered area (Ventures), as the real one does (drivemap.py). A test of the guard itself
    replaces this."""
    from hunter import drivemap
    m = drivemap.DriveMap(
        destinations={"hunter.applications": "apps", "hunter.masters": "masters",
                      "hunter.previews": "prev"},
        roots={"Ventures": "cur"}, protected={"FamilyContentLibrary": "cl"},
        top_level={"cur": "Ventures", "cl": "FamilyContentLibrary", "inbox": "Inbox from My AI"}, inbox="inbox",
        written_at="2026-10-18T00:00:00Z")
    monkeypatch.setattr(drivemap, "load", lambda cfg: m)
    return m
