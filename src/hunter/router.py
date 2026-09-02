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


def select_for_build(cfg: Config) -> list[dict]:
    cap = int(cfg.optional("hunter_max_packages_per_run", "5"))
    rows = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,score,comp,location,"
                  "warm_path_person,package_status,krish_verdict",
        "krish_verdict": "not.is.null",
        "package_status": "in.(none,queued)",
        "order": "score.desc.nullslast",
        "limit": str(cap * 3),
    })
    picked = [r for r in rows
              if classify_verdict(r.get("krish_verdict") or "") == "go"]
    return picked[:cap]


def route_status(role_row: dict) -> str:
    """Column O on build: bridge first where a warm path exists (P5+)."""
    if (role_row.get("warm_path_person") or "").strip():
        return O_BUILT_BRIDGE
    return O_BUILT_DIRECT
