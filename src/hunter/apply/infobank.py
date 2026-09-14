"""Reader for the three answer tabs Krish already maintains in the workbook.

Krish's ruling 2026-09-13: the answers live in the sheet, not in system_config,
and not in a new tab. These three already exist and are authoritative:

  Application Info Bank   sections A to J, rows shaped
                          Field | Value | Status | Notes
  Profile                 core facts, voice and tone rules, positioning rules
  Interview Answers       long form answers in his voice, plus the three slot
                          "why this company" template

The status legend in the Info Bank is real state, not decoration:
  LOCKED        usable without asking
  NEEDS INPUT   usable IF the value cell is non empty, otherwise Unanswered
  SENSITIVE     Krish's to disclose; never auto filled, always flagged
  PER ROLE      drafted per posting, never reused verbatim
  OPTIONAL      skip when empty

Reads are formula rendered so the link cells survive; a rendered read loses
every URL (the same rule as sheet.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INFO_TAB = "Application Info Bank"
PROFILE_TAB = "Profile"
INTERVIEW_TAB = "Interview Answers"

LOCKED = "locked"
NEEDS_INPUT = "needs_input"
SENSITIVE = "sensitive"
PER_ROLE = "per_role"
OPTIONAL = "optional"
UNKNOWN = "unknown"

_STATUS_PATTERNS = (
    (PER_ROLE, ("per role",)),
    (NEEDS_INPUT, ("needs your input", "needs input", "edit to taste")),
    (SENSITIVE, ("sensitive",)),
    (OPTIONAL, ("optional",)),
    (LOCKED, ("locked",)),
)

# Section headers in the Info Bank, keyed by the letter used on the tab.
_SECTION_RE = re.compile(r"^SECTION\s+([A-J])\b", re.I)


def parse_status(cell: str) -> str:
    low = (cell or "").strip().lower()
    for status, needles in _STATUS_PATTERNS:
        if any(n in low for n in needles):
            return status
    return UNKNOWN


def norm_label(text: str) -> str:
    """Normalise a question or field label for matching. Case, punctuation and
    whitespace are noise; a trailing non breaking space is common in vendor
    labels and has already caused one mismatch."""
    s = (text or "").replace(" ", " ").strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class BankEntry:
    section: str
    field_name: str
    value: str
    status: str
    notes: str = ""

    @property
    def usable(self) -> bool:
        """A value we may enter on a form.

        Krish supplied his demographic answers on 2026-09-14 and said he
        consents to the acknowledgement checkboxes, while also requiring that
        every application reach him by email first. So SENSITIVE stopped meaning
        "unusable" and started meaning "usable but always shown": with a value
        it fills the field and appears in the approval email, without one it
        stays Unanswered. PER_ROLE is never reused verbatim.
        """
        if self.status == PER_ROLE:
            return False
        return bool(self.value.strip())

    @property
    def always_flagged(self) -> bool:
        """Answers that must appear in the approval email every single time,
        however routine they become."""
        return self.status == SENSITIVE

    @property
    def blocking(self) -> bool:
        """Declared as needed, still empty."""
        return self.status == NEEDS_INPUT and not self.value.strip()


@dataclass
class AnswerBank:
    entries: dict[str, BankEntry] = field(default_factory=dict)
    profile: dict[str, str] = field(default_factory=dict)
    interview: dict[str, str] = field(default_factory=dict)
    banned_phrases: tuple[str, ...] = ()
    positioning_rules: tuple[str, ...] = ()
    why_company_slots: tuple[str, ...] = ()

    def get(self, field_name: str) -> BankEntry | None:
        return self.entries.get(norm_label(field_name))

    def value(self, field_name: str) -> str:
        e = self.get(field_name)
        return e.value.strip() if e and e.usable else ""

    @property
    def blocking(self) -> list[BankEntry]:
        return [e for e in self.entries.values() if e.blocking]

    @property
    def sensitive(self) -> list[BankEntry]:
        return [e for e in self.entries.values() if e.status == SENSITIVE]


def _rows(values: list[list], width: int = 4) -> list[list[str]]:
    out = []
    for row in values:
        cells = [str(c) if c is not None else "" for c in row]
        cells += [""] * (width - len(cells))
        out.append(cells[:width])
    return out


def parse_info_bank(values: list[list]) -> dict[str, BankEntry]:
    entries: dict[str, BankEntry] = {}
    section = ""
    for cells in _rows(values):
        first = cells[0].strip()
        m = _SECTION_RE.match(first)
        if m:
            section = m.group(1).upper()
            continue
        if not first or first.lower() == "field" or first.lower() == "question prompt":
            continue
        status = parse_status(cells[2])
        if status == UNKNOWN and not cells[1].strip():
            continue  # a header or a legend line, not an answer row
        entries[norm_label(first)] = BankEntry(
            section=section, field_name=first, value=cells[1].strip(),
            status=status, notes=cells[3].strip())
    return entries


def parse_profile(values: list[list]) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    """(core facts, banned phrases, positioning rules)."""
    facts: dict[str, str] = {}
    banned: list[str] = []
    rules: list[str] = []
    mode = ""
    for cells in _rows(values, width=3):
        first = cells[0].strip()
        low = first.lower()
        if low.startswith("voice & tone") or low.startswith("voice and tone"):
            mode = "voice"
            continue
        if low.startswith("hard positioning"):
            mode = "rules"
            continue
        if low in ("core facts", "current cv + cover letter", "key proof points",
                   "how he describes himself (use verbatim)"):
            mode = "facts"
            continue
        if not first or low in ("field", "rule", "asset", "label", "category"):
            continue
        if mode == "voice":
            # "Banned: a, b, c" is the machine readable half of these rows.
            body = cells[1] or first
            for chunk in re.findall(r"Banned:\s*(.+)", body, re.I):
                banned.extend(p.strip().strip('"').strip("'")
                              for p in chunk.split(",") if p.strip())
        elif mode == "rules":
            rules.append(first if not cells[1] else f"{first}: {cells[1]}")
        if cells[1].strip():
            facts.setdefault(norm_label(first), cells[1].strip())
    return facts, tuple(banned), tuple(rules)


def parse_interview(values: list[list]) -> tuple[dict[str, str], tuple[str, ...]]:
    """(answer by prompt, why-this-company slots)."""
    answers: dict[str, str] = {}
    slots: list[str] = []
    for cells in _rows(values, width=3):
        first = cells[0].strip()
        body = cells[1].strip()
        if not first or not body:
            continue
        if first.lower().startswith("slot "):
            slots.append(f"{first}: {body}")
            continue
        answers[norm_label(first)] = body
    return answers, tuple(slots)


def load_bank(read_tab) -> AnswerBank:
    """read_tab(tab_name) -> list[list], formula rendered. Injected so this is
    testable offline and so it can sit on the existing Sheet client."""
    entries = parse_info_bank(read_tab(INFO_TAB))
    facts, banned, rules = parse_profile(read_tab(PROFILE_TAB))
    interview, slots = parse_interview(read_tab(INTERVIEW_TAB))
    return AnswerBank(entries=entries, profile=facts, interview=interview,
                      banned_phrases=banned, positioning_rules=rules,
                      why_company_slots=slots)
