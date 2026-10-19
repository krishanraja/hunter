"""His rulings on how a package reads, kept where every writer reads them.

Until 2026-10-19 a complaint he made about a package went into a chat and
nowhere the engine could read, and a line he cut from a letter taught the
writer only while it was one of his three newest edits. So the same failure
could come back the week after he named it. His words that day: "It doesn't
mean then we'll have to fix the next fuck-up."

A rule is his words, quoted, with the phrases it rules out. It is stored in
system_config (hunter_package_rules), never in this public repository, and is
written by a Claude session when he rules, then read back. Every reader brief
carries every rule as a doubt (compose.RULES), every letter, CV summary and
form answer is checked against the phrases (compose.altitude_problems,
essays), and the lines he deletes from letters go to the writer as HIS CUTS
(docedits.his_cuts).
"""
from __future__ import annotations

import datetime as dt
import json

from ..config import Config, db_get, db_insert, db_patch

CONFIG_KEY = "hunter_package_rules"


def _rows(cfg: Config) -> list[dict]:
    got = db_get(cfg, "system_config", {"key": f"eq.{CONFIG_KEY}", "select": "value"})
    if not got:
        return []
    raw = got[0].get("value")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [r for r in (data or []) if isinstance(r, dict) and r.get("words")]


def load(cfg: Config) -> tuple[str, tuple[str, ...]]:
    """(the rules as the writer reads them, every phrase they rule out). An
    unreadable store is no rules, never a failed build."""
    try:
        rows = _rows(cfg)
    except Exception:
        return "", ()
    return text_of(rows), ruled_out_of(rows)


def text_of(rows: list[dict]) -> str:
    return "\n".join(f"- ({r.get('since') or ''}) \"{' '.join(str(r['words']).split())}\""
                     for r in rows)


def ruled_out_of(rows: list[dict]) -> tuple[str, ...]:
    out: list[str] = []
    for r in rows:
        for p in r.get("ruled_out") or []:
            p = " ".join(str(p).lower().split())
            if p and p not in out:
                out.append(p)
    return tuple(out)


def add(cfg: Config, words: str, ruled_out: list[str] | None = None,
        since: str = "") -> int:
    """Record one ruling and read it back. Returns how many rules there are."""
    words = " ".join((words or "").split())
    if not words:
        raise ValueError("a rule needs his words")
    rows = _rows(cfg)
    if any(r.get("words") == words for r in rows):
        return len(rows)
    rows.append({"words": words, "ruled_out": [p for p in (ruled_out or []) if p.strip()],
                 "since": since or dt.date.today().isoformat()})
    value = json.dumps(rows, ensure_ascii=False)
    if db_get(cfg, "system_config", {"key": f"eq.{CONFIG_KEY}", "select": "key"}):
        db_patch(cfg, "system_config", {"key": CONFIG_KEY}, {"value": value})
    else:
        db_insert(cfg, "system_config", [{"key": CONFIG_KEY, "value": value}])
    back = _rows(cfg)
    if not any(r.get("words") == words for r in back):
        raise RuntimeError("the rule did not read back")
    return len(back)
