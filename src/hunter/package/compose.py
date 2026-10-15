"""The letter written for the posting, in his house style (L5).

His rulings, 2026-10-15: everything but the header and the sign-off is
written per role, with his edited Sonos letter as the model; the letter is
one page; UK spelling for UK and Europe roles; one line of his own for the
kinds of business he named; and no API spend on it. So the words come from
the writing routine on his subscription (drafts.py, kind "letter"), and this
module is everything hunter does around them, all of it free:

  request     the per-role prompt and the shared context the routine reads
  problems    whether an answer may be used: its shape, its quotes of the
              posting, the register (A13), his personal line (A14) and the
              evidence (A15); the routine runs the same check before it
              writes back (python -m hunter.drafts check)
  spell       US or UK spelling for the role's base, from a curated table
  fill        the slot requests for the v5 master, his styles kept whole,
              the bullet labels bold
  build       copy, fill, fit one page, read back (house.check plus the
              read-back of A13 and A14), for the letter and the CV

Nothing here prints a word of the letter: the callers log codes and counts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import house, masters, personal

KIND = "letter"

# Lengths, from his Sonos letter, which fits one page at the v5 margins. The
# routine is told these; the page-fit ladder is the backstop.
LIMITS = {"hook": 600, "identity": 520, "why_now": 340, "close": 380,
          "label": 48, "bullet": 260, "cv_summary": 900}
BODY_BUDGET = 2400

SCHEMA = {
    "type": "object",
    "required": ["hiring_lead", "hook", "identity", "bullets", "why_now", "close", "asks"],
    "properties": {
        "hiring_lead": {"type": "string"},
        "hook": {"type": "string"},
        "identity": {"type": "string"},
        "bullets": {"type": "array", "minItems": 3, "maxItems": 4, "items": {
            "type": "object", "required": ["label", "text"],
            "properties": {"label": {"type": "string"}, "text": {"type": "string"}}}},
        "why_now": {"type": "string"},
        "close": {"type": "string"},
        "cv_summary": {"type": "string"},
        "asks": {"type": "array", "items": {
            "type": "object", "required": ["ask", "quote", "answered_by"],
            "properties": {"ask": {"type": "string"}, "quote": {"type": "string"},
                           "answered_by": {"type": "string"}}}},
    },
}


class ComposeError(RuntimeError):
    """Plain words and codes; never a word of the letter."""


@dataclass
class Letter:
    hiring_lead: str
    hook: str
    identity: str
    bullets: list[tuple[str, str]]
    why_now: str
    close: str
    cv_summary: str = ""
    asks: list[dict] = field(default_factory=list)

    def prose(self) -> list[str]:
        return [self.hook, self.identity, self.why_now, self.close]

    def spans(self) -> list[str]:
        return self.prose() + [f"{a} {b}" for a, b in self.bullets]

    def body_chars(self) -> int:
        return sum(len(s) for s in self.spans())


def _one_line(s) -> str:
    return " ".join(str(s or "").split())


def parse(output) -> Letter:
    data = output if isinstance(output, dict) else json.loads(output or "{}")
    if not isinstance(data, dict):
        raise ValueError("not an object")
    bullets = []
    for b in data.get("bullets") or []:
        if not isinstance(b, dict):
            raise ValueError("a bullet is not an object")
        bullets.append((_one_line(b.get("label")), _one_line(b.get("text"))))
    asks = [a for a in (data.get("asks") or []) if isinstance(a, dict)]
    return Letter(hiring_lead=_one_line(data.get("hiring_lead")) or "Hiring Team",
                  hook=_one_line(data.get("hook")), identity=_one_line(data.get("identity")),
                  bullets=bullets, why_now=_one_line(data.get("why_now")),
                  close=_one_line(data.get("close")),
                  cv_summary=_one_line(data.get("cv_summary")), asks=asks)


# ---------- spelling, by base ----------

# US form to UK form. Curated: only words whose UK form is unambiguous in a
# letter, never "program" (a programme of work, a computer program) and never
# "license" (a verb in both). Lowercase only: a capitalised word mid-sentence
# is a name ("World Health Organization") and is never touched.
_STEMS_IZE = (
    "organiz", "commercializ", "monetiz", "optimiz", "prioritiz", "realiz",
    "recogniz", "specializ", "standardiz", "utiliz", "maximiz", "minimiz",
    "mobiliz", "personaliz", "operationaliz", "productiz", "localiz", "globaliz",
    "visualiz", "industrializ", "harmoniz", "synchroniz", "categoriz", "customiz",
    "democratiz", "emphasiz", "summariz", "capitaliz", "authoriz", "finaliz",
    "incentiviz", "socializ", "strategiz", "evangeliz", "revolutioniz",
    "systematiz", "centraliz", "decentraliz", "moderniz", "normaliz",
    "energiz", "familiariz", "memoriz", "criticiz", "apologiz", "itemiz",
)
_IZE_ENDINGS = ("e", "es", "ed", "ing", "ation", "ations", "er", "ers")
_PAIRS = {
    "analyze": "analyse", "analyzed": "analysed", "analyzes": "analyses",
    "analyzing": "analysing", "catalyze": "catalyse", "catalyzed": "catalysed",
    "behavior": "behaviour", "behaviors": "behaviours", "behavioral": "behavioural",
    "favorite": "favourite", "favorites": "favourites", "honor": "honour",
    "honored": "honoured", "color": "colour", "colors": "colours",
    "defense": "defence", "catalog": "catalogue", "catalogs": "catalogues",
    "traveled": "travelled", "traveling": "travelling", "modeling": "modelling",
    "modeled": "modelled", "labeled": "labelled", "labeling": "labelling",
    "enrollment": "enrolment", "fulfill": "fulfil", "fulfillment": "fulfilment",
    "neighbor": "neighbour", "neighbors": "neighbours", "endeavor": "endeavour",
    "endeavors": "endeavours", "rumor": "rumour", "savor": "savour",
}


def _spelling_table() -> dict[str, str]:
    out = dict(_PAIRS)
    for stem in _STEMS_IZE:
        for end in _IZE_ENDINGS:
            us = stem + end
            uk = stem[:-1] + "s" + end
            out[us] = uk
    return out


US_TO_UK = _spelling_table()
UK_TO_US = {v: k for k, v in US_TO_UK.items()}
_WORD = re.compile(r"[A-Za-z]+")


def _spell(text: str, table: dict[str, str]) -> str:
    """Words in the table, lowercase or capitalised at a sentence start,
    respelled; any other capitalised word is a name and stays."""
    out, last = [], 0
    for m in _WORD.finditer(text or ""):
        w = m.group(0)
        low = w.lower()
        rep = None
        if low in table:
            if w == low:
                rep = table[low]
            elif w == low.capitalize() and re.search(r"(^|[.!?:]\s+)$", text[:m.start()]):
                rep = table[low].capitalize()
        out.append(text[last:m.start()])
        out.append(rep or w)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def spell(text: str, base_key: str) -> str:
    return _spell(text, US_TO_UK if base_key == "uk" else UK_TO_US)


def spelling_problems(text: str, base_key: str) -> list[str]:
    """A13 spelling, read back: a lowercase word spelled for the other base."""
    wrong = US_TO_UK if base_key == "uk" else UK_TO_US     # keys: the other base's words
    found = sorted({w for w in _WORD.findall(text or "") if w in wrong})
    if not found:
        return []
    side = "US" if base_key == "uk" else "UK"
    return [f"A13 {len(found)} word(s) spelled the {side} way for a "
            f"{base_key.upper()} role"]


# ---------- the register (A13) ----------

# Openers that drop the subject: "Would rather show you", "Built the team".
# His edit put the subject back every time ("I'd rather", "I hand built").
DROPPED_SUBJECT = frozenset("""
would could should have had has built led spent love loved happy keen looking
currently been am was can will did made grew scaled ran worked started joined
helped know think believe want hope bring brings bringing running leading
building shipped launched delivered drove driving managed own owned
""".split())
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'd|I'll|my|me)\b|\bI[’']", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+")


SUBJECT = re.compile(r"\b(I|I'm|I've|I'd|I'll|we|we're|we've)\b|\bI[’']", re.I)


def register_problems(letter: Letter) -> list[str]:
    """A sentence opening on a verb or a feeling with no subject anywhere in
    it ("Would rather show you", "Happy to share"), outside the bullets,
    which keep his own clipped wording."""
    out = []
    for name, text in (("hook", letter.hook), ("identity", letter.identity),
                       ("why_now", letter.why_now), ("close", letter.close)):
        for s in _SENT.split(text or ""):
            first = re.match(r"[A-Za-z']+", s.strip())
            if first and first.group(0).lower() in DROPPED_SUBJECT and not SUBJECT.search(s):
                out.append(f"A13 the {name} opens a sentence without its subject")
                break
    first_sentence = (_SENT.split(letter.identity or "") or [""])[0]
    if not re.search(r"\bI\b|\bI[’']|\bmy\b", first_sentence):
        out.append("A13 the identity paragraph does not open in the first person")
    return out


# ---------- his personal line (A14) ----------

_STOP = frozenset("""
a an the and or but of to in on for with at by from as is are was were be been
i i'm i've i'd my me this that these those it its who which would love like
have has had all life people
""".split())


def _content(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))
            if w not in _STOP and len(w) > 2]


def _shingles(text: str, n: int = 7) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def carries(text: str, entry_words: str, share: float = 0.8) -> bool:
    """Whether the text carries his line: most of its content words, so a
    company name or a tense may change and his line is still his line."""
    want = set(_content(entry_words))
    if not want:
        return False
    have = set(_content(text))
    return len(want & have) / len(want) >= share


def personal_problems(letter: Letter, needs: list[str], entries: list[str],
                      bank_words: list[str]) -> list[str]:
    """needs: the categories the business is in; entries: his words that fit
    it; bank_words: every entry in 9.14. Codes only, never his words."""
    prose = " ".join(letter.prose())
    if needs:
        if not entries:
            return ["A14 the business needs a line of his and 9.14 has none that fits"]
        if not any(carries(prose, w) for w in entries):
            return ["A14 his personal line for this business is missing"]
        return []
    whole = " ".join(letter.spans())
    shingles = _shingles(whole)
    if any(_shingles(w) & shingles for w in bank_words):
        return ["A14 a personal line of his is in a letter to a business it is not for"]
    return []


# ---------- the evidence (A15) and the shape ----------

def _norm_quote(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9$%]+", " ", (s or "").lower()).split())


def shape_problems(letter: Letter) -> list[str]:
    out = []
    for name in ("hook", "identity", "why_now", "close"):
        text = getattr(letter, name)
        if not text:
            out.append(f"shape: the {name} is empty")
        elif len(text) > LIMITS[name]:
            out.append(f"shape: the {name} is over {LIMITS[name]} characters")
    if len(letter.bullets) not in (3, 4):
        out.append(f"shape: {len(letter.bullets)} bullets, not 3 or 4")
    for i, (label, text) in enumerate(letter.bullets, 1):
        if not label or not text:
            out.append(f"shape: bullet {i} has no label or no text")
        elif len(label) > LIMITS["label"] or len(text) > LIMITS["bullet"]:
            out.append(f"shape: bullet {i} is too long")
    if letter.cv_summary and len(letter.cv_summary) > LIMITS["cv_summary"]:
        out.append("shape: the CV summary is too long")
    if letter.body_chars() > BODY_BUDGET:
        out.append(f"shape: the letter is over {BODY_BUDGET} characters")
    if "{{" in " ".join(letter.spans()) or "\n" in letter.hiring_lead:
        out.append("shape: a slot is left in the text")
    return out


def lead_problems(letter: Letter, jd_text: str) -> list[str]:
    """The letter is addressed to the hiring team, or to a person the posting
    names: never to a name the writer supplied."""
    if letter.hiring_lead == "Hiring Team":
        return []
    if _norm_quote(letter.hiring_lead) and _norm_quote(letter.hiring_lead) in _norm_quote(jd_text):
        return []
    return ["shape: the letter is addressed to a name the posting does not give"]


def asks_problems(letter: Letter, jd_text: str) -> list[str]:
    jd = _norm_quote(jd_text)
    out = []
    if not letter.asks:
        out.append("asks: none read from the posting")
    bad = sum(1 for a in letter.asks if not _norm_quote(str(a.get("quote") or ""))
              or _norm_quote(str(a.get("quote") or "")) not in jd)
    if bad:
        out.append(f"asks: {bad} quote(s) are not in the posting")
    return out


def evidence_problems(letter: Letter, *, evidence: str, banned: tuple[str, ...],
                      names: frozenset[str]) -> list[str]:
    from . import voicegate
    out = []
    spans = [("hook", letter.hook), ("identity", letter.identity), ("why_now", letter.why_now),
             ("close", letter.close)] + [(f"bullet {i}", f"{a} {b}")
                                          for i, (a, b) in enumerate(letter.bullets, 1)]
    if letter.cv_summary:
        spans.append(("cv_summary", letter.cv_summary))
    for name, text in spans:
        v = voicegate.check(voicegate.plain_prose(text), evidence=evidence,
                            banned_phrases=banned, allow_names=names)
        # Kinds of failure only: a failure line can carry a figure or a name.
        kinds = sorted({f.split(" ", 1)[0] for f in v.failures})
        if kinds:
            out.append(f"A15 the {name} fails the evidence gate ({', '.join(kinds)})")
    return out


def problems(output, *, prompt: str = "", evidence: str = "", meta: dict | None = None) -> list[str]:
    """Whether a letter answer may be used. Pure: the routine runs it too.

    meta carries what the answer is checked against: company, short_name,
    base ('us' or 'uk'), needs (categories), entries (his words that fit),
    bank (every 9.14 entry's words), banned, jd (the posting text)."""
    meta = meta or {}
    try:
        letter = parse(output)
    except (ValueError, TypeError, AttributeError) as e:
        return [f"the answer is not the letter schema: {e.__class__.__name__}"]
    base = meta.get("base") or "us"
    letter = respelled(letter, base)
    names = frozenset(n for n in (meta.get("company"), meta.get("short_name"),
                                  meta.get("role")) if n)
    # His own words from 9.14 are evidence: they are his.
    hay = "\n".join([evidence or "", *(meta.get("entries") or [])]).lower()
    out = shape_problems(letter)
    out += lead_problems(letter, meta.get("jd") or prompt)
    out += asks_problems(letter, meta.get("jd") or prompt)
    out += register_problems(letter)
    out += personal_problems(letter, list(meta.get("needs") or []),
                             list(meta.get("entries") or []), list(meta.get("bank") or []))
    out += evidence_problems(letter, evidence=hay, banned=tuple(meta.get("banned") or ()),
                             names=names)
    return out


def respelled(letter: Letter, base_key: str) -> Letter:
    return Letter(hiring_lead=letter.hiring_lead, hook=spell(letter.hook, base_key),
                  identity=spell(letter.identity, base_key),
                  bullets=[(spell(a, base_key), spell(b, base_key)) for a, b in letter.bullets],
                  why_now=spell(letter.why_now, base_key), close=spell(letter.close, base_key),
                  cv_summary=spell(letter.cv_summary, base_key), asks=letter.asks)


# ---------- the request the routine answers ----------

RULES = """You write one cover letter for Krish Raja, for one posting, in his own register.

His model is the letter he rewrote by hand (MODEL LETTER below): formal first person,
warm, specific. Follow it:
- Write in the first person throughout. Never drop the subject ("Would rather", "Built the
  team"): write "I'd rather", "I built". The identity paragraph opens with "I".
- hook: what this business is doing and why it is worth his attention, from the posting
  and the judge's read, then why he is the person for it. No flattery, no "I am excited".
- identity: who he is, in the first person, as his model letter says it, adapted to what
  this posting asks.
- bullets: three or four of his proof points, the ones that answer what this posting asks,
  each with a short bold label (an employer or a period, ending with a colon) and his own
  wording from the evidence. One clause may mirror the posting. Every figure and every
  name must be in the evidence as written.
- why_now: why he is looking, in one or two sentences, as his model says it.
- close: what he would bring to a first conversation, warm and specific.
- asks: what the posting asks for, each with an exact quote from the posting and which
  proof answers it (or "gap" when none does).
- cv_summary: two or three sentences for the top of his CV for this role, his facts only,
  or "" to keep his own.
- When PERSONAL LINE is given, the letter must carry it, his words as written, in the hook
  or the close. When none is given, never write anything personal about his life.
- Never write his email, phone or address; the header carries them.
- Spelling: as the BASE says (US or UK).
- Lengths: hook up to 600 characters, identity up to 520, each bullet up to 260 with a
  label up to 48, why_now up to 340, close up to 380; the whole letter fits one page.
Answer with one JSON object matching the schema, and nothing else."""


def strip_his_lines(text: str, bank_words: list[str]) -> str:
    """His model letter without any personal line of his, so a line written
    for one business never becomes the template for another."""
    keep = []
    for s in _SENT.split(text or ""):
        if any(carries(s, w, share=0.6) for w in bank_words):
            continue
        keep.append(s)
    return " ".join(keep)


def shared_context(*, model_letter: str, evidence_text: str, bank_words: list[str],
                   examples: str = "") -> str:
    parts = [
        RULES,
        "SCHEMA\n" + json.dumps(SCHEMA),
        "MODEL LETTER (his own, with his personal lines taken out)\n"
        + strip_his_lines(model_letter, bank_words),
        "EVIDENCE (his recorded facts; every figure and name must come from here or the posting)\n"
        + evidence_text,
    ]
    if examples:
        parts.append("HIS EDITS (newest first): what he changed in letters hunter wrote. Write "
                     "the way he edits; these are examples, never evidence\n" + examples)
    return "\n\n".join(parts)


def role_prompt(*, company: str, title: str, location: str, base_key: str, jd_text: str,
                judge_answers, his_view: str, entries: list[str], needs: list[str]) -> str:
    ja = judge_answers
    if isinstance(ja, str):
        try:
            ja = json.loads(ja)
        except ValueError:
            ja = {}
    parts = [f"COMPANY: {company}", f"ROLE: {title}", f"LOCATION: {location}",
             f"BASE: {'UK (UK spelling)' if base_key == 'uk' else 'US (US spelling)'}"]
    if needs:
        labels = ", ".join(personal.LABEL.get(c, c) for c in needs)
        parts.append(f"PERSONAL LINE (this is a {labels} business; carry one of these, "
                     f"his words as written):\n" + "\n".join(f"- {w}" for w in entries))
    if ja:
        parts.append("THE JUDGE'S READ\n" + json.dumps(ja, ensure_ascii=False)[:4000])
    if his_view:
        parts.append("HIS VIEW OF THIS ROLE\n" + his_view[:1500])
    parts.append("POSTING\n" + (jd_text or "")[:12000])
    return "\n\n".join(parts)


# ---------- filling the v5 master ----------

_OUTPUT_ONLY = ("link",)


def _clean_style(ts: dict) -> dict:
    return {k: v for k, v in (ts or {}).items() if k not in _OUTPUT_ONLY}


def _run_style(doc: dict, index: int) -> dict:
    """The textStyle of the run at a document index, as the document holds it."""
    for el in doc["body"]["content"]:
        p = el.get("paragraph")
        if not p:
            continue
        for e in p.get("elements", []):
            if "textRun" in e and e.get("startIndex", 0) <= index < e.get("endIndex", 0):
                return _clean_style(e["textRun"].get("textStyle") or {})
    return {}


def slot_values(letter: Letter, *, base, date_text: str, company: str, role: str) -> dict:
    vals = {masters.PLACE: base.place, masters.PHONE: base.phone_display,
            masters.DATE: date_text, masters.LEAD: letter.hiring_lead,
            masters.COMPANY: company, masters.ROLE: role, masters.HOOK: letter.hook,
            masters.IDENTITY: letter.identity, masters.WHY_NOW: letter.why_now,
            masters.CLOSE: letter.close}
    for n in range(1, 5):
        if n <= len(letter.bullets):
            label, text = letter.bullets[n - 1]
            vals[masters.bullet_slot(n)] = f"{label} {text}"
        else:
            vals[masters.bullet_slot(n)] = None      # the paragraph goes
    return vals


def fill_requests(doc: dict, letter: Letter, vals: dict) -> list[dict]:
    """Every slot paragraph rewritten with its own style whole, from the end
    backwards; a bullet label set bold; a slot with no value removed with its
    paragraph."""
    paras = house.snapshot(doc)
    labels = {masters.bullet_slot(n): letter.bullets[n - 1][0]
              for n in range(1, len(letter.bullets) + 1)}
    reqs: list[dict] = []
    for p in sorted(paras, key=lambda p: -p.start):
        if "{{" not in p.text:
            continue
        text = p.text.rstrip("\n")
        tokens = re.findall(r"\{\{[A-Z_0-9]+\}\}", text)
        unknown = [t for t in tokens if t not in vals]
        if unknown:
            raise ComposeError(f"the master has a slot hunter does not fill ({len(unknown)})")
        if any(vals[t] is None for t in tokens):
            reqs.append({"deleteContentRange": {"range": {"startIndex": p.start,
                                                          "endIndex": p.end}}})
            continue
        new = text
        for t in tokens:
            new = new.replace(t, vals[t])
        style = _run_style(doc, p.start)
        s, e = p.start, p.end - 1
        if e > s:
            reqs.append({"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}})
        reqs.append({"insertText": {"location": {"index": s}, "text": new}})
        reqs.append({"updateTextStyle": {"range": {"startIndex": s, "endIndex": s + len(new)},
                                         "textStyle": style, "fields": "*"}})
        label = next((labels[t] for t in tokens if t in labels), "")
        if label and new.startswith(label):
            reqs.append({"updateTextStyle": {
                "range": {"startIndex": s, "endIndex": s + len(label)},
                "textStyle": {"bold": True, "weightedFontFamily": {"fontFamily": house.FONT,
                                                                  "weight": 700}},
                "fields": "bold,weightedFontFamily"}})
    return reqs


def floor_margins() -> dict:
    style = {k: {"magnitude": masters.MARGIN_FLOOR, "unit": "PT"} for k in masters.MARGINS}
    return {"updateDocumentStyle": {"documentStyle": style, "fields": ",".join(masters.MARGINS)}}


def _paragraph_with(paras: list[house.Para], text: str) -> house.Para | None:
    want = house.norm(text)
    return next((p for p in paras if house.norm(p.text) == want), None)


@dataclass
class Built:
    doc_id: str = ""
    pages: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pdf: bytes | None = None


def read_back(doc: dict, letter: Letter, *, kind: str, base, bases: dict, pages: int,
              needs: list[str], entries: list[str], bank_words: list[str],
              heading_sizes: dict | None = None) -> list[str]:
    paras = house.snapshot(doc)
    text = " ".join(p.text for p in paras)
    out = house.check(paras, kind=kind, base=base, bases=bases, page_count=pages,
                      heading_sizes=heading_sizes)
    if "{{" in text:
        out.append(f"A12 a slot is left unfilled in the {kind}")
    out += spelling_problems(text, base.key)
    if kind == "letter":
        if needs and not any(carries(text, w) for w in entries):
            out.append("A14 his personal line is not in the letter as built")
        bullets = [p for p in paras if p.bullet]
        for p in bullets:
            label = next((r for r in p.runs if r.text.strip()), None)
            if label is None or not label.bold:
                out.append("A11 a bullet's label is not bold")
                break
        # The date, the recipient block and the Re: line are bold in his
        # letter; from the salutation on, only the bullet labels are.
        sal = next((i for i, p in enumerate(paras) if house.norm(p.text).startswith("Dear ")), None)
        for p in paras[(sal if sal is not None else len(paras)):]:
            if p.bullet or not p.text.strip():
                continue
            if any(r.bold for r in p.runs if r.text.strip()):
                out.append("A11 a body paragraph carries bold")
                break
    return out


def build_letter(db, letter: Letter, *, master_id: str, title: str, folder_id: str, base,
                 bases: dict, date_text: str, company: str, role: str, page_count,
                 needs: list[str], entries: list[str], bank_words: list[str]) -> Built:
    """Copy the v5 master, fill it, fit it to one page, read it back.

    The ladder, his order: the margins to the floor, then the fourth bullet,
    then why-now; a letter still over one page holds. The font is never made
    smaller and his words are never cut mid-sentence."""
    out = Built()
    letter = respelled(letter, base.key)
    out.doc_id = db.copy_master(master_id, title, folder_id)
    vals = slot_values(letter, base=base, date_text=date_text, company=company, role=role)
    db.batch(out.doc_id, fill_requests(db.get(out.doc_id), letter, vals))
    rungs = [("margins at the floor", lambda paras: [floor_margins()])]
    if len(letter.bullets) == 4:
        rungs.append(("the fourth bullet cut to fit one page",
                      lambda paras: _drop(paras, f"{letter.bullets[3][0]} {letter.bullets[3][1]}")))
    rungs.append(("why-now cut to fit one page", lambda paras: _drop(paras, letter.why_now)))
    pdf = db.export_pdf(out.doc_id)
    pages = page_count(pdf)
    for label, reqs in rungs:
        if pages <= house.LETTER_PAGES:
            break
        r = reqs(house.snapshot(db.get(out.doc_id)))
        if not r:
            continue
        db.batch(out.doc_id, r)
        out.notes.append(label)
        pdf = db.export_pdf(out.doc_id)
        pages = page_count(pdf)
    out.pages, out.pdf = pages, pdf
    out.problems = read_back(db.get(out.doc_id), letter, kind="letter", base=base, bases=bases,
                             pages=pages, needs=needs, entries=entries, bank_words=bank_words)
    return out


def _drop(paras: list[house.Para], text: str) -> list[dict]:
    p = _paragraph_with(paras, text)
    if p is None:
        return []
    return [{"deleteContentRange": {"range": {"startIndex": p.start, "endIndex": p.end}}}]


def cv_requests(doc: dict, *, base) -> list[dict]:
    """The CV master's contact slot filled, its grey style kept whole."""
    paras = house.snapshot(doc)
    p = _paragraph_with(paras, masters.CONTACT_LINE)
    if p is None:
        raise ComposeError("the CV master has no contact slot")
    new = base.header_line()
    style = _run_style(doc, p.start)
    s, e = p.start, p.end - 1
    return [{"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}},
            {"insertText": {"location": {"index": s}, "text": new}},
            {"updateTextStyle": {"range": {"startIndex": s, "endIndex": s + len(new)},
                                 "textStyle": style, "fields": "*"}}]


def cv_spelling_requests(doc: dict, base_key: str) -> list[dict]:
    """Lowercase words spelled for the other base, respelled in place with
    their formatting (replaceAllText keeps each run's style)."""
    wrong = US_TO_UK if base_key == "uk" else UK_TO_US
    text = " ".join(p.text for p in house.snapshot(doc))
    words = set(_WORD.findall(text))
    found = sorted(w for w in words if w in wrong)
    # replaceAllText matches inside words too, so a word that is part of a
    # longer one ("honor" in "honorary") is left, and the read-back holds the
    # CV rather than changing a word nobody chose.
    safe = [w for w in found if not any(w in o and w != o and o not in wrong for o in words)]
    return [{"replaceAllText": {"containsText": {"text": w, "matchCase": True},
                                "replaceText": wrong[w]}} for w in safe]


def summary_requests(doc: dict, anchor: str, count: int, text: str) -> list[dict]:
    """The CV summary's paragraphs replaced by one, in the first one's own
    style set whole and not bold, so nothing is inherited by accident."""
    paras = [p for p in house.snapshot(doc)]
    idx = next((i for i, p in enumerate(paras) if house.norm(p.text).startswith(anchor)), None)
    if idx is None:
        raise ComposeError("the CV summary was not found")
    span = [p for p in paras[idx:] if p.text.strip()][:count]
    style = dict(_run_style(doc, span[0].start))
    style.pop("bold", None)
    if "weightedFontFamily" in style:
        style["weightedFontFamily"] = dict(style["weightedFontFamily"], weight=400)
    s, e = span[0].start, span[-1].end - 1
    return [{"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}},
            {"insertText": {"location": {"index": s}, "text": text}},
            {"updateTextStyle": {"range": {"startIndex": s, "endIndex": s + len(text)},
                                 "textStyle": style, "fields": "*"}}]


def cv_summary_span(paras: list[house.Para]) -> tuple[str, int]:
    """(anchor, count) of the CV's summary paragraphs: every non-empty
    paragraph between PROFESSIONAL SUMMARY and the next heading."""
    from .assertions import CV_HEADINGS
    idx = next((i for i, p in enumerate(paras)
                if house.norm(p.text) == "PROFESSIONAL SUMMARY"), None)
    if idx is None:
        return "", 0
    span = []
    for p in paras[idx + 1:]:
        t = house.norm(p.text)
        if t in CV_HEADINGS:
            break
        if t:
            span.append(p)
    if not span:
        return "", 0
    return house.norm(span[0].text)[:60], len(span)


def build_cv(db, *, master_id: str, title: str, folder_id: str, base, bases: dict,
             page_count, heading_sizes: dict, summary: str = "",
             summary_anchor: str = "", summary_count: int = 1) -> Built:
    """Copy the house CV master, fill its header, respell for the base, keep
    it to two pages (the banner goes before anything of his is cut), read it
    back."""
    out = Built()
    out.doc_id = db.copy_master(master_id, title, folder_id)
    db.batch(out.doc_id, cv_requests(db.get(out.doc_id), base=base))
    if summary and summary_anchor:
        db.batch(out.doc_id, summary_requests(db.get(out.doc_id), summary_anchor,
                                              summary_count, spell(summary, base.key)))
        out.notes.append("summary written for the role")
    db.batch(out.doc_id, cv_spelling_requests(db.get(out.doc_id), base.key))
    pdf = db.export_pdf(out.doc_id)
    pages = page_count(pdf)
    if pages > house.CV_PAGES:
        paras = house.snapshot(db.get(out.doc_id))
        lay = house.layout(paras)
        if lay.banner is not None:
            b = paras[lay.banner]
            db.batch(out.doc_id, [{"deleteContentRange": {"range": {"startIndex": b.start,
                                                                    "endIndex": b.end}}}])
            out.notes.append("banner taken off to keep the CV to two pages")
            pdf = db.export_pdf(out.doc_id)
            pages = page_count(pdf)
    out.pages, out.pdf = pages, pdf
    doc = db.get(out.doc_id)
    paras = house.snapshot(doc)
    fails = house.check(paras, kind="cv", base=base, bases=bases, page_count=pages,
                        heading_sizes=heading_sizes)
    if "banner taken off to keep the CV to two pages" in out.notes:
        fails = [f for f in fails if "banner" not in f]
    text = " ".join(p.text for p in paras)
    if "{{" in text:
        fails.append("A12 a slot is left unfilled in the cv")
    fails += spelling_problems(text, base.key)
    out.problems = fails
    return out
