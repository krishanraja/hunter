"""What must always be true of the sheet, checked and repaired on every run.

Krish 2026-09-19: "I need this system to be predictable, robust, autonomous as
possible, ensuring the sheet always reflects what I have yet to review, have
approved, have applied".

The rest of the codebase is made of steps: source, build, archive, sort. A step
can fail, be interrupted, or be raced by him editing the sheet while it runs,
and when one does the sheet is left in a shape nothing else will ever correct.
Every drift the system has had was of that kind: a dropdown that stopped at row
400 so rows appended below it took free text, scores written as strings so the
sort produced 9, 9, 6, 10, a decided row that stayed on Pipeline because the
archive call threw after the copy.

This module is the answer to that class of bug. It states the invariants as
code, checks them against the live sheet, and repairs the ones that have a safe
repair. It is idempotent and it runs at the end of every process and every run,
so the sheet converges on its contract no matter which step failed on the way.

Three rules:

  a repair may only write what hunter is allowed to write. Column A is his.
  The three system codes in verdicts.py (dead posting, already applied,
  duplicate row) are the only values hunter may ever put there, because canon
  9.4 calls them hunter's own failures rather than his taste.

  a check that cannot repair itself reports instead, and the report reaches
  him in the run email rather than dying in a log.

  every repair re-reads before it writes. A row number from the top of the
  pass is not a row number after an archive, and acting on a stale one moves
  the wrong role.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from . import verdicts
from .sheet import (BUILT_STATUSES, DATA_START_ROW, DEFAULTS_BY_NAME,
                    PACKAGE_STATUSES, PKG_DEAD, Sheet, cell_range)

BROKEN, WATCH, OK = "broken", "watch", "ok"


@dataclass
class Finding:
    name: str
    state: str
    detail: str
    repaired: str = ""
    rows: list[int] = dc_field(default_factory=list)

    def line(self) -> str:
        mark = {OK: "ok  ", WATCH: "note", BROKEN: "FIX "}[self.state]
        out = f"{mark} {self.name}: {self.detail}"
        if self.repaired:
            out += f" -> {self.repaired}"
        return out


def _num(text: str):
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError):
        return None


# ---------- the checks ----------

def check_verdict_vocabulary(rows) -> Finding:
    """Anything in column A that is not the dropdown and not a sentence hunter
    can read. Reported, never repaired: his words are his."""
    unknown = []
    for r in rows:
        text = (r.verdict or "").strip()
        if not text:
            continue
        kind, code = verdicts.parse(text)
        if kind == "rejection" and code is None and len(text.split()) <= 2:
            # A one or two word rejection with no code is more likely a typo
            # than a reason. "Declined" alone, "no", "nah".
            unknown.append((r.row_number, text))
    if not unknown:
        return Finding("verdict vocabulary", OK, "every verdict reads cleanly")
    sample = ", ".join(f"row {n} {t!r}" for n, t in unknown[:6])
    return Finding("verdict vocabulary", WATCH,
                   f"{len(unknown)} verdict(s) carry no reason hunter can learn "
                   f"from: {sample}", rows=[n for n, _ in unknown])


def check_no_decided_rows(rows) -> Finding:
    """Applied and Declined rows belong on the Applied tab. One still here
    means an archive did not finish."""
    left = [r.row_number for r in rows
            if verdicts.parse(r.verdict or "")[0] in ("applied", "rejection")]
    if not left:
        return Finding("decided rows archived", OK,
                       "Pipeline holds only New and Yes")
    return Finding("decided rows archived", BROKEN,
                   f"{len(left)} decided row(s) still on Pipeline", rows=left)


def check_scores_numeric(rows) -> Finding:
    bad = [r.row_number for r in rows
           if r.cell("Score").strip() and _num(r.cell("Score")) is None]
    if not bad:
        return Finding("scores are numbers", OK, f"{len(rows)} row(s) sort correctly")
    return Finding("scores are numbers", BROKEN,
                   f"{len(bad)} score(s) are text, so the sort is wrong", rows=bad)


def check_no_empty_cells(rows) -> Finding:
    """canon 9.13: no blank cells. A blank reads as missing data rather than
    as the default it should carry."""
    holes: dict[int, list[str]] = {}
    for r in rows:
        for name, default in DEFAULTS_BY_NAME.items():
            if not str(r.cell(name)).strip():
                holes.setdefault(r.row_number, []).append(name)
    if not holes:
        return Finding("no empty cells", OK, "every cell carries a value")
    total = sum(len(v) for v in holes.values())
    return Finding("no empty cells", BROKEN,
                   f"{total} empty cell(s) across {len(holes)} row(s)",
                   rows=sorted(holes))


def check_package_status_vocabulary(rows) -> Finding:
    allowed = PACKAGE_STATUSES | {DEFAULTS_BY_NAME["Package Status"]}
    bad = [(r.row_number, r.cell("Package Status"))
           for r in rows if r.cell("Package Status").strip() not in allowed]
    if not bad:
        return Finding("package status vocabulary", OK, "all values are in canon")
    return Finding("package status vocabulary", WATCH,
                   f"{len(bad)} row(s) carry a status canon 9.13 does not "
                   f"define: {bad[:4]}", rows=[n for n, _ in bad])


def check_yes_rows_accounted_for(rows) -> Finding:
    """Every role he said Yes to has either materials or a stated reason it
    has none. A Yes with neither is a role quietly going nowhere, which is
    the single worst state this system can be in."""
    silent = []
    for r in rows:
        if verdicts.parse(r.verdict or "")[0] != "go":
            continue
        built = r.package_urls.get("cv") and r.package_urls.get("letter")
        status = r.cell("Package Status").strip()
        if built or status in PACKAGE_STATUSES:
            continue
        silent.append(r.row_number)
    if not silent:
        return Finding("every Yes is accounted for", OK,
                       "each approved role has materials or a stated reason")
    return Finding("every Yes is accounted for", BROKEN,
                   f"{len(silent)} approved role(s) have no materials and no "
                   f"reason why", rows=silent)


def check_dead_yes_rows(rows) -> Finding:
    """He approved it and the posting has since closed. Hunter must not
    archive this: it was his call, so it is his to withdraw."""
    dead = [r.row_number for r in rows
            if verdicts.parse(r.verdict or "")[0] == "go"
            and r.cell("Package Status").strip() == PKG_DEAD]
    if not dead:
        return Finding("approved roles still live", OK, "no dead approvals")
    return Finding("approved roles still live", WATCH,
                   f"{len(dead)} role(s) you approved have closed since; they "
                   f"stay until you decide", rows=dead)


def check_applied_state_agrees(rows) -> Finding:
    """Column A says Applied or it does not; Application Status must agree."""
    from .sheet import APPLIED_STATUS
    wrong = []
    for r in rows:
        kind = verdicts.parse(r.verdict or "")[0]
        marked = r.cell("Application Status").strip() == APPLIED_STATUS
        if (kind == "applied") != marked:
            wrong.append(r.row_number)
    if not wrong:
        return Finding("applied state agrees", OK, "column A and column T match")
    return Finding("applied state agrees", BROKEN,
                   f"{len(wrong)} row(s) disagree about whether you applied",
                   rows=wrong)


def _standing(r) -> tuple:
    """Which twin is the real row. His verdict outranks a built package, which
    outranks being higher up the sheet. Matches router.same_posting, so the
    sheet and the database agree on which twin survives."""
    kind = verdicts.parse(r.verdict or "")[0]
    return (kind in ("go", "applied"),
            bool(r.package_urls.get("cv") and r.package_urls.get("letter")),
            r.cell("Package Status").strip() in BUILT_STATUSES,
            -r.row_number)


def check_no_duplicate_postings(rows) -> Finding:
    """The same posting on two rows.

    Two identities, because one does not cover the sheet. The ATS key catches
    two rows whose links resolve to the same board posting, which is what
    relinking creates when two company spellings turn out to be one company.
    Company plus title catches the rest: 14 rows were sitting on the sheet in
    pairs with LinkedIn links that differ only in their tracking parameters,
    invisible to the ATS test because a LinkedIn URL has no ATS key at all.

    Company plus title is the identity the sourcing dedupe and reconcile both
    already use. Two postings sharing both are one application target, so
    treating them as one row here is the same rule, not a new one.
    """
    from .run import _norm_title, _squash, ats_key
    seen: dict[tuple, list] = {}
    for r in rows:
        key = ats_key(r.jd_url) if r.jd_url else None
        if not key:
            key = ("identity", _squash(r.company), _norm_title(r.role))
            if not key[1] or not key[2]:
                continue
        seen.setdefault(key, []).append(r)
    losers: list[int] = []
    groups = 0
    for twins in seen.values():
        if len(twins) < 2:
            continue
        groups += 1
        keeper = max(twins, key=_standing)
        losers += [t.row_number for t in twins if t.row_number != keeper.row_number]
    if not losers:
        return Finding("no duplicate postings", OK, "every row is a distinct posting")
    return Finding("no duplicate postings", BROKEN,
                   f"{len(losers)} duplicate row(s) across {groups} posting(s)",
                   rows=sorted(losers))


def check_sorted_by_score(rows) -> Finding:
    scores = [_num(r.cell("Score")) or 0 for r in rows]
    if scores == sorted(scores, reverse=True):
        return Finding("sorted by score", OK, "highest scoring role is at the top")
    return Finding("sorted by score", BROKEN, "rows are out of score order")


def check_row_headroom(sheet: Sheet, rows) -> Finding:
    """A tab that runs out of rows fails an append silently at the worst
    possible moment, which is the middle of a sourcing run."""
    try:
        meta = sheet._sheet_meta()
        total = meta["properties"]["gridProperties"]["rowCount"]
    except Exception as e:
        return Finding("row headroom", WATCH, f"could not read: {e.__class__.__name__}")
    used = len(rows) + DATA_START_ROW
    if total - used < 100:
        return Finding("row headroom", BROKEN,
                       f"{total - used} spare row(s) left of {total}")
    return Finding("row headroom", OK, f"{total - used} spare rows")


def check_archive_stamped(archive) -> Finding:
    """Every archived row carries the date it left, or the archive stops being
    a record of when a decision was made."""
    from .sheet import ARCHIVE_WIDTH
    missing = []
    for r in archive:
        cells = r.cells
        stamp = cells[ARCHIVE_WIDTH - 1] if len(cells) >= ARCHIVE_WIDTH else ""
        if not str(stamp).strip():
            missing.append(r.row_number)
    if not missing:
        return Finding("archive stamped", OK, f"{len(archive)} archived row(s) dated")
    return Finding("archive stamped", WATCH,
                   f"{len(missing)} archived row(s) carry no date", rows=missing)


def check_dropdown_reaches_the_end(sheet: Sheet, rows) -> Finding:
    """The failure that makes every other check moot: rows appended below the
    validated range take free text, so his click is not a click any more."""
    last = (rows[-1].row_number if rows else DATA_START_ROW) + 50
    try:
        at_top = sheet._validation_at(DATA_START_ROW)
        at_end = sheet._validation_at(last)
    except Exception as e:
        return Finding("dropdown reaches the end", WATCH,
                       f"could not read: {e.__class__.__name__}")
    if at_top and at_end:
        return Finding("dropdown reaches the end", OK,
                       f"column A is a dropdown through row {last}")
    if not at_top:
        return Finding("dropdown reaches the end", BROKEN,
                       "column A has no dropdown at all")
    return Finding("dropdown reaches the end", BROKEN,
                   f"the dropdown stops before row {last}")


# ---------- the repairs ----------

def repair_scores(sheet: Sheet, rows, bad: list[int]) -> str:
    blocks = []
    for r in rows:
        if r.row_number not in bad:
            continue
        n = _num(r.cell("Score"))
        blocks.append((cell_range("Score", r.row_number), [[n if n is not None else 0]]))
    if not blocks:
        return ""
    sheet._write(blocks)
    return f"rewrote {len(blocks)} score(s) as numbers"


def repair_empty_cells(sheet: Sheet, rows, targets: list[int]) -> str:
    blocks, filled = [], 0
    for r in rows:
        if r.row_number not in targets:
            continue
        for name, default in DEFAULTS_BY_NAME.items():
            if str(r.cell(name)).strip():
                continue
            blocks.append((cell_range(name, r.row_number), [[default]]))
            filled += 1
    if not blocks:
        return ""
    for i in range(0, len(blocks), 200):
        sheet._write(blocks[i:i + 200], raw=True)
    return f"filled {filled} empty cell(s) with the canon default"


def repair_applied_state(sheet: Sheet, rows, targets: list[int]) -> str:
    from .sheet import APPLIED_STATUS
    blocks = []
    for r in rows:
        if r.row_number not in targets:
            continue
        kind = verdicts.parse(r.verdict or "")[0]
        want = APPLIED_STATUS if kind == "applied" else DEFAULTS_BY_NAME["Application Status"]
        blocks.append((cell_range("Application Status", r.row_number), [[want]]))
    if not blocks:
        return ""
    sheet._write(blocks, raw=True)
    return f"set Application Status on {len(blocks)} row(s) to match column A"


def repair_duplicates(sheet: Sheet, rows, targets: list[int]) -> str:
    """Stamp the later twin with the one system code that says so. Column A is
    his, and this is the single exception canon 9.4 allows: a duplicate row is
    hunter's dedupe failing, not his taste."""
    mapping = {}
    for r in rows:
        if r.row_number not in targets:
            continue
        if (r.verdict or "").strip() not in ("", "New"):
            continue  # he has already ruled on it; leave it alone
        mapping[r.row_number] = f"{verdicts.DECLINE_PREFIX}duplicate row"
    if not mapping:
        return ""
    sheet.set_verdicts(mapping)
    return f"marked {len(mapping)} duplicate row(s) for archiving"


