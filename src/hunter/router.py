"""Routing: which approved roles get packages, and what Package Status says.

Verdict vocabulary observed in hunter_seen_roles and the sheet: go, y, yes
(case-insensitive) mean build; applied means Krish already applied and no
build is queued; anything else in column A is his free-text rejection and is
quoted verbatim, never paraphrased.
"""
from __future__ import annotations

import re

from . import verdicts
from .config import Config, db_get
from .sheet import PKG_BUILT_BRIDGE, PKG_BUILT_DIRECT, PKG_DEAD

GO_WORDS = {"go", "y", "yes", "build"}

# The retired incumbent wrote sentences into warm_path_person ("None
# identified with a current connection", "None. No connections at Mutiny").
# A placeholder is not a person.
NO_PERSON = re.compile(r"^\s*(none|n/a|nobody|no\b|not found|unknown|-)", re.I)


def classify_verdict(text: str) -> str:
    """'go' | 'applied' | 'rejection' | 'none'. The vocabulary lives in
    verdicts.py, which also carries the reason code; this keeps the older
    call sites that only want the verdict."""
    return verdicts.parse(text)[0]


def is_warm_path(role_row: dict) -> bool:
    person = (role_row.get("warm_path_person") or "").strip()
    return bool(person) and not NO_PERSON.match(person)


def select_for_build(cfg: Config, sheet=None, headers=None, *,
                     cap: int | None = None, retry_dead: bool = False) -> list[dict]:
    """Rows to build packages for.

    Authority is column A as it reads now, not a DB field. Krish's ruling
    2026-09-02: he will set every verdict himself once he trusts the system,
    and twelve rows still carry a 'go' the retired incumbent wrote weeks ago.
    Building from those would produce packages he never asked for. When the
    sheet is unavailable the function returns nothing rather than falling
    back to the DB, because a wrong build is worse than a missed one.

    cap None reads hunter_max_packages_per_run (the packages button); cap 0
    means every Yes row (process). A row whose Package Status already says
    the posting is dead is skipped unless retry_dead, so a dead role is not
    re-fetched on every run.
    """
    if sheet is None or headers is None:
        return []
    if cap is None:
        cap = int(cfg.optional("hunter_max_packages_per_run", "5"))
    yes_rows = []
    for r in sheet.read_pipeline(headers):
        if classify_verdict(r.verdict or "") != "go":
            continue
        if r.package_urls.get("cv") and r.package_urls.get("letter"):
            continue  # links already on the sheet
        if r.cell("Package Status").strip() == PKG_DEAD and not retry_dead:
            continue
        yes_rows.append(r)
    if not yes_rows:
        return []
    from .run import match_rows
    known = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,comp,location,"
                  "warm_path_person,warm_path_tier,package_status,krish_verdict,"
                  "rejection_reason,status",
        "limit": "5000"})
    pairs, _, _, _ = match_rows(yes_rows, list(known))
    picked = [d for _, d in pairs if (d.get("package_status") or "none") != "built"]
    picked.sort(key=lambda d: d.get("score") or 0, reverse=True)
    return picked[:cap] if cap else picked


def route_status(role_row: dict) -> str:
    """Package Status on build: bridge first where a warm path exists."""
    if is_warm_path(role_row):
        return PKG_BUILT_BRIDGE
    return PKG_BUILT_DIRECT
