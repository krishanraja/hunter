"""Ingest the LinkedIn data export plus the existing Supabase graph into
network_contacts. The export's Connections.csv is the ground truth of who is
connected; the linkedin_connections table (older sync) fills anyone the
export dropped; contacts and contact_intelligence supply priors by slug or
normalized email. Message CONTENT is read only to measure counts and dates
and is never written anywhere.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

from ..config import Config, db_get, db_insert
from . import li_slug, norm_name
from .strength import compute_strength

SELF_SLUGS = {"krish-raja"}


def _find_export_dir(root: str) -> Path:
    p = Path(root)
    if (p / "Connections.csv").exists():
        return p
    hits = list(p.glob("**/Connections.csv"))
    if not hits:
        raise FileNotFoundError(f"no Connections.csv under {root}")
    return hits[0].parent


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _date(raw: str | None, fmt: str) -> str | None:
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw.strip(), fmt).date().isoformat()
    except ValueError:
        return None


def parse_connections(export_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in _read_csv(export_dir / "Connections.csv"):
        slug = li_slug(row.get("URL"))
        if not slug:
            continue
        out[slug] = {
            "contact_key": slug,
            "linkedin_url": f"https://www.linkedin.com/in/{slug}",
            "first_name": (row.get("First Name") or "").strip(),
            "last_name": (row.get("Last Name") or "").strip(),
            "full_name": " ".join(x for x in [(row.get("First Name") or "").strip(),
                                              (row.get("Last Name") or "").strip()] if x),
            "email": (row.get("Email Address") or "").strip() or None,
            "current_company": (row.get("Company") or "").strip() or None,
            "current_title": (row.get("Position") or "").strip() or None,
            "connected_on": _date(row.get("Connected On"), "%d-%b-%y"),
            "source": "linkedin_export_2026_08",
        }
    return out


def parse_messages(export_dir: Path) -> dict[str, dict]:
    """Per-counterpart aggregates from 1:1 conversations only."""
    agg: dict[str, dict] = {}
    for row in _read_csv(export_dir / "messages.csv"):
        if (row.get("IS MESSAGE DRAFT") or "").strip().lower() == "yes":
            continue
        recips = (row.get("RECIPIENT PROFILE URLS") or "").strip()
        if not recips or "," in recips or ";" in recips:
            continue  # group threads carry no per-person signal we trust
        sender = li_slug(row.get("SENDER PROFILE URL"))
        recip = li_slug(recips)
        if not sender or not recip:
            continue
        if sender in SELF_SLUGS:
            other, direction = recip, "out"
        elif recip in SELF_SLUGS:
            other, direction = sender, "in"
        else:
            continue
        a = agg.setdefault(other, {"msgs_in": 0, "msgs_out": 0,
                                   "last_message_at": None,
                                   "last_message_direction": None})
        a["msgs_in" if direction == "in" else "msgs_out"] += 1
        when = (row.get("DATE") or "")[:10]
        if when and (a["last_message_at"] is None or when > a["last_message_at"]):
            a["last_message_at"] = when
            a["last_message_direction"] = direction
    return agg


def parse_endorsements(export_dir: Path) -> dict[str, dict]:
    agg: dict[str, dict] = {}
    for fname, key in (("Endorsement_Received_Info.csv", "endorsements_received"),
                       ("Endorsement_Given_Info.csv", "endorsements_given")):
        for row in _read_csv(export_dir / fname):
            status = (row.get("Endorsement Status") or "ACCEPTED").strip().upper()
            if status != "ACCEPTED":
                continue
            url = row.get("Endorser Public Url") or row.get("Endorsee Public Url")
            slug = li_slug(url)
            if not slug:
                continue
            agg.setdefault(slug, {})[key] = agg.get(slug, {}).get(key, 0) + 1
    return agg


def parse_invitations(export_dir: Path) -> dict[str, dict]:
    agg: dict[str, dict] = {}
    for row in _read_csv(export_dir / "Invitations.csv"):
        direction = (row.get("Direction") or "").strip().upper()
        if direction == "OUTGOING":
            slug = li_slug(row.get("inviteeProfileUrl"))
            if slug and len((row.get("Message") or "").strip()) >= 20:
                agg.setdefault(slug, {})["invite_out_personal"] = True
        elif direction == "INCOMING":
            slug = li_slug(row.get("inviterProfileUrl"))
            if slug:
                agg.setdefault(slug, {})["invite_in"] = True
    return agg


def parse_recommendations(export_dir: Path) -> tuple[set[str], set[str]]:
    """Name-keyed; the export carries no profile URL for recommendations."""
    received, given = set(), set()
    for fname, bucket in (("Recommendations_Received.csv", received),
                          ("Recommendations_Given.csv", given)):
        for row in _read_csv(export_dir / fname):
            n = norm_name(f"{row.get('First Name', '')} {row.get('Last Name', '')}")
            if n:
                bucket.add(n)
    return received, given


def _page_db(cfg: Config, table: str, select: str, extra: dict | None = None,
             page: int = 1000) -> list[dict]:
    rows, offset = [], 0
    while True:
        params = {"select": select, "limit": str(page), "offset": str(offset)}
        params.update(extra or {})
        batch = db_get(cfg, table, params)
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page


def db_priors(cfg: Config) -> tuple[dict[str, dict], dict[str, dict]]:
    """(slug -> contacts row, contact_id -> contact_intelligence row)."""
    by_slug: dict[str, dict] = {}
    contacts = _page_db(cfg, "contacts", "id,linkedin_url_norm,email_normalized",
                        {"linkedin_url_norm": "not.is.null"})
    for c in contacts:
        slug = li_slug(c.get("linkedin_url_norm"))
        if slug:
            by_slug[slug] = c
    ci = _page_db(cfg, "contact_intelligence",
                  "contact_id,warmth,email_inbound,email_outbound,email_last,network_tier")
    return by_slug, {r["contact_id"]: r for r in ci}


def ingest(cfg: Config, export_root: str, now: datetime.date | None = None) -> dict:
    export_dir = _find_export_dir(export_root)
    conns = parse_connections(export_dir)
    msgs = parse_messages(export_dir)
    endo = parse_endorsements(export_dir)
    inv = parse_invitations(export_dir)
    rec_received, rec_given = parse_recommendations(export_dir)

    # the older synced table fills connections the fresh export lacks
    for row in _page_db(cfg, "linkedin_connections",
                        "linkedin_slug,linkedin_url,first_name,last_name,"
                        "full_name,email,company,position,connected_on"):
        slug = (row.get("linkedin_slug") or "").strip().lower() or li_slug(row.get("linkedin_url"))
        if not slug or slug in conns:
            continue
        conns[slug] = {
            "contact_key": slug,
            "linkedin_url": row.get("linkedin_url") or f"https://www.linkedin.com/in/{slug}",
            "first_name": row.get("first_name") or "",
            "last_name": row.get("last_name") or "",
            "full_name": row.get("full_name") or slug,
            "email": row.get("email"),
            "current_company": row.get("company"),
            "current_title": row.get("position"),
            "connected_on": row.get("connected_on"),
            "source": "linkedin_connections_table",
        }

    contacts_by_slug, ci_by_id = db_priors(cfg)
    upserts = []
    for slug, base in conns.items():
        sig: dict = {}
        sig.update(msgs.get(slug, {}))
        sig.update(endo.get(slug, {}))
        sig.update(inv.get(slug, {}))
        n = norm_name(base["full_name"])
        if n and n in rec_received:
            sig["recommendation_received"] = True
        if n and n in rec_given:
            sig["recommendation_given"] = True
        contact = contacts_by_slug.get(slug)
        ci = ci_by_id.get(contact["id"]) if contact else None
        if ci:
            sig["warmth_prior"] = ci.get("warmth")
            sig["email_inbound"] = ci.get("email_inbound")
            sig["email_outbound"] = ci.get("email_outbound")
            sig["email_last"] = ci.get("email_last")
            sig["ci_tier"] = ci.get("network_tier")
        score, evidence = compute_strength(sig, now=now)
        row = dict(base)
        row["strength_score"] = score
        row["strength_evidence"] = evidence
        row["contact_id"] = contact["id"] if contact else None
        row["ci_matched"] = ci is not None
        row["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        upserts.append(row)

    for i in range(0, len(upserts), 200):
        db_insert(cfg, "network_contacts", upserts[i:i + 200],
                  on_conflict="contact_key", merge=True)
    return {"connections": len(conns), "with_messages": len(msgs),
            "ci_matched": sum(1 for u in upserts if u["ci_matched"]),
            "strength_50_plus": sum(1 for u in upserts if u["strength_score"] >= 50)}
