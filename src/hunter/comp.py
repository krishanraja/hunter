"""Reading pay out of a posting that did not put it in a field.

Measured 2026-09-20 across the 5,096 roles September sourced: 8 percent carried
any pay string at all, and of the 164 that reached Krish's sheet, 13 did.

That is why a $140,000 seat and a $350,000 seat look identical to this system.
Canon 9.3 auto-rejects a band bottom below the $200,000 floor and canon 9.2
pays a point for a bottom over $250,000, and both were dead letters on 92
percent of roles because G2 reads an absent band as "flag for review" rather
than as a fail, which is correct and also means nothing is ever filtered on
pay.

Only Ashby ever populated the field. Greenhouse, Lever, the a16z board and the
LinkedIn sweep all leave it empty while very often stating the band in the body
of the posting, because US pay transparency law requires it. So this reads the
body.

The hard part is not finding dollars, it is not finding the WRONG dollars. A
posting says "$2B in annual revenue", "we raised $50M", "a $10M quota" and
"OTE up to $400K" in the same paragraph as the band. So a match has to look
like a salary and sit near a salary word, and anything that does not is left
alone. An absent band stays absent: a wrong number here would auto-reject a
role Krish wants, which is far worse than reading nothing.
"""
from __future__ import annotations

import re

# Words that mark the sentence as being about this job's pay. US transparency
# clauses are formulaic, and these are the formulas.
SALARY_CONTEXT = re.compile(
    r"salary|base pay|base compensation|compensation range|pay range|"
    r"salary range|base salary|annual salary|pay band|compensation band|"
    r"cash compensation|target compensation|\bote\b|on.target earnings|"
    r"expected (?:pay|salary|compensation)|compensation for this|"
    r"the (?:pay|salary) range|base range|annual base", re.I)

# Words that mark a number as NOT this job's pay.
NOT_SALARY = re.compile(
    r"revenue|\barr\b|\bacv\b|funding|raised|valuation|quota|pipeline|"
    r"book of business|bookings|market (?:size|cap)|budget|portfolio|"
    r"assets under management|\baum\b|transaction|deal size|"
    r"savings|cost|spend", re.I)

# A salary-shaped amount: $150,000 or $150K or 150,000 USD.
AMOUNT = re.compile(
    r"(?:\$|usd\s*|gbp\s*|£|€)\s*"
    r"(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s*[kK]\b|\d{6})"
    r"|(\d{2,3}(?:,\d{3})+|\d{3},\d{3})\s*(?:usd|gbp|per year|annually|/yr)",
    re.I)

# A plausible band for the seats canon section 5 describes. Outside this, the
# number is something else: an hourly rate, a signing bonus, a revenue figure.
MIN_PLAUSIBLE = 40_000
MAX_PLAUSIBLE = 2_000_000

# How far from a salary word a number may sit and still be about pay. One
# sentence, roughly.
WINDOW = 220


def _to_int(raw: str) -> int | None:
    s = (raw or "").strip().lower().replace(",", "").replace(" ", "")
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except (TypeError, ValueError):
        return None


SYMBOL = {"$": "$", "usd": "$", "£": "£", "gbp": "£",
          "€": "€", "eur": "€"}

# The two halves of a stated range, close together: "$250,000 - $300,000",
# "250K to 300K", "$250,000-$300,000".
JOINER = re.compile(r"^\s*(?:-|\u2013|\u2014|to|through|up to)\s*$", re.I)
JOIN_GAP = 24


def _currency(body: str, start: int) -> str:
    """The symbol immediately before an amount, defaulting to dollars."""
    lead = body[max(0, start - 6):start + 6].lower()
    for token, sym in SYMBOL.items():
        if token in lead:
            return sym
    return "$"


def _hits(text: str) -> list[tuple[int, int, int, str]]:
    """(value, start, end, symbol) for every plausible salary figure that
    sits in a pay sentence."""
    body = text or ""
    out = []
    for m in AMOUNT.finditer(body):
        raw = m.group(1) or m.group(2)
        value = _to_int(raw)
        if value is None or not (MIN_PLAUSIBLE <= value <= MAX_PLAUSIBLE):
            continue
        window = body[max(0, m.start() - WINDOW):m.end() + WINDOW]
        if not SALARY_CONTEXT.search(window):
            continue
        # The nearest disqualifying word wins only when it is closer than the
        # nearest salary word, so "the $400K compensation range" survives a
        # "revenue" three sentences away.
        near = body[max(0, m.start() - 90):m.end() + 90]
        if NOT_SALARY.search(near) and not SALARY_CONTEXT.search(near):
            continue
        out.append((value, m.start(), m.end(), _currency(body, m.start())))
    return out


def stated_range(text: str) -> tuple[int, int, str] | None:
    """The two halves of a range the posting actually writes as a range.

    This is preferred over collecting every figure in the paragraph, because
    collecting swept up a signing bonus: "A signing bonus of $50,000 is
    available. The base pay range is $230,000 - $270,000" produced
    "$50,000 - $270,000". A range is two amounts with a dash or the word "to"
    between them and nothing else.
    """
    body = text or ""
    hits = _hits(body)
    best = None
    for (lo_v, _ls, lo_e, lo_sym), (hi_v, hi_s, _he, hi_sym) in zip(hits, hits[1:]):
        if hi_s - lo_e > JOIN_GAP:
            continue
        if not JOINER.match(body[lo_e:hi_s]):
            continue
        if hi_v < lo_v:
            continue
        sym = lo_sym if lo_sym == hi_sym else lo_sym
        if best is None or (hi_v - lo_v) > (best[1] - best[0]):
            best = (lo_v, hi_v, sym)
    return best


def extract(text: str) -> str:
    """A comp string in the shape the sheet already uses, or "".

    A range the posting states as a range wins. Failing that, the figures in
    its pay sentences. Never guesses: no pay sentence means no string, and
    the currency is preserved rather than assumed, because rendering
    "£180,000 - £220,000" as dollars is a different number.
    """
    band = stated_range(text)
    if band:
        lo, hi, sym = band
        return f"{sym}{lo:,} - {sym}{hi:,}"
    hits = _hits(text)
    if not hits:
        return ""
    values = [h[0] for h in hits]
    sym = hits[0][3]
    lo, hi = min(values), max(values)
    if lo == hi:
        return f"{sym}{lo:,}"
    return f"{sym}{lo:,} - {sym}{hi:,}"


def amounts_near_salary_words(text: str) -> list[int]:
    """Kept for callers that only want the figures."""
    return [h[0] for h in _hits(text)]


def best(structured: str | None, jd_text: str | None) -> str:
    """What the posting says about pay, preferring the structured field.

    A structured field is the employer's own answer and is always right. The
    body is read only when there is no field, and only to fill a blank.
    """
    have = (structured or "").strip()
    if have and have.lower() not in ("not disclosed", "not stated", "not posted",
                                     "n/a", "unknown", "none", "-"):
        return have
    return extract(jd_text or "")
