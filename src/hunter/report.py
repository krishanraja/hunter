"""Run reporting: how hunter becomes visible in Control Center.

The OS surfaces already exist and read two tables. Hunter writes one
workflow_runs row per run (that is what puts it in Flows, the agent card
and /api/health), and raises a silent_failures alert when a run fails or
writes nothing, which is what puts a named line on the Home banner.

Hunter resolves its OWN alerts on the next good run: nothing in the
Control Center UI ever writes resolved_at, so an alert raised here and
never cleared here would sit on the banner forever.

One row per run, never a heartbeat. Fleet liveness takes a global max
over workflow_runs, so a twice-weekly system that chattered hourly would
mask every other agent's silence.
"""
from __future__ import annotations

import datetime
import uuid

from .config import Config, db_get, db_insert, db_patch

AGENT_ID = "hunter"
WORKFLOW_ID = "hunter-run"
WORKFLOW_NAME = "Hunter Job Sourcing Run"
FAILURE_TIER = 3  # the Home critical banner reads tier >= 3


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def report_run(cfg: Config, *, started_at: datetime.datetime, ok: bool,
               counts: dict, spend_usd: float, summary_line: str,
               error: str | None = None) -> None:
    """One workflow_runs row, then the alert ledger, then the roster stamp.
    Reporting must never take the run down with it: a failure here is
    printed and swallowed, because the work itself already happened."""
    duration_ms = int(
        (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds() * 1000)
    staged = int(counts.get("staged") or 0)
    recorded = int(counts.get("recorded") or 0)
    built = int(counts.get("built") or 0)
    try:
        db_insert(cfg, "workflow_runs", [{
            "workflow_id": WORKFLOW_ID,
            "workflow_name": WORKFLOW_NAME,
            "agent_id": AGENT_ID,
            "run_at": started_at.isoformat(),
            "duration_ms": duration_ms,
            "cost_usd": round(float(spend_usd or 0), 4),
            "outcome": summary_line[:300],
            "outcome_count": staged + built,
            "status": "success" if ok else "error",
            "error_message": (error or "")[:500] or None,
            "metadata": {k: counts.get(k) for k in
                         ("discovered", "senior", "fresh", "recorded", "staged",
                          "unresolved", "built", "bridges", "reconciled")
                         if counts.get(k) is not None},
        }])
    except Exception as e:
        print(f"run reporting failed (workflow_runs): {e.__class__.__name__}: {e}")

    try:
        _sync_alerts(cfg, ok=ok, recorded=recorded, staged=staged, error=error)
    except Exception as e:
        print(f"run reporting failed (silent_failures): {e.__class__.__name__}: {e}")

    try:
        db_patch(cfg, "agents", {"id": AGENT_ID},
                 {"last_run": _now(), "last_output": summary_line[:500]})
    except Exception as e:
        print(f"run reporting failed (agents): {e.__class__.__name__}: {e}")


def _open_alerts(cfg: Config) -> list[dict]:
    return db_get(cfg, "silent_failures", {
        "select": "id,run_count,detected_at",
        "workflow_id": f"eq.{WORKFLOW_ID}",
        "resolved_at": "is.null",
    })


def _sync_alerts(cfg: Config, *, ok: bool, recorded: int, staged: int,
                 error: str | None) -> None:
    open_rows = _open_alerts(cfg)
    if ok and recorded > 0:
        for row in open_rows:
            db_patch(cfg, "silent_failures", {"id": row["id"]},
                     {"resolved_at": _now(),
                      "resolution_note": "cleared by a healthy hunter run"})
        return

    if error:
        failure_type, detail = "run_error", error[:400]
    else:
        failure_type = "empty_run"
        detail = ("the run completed but recorded no roles; sourcing, the "
                  "canon gates or the boards are the place to look")
    if open_rows:
        # keep one open row per incident and count the repeats, which is
        # what the banner's run_count means
        row = max(open_rows, key=lambda r: r.get("detected_at") or "")
        db_patch(cfg, "silent_failures", {"id": row["id"]},
                 {"run_count": int(row.get("run_count") or 1) + 1,
                  "failure_type": failure_type, "detail": detail})
        return
    db_insert(cfg, "silent_failures", [{
        "id": str(uuid.uuid4()),
        "workflow_id": WORKFLOW_ID,
        "workflow_name": WORKFLOW_NAME,
        "detected_at": _now(),
        "tier": FAILURE_TIER,
        "failure_type": failure_type,
        "detail": detail,
        "run_count": 1,
    }])
