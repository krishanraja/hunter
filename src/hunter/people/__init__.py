"""The bridge layer: who in Krish's network can warm a path into a target
role. It reads the LinkedIn data export he supplied plus the existing
contacts and contact_intelligence tables, keeps only professional fields and
aggregate evidence (never message bodies), and writes network_contacts and
bridge_candidates. It drafts asks; it never sends anything to anyone.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

_IN_PATH = re.compile(r"/in/([^/?#]+)")


def li_slug(url: str | None) -> str | None:
    """The /in/<slug> identity from any LinkedIn profile URL form, scheme
    optional, percent-encoding resolved, case folded."""
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if "//" not in u.split("?")[0][:12]:
        u = "https://" + u
    path = urlsplit(u).path
    m = _IN_PATH.search(path)
    if not m:
        return None
    return unquote(m.group(1)).strip().lower().rstrip("/") or None


def norm_name(s: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))
