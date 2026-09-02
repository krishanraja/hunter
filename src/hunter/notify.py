"""The ONLY outbound module in the repo, by design and by guard test.

Krish's ruling 2026-09-02: Telegram is gone, email only. The email is the
Routine's own completion notification, which the cloud session sends when the
run ends, so this module's job is to put the summary where that notification
will carry it: stdout, which the session reports as its final message.

It reaches no one. That is the point. A guard test greps the tree for any
other outbound path, and this file is the one place allowed to have one.
"""
from __future__ import annotations

from .config import Config


def send_summary(cfg: Config, text: str) -> dict:
    """Emit the run summary. The Routine mails it to Krish and nobody else."""
    print(text)
    return {"sent": True, "channel": "run summary, carried by the Routine's email"}
