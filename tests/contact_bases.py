"""Invented contact bases for the offline suite. Never his: the numbers are in
the ranges reserved for fiction (US 555-01xx, UK 07700 900xxx)."""
import json

from hunter.package.contact import BASES_KEY, Base

US_BASE = Base(key="us", place="Brooklyn, NY", phone_display="+1 (212) 555-0142",
               phone_international="+1 212 555 0142", city="Brooklyn", country="United States",
               postal="11222", state="New York", street="1 Invented Street")
UK_BASE = Base(key="uk", place="London", phone_display="07700 900123",
               phone_international="+44 7700 900123", city="London", country="United Kingdom",
               postal="EC1A 1BB")
BASES = {"us": US_BASE, "uk": UK_BASE}


def raw_bases() -> str:
    """BASES as system_config holds them."""
    return json.dumps({k: {f: getattr(b, f) for f in ("place", "phone_display",
                                                      "phone_international", "city",
                                                      "country", "postal", "state", "street")}
                       for k, b in BASES.items()})
