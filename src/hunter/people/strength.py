"""Relationship strength, per Krish's 2026-08-31 ruling: inferred from his
LinkedIn export signals (messages, endorsements, invitations,
recommendations) combined with the priors that already exist in
contact_intelligence (warmth, email reciprocity). Deterministic 0-100.

Privacy contract: evidence carries AGGREGATES ONLY. Counts, dates,
directions, flags. Message content never leaves the scratchpad; the guard
test enforces the evidence shape.
"""
from __future__ import annotations

import datetime

# The only keys evidence may carry; the repo guard test pins this.
EVIDENCE_KEYS = frozenset({
    "msgs_in", "msgs_out", "last_message_at", "last_message_direction",
    "invite_out_personal", "invite_in", "endorsements_received",
    "endorsements_given", "recommendation_received", "recommendation_given",
    "warmth_prior", "email_inbound", "email_outbound", "email_last",
    "ci_tier",
})

MAX_EVIDENCE_STR = 40  # a date or a tier label, never prose


def _months_since(iso_date: str | None, now: datetime.date) -> float | None:
    if not iso_date:
        return None
    try:
        d = datetime.date.fromisoformat(iso_date[:10])
    except ValueError:
        return None
    return (now - d).days / 30.44


def compute_strength(sig: dict, now: datetime.date | None = None) -> tuple[int, dict]:
    """sig keys are a subset of EVIDENCE_KEYS; returns (score, evidence)."""
    now = now or datetime.date.today()
    ev = {k: sig.get(k) for k in EVIDENCE_KEYS if sig.get(k) is not None}
    score = 0

    msgs_in = int(sig.get("msgs_in") or 0)
    msgs_out = int(sig.get("msgs_out") or 0)
    last_msg = _months_since(sig.get("last_message_at"), now)
    if msgs_in and msgs_out:
        score += 30
        if last_msg is not None and last_msg <= 18:
            score += 15
    elif msgs_out:
        score += 10
    elif msgs_in:
        score += 8
    if last_msg is not None and last_msg <= 6:
        score += 10

    if sig.get("invite_out_personal"):
        score += 8
    if sig.get("invite_in"):
        score += 5
    if int(sig.get("endorsements_received") or 0) > 0:
        score += 5
    if int(sig.get("endorsements_given") or 0) > 0:
        score += 3
    if sig.get("recommendation_received") or sig.get("recommendation_given"):
        score += 10

    warmth = sig.get("warmth_prior")
    if warmth is not None:
        score += min(round(int(warmth) / 10), 10)
    if int(sig.get("email_inbound") or 0) and int(sig.get("email_outbound") or 0):
        score += 10
    last_email = _months_since(sig.get("email_last"), now)
    if last_email is not None and last_email <= 12:
        score += 5

    return max(0, min(100, score)), ev