def repair_yes_without_status(sheet: Sheet, rows, targets: list[int]) -> str:
    """A Yes with nothing to show gets the honest holding status, so the next
    run picks it up and he can see it has not been forgotten."""
    n = 0
    for r in rows:
        if r.row_number not in targets:
            continue
        sheet.update_package_status(r.row_number, DEFAULTS_BY_NAME["Package Status"])
        n += 1
    return f"reset {n} approved row(s) to Not started so the next build picks them up" if n else ""


def repair_dropdown(sheet: Sheet) -> str:
    rows = sheet.set_verdict_dropdown(verdicts.dropdown_values())
    return f"extended the column A dropdown to row {rows}"


def repair_sort(sheet: Sheet) -> str:
    out = sheet.sort_by_score()
    return f"sorted {out['rows']} row(s) by score"


# ---------- the pass ----------

def check_all(sheet: Sheet, rows, archive) -> list[Finding]:
    return [
        check_dropdown_reaches_the_end(sheet, rows),
        check_verdict_vocabulary(rows),
        check_no_decided_rows(rows),
        check_no_duplicate_postings(rows),
        check_scores_numeric(rows),
        check_no_empty_cells(rows),
        check_package_status_vocabulary(rows),
        check_yes_rows_accounted_for(rows),
        check_dead_yes_rows(rows),
        check_applied_state_agrees(rows),
        check_sorted_by_score(rows),
        check_archive_stamped(archive),
        check_row_headroom(sheet, rows),
    ]


