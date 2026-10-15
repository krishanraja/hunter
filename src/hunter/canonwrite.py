"""His own words, appended to canon 9.14, and nothing else.

hunter does not edit canon (canon.py), with one exception he ruled on
2026-10-15: when a letter needs a personal line the bank in 9.14 has no
entry for, hunter asks him, and his answer is written into 9.14 exactly as
he wrote it. This module is that exception and no wider. It can add an
entry to 9.14 and take back an entry it added, on his UNDO, and it can do
nothing to any other section or to an entry he has since changed.

Every write is:
  1. version locked: the patch matches the version it read, so a canon that
     moved while hunter was composing is never overwritten;
  2. checked before it is sent: every other section of the new body is
     identical to the old one, and 9.14 reads back as the old bank plus the
     new entry, or minus the one taken back;
  3. read back after it is sent, and restored if what came back is not what
     was written.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .canon import CANON_SLUG, HEADING, _parse_sections
from .config import Config, db_get, db_patch_where
from .package import personal

SECTION = "9.14"
TABLE = "canon_documents"

SECTION_TEXT = (
    "## 9.14 PERSONAL CONNECTIONS\n"
    "Ruled by Krish, 2026-10-15. A letter to a voice, audio or speech, mindset or "
    "psychology, future of work, personal development, emerging media, content or "
    "IP, live entertainment, or health and fitness business carries one line of "
    "his own, chosen from the entries below. His words: \"I have similar passions "
    "in live entertainment, content IP, psychology, self development, voice and "
    "speech technologies, health and fitness, and more\". When "
    "none fits, the package holds and hunter asks him; his answer is added here "
    "exactly as he wrote it. hunter adds entries and takes back one it added "
    "when he replies UNDO; it never changes one. He may edit this section "
    "himself.\n"
    "Format, read by hunter/src/hunter/package/personal.py: a line starting "
    "ENTRY n, then Categories, Terms and the date added, and on the next line "
    "his words in double quotes. Terms are words the business must say about "
    "itself for the line to fit; none means any business in its categories.\n"
)


class CanonWriteError(RuntimeError):
    pass


@dataclass
class Written:
    entry: int
    version_before: int
    version_after: int


def _read(cfg: Config) -> tuple[str, int]:
    rows = db_get(cfg, TABLE, {"select": "body,version", "slug": f"eq.{CANON_SLUG}"})
    if len(rows) != 1:
        raise CanonWriteError(f"expected one canon row, found {len(rows)}")
    return rows[0].get("body") or "", int(rows[0].get("version") or 0)


def _bounds(lines: list[str], number: str) -> tuple[int, int]:
    """(first line of the section, first line after it)."""
    start = None
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        if start is not None:
            return start, i
        if m.group(1) == number:
            start = i
    if start is None:
        raise CanonWriteError(f"canon has no section {number}")
    return start, len(lines)


def _others_unchanged(old: str, new: str) -> bool:
    a, b = _parse_sections(old), _parse_sections(new)
    a.pop(SECTION, None)
    b.pop(SECTION, None)
    return a == b


def _bank(body: str) -> list[personal.Entry]:
    sections = _parse_sections(body)
    return personal.parse_bank(sections[SECTION][1]) if SECTION in sections else []


def _write(cfg: Config, old: str, version: int, new: str) -> int:
    """Version-locked patch, read back, restored on a mismatch."""
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    n = db_patch_where(cfg, TABLE, {"slug": f"eq.{CANON_SLUG}", "version": f"eq.{version}"},
                       {"body": new, "version": version + 1, "updated_at": stamp})
    if n != 1:
        raise CanonWriteError(f"canon moved from version {version} while this was "
                              f"written; nothing was written")
    body, after = _read(cfg)
    if body == new and after == version + 1:
        return after
    # Not what was written. Put back what was there, under the same lock.
    back = db_patch_where(cfg, TABLE, {"slug": f"eq.{CANON_SLUG}", "version": f"eq.{after}"},
                          {"body": old, "version": after + 1, "updated_at": stamp})
    body2, _ = _read(cfg) if back == 1 else ("", 0)
    restored = "restored" if back == 1 and body2 == old else "NOT restored, check canon now"
    raise CanonWriteError(f"canon read back differently after the write ({restored})")


def has_section(body: str) -> bool:
    return SECTION in _parse_sections(body)


def section_body(cfg: Config) -> str:
    """The text of 9.14 as canon holds it now, or empty when it has none."""
    body, _ = _read(cfg)
    sections = _parse_sections(body)
    return sections[SECTION][1] if SECTION in sections else ""


def add_section(cfg: Config, *, expect_version: int) -> int:
    """Create 9.14, empty, after the last section. A canon write he approves
    by name: expect_version is the version he was shown."""
    old, version = _read(cfg)
    if version != expect_version:
        raise CanonWriteError(f"canon is at version {version}, not {expect_version}; "
                              f"nothing written")
    if has_section(old):
        raise CanonWriteError("canon already has 9.14")
    new = old.rstrip("\n") + "\n\n" + SECTION_TEXT
    if not _others_unchanged(old, new) or not has_section(new) or _bank(new):
        raise CanonWriteError("the new 9.14 did not parse as an empty section; nothing written")
    return _write(cfg, old, version, new)


def add_entry(cfg: Config, *, categories: list[str], terms: list[str], words: str,
              added: str | None = None) -> Written:
    """Append one entry, his words as he wrote them. The same words already
    in the bank are not written twice: the entry that holds them is returned."""
    old, version = _read(cfg)
    if not has_section(old):
        raise CanonWriteError("canon has no 9.14 yet; nothing written")
    bank = _bank(old)
    for e in bank:
        if e.words == words:
            return Written(e.number, version, version)
    number = max((e.number for e in bank), default=0) + 1
    block = personal.entry_block(number, categories, terms, words,
                                 added or dt.date.today().isoformat())
    lines = old.split("\n")
    start, end = _bounds(lines, SECTION)
    section = lines[start:end]
    while section and not section[-1].strip():
        section.pop()
    new_lines = lines[:start] + section + [""] + block.rstrip("\n").split("\n") + \
        ([""] + lines[end:] if end < len(lines) else [""])
    new = "\n".join(new_lines)
    want = bank + [personal.Entry(number, tuple(categories),
                                  tuple(t.lower() for t in terms), words)]
    if not _others_unchanged(old, new) or _bank(new) != want:
        raise CanonWriteError("the new entry did not read back as written; nothing written")
    return Written(number, version, _write(cfg, old, version, new))


def remove_entry(cfg: Config, *, number: int, words: str) -> Written:
    """Take back entry `number`, only while it still holds exactly `words`:
    an entry he has changed since is his, and stays."""
    old, version = _read(cfg)
    bank = _bank(old)
    target = [e for e in bank if e.number == number]
    if not target:
        raise CanonWriteError(f"canon 9.14 has no entry {number}")
    if target[0].words != words:
        raise CanonWriteError(f"entry {number} has changed since it was written; left as he has it")
    lines = old.split("\n")
    start, end = _bounds(lines, SECTION)
    i = next(k for k in range(start, end)
             if (m := personal.ENTRY.match(lines[k].strip())) and int(m.group(1)) == number)
    j = next(k for k in range(i + 1, end) if lines[k].strip())
    drop = {i, j} | ({j + 1} if j + 1 < end and not lines[j + 1].strip() else set())
    new = "\n".join(line for k, line in enumerate(lines) if k not in drop)
    want = [e for e in bank if e.number != number]
    if not _others_unchanged(old, new) or _bank(new) != want:
        raise CanonWriteError("taking the entry back did not read as intended; nothing written")
    return Written(number, version, _write(cfg, old, version, new))
