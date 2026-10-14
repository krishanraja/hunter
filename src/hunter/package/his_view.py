"""What he said about a role, for the documents written in his name.

Krish, 2026-10-14: "Make sure that, when you're creating all the packages, you
include all the rationales that I've put in the new column, as well as the
combined rationale. Increased understanding of who I am and what I want is
what makes it a more compelling personalised pitch for the packages: more
human without sounding arrogant, but sounding confident and like I can
deliver ASAP."

The new column is M, Krish Reaction: his own words about why a role fits
him. The combined rationale is that reaction beside hunter's own case in L,
Why AI thinks it Fits, which he read before he reacted. With his score in K,
those three are his view of the role, and this module is the one place a
package or an essay reads them.

What it keeps, and why:

- The sheet is the authority. K and M are read off his row as it reads now:
  a reaction he edited this morning is the one he means, and the record
  (ratings.latest) is an hour behind the sheet at best. The record is read
  only when there is no row to read, a build for a role whose row hunter
  could not find. A blank cell on a row he has is a blank, never a reason to
  go to the record, which would bring back a reaction he deleted.
- Empty stays empty (CLAUDE.md 1.3). A blank K is no score, "7.5" is no
  score either (ratings.whole_score, the parser his record is kept with), a
  blank M is no reaction, "Duplicate" in M is not a reason, and "Not
  assessed" in L is the sheet's fill default, not a case. Nothing is
  defaulted in, so an empty view leaves the prompt byte for byte as it was.
- His words go to the model writing his own application and nowhere else.
  Nothing here prints, logs or raises with his text in it: the repository
  and its Actions logs are public (CLAUDE.md 3f). Nothing here feeds a
  verdict path either; his reaction is not a ruling (ratings.py).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources

from ..sheet import AI_WHY, DEFAULTS_BY_NAME, KRISH_REACTION, KRISH_SCORE, says_duplicate

SHEET, RECORD = "sheet", "record"

# Which of his reasons a built package carries, on hunter_seen_roles: the
# fingerprint below, or null for a package written without any, or a held
# mark (HELD) for a rebuild tried on these inputs and held. Not
# package_trigger, which Control Center parses with an exact pattern
# ("Krish Score N") to put Prepare on a role, so a suffix there would take
# Prepare away from every package it marked. The column arrives with a
# migration (cc6); until it does, a build says it could not record this and
# rebuild-packages refuses to pay for rebuilds it could not then tell apart.
FIELD = "package_his_view"

# The section of prompts/tailor.md that carries his view. Kept in that file,
# between these two markers, so the words the model reads sit beside the rest
# of the prompt they belong to; cut out whole when he has said nothing, which
# is what keeps an empty view's prompt identical to the one before it.
START, END = "[[HIS_VIEW_START]]\n", "[[HIS_VIEW_END]]\n"
FACTS_SLOT = "[[HIS_VIEW_FACTS]]"


# A rebuild the gate held or verification failed writes this, a prefix and
# what it was tried on, in the same column, in place of a fingerprint: each
# try is a paid tailor call and two new documents, and a held rebuild chosen
# again unchanged would fail the same way every run and keep its place at the
# head of every --max batch. A changed reaction or score, a package rebuilt
# some other way, or a new link is a new try; --job-id is a try regardless.
HELD = "held:"


def held_mark(view_fingerprint: str, built_at: str | None, link: str | None) -> str:
    from ..run import norm_url
    blob = json.dumps([view_fingerprint or "", built_at or "", norm_url(link) or ""])
    return HELD + hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class HisView:
    score: int | None = None
    reaction: str = ""
    ai_case: str = ""
    # Where K and M were read: the sheet, the record, or nowhere ("").
    source: str = ""

    @property
    def empty(self) -> bool:
        """No score and no reaction. Hunter's case alone is not his view: it
        is what he was shown, and he has said nothing about it yet."""
        return self.score is None and not self.reaction

    @property
    def fingerprint(self) -> str:
        """What is recorded against a package written with this view: a hash,
        never his words, as hunter_krish_ratings keys his reactions."""
        if self.empty:
            return ""
        blob = json.dumps([self.score, self.reaction, self.ai_case])
        return "v1:" + hashlib.sha256(blob.encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {"score": self.score, "reaction": self.reaction, "ai_case": self.ai_case}


NONE = HisView()


def _case(text) -> str:
    t = (text or "").strip()
    return "" if t == DEFAULTS_BY_NAME[AI_WHY] else t


def _reaction(text) -> str:
    t = (text or "").strip()
    return "" if says_duplicate(t) else t


def from_row(row) -> HisView:
    """His view as his row reads now. K through the same parser his record
    and the build trigger use, so the number the prompt is told is the number
    that asked for the package."""
    from ..ratings import whole_score
    return HisView(score=whole_score(row.cell(KRISH_SCORE)),
                   reaction=_reaction(row.cell(KRISH_REACTION)),
                   ai_case=_case(row.cell(AI_WHY)), source=SHEET)


def from_record(rating: dict | None, record: dict | None = None) -> HisView:
    """His view from what hunter recorded, for a role with no row to read.
    Only a rating of kind scored is a score; a duplicate or an unreadable K
    is not. Hunter's case is the one written to the role's record."""
    from ..ratings import SCORED
    r = rating or {}
    score = r.get("score") if r.get("kind") == SCORED else None
    score = score if isinstance(score, int) and not isinstance(score, bool) else None
    reaction = _reaction(r.get("reaction"))
    found = score is not None or bool(reaction)
    return HisView(score=score, reaction=reaction,
                   ai_case=_case((record or {}).get("why_it_fits")),
                   source=RECORD if found else "")


def his_view(cfg, job_id: str, sheet_row, *, record: dict | None = None,
             latest: dict | None = None) -> HisView:
    """{score, reaction, ai_case} for one role.

    sheet_row is his row for this posting as just read, or None when there is
    none. latest is ratings.latest(cfg) already loaded, for a caller reading
    many roles; it is loaded here only when a row is missing. A record that
    cannot be read is no view, said by nothing: the caller carries on as it
    did before his columns existed."""
    if sheet_row is not None:
        return from_row(sheet_row)
    held = latest
    if held is None:
        try:
            from .. import ratings
            held = ratings.latest(cfg)
        except Exception:
            held = {}
    return from_record(held.get(job_id), record)


# The gate traces every number and every name against his CV and proof
# points alone, never against his note. His note was in the haystack once, so
# that a figure he gives about his own record would trace, and that let
# through everything else a note to himself carries: a contact by name, an
# employer missing from his CV, another employer's offer, a salary, his own
# rounded figure against the CV's exact one. The prompt tells the model to
# leave those out; the gate is what holds it to that.
#
# Which leaves the failures. A name or a figure refused because it came from
# his note would be quoted back in the failure ("name 'X' is not in the
# recorded evidence"), and those failures are stored as build flags and
# printed in run summaries and approval runs, which are public. So any
# failure quoting a word of his note is replaced by one that says what kind
# of thing it was, and nothing of what it said.
_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _in_note(value: str, low: str, compact: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return False
    if re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", low):
        return True
    # Figures as the gate compares them: "$4.2 M" and "$4.2m" are one figure.
    return any(c.isdigit() for c in v) and v.replace(",", "").replace(" ", "") in compact


def redact(failures: list[str], view: "HisView | None") -> list[str]:
    """The gate's failures with every word of his note taken out. A failure
    that quotes nothing of his stands as it was, so a refused name the model
    invented on its own is still named, for the regeneration and for him."""
    if view is None or not view.reaction:
        return list(failures)
    low = view.reaction.lower()
    compact = low.replace(",", "").replace(" ", "")
    out: list[str] = []
    for f in failures:
        quoted = [a or b for a, b in _QUOTED.findall(f)]
        if not any(_in_note(q, low, compact) for q in quoted):
            out.append(f)
            continue
        if f.startswith("number"):
            said = ("a figure from his note is not in the recorded evidence; leave "
                    "it out")
        elif f.startswith("name"):
            said = ("a name from his note is not in the recorded evidence; leave "
                    "it out")
        elif f.startswith("banned phrase"):
            said = "a banned phrase is in it; say it another way"
        else:
            said = "a word from his note was refused; leave it out"
        if said not in out:
            out.append(said)
    return out


# A run of his words this long, copied into the prose and found nowhere in his
# CV or proof points, is his note quoted. Seven, because a shorter run catches
# ordinary phrases ("the first ninety days of the"), and a longer one lets a
# whole clause of his through.
QUOTE_WORDS = 7


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%']+", (text or "").lower())


def quotes_note(text: str, view: HisView | None, evidence: str = "") -> bool:
    """True when the prose copies QUOTE_WORDS or more of his reaction word for
    word and that run is not also in the evidence. The prompt tells the model
    never to quote his note; this is the gate holding it to that, and the
    reason a line of his own never reaches a document, an email or the
    Actions log that prints the application."""
    if view is None or not view.reaction:
        return False
    note = _words(view.reaction)
    if len(note) < QUOTE_WORDS:
        return False
    body = " " + " ".join(_words(text)) + " "
    hay = " " + " ".join(_words(evidence)) + " "
    for i in range(len(note) - QUOTE_WORDS + 1):
        run = " " + " ".join(note[i:i + QUOTE_WORDS]) + " "
        if run in body and run not in hay:
            return True
    return False


# ---------- the prompt section ----------

def template() -> str:
    return resources.files("hunter").joinpath("prompts/tailor.md").read_text()


def split(text: str) -> tuple[str, str, str]:
    """(before, the section, after) of the tailor prompt."""
    i = text.index(START)
    j = text.index(END, i)
    return text[:i], text[i + len(START):j], text[j + len(END):]


def place(text: str, view: HisView | None) -> str:
    """The tailor template with the section kept, markers gone, when he has
    said something about the role, and cut out whole when he has not."""
    before, block, after = split(text)
    if view is None or view.empty:
        return before + after
    return before + block + after


def facts(view: HisView, *, piece_note: str = "") -> str:
    """What he said, labelled. Only what is there: a missing part is left
    out, never described as missing."""
    parts = []
    if view.score is not None:
        parts.append(f"His score for this role: {view.score} out of 10. It is for "
                     f"your understanding only; never mention it.")
    if view.reaction:
        parts.append('His reaction, in his own words:\n"""\n' + view.reaction + '\n"""')
    if view.ai_case:
        parts.append("Hunter's case for the role, which he read before he "
                     'reacted:\n"""\n' + view.ai_case + '\n"""')
    if piece_note:
        parts.append(piece_note)
    return "\n\n".join(parts)


def fill(text: str, view: HisView | None, *, piece_note: str = "") -> str:
    """The facts into the section. Called last on a finished prompt, so
    nothing he wrote is read as a template slot by a later replacement."""
    if view is None or view.empty:
        return text
    return text.replace(FACTS_SLOT, facts(view, piece_note=piece_note))


def section(view: HisView | None, *, company: str, piece_note: str = "") -> str:
    """The section alone, filled: what the essay writer's system text
    carries, with the same rules the package is held to. Empty for an empty
    view."""
    if view is None or view.empty:
        return ""
    _, block, _ = split(template())
    return fill(block.replace("[[COMPANY]]", company), view, piece_note=piece_note)