REPAIRS = {
    "dropdown reaches the end": lambda sheet, rows, f: repair_dropdown(sheet),
    "scores are numbers": lambda sheet, rows, f: repair_scores(sheet, rows, f.rows),
    "no empty cells": lambda sheet, rows, f: repair_empty_cells(sheet, rows, f.rows),
    "applied state agrees": lambda sheet, rows, f: repair_applied_state(sheet, rows, f.rows),
    "no duplicate postings": lambda sheet, rows, f: repair_duplicates(sheet, rows, f.rows),
    "every Yes is accounted for": lambda sheet, rows, f: repair_yes_without_status(sheet, rows, f.rows),
    "sorted by score": lambda sheet, rows, f: repair_sort(sheet),
}


def enforce(sheet: Sheet, canon_headers: list[str], *, apply: bool = True) -> dict:
    """Check every invariant, repair what has a safe repair, report the rest.

    Re-reads the sheet itself rather than taking rows from the caller, because
    a repair is only safe against the row numbers that exist at the moment it
    writes. Returns findings plus lines for the run summary.
    """
    rows = sheet.read_pipeline(canon_headers)
    try:
        archive = sheet.read_archive()
    except Exception:
        archive = []
    findings = check_all(sheet, rows, archive)

    repaired: list[str] = []
    if apply:
        for f in findings:
            if f.state != BROKEN or f.name not in REPAIRS:
                continue
            try:
                note = REPAIRS[f.name](sheet, rows, f)
                if note:
                    f.repaired = note
                    repaired.append(note)
                    rows = sheet.read_pipeline(canon_headers)
            except Exception as e:
                f.repaired = f"repair failed: {e.__class__.__name__}: {e}"

    after = check_all(sheet, rows, archive) if repaired else findings
    still = [f for f in after if f.state == BROKEN]
    return {"findings": findings, "repaired": repaired,
            "still_broken": [f.name for f in still],
            "lines": ["sheet invariants:"] + [f"  {f.line()}" for f in findings],
            "ok": not still}
