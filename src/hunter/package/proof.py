"""What each of his facts proves, and at what altitude it may be said.

His words, 2026-10-19, on the Replit Head of Corporate Development package:
"it drones on and on about Supabase and N8N, which a) is such a lame thing to
say in applications, it's like a kids toy for these guys - the less
specificity the better. and b) this is a Corporate Development role, and you
are goin on and on about me building widgets and AI agents." Then: "I have a
14-agent, 45-workflow autonomous AI OS on n8n + Supabase + Anthropic +
OpenAI, that is true ... Try to make me sound not like the IT guy using N8N
to build a $5 business."

Nothing was false in that package. The fault was altitude. The evidence pool
was one heap: "on n8n + supabase + anthropic + openai" sat beside "two
technology M&As" with the same standing, so the most vivid fact won, and the
14-agent system reached 44 of the 93 drafts written in the fortnight before.

So every fact keeps its truth (the gate still traces against all of it) and
gains two things:

  mechanics   how a thing is built: tool and vendor names, counts of agents
              and workflows, internals. Never said in a letter, a CV summary
              or a form answer, unless the posting itself names it, because
              then the reader is technical and asked.
  capability  what it proves to a hiring reader: deals, P&L, market build,
              partnerships, operating change, AI practice. The writer is
              shown these so it picks proof for the seat, not for vividness.

When he edits AI credibility in, he writes it at executive altitude: "two
years of receipts", "an AI Chief of Staff inside 8 organizations". That is the
bar, and tests/test_package_taste.py holds the engine to it.

Pure functions: the routine runs them through `python -m hunter.drafts check`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# How a thing is built. Each is a pattern and a plain label; the label is what
# a failure says, so a failure never quotes his words.
MECHANICS: tuple[tuple[str, str], ...] = (
    (r"\bn8n\b", "n8n"),
    (r"\bsupabase\b", "Supabase"),
    (r"\bopenai\b", "OpenAI"),
    (r"\banthropic\b", "Anthropic"),
    (r"\bclaude(?: projects?| code)?\b", "Claude"),
    (r"\bchat ?gpt\b|\bgpt-?\d", "ChatGPT"),
    (r"\blovable\b", "Lovable"),
    (r"\bvercel\b", "Vercel"),
    (r"\bzapier\b", "Zapier"),
    (r"\bmake\.com\b", "Make"),
    (r"\blangchain\b|\blanggraph\b", "LangChain"),
    (r"\bairtable\b", "Airtable"),
    (r"\bretool\b", "Retool"),
    (r"\bpinecone\b", "Pinecone"),
    (r"\bpostgres(?:ql)?\b", "Postgres"),
    (r"\bpython\b", "Python"),
    (r"\b\d+[- ]agents?\b", "an agent count"),
    (r"\b\d+[- ]workflows?\b", "a workflow count"),
    (r"\bmulti[- ]agent\b", "multi-agent"),
    (r"\bagent swarm\b|\bswarm\b", "swarm"),
    (r"\btask queues?\b", "task queues"),
    (r"\bexecution state\b", "execution state"),
    (r"\bdeterministic workflows?\b", "deterministic workflows"),
    (r"\bsource of truth\b", "source of truth"),
    (r"\bpersistent memory\b", "persistent memory"),
    (r"\bcognitive stack\b", "cognitive stack"),
    (r"\bvibe[- ]?cod\w*", "vibe-coding"),
    (r"\blow[- ]code\b|\bno[- ]code\b", "low-code"),
    (r"\bwidgets?\b", "widgets"),
    (r"\btech(?:nology)? stack\b|\bstack components\b", "stack"),
)

# His AI operating system and the hands-on AI building around it. His ruling,
# 2026-10-19: outside an AI transformation seat, at most one part of the whole
# package says it, at outcome level.
AI_SYSTEM = re.compile(
    r"\boperating system\b|\bai os\b|\bvibe[- ]?cod|\bai brain\b|\bmulti[- ]agent\b"
    r"|\b\d+[- ]agents?\b|\bautonomous ai\b|\bmy (?:own )?agents\b"
    r"|\bagents? (?:i|that i) (?:built|run|designed)\b", re.I)
AI_SEAT_FAMILIES = frozenset({"ai_chief_of_staff_transformation"})
AI_BUDGET = 1

# What a fact proves to a hiring reader. Kept broad on purpose: it orders the
# evidence for the writer and gates nothing.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("deals and M&A", r"\bm&as?\b|\bacqui|\bmerger|\bdeal|\bnegotiat|\btransaction|\bfund\b"
                      r"|\bthesis\b|\bventure|\binvest|\bbuild, buy|\bbuy or partner"),
    ("P&L and growth", r"\bp&l\b|\barr\b|\brevenue\b|\bebitda\b|\bgrew\b|\bscaled?\b|\bgrowth\b"),
    ("market build", r"\bfirst hire\b|\bfrom zero\b|\bfrom \$0\b|\bzero-to\b|\blaunch|\bmarkets?\b"),
    ("partnerships", r"\bpartner"),
    ("operating change", r"\btransformation\b|\brestructur|\boperating\b|\balignment\b"
                         r"|\bchief of staff\b|\bcross-functional\b"),
    ("AI practice", r"\bai\b|\bagents?\b|\bautomat"),
)


@dataclass
class Proof:
    text: str
    capabilities: list[str] = field(default_factory=list)
    mechanics: list[str] = field(default_factory=list)


def _hits(text: str) -> list[tuple[str, str]]:
    """(pattern, label) for every mechanic the text carries."""
    low = (text or "").lower()
    return [(p, label) for p, label in MECHANICS if re.search(p, low)]


def mechanics_in(text: str, posting: str = "", extra: tuple[str, ...] = ()) -> list[str]:
    """The labels of the mechanics this text says that the posting does not.

    A posting that names a tool has a technical reader who asked, so saying it
    back is answering. extra is the phrases he has cut or ruled out since
    (package_rules), matched as words."""
    post = (posting or "").lower()
    out = []
    for p, label in _hits(text):
        if not re.search(p, post) and label not in out:
            out.append(label)
    low = (text or "").lower()
    for phrase in extra:
        ph = " ".join((phrase or "").lower().split())
        if ph and re.search(r"\b" + re.escape(ph) + r"\b", low) and ph not in post:
            label = f"a phrase he ruled out ({len(ph.split())} words)"
            if label not in out:
                out.append(label)
    return out


def says_ai_system(text: str) -> bool:
    return bool(AI_SYSTEM.search(text or ""))


def ai_budget(family: str) -> int | None:
    """How many parts of a package may speak of his AI system: None is no
    limit, for an AI transformation seat, where it is the job."""
    return None if (family or "") in AI_SEAT_FAMILIES else AI_BUDGET


# A seat read from its title, for a role the judge never read: one he scored
# himself, as Replit's Head of Corporate Development was. The order is the
# builder's before 2026-10-15 (package/tailor.py), its names the judge's.
TITLE_FAMILIES: tuple[tuple[str, str], ...] = (
    ("partnerships_alliances", r"\bpartnership|\balliances?\b|\bpartner\b|\bchannel\b|\becosystem\b"
                               r"|\bbusiness development\b|\bdistribution\b"),
    ("country_regional_gm", r"\bgeneral manager\b|\bcountry manager\b|\bmanaging director\b"
                            r"|\bregional gm\b|\bgm\b|\bmarket entry\b|\bcountry lead\b"),
    ("corp_dev_strategy", r"\bcorporate development\b|\bcorp dev\b|\bcorporate strategy\b"
                          r"|\bvp,? strategy\b|\bhead of strategy\b|\bdirector of strategy\b"
                          r"|\bm&a\b"),
    ("ai_chief_of_staff_transformation", r"\bchief of staff\b|\bhead of ai\b|\bai operations\b"
                                         r"|\bai transformation\b|\bai enablement\b"
                                         r"|\bai\b.*\b(?:agentic|transformation)\b"),
    ("chief_commercial_strategy", r"\bchief commercial\b|\bcco\b|\bhead of commercial\b"
                                  r"|\bcommercial strategy\b|\bhead of gtm\b|\bgtm\b|\brevenue\b"
                                  r"|\bsales\b"),
)


def family_of(judge_answers, title: str = "") -> str:
    """The judge's read of what kind of seat this is; with none on record,
    the seat its title names; empty when neither says."""
    import json
    ja = judge_answers
    if isinstance(ja, str):
        try:
            ja = json.loads(ja)
        except ValueError:
            ja = None
    if isinstance(ja, dict) and isinstance(ja.get("function"), dict):
        fam = str(ja["function"].get("family") or "")
        if fam and fam != "none":
            return fam
    low = (title or "").lower()
    for fam, pattern in TITLE_FAMILIES:
        if re.search(pattern, low):
            return fam
    return ""


_SENTENCES = re.compile(r"(?<=[.;])\s+(?=[A-Z0-9])")


def _lines(text: str) -> list[str]:
    """Lines, with a hard-wrapped sentence joined back: a line that ends
    without a stop and a next line that starts in lower case are one line."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            out.append("")
            continue
        if out and out[-1] and not re.search(r"[.;:!?]$", out[-1]) and line[0].islower():
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    return [l for l in out if l]


def proofs(evidence_text: str) -> list[Proof]:
    """His evidence as proof points, one per line or sentence."""
    out = []
    for piece in (s for line in _lines(evidence_text) for s in _SENTENCES.split(line)):
        t = " ".join(piece.split()).strip(" -*")
        if len(t) < 12:
            continue
        low = t.lower()
        caps = [name for name, p in CAPABILITIES if re.search(p, low)]
        out.append(Proof(text=t, capabilities=caps, mechanics=[lab for _, lab in _hits(t)]))
    return out


def annotated(evidence_text: str) -> str:
    """The evidence as the writer sees it: every fact as he recorded it, each
    marked with what it proves and, where it carries them, the mechanics that
    stay out of the prose. The gate still reads the evidence untouched."""
    lines = []
    for p in proofs(evidence_text):
        tags = []
        if p.capabilities:
            tags.append("proves: " + ", ".join(p.capabilities))
        if p.mechanics:
            tags.append("mechanics, never in prose: " + ", ".join(p.mechanics))
        lines.append(f"- {p.text}" + (f"  [{'; '.join(tags)}]" if tags else ""))
    return "\n".join(lines)
