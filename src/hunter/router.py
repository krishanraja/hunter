"""Routing: which approved roles get packages, and what column O says.

Verdict vocabulary observed in hunter_seen_roles and the sheet: go, y, yes
(case-insensitive) mean build; applied means Krish already applied and no
build is queued; anything else in column A is his free-text rejection and is
quoted verbatim, never paraphrased.
"""
from __future__ import annotations

from . import verdicts
from .config import Config, db_get
from .sheet import O_BUILT_BRIDGE, O_BUILT_DIRECT

GO_WORDS = {"go", "y", "yes", "build"}


def classify_verdict(text: str) -> str:
    """'go' | 'applied' | 'rejection' | 'none'. The vocabulary lives in
    verdicts.py, which also carries the reason code; this keeps the older
    call sites that only want the verdict."""
    return verdicts.parse(text)[0]


def select_for_build(cfg: Config, sheet=None, headers=None) -> list[dict]:
    """Rows to build packages for.

    Authority is column A as it reads now, not a DB field. Krish's ruling
    2026-09-02: he will set every verdict himself once he trusts the system,
    and twelve rows still carry a 'go' the retired incumbent wrote weeks ago.
    Building from those would produce packages he never asked for. When the
    sheet is unavailable the function returns nothing rather than falling
    back to the DB, because a wrong build is worse than a missed one.
    """
    if sheet is None or headers is None:
        return []
    cap = int(cfg.optional("hunter_max_packages_per_run", "5"))
    yes_rows = [r for r in sheet.read_pipeline(headers)
                if classify_verdict(r.verdict or "") == "go"]
    if not yes_rows:
        return []
    from .run import match_rows
    known = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,comp,location,"
                  "warm_path_person,package_status,krish_verdict",
        "limit": "5000"})
    pairs, _, _, _ = match_rows(yes_rows, list(known))
    picked = [d for _, d in pairs
              if (d.get("package_status") or "none") in ("none", "queued")]
    picked.sort(key=lambda d: d.get("score") or 0, reverse=True)
    return picked[:cap]


def route_status(role_row: dict) -> str:
    """Column O on build: bridge first where a warm path exists (P5+)."""
    if (role_row.get("warm_path_person") or "").strip():
        return O_BUILT_BRIDGE
    return O_BUILT_DIRECT
