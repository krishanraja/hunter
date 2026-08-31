import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))  # for fake_docs

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


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
