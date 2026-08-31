"""Canon loader. The canon (Supabase canon_documents, slug krish-canon) governs
scoring, gates, the sourcing universe, and the sheet contract. hunter fails hard
and runs nothing paid if canon is absent, incomplete, or disagrees with the
verified artifact IDs. hunter never edits canon; changes go to workflow_proposals.

Parser notes, verified against the live body 2026-08-31:
  - Headings mix two styles: bare "9.1 SOURCING UNIVERSE. ..." and markdown
    "## 9.12 TEMPLATE CONTRACT, ...". Both are handled.
  - Section numbering skips 9.8. Never assume contiguity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import config
from .config import Config, db_get

CANON_SLUG = "krish-canon"

REQUIRED_SECTIONS = ["2", "3", "4", "6", "9.1", "9.2", "9.3", "9.4", "9.9", "9.12", "9.13"]

# A heading is an optional ## or ### prefix, a section number, then an
# uppercase title. The uppercase requirement keeps numbered list items
# ("1. Header row columns...") from matching, and the no-leading-zero rule
# keeps the 9.13 tab list ("00 START HERE, Profile, ...") from opening a
# phantom section 00.
HEADING = re.compile(r"^(?:#{2,3}\s+)?((?:0|[1-9]\d*)(?:\.\d+)?)\.?\s+([A-Z][A-Z0-9 ,.'&/()-]{2,})")

GATE_LINE = re.compile(r"^G(\d+)\s+([A-Z]+)\.\s*(.*)$")

DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{25,}")


class CanonError(RuntimeError):
    pass


@dataclass
class Canon:
    sections: dict[str, tuple[str, str]]  # number -> (title, body)
    bar: int                              # presentation bar from 9.2
    gates: dict[str, str]                 # "G1".."G10" -> rule text
    universe: list[str]                   # named companies from 9.1
    sheet_headers: list[str]              # 28 exact headers from 9.13
    version: int
    body: str

    def section_text(self, number: str) -> str:
        if number not in self.sections:
            raise CanonError(f"canon section {number} not found")
        title, body = self.sections[number]
        return f"{title}\n{body}"


def _parse_sections(body: str) -> dict[str, tuple[str, str]]:
    """number -> (heading line, full section text including the heading line).
    Sections like 9.2 carry their entire content on the heading line, so the
    body always includes it."""
    sections: dict[str, tuple[str, str]] = {}
    current: str | None = None
    title = ""
    buf: list[str] = []

    def close():
        if current is not None:
            sections[current] = (title, "\n".join([title] + buf).strip())

    for line in body.split("\n"):
        m = HEADING.match(line)
        if m:
            close()
            current = m.group(1)
            title = line.strip()
            buf = []
        elif current is not None:
            buf.append(line)
    close()
    return sections


def _parse_gates(body_94: str) -> dict[str, str]:
    gates: dict[str, str] = {}
    for line in body_94.split("\n"):
        m = GATE_LINE.match(line.strip())
        if m:
            gates[f"G{m.group(1)}"] = line.strip()
    return gates


def _parse_bar(body_92: str) -> int:
    m = re.search(r"(\d+)\s+out of 10", body_92)
    if not m:
        raise CanonError("canon 9.2 does not state the presentation bar as 'N out of 10'")
    return int(m.group(1))


def _parse_universe(body_91: str) -> list[str]:
    companies: list[str] = []
    for line in body_91.split("\n"):
        line = line.strip()
        if line.startswith("RETIRED") or line.startswith("ATS SLUG"):
            break
        if ":" in line and "," in line.split(":", 1)[1]:
            names = [n.strip() for n in line.split(":", 1)[1].split(",")]
            companies.extend(n for n in names if n)
    seen: set[str] = set()
    out: list[str] = []
    for c in companies:
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def _parse_sheet_headers(body_913: str) -> list[str]:
    m = re.search(r"Header, exact, in order:\s*\n(.+?)(?:\n\n|\n[A-Z]{2,})", body_913, re.S)
    if not m:
        raise CanonError("canon 9.13 header list not found")
    blob = " ".join(m.group(1).split("\n")).strip().rstrip(".")
    headers: list[str] = []
    for token in blob.split(", "):
        parts = token.strip().split(" ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[A-Z]{1,2}", parts[0]):
            raise CanonError(f"canon 9.13 header token not parseable: {token!r}")
        headers.append(parts[1].strip())
    if len(headers) != 28:
        raise CanonError(f"canon 9.13 lists {len(headers)} headers, expected 28")
    return headers


def _registry_id(body_99: str, artifact_label: str) -> str:
    for line in body_99.split("\n"):
        if artifact_label in line:
            after = line.split(artifact_label, 1)[1]
            m = DRIVE_ID.search(after)
            if m:
                return m.group(0)
    raise CanonError(f"canon 9.9 registry row for {artifact_label!r} not found")


def load_canon(cfg: Config) -> Canon:
    rows = db_get(cfg, "canon_documents", {"select": "slug,title,body,version",
                                           "slug": f"eq.{CANON_SLUG}"})
    if not rows:
        raise CanonError("canon row missing from canon_documents; run aborted")
    body = rows[0]["body"] or ""
    sections = _parse_sections(body)

    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise CanonError(f"canon is missing required sections: {missing}; run aborted")

    gates = _parse_gates(sections["9.4"][1])
    expected_gates = [f"G{i}" for i in range(1, 11)]
    if sorted(gates, key=lambda g: int(g[1:])) != expected_gates:
        raise CanonError(f"canon 9.4 must define G1..G10, found {sorted(gates)}")

    # The 9.9 registry must agree with the verified constants. On mismatch,
    # abort and tell Krish which side moved. hunter never picks a winner.
    checks = [
        ("Master CV", config.CV_MASTER_ID),
        ("Cover letter template", config.LETTER_MASTER_ID),
        ("Pipeline surface", config.WORKBOOK_ID),
    ]
    for label, expected in checks:
        actual = _registry_id(sections["9.9"][1], label)
        if actual != expected:
            raise CanonError(
                f"canon 9.9 lists {label!r} as {actual} but hunter was verified against "
                f"{expected}. One side moved. Confirm with Krish and update the losing "
                f"side; hunter will not choose."
            )

    return Canon(
        sections=sections,
        bar=_parse_bar(sections["9.2"][1]),
        gates=gates,
        universe=_parse_universe(sections["9.1"][1]),
        sheet_headers=_parse_sheet_headers(sections["9.13"][1]),
        version=rows[0].get("version", 0),
        body=body,
    )
