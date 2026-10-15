"""Which roles carry a personal line of his, and which line (canon 9.14).

His ruling, 2026-10-15: a role at a voice, audio, speech, mindset, future of
work, personal development, emerging media, or content or IP business gets
one personal line, from a bank of his own words in canon 9.14. If the bank
has none that fits, the package holds, hunter asks him, and his answer is
written to canon verbatim. His Sonos letter is the model: a line about his
own life with the product, which hunter could not have written because
nothing it held said so. His words live in canon, never in this repository.

Free and deterministic. It reads only sentences about the business: the
judge's quote of what the business sells and its one line on it, and the
company's own description of itself. Never the posting. Measured on 2026-10-15
over 495 roles: a whole posting placed 252 of them (141 as "mindset", mostly
wellbeing benefits and "an entrepreneurial mindset"), and even its opening
paragraph placed 68, many on "remote work", "consulting magazine" or "an
intellectual property strategy". The judge's lines placed 24 of 424, every one
a business that says so. Compound phrases go first, so "machine learning"
never reads as learning and "content marketing" never as a content business;
then only strong phrases count. Measured on his 138 labelled companies before
it gates anything (tests/test_personal.py).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

# His categories, as he named them, and the strong phrases that place a
# business in each. A phrase must say what the business is or sells.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "voice_audio_speech": (
        "voice ai", "ai voice", "voice agent", "voice agents", "voice assistant",
        "text to speech", "text-to-speech", "speech to text", "speech-to-text",
        "speech recognition", "speech synthesis", "synthetic voice", "voice cloning",
        "audio ai", "ai audio", "generative audio", "audio platform", "audio content",
        "music", "musicians", "podcast", "podcasts", "podcasting", "sound system",
        "sound systems", "sound experience", "home sound", "home audio", "audio products",
        "audio equipment", "listening experience", "wireless speakers", "speakers",
        "headphones", "hearing", "dubbing", "audiobook", "audiobooks"),
    "mindset": (
        "mindset", "mental fitness", "mental health", "meditation", "mindfulness",
        "wellbeing", "well-being", "emotional health", "resilience training", "therapy"),
    "future_of_work": (
        "future of work", "remote work", "hybrid work", "distributed teams",
        "employee experience", "upskilling", "reskilling", "talent marketplace",
        "freelance", "freelancers", "gig economy", "independent workers",
        "workplace learning", "ai coworker", "ai coworkers", "digital workers",
        "ai employees", "ai workforce"),
    "personal_development": (
        "personal development", "personal growth", "self-improvement", "self improvement",
        "coaching platform", "life coaching", "executive coaching", "leadership coaching",
        "learn new skills", "online courses", "online learning", "language learning",
        "habit", "habits"),
    "emerging_media": (
        "generative video", "ai video", "video generation", "video generator",
        "virtual reality", "augmented reality", "mixed reality", "spatial computing",
        "immersive", "metaverse", "creator economy", "creators", "avatar", "avatars",
        "digital humans", "3d worlds", "world models", "interactive entertainment",
        "ai-generated media", "ai generated media", "generative media",
        "image generation", "ai art"),
    "content_ip": (
        "media company", "content studio", "studio for", "publishing house",
        "book publisher", "news publisher", "digital publisher",
        "intellectual property", "ip licensing", "licensing ip", "franchise",
        "entertainment company", "film", "films", "television", "tv shows",
        "streaming service", "storytelling", "stories", "fans", "fandom",
        "news organization", "newsroom", "magazine"),
}

LABEL = {
    "voice_audio_speech": "voice, audio or speech",
    "mindset": "mindset",
    "future_of_work": "future of work",
    "personal_development": "personal development",
    "emerging_media": "emerging media",
    "content_ip": "content or IP",
}

# Removed before any phrase is matched: each contains a strong phrase and
# says something else.
COMPOUNDS = (
    "machine learning", "deep learning", "learning management", "learning rate",
    "content marketing", "content moderation", "content management", "content delivery",
    "content security", "content writer", "content writers", "social media management",
    "social media marketing", "media buying", "media agency", "paid media",
    "media mix", "earned media", "speaker identification", "speaker diarization",
    "film coating", "thin film", "habit tracking for teams",
    "workforce management", "mental model", "immersive analytics",
    "voice of the customer", "voice of customer", "stories of our customers",
    "customer stories", "success stories", "case studies", "fans of",
    "therapy area", "therapy areas", "therapies", "gene therapy", "cell therapy",
    "creators of", "music to our ears",
)

_WORD = re.compile(r"[a-z0-9']+(?:[-'][a-z0-9]+)*")


def _clean(text: str) -> str:
    low = " " + " ".join((text or "").lower().replace("’", "'").split()) + " "
    for c in COMPOUNDS:
        low = low.replace(f" {c} ", " ").replace(f" {c},", " ,").replace(f" {c}.", " .")
    return low


def business_sentences(judge_answers, company_said: str = "") -> list[str]:
    """What the business is, in the judge's quote and line and in the
    company's own description of itself. Empty when none was recorded: a
    business hunter cannot describe gets no personal line, never a guess."""
    if isinstance(judge_answers, str):
        try:
            judge_answers = json.loads(judge_answers)
        except ValueError:
            judge_answers = {}
    biz = (judge_answers or {}).get("business") if isinstance(judge_answers, dict) else None
    biz = biz if isinstance(biz, dict) else {}
    out = [str(biz.get("quote") or ""), str(biz.get("what_it_sells") or ""), company_said or ""]
    return [s for s in out if s.strip()]


def categories_of(sentences: list[str]) -> dict[str, str]:
    """{category: the phrase that placed it} over sentences about the
    business only."""
    out: dict[str, str] = {}
    for text in sentences:
        low = _clean(text)
        for cat, phrases in CATEGORIES.items():
            if cat in out:
                continue
            for ph in phrases:
                if re.search(rf"(?<![a-z0-9]){re.escape(ph)}(?![a-z0-9])", low):
                    out[cat] = ph
                    break
    return out


# ---------- canon 9.14 ----------

@dataclass(frozen=True)
class Entry:
    number: int
    categories: tuple[str, ...]
    terms: tuple[str, ...]
    words: str          # his words, verbatim

    def key(self) -> str:
        return hashlib.sha256(f"{self.number}|{self.words}".encode()).hexdigest()[:16]


ENTRY = re.compile(r"^ENTRY (\d+)\. Categories: ([a-z_, ]+)\. Terms: ([^.]*)\.")


def parse_bank(section_text: str) -> list[Entry]:
    """Entries of canon 9.14: a line "ENTRY n. Categories: a, b. Terms: x,
    y. ..." and, on the next non-empty line, his words in double quotes.
    An entry without its quoted line is not an entry: never a guess."""
    lines = (section_text or "").split("\n")
    out: list[Entry] = []
    for i, line in enumerate(lines):
        m = ENTRY.match(line.strip())
        if not m:
            continue
        nxt = next((x.strip() for x in lines[i + 1:] if x.strip()), "")
        if not (len(nxt) > 2 and nxt[0] in "\"“" and nxt[-1] in "\"”"):
            continue
        cats = tuple(c.strip() for c in m.group(2).split(",") if c.strip() in CATEGORIES)
        terms = tuple(t.strip().lower() for t in m.group(3).split(",") if t.strip())
        if cats:
            out.append(Entry(int(m.group(1)), cats, terms, nxt[1:-1]))
    return out


def entry_block(number: int, categories: list[str], terms: list[str], words: str,
                added: str) -> str:
    """One canon 9.14 entry, as parse_bank reads it. His words verbatim, on
    one line: words that would not read back exactly are refused, never cut."""
    if "\n" in words or "\r" in words or not words.strip():
        raise ValueError("his words must be one non-empty line to be written as they are")
    bad = [t for t in terms if "." in t or "," in t or not t.strip()]
    if bad or not categories or any(c not in CATEGORIES for c in categories):
        raise ValueError("an entry needs known categories and terms without a stop or comma")
    return (f"ENTRY {number}. Categories: {', '.join(categories)}. Terms: "
            f"{', '.join(terms)}. Added {added}.\n\"{words}\"\n")


@dataclass
class Match:
    categories: dict[str, str]      # the business's categories, with the phrase
    entries: list[Entry]            # his entries that fit
    missing: list[str]              # categories with no entry: hold and ask

    @property
    def needs_line(self) -> bool:
        return bool(self.categories)

    def fingerprint(self) -> str:
        """A hash of only the entries in this role's categories, so his next
        answer re-runs the roles it is about and no others."""
        blob = "|".join(sorted(e.key() for e in self.entries)) + "#" + ",".join(self.missing)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def match(sentences: list[str], bank: list[Entry]) -> Match:
    """His entries for a business, from sentences about it. An entry fits a
    category the business is in, and when it names terms, one of its terms
    appears in those sentences: his Sonos line is about music and audio, and
    goes to a business that says so."""
    cats = categories_of(sentences)
    low = " ".join(_clean(s) for s in sentences)
    fits = [e for e in bank if set(e.categories) & set(cats)
            and (not e.terms or any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low)
                                    for t in e.terms))]
    covered = {c for e in fits for c in e.categories}
    return Match(categories=cats, entries=fits,
                 missing=sorted(c for c in cats if c not in covered))
