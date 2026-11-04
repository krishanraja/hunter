"""His standing: what makes the reader shortlist him before they read a bullet.

His words, 2026-11-04: "I honestly don't think you make me sound impressive
enough on any application." The three letters written just before read as a
candidate answering a checklist: each opened by restating what the posting
said the company needed, each bullet ended by mapping itself back to an ask,
his title and his rank were nowhere, and one volunteered that he had been
wrong. Every figure was true. The posture was an applicant's.

Two halves:

- The STANDING block the writer leads with. It comes only from his Info Bank
  rows named "Standing: ..." (his headline, his titles, his board and advisory
  seats, who backed him, press, talks, awards), in his words, so nothing here
  is a claim the code makes and the voice gate traces every word, as it does
  the rest of the bank. No row, no block: never a stand-in.
- A19 (`problems`), the mechanical half: a letter that opens by restating the
  job, maps a part back to the posting, hedges or apologises, or never says
  his rank is refused, and the routine writes it again.
"""
from __future__ import annotations

import re

PREFIX = "standing:"
# The row that names his employers, comma separated, for the rank check. With
# no such row the employer half of the check is skipped: never block on no
# evidence.
EMPLOYERS_ROW = "standing: employers"


def rows(entries) -> list[tuple[str, str]]:
    """(label, his words) for every usable Standing row on the Info Bank."""
    out = []
    for e in (entries or {}).values():
        name = (getattr(e, "field_name", "") or "").strip()
        value = (getattr(e, "value", "") or "").strip()
        if name.lower().startswith(PREFIX) and value and getattr(e, "usable", True):
            out.append((name.split(":", 1)[1].strip(), value))
    return out


def employers(entries) -> tuple[str, ...]:
    for e in (entries or {}).values():
        if (getattr(e, "field_name", "") or "").strip().lower() == EMPLOYERS_ROW:
            return tuple(x.strip() for x in (getattr(e, "value", "") or "").split(",")
                         if x.strip())
    return ()


def block(entries) -> str:
    """The section the writer leads with, or "" when he has recorded none."""
    have = [(k, v) for k, v in rows(entries) if k.lower() != "employers"]
    if not have:
        return ""
    return "\n".join(f"- {k}: {v}" for k, v in have)


# ---------- A19 ----------

_ASKS = ("needs", "need", "wants", "want", "is looking for", "are looking for",
         "seeks", "is seeking", "is hiring", "hires", "is searching for")
ECHO = ("the posting", "the job description", "the role asks", "this role asks",
        "the first ask", "the second ask", "what this seat needs", "this seat needs",
        "the seat needs", "as the posting", "the jd", "your posting", "you ask for",
        "you are asking for", "you're asking for")
HEDGES = ("i was wrong", "and was wrong", "although i have not", "although i haven't", "while i lack",
          "i may not", "despite not", "i don't have direct", "i do not have direct",
          "i lack", "i haven't yet", "i have not yet", "admittedly", "i'm not an expert",
          "i am not an expert")
TITLES = re.compile(
    r"\b(head of|director|vp|vice president|chief|managing director|md|general manager|"
    r"gm|founder|co-?founder|partner|president|lead of|country manager|regional|"
    r"commercial lead|chief of staff)\b", re.I)
HEADLINE = re.compile(r"(\$\s?\d|\b\d+\s?(?:\+\s?)?(?:years?|countries|markets|"
                      r"organi[sz]ations|businesses|companies|people)\b|\b\d+x\b)", re.I)


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)[0]


def _low(text: str) -> str:
    return " ".join((text or "").lower().replace("’", "'").split())


def problems(letter, companies: frozenset[str], his_employers: tuple[str, ...] = ()) -> list[str]:
    """A19: the letter at his rank. Part names and kinds only, never his words."""
    out = []
    names = sorted({_low(c) for c in companies if c}, key=len, reverse=True)
    for part in ("hook", "why_now"):
        first = _low(_first_sentence(getattr(letter, part, "")))
        for n in names:
            stem = re.escape(n) + r"(?:'s)?"
            # "Acme needs", and "Acme's team is looking for": one word between.
            if re.match(rf"{stem}(?:\s+\w+)?\s+(?:{'|'.join(re.escape(a) for a in _ASKS)})\b",
                        first):
                out.append(f"A19 the {part} opens by restating what the company needs; "
                           f"open on him")
                break
    asked = tuple(f"{n} asks" for n in names) + tuple(f"{n} wants" for n in names)
    for name, text in letter.parts() + [("leader note", getattr(letter, "leader_note", ""))]:
        low = _low(text)
        if any(e in low for e in ECHO + asked):
            out.append(f"A19 the {name} maps itself back to the posting; state the result "
                       f"and let the reader connect it")
        if any(h in low for h in HEDGES):
            out.append(f"A19 the {name} hedges or apologises; never volunteer a weakness")
    ident = letter.identity or ""
    if not TITLES.search(ident):
        out.append("A19 the identity never says his rank; name the title he held")
    if his_employers:
        hay = _low(ident + " " + letter.hook)
        if not any(_low(e) in hay for e in his_employers):
            out.append("A19 neither the hook nor the identity names an employer of his")
    if not HEADLINE.search(" ".join([letter.hook, ident, letter.cv_summary])):
        out.append("A19 no headline figure in the hook, identity or CV summary; give the "
                   "scale of his record")
    return out
