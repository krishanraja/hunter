"""Which header a role takes, and the place and phone that go with it.

His ruling, 2026-10-15: a US or US-remote role lists Brooklyn and his US
number; a role in the UK, any European country, Europe or EMEA lists London and
his UK number; a role anywhere else, or one naming both, holds until he
decides. The letter, the CV and the form fields all agree. Until then every
form got the one Phone row in his Info Bank, so a London form was given his US
number.

Where a role is, is read from its location field, and from the posting only
where it says so in so many words ("Location: Berlin", "based in London",
"Remote (EMEA)"). Never from the posting's prose: "Join us" reads as the US to
sources.country_of, and so does every posting that says "us".

The numbers are not in this file. The repository is public, so the place,
phones and postal details of each base are read at runtime from system_config
BASES_KEY, and a failure says which base, never a digit.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

US, UK = "us", "uk"
BASES = (US, UK)
BASES_KEY = "hunter_contact_bases"

# The European countries sources.country_of can name. Turkey and Israel are
# left out on purpose: his rule names Europe and EMEA, and a posting in either
# is his to place.
EUROPE = frozenset({"ireland", "france", "germany", "netherlands", "spain", "portugal",
                    "italy", "switzerland", "sweden", "denmark", "norway", "finland",
                    "poland", "czech", "austria", "belgium"})
_REGION_UK = re.compile(r"\b(europe|european union|emea|eu)\b")

# Where a posting says where the role is, in so many words. Each capture is
# read as a location field would be, and nothing else in the posting is.
_SAYS_WHERE = (
    re.compile(r"\blocations?\s*[:\-]\s*([^\n.;|]{2,80})", re.I),
    # Place names only: capitalised words, so "based in New York and we ..."
    # reads "New York" and never the "we" or the "us" after it.
    re.compile(r"\b(?:[Bb]ased|[Ll]ocated)\s+in\s+((?:[A-Z][\w'-]*,?\s*){1,4})"),
    re.compile(r"\bremote\s*\(([^)\n]{2,40})\)", re.I),
)

HOLD_BOTH = "the role names places in both the US and the UK or Europe"
HOLD_ELSEWHERE = "the role is outside the US, the UK and Europe"
HOLD_UNKNOWN = "the role does not say where it is"


def _bases_named(text: str) -> tuple[set[str], bool]:
    """({"us", "uk"} the text places the role in, whether it names any place)."""
    from ..sources import country_of
    low = " ".join((text or "").lower().split())
    countries = set(country_of(low))
    out: set[str] = set()
    if _REGION_UK.search(low):
        out.add(UK)
    for c in countries:
        if c == "us":
            out.add(US)
        elif c == "uk" or c in EUROPE:
            out.add(UK)
    return out, bool(countries or out)


def base_for(location: str, jd_text: str = "") -> tuple[str, str]:
    """(US, UK or "" to hold, why in plain words with no digits).

    The location field first. The posting is read only when the location
    names no place at all, and then only its explicit location phrases."""
    named, placed = _bases_named(location)
    if not placed and jd_text:
        for pat in _SAYS_WHERE:
            for m in pat.finditer(jd_text):
                got, hit = _bases_named(m.group(1))
                named |= got
                placed = placed or hit
    if len(named) == 1:
        key = next(iter(named))
        return key, f"the role is in the {key.upper()}" if key == US else \
            "the role is in the UK or Europe"
    if len(named) == 2:
        return "", HOLD_BOTH
    return "", HOLD_ELSEWHERE if placed else HOLD_UNKNOWN


@dataclass(frozen=True)
class Base:
    """One of his two contact bases, as system_config holds it."""
    key: str
    place: str                 # the header's place, as he writes it
    phone_display: str         # the header's phone, as he writes it
    phone_international: str   # what a form's phone field gets
    country: str
    city: str = ""
    postal: str = ""
    state: str = ""
    street: str = ""

    def header_line(self) -> str:
        return f"{self.place}  ·  {self.phone_display}"


_REQUIRED = ("place", "phone_display", "phone_international", "country")
_OPTIONAL = ("city", "postal", "state", "street")


def load_bases(cfg) -> tuple[dict[str, Base], str]:
    """({key: Base} for every base system_config holds whole, a note in
    counts only). A base missing a required field is left out rather than
    filled in: a header with no phone is not a header."""
    raw = (getattr(cfg, "raw", None) or {}).get(BASES_KEY)
    if not raw:
        return {}, f"system_config {BASES_KEY} is not set"
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}, f"system_config {BASES_KEY} is not valid JSON"
    if not isinstance(data, dict):
        return {}, f"system_config {BASES_KEY} is not an object"
    out: dict[str, Base] = {}
    for key in BASES:
        d = data.get(key)
        if not isinstance(d, dict) or not all(str(d.get(f) or "").strip() for f in _REQUIRED):
            continue
        out[key] = Base(key=key, **{f: str(d[f]).strip() for f in _REQUIRED},
                        **{f: str(d.get(f) or "").strip() for f in _OPTIONAL})
    missing = [k.upper() for k in BASES if k not in out]
    return out, (f"{len(out)} contact base(s) read"
                 + (f"; missing or incomplete: {', '.join(missing)}" if missing else ""))


def digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


_PHONE_LABEL = (" phone", "phone number", "mobile number", "mobile phone", "telephone")
_PLACE_LABEL = ("city", "location", "address", "country", "state", "province",
                "zip", "postal", "postcode", "reside", "based", "where are you")


def is_phone_field(kind: str, label: str) -> bool:
    low = " " + " ".join((label or "").lower().split()) + " "
    if any(w in low for w in ("phone screen", "phone interview", "phone call")):
        return False
    return kind == "phone" or any(w in low for w in _PHONE_LABEL)


def check_plan(plan, base: Base, bases: dict[str, Base]) -> list[str]:
    """What in a fill plan disagrees with the role's base: a phone field
    without its number, any field or essay carrying the other base's number,
    or a place field naming the other base's place. Plain words, no digits:
    the lines reach the run log."""
    problems: list[str] = []
    own = digits(base.phone_international)
    others = [b for k, b in bases.items() if k != base.key]
    texts = [(f.kind, f.label, f.value or "") for f in plan.fields] + \
        [("long_text", q, a or "") for q, a in (plan.essays or {}).items()]
    for kind, label, value in texts:
        if not value:
            continue
        if is_phone_field(kind, label) and digits(value) != own:
            problems.append(f"a phone field does not carry the {base.key.upper()} number")
        for o in others:
            theirs = {digits(o.phone_international), digits(o.phone_display)}
            if any(t and len(t) >= 7 and t in digits(value) for t in theirs):
                problems.append(f"a field carries the {o.key.upper()} number")
            low = " " + (label or "").lower() + " "
            if kind != "long_text" and any(w in low for w in _PLACE_LABEL):
                v = value.lower()
                if any(p and re.search(rf"\b{re.escape(p.lower())}\b", v)
                       for p in (o.city, o.place)):
                    problems.append(f"a place field names the {o.key.upper()} place")
    return sorted(set(problems))
