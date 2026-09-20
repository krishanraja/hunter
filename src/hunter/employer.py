"""What kind of company is this, on evidence rather than on a taxonomy.

Krish 2026-09-20: "legacy businesses like SiriusXM, Citi, Omnicom ... businesses
that are just not in my wheelhouse ... so many financial services and healthcare".

The obvious answer is a sector blocklist, and it is wrong. Measured against his
own 148 verdicts before a line of it was written:

  blocking pharma and healthcare providers catches 8 of his declines and kills
  BioSpace, Recursion and Talkspace, all of which he approved;

  blocking consultancies catches 9 and kills Harvey;

  blocking agency holdcos catches 1 and kills Razorfish.

He is not declining healthcare. He is declining healthcare that is not an
AI-native business, and no regular expression over a company name can tell
those apart. Meanwhile he approved Anaplan and MongoDB and declined Asana,
Duolingo, Gong and UiPath, so "established SaaS" is not the line either, and
he approved one role and declined another at Cloudflare, ElevenLabs, Harvey,
OpenAI, Sierra, Suno and Writer, so the company is not the whole answer at all.

So this module holds no opinions of its own. It reports what is on record:

  PORTFOLIO   the company is in the a16z index, which is 859 companies of
              exactly the kind canon 9.1 describes. Hard positive evidence.
  APPROVED    he has said yes to this company before.
  DECLINED    he has declined it for a company-level reason. G12 owns the
              block; this is the same fact, reported for scoring.
  INSTITUTION the one sector his data does support: banks, insurers and asset
              managers. 7 of his declines, 0 of his approvals.
  UNKNOWN     everything else, which is most things, and is neutral.

UNKNOWN is the common answer and it is deliberately worth nothing either way.
A company hunter cannot place is a company he should judge, not one it should
guess about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import ALL_ROWS, Config, db_get
from .sources import distinctive_tokens, slugify

PORTFOLIO = "portfolio"
APPROVED = "approved"
DECLINED = "declined"
INSTITUTION = "institution"
UNKNOWN = "unknown"

# What each kind is worth to the score. Positive evidence outweighs nothing;
# the institution penalty is large because it is the one class his verdicts
# establish without a single counterexample.
POINTS = {PORTFOLIO: 2, APPROVED: 2, UNKNOWN: 0, INSTITUTION: -3, DECLINED: -5}

# Banks, insurers and asset managers. Measured against his 148 verdicts: 7
# declines, 0 approvals. Every other sector he declines has an approval in it,
# which is why no other sector is here.
#
# Word boundaries on both sides and a minimum length, because "EY" inside
# "Harvey" is how a blunt version of this list would have blocked the legal AI
# company he approved.
INSTITUTIONS = re.compile(
    r"(?<![a-z])("
    r"citi(?:bank|group)?|citigroup|bny|bny mellon|mellon|tiaa|"
    r"new york life|metlife|prudential|aviva|allianz|axa|zurich insurance|"
    r"janus henderson|schroders|fidelity investments|vanguard|blackrock|"
    r"td securities|td bank|jpmorgan|jp morgan|goldman sachs|morgan stanley|"
    r"wells fargo|bank of america|barclays|hsbc|lloyds|natwest|santander|"
    r"deutsche bank|ubs|credit suisse|bnp paribas|societe generale|"
    r"state street|northern trust|charles schwab|raymond james|"
    r"american express|mastercard|visa inc|capital one|discover financial|"
    r"nationwide|aflac|geico|progressive insurance|travelers insurance|"
    r"legal & general|standard life|m&g|abrdn"
    r")(?![a-z])", re.I)


@dataclass
class Verdict:
    kind: str
    evidence: str

    @property
    def points(self) -> int:
        return POINTS[self.kind]

    @property
    def is_ai_native(self) -> bool:
        """Enough evidence to treat the employer as an AI-native business.

        This is what G7's escape hatch should have asked. It used to ask
        whether the posting text contained the words "artificial
        intelligence", which is true of every AI role at every bank, so the
        gate that exists to block banks was switched off by the job title.
        """
        return self.kind in (PORTFOLIO, APPROVED)


@dataclass
class Index:
    """Everything on record about companies, loaded once per run."""
    portfolio: dict[str, str]      # slug -> display name
    portfolio_tokens: dict[frozenset, str]
    approved: set[str]             # slugs he has approved
    declined: dict[str, dict]      # token -> the decline, from learn.company_declines

    @classmethod
    def empty(cls) -> "Index":
        return cls(portfolio={}, portfolio_tokens={}, approved=set(), declined={})


def build_index(cfg: Config, *, declines: dict | None = None) -> Index:
    """Read the a16z portfolio and his own approval history."""
    portfolio, tokens = {}, {}
    try:
        for c in db_get(cfg, "hunter_a16z_companies",
                        {"select": "slug,name", "limit": ALL_ROWS}):
            slug = (c.get("slug") or "").strip().lower()
            name = (c.get("name") or "").strip()
            if not slug or not name:
                continue
            portfolio[slug] = name
            portfolio[slugify(name)] = name
            toks = distinctive_tokens(name)
            if toks:
                tokens[frozenset(toks)] = name
    except Exception:
        pass

    approved: set[str] = set()
    try:
        from . import verdicts as verdicts_mod
        for r in db_get(cfg, "hunter_seen_roles",
                        {"select": "company,krish_verdict,verdict_source",
                         "krish_verdict": "not.is.null", "limit": ALL_ROWS}):
            if (r.get("verdict_source") or "").lower().startswith("hunter"):
                continue  # never learn from its own output
            if verdicts_mod.parse(r.get("krish_verdict") or "")[0] in ("go", "applied"):
                slug = slugify(r.get("company") or "")
                if slug:
                    approved.add(slug)
    except Exception:
        pass

    if declines is None:
        try:
            from . import learn
            declines = learn.company_declines(learn.load_events(cfg),
                                              learn.load_company_allow(cfg))
        except Exception:
            declines = {}
    return Index(portfolio=portfolio, portfolio_tokens=tokens,
                 approved=approved, declined=declines or {})


def classify(company: str, index: Index | None = None) -> Verdict:
    """What is on record about this employer.

    Order matters. A decline he made outranks everything, because it is the
    most recent thing he said. Portfolio membership outranks his older
    approvals only in the evidence string, not in the points, since both mean
    the same thing here.
    """
    name = (company or "").strip()
    if not name:
        return Verdict(UNKNOWN, "no company name")
    index = index or Index.empty()

    from . import learn
    hit = learn.declined_company(index.declined, name)
    if hit:
        return Verdict(DECLINED, f"you declined {hit['company']} on {hit['date']} "
                                 f"({hit['code']})")

    slug = slugify(name)
    if slug in index.portfolio:
        return Verdict(PORTFOLIO, f"in the a16z portfolio as "
                                  f"{index.portfolio[slug]}")
    toks = frozenset(distinctive_tokens(name))
    if toks and toks in index.portfolio_tokens:
        return Verdict(PORTFOLIO, f"in the a16z portfolio as "
                                  f"{index.portfolio_tokens[toks]}")
    if slug in index.approved:
        return Verdict(APPROVED, "you have approved a role here before")
    m = INSTITUTIONS.search(name)
    if m:
        return Verdict(INSTITUTION, f"a bank, insurer or asset manager "
                                    f"({m.group(0)}); 7 of your declines, none "
                                    f"of your approvals")
    return Verdict(UNKNOWN, "no record either way")
