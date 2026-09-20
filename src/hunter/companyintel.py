"""Find out what a company actually is, and record where that was read.

company.py turns facts into points. This module is the half that goes and
gets the facts, and its only hard rule is the one in CLAUDE.md section 1: a
claim hunter cannot point at a source for is discarded, not stored. A Fact
refuses construction without a source URL, so the rule is enforced by the
type rather than by remembering.

Order of cost, cheapest first, and the expensive one runs only for what the
cheap ones left unknown:

  1. What hunter already holds. The a16z portfolio index (867 companies with
     market, stage, headcount band and open job count), his Target Companies
     tab, and his own contacts graph.
  2. The company's own job board. Already fetched for the sweep, and the
     title list answers "is a senior commercial seat open" and "is the
     commercial leadership in post" for nothing.
  3. One Claude call with web search, capped per run, returning cited facts
     only. A field whose citation is missing is dropped rather than kept.
"""
from __future__ import annotations

import html as _html
import json
import re

import requests
from datetime import datetime, timezone

from .company import Fact, Facts
from .config import Config, db_get, db_insert, ALL_ROWS
from .sources import slugify

UA = {"User-Agent": "Mozilla/5.0 (compatible; hunter/1.0)"}

TABLE = "hunter_company_intel"
MAX_WEB_PER_RUN = "hunter_max_company_evidence_per_run"

# Fields the web pass may return, and the Facts attribute each one fills.
WEB_FIELDS = {
    "what_it_does": "what_it_does",
    "investors": "investors",
    "stage": "stage",
    "founded": "founded",
    "headcount": "headcount",
    "commercial_leadership": "commercial_leadership",
    "locations": "locations",
}

SCHEMA = {
    "type": "object",
    "properties": {
        f: {"type": ["object", "null"],
            "properties": {"value": {"type": "string"},
                           "source": {"type": "string"}},
            "required": ["value", "source"]}
        for f in WEB_FIELDS
    },
    "required": list(WEB_FIELDS),
    "additionalProperties": False,
}

PROMPT = """Research the company "{name}"{domain_hint} and report only what you
can cite.

Return one JSON object with these keys. For each one give a short factual
value and the URL you read it on. If you cannot find it, or you are not
confident the page is about this company rather than a similarly named one,
return null for that key. A guess is worse than a null here: the null is
recorded as unknown and costs the company nothing, while a wrong fact is
scored and acted on.

what_it_does: one sentence saying what the business sells and to whom, in the
  plain words the company uses about itself. Do not editorialise and do not
  say it is promising or exciting.
investors: the named investors or lead investor on its most recent round.
stage: the funding stage, for example "Series B" or "publicly traded (NYSE:
  XYZ)" or "private equity owned".
founded: the year it was founded.
headcount: approximate employee count or band.
commercial_leadership: whether it has a Chief Revenue Officer or Chief
  Commercial Officer in post, and since when, or that it does not.
locations: its offices or where it hires.

The company may be small or recent. If web results are thin, return nulls
rather than filling the object from a company with a similar name.

Two rules about the shape of the answer, because a claim that breaks either
one is discarded rather than corrected:

- Every key is either null or an object with exactly "value" and "source".
  "source" must be a full URL starting with http. A value with no URL is
  thrown away, so an uncited fact is the same as no fact.
- If you could not establish something, the key is null. Do not return a
  sentence saying that you could not find it, and do not return a caveat in
  place of a value. "No published figure found" is a null, not a value."""


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


URL = re.compile(r"https?://\S+")

# A model asked for a fact it cannot find will often answer with a sentence
# about not finding it rather than with the null it was asked for. That
# sentence would be stored as an observation and scored as one, which is the
# manufactured outcome CLAUDE.md section 1 forbids. These are what those
# non-answers look like, and they are dropped as if the key had been null.
NON_ANSWER = re.compile(
    r"^\s*(?:n/?a|none|null|unknown|unclear|not (?:found|known|disclosed|"
    r"stated|available|public|published))\b|"
    r"\b(?:no|not any) (?:company[\s-]published |publicly [\s\w]{0,20})?"
    r"(?:figure|number|data|information|record|evidence|source|details?)\s+"
    r"(?:was |were |is |are )?(?:found|available|disclosed|published)\b|"
    r"\bcould not (?:be )?(?:find|found|establish|determine|verif)\w*\b|"
    r"\bunable to (?:find|determine|verify)\b", re.I)


def _source_of(val: dict) -> str:
    """The citation, whatever the model called the key.

    Asked for "source" it has answered with "url" and with "sources". The
    citation is the thing that matters, so it is read from any of them and
    reduced to the first real URL.
    """
    for key in ("source", "url", "sources", "urls", "citation"):
        raw = val.get(key)
        if isinstance(raw, list):
            raw = " ".join(str(x) for x in raw)
        m = URL.search(str(raw or ""))
        if m:
            return m.group(0).rstrip(" ;,")
    return ""


def from_a16z(row: dict) -> dict:
    """Facts the portfolio index already recorded. Cheapest evidence there is."""
    src = "a16z portfolio index"
    out = {"a16z_portfolio": True}
    markets = row.get("markets")
    if markets:
        if isinstance(markets, str):
            try:
                markets = json.loads(markets.replace("'", '"'))
            except (ValueError, json.JSONDecodeError):
                markets = [markets]
        text = ", ".join(str(m) for m in markets if m)
        if text:
            out["what_it_does"] = Fact(f"a16z lists its markets as {text}", src)
    if row.get("stage"):
        out["stage"] = Fact(str(row["stage"]), src)
    if row.get("band"):
        out["headcount"] = Fact(f"{row['band']} employees", src)
    return out


def from_board(titles: list[str], board_url: str) -> dict:
    """What the open roles say about the shape of the company.

    A board carrying a Chief Revenue Officer search says the commercial model
    is open. A board carrying no senior commercial seat says the opposite,
    and both are observations rather than inferences.
    """
    if not titles:
        return {}
    joined = "; ".join(titles[:60])
    out = {"open_roles": Fact(f"open roles include {joined[:600]}", board_url)}
    chief = [t for t in titles
             if re.search(r"\bchief (revenue|commercial|business) officer\b|\bcro\b|\bcco\b", t, re.I)]
    if chief:
        out["commercial_leadership"] = Fact(
            f"hiring {chief[0]}, so no {chief[0]} is in post", board_url)
    return out


# ---------- the company's own site, which is free and cited by definition ----------
#
# The model pass was built first and is the wrong primary source for what a
# business does: it costs money, it depends on an API budget that ran out on
# 2026-09-20 with the sourcing run half built, and it paraphrases a sentence
# the company already publishes. The company's own homepage says what it
# sells, in its own words, at a URL that is the citation.

META = re.compile(
    r'<meta[^>]+(?:name|property)\s*=\s*["\'](?:og:)?description["\'][^>]*'
    r'content\s*=\s*["\'](.*?)["\']', re.I | re.S)
META_REV = re.compile(
    r'<meta[^>]+content\s*=\s*["\'](.*?)["\'][^>]*(?:name|property)\s*=\s*'
    r'["\'](?:og:)?description["\']', re.I | re.S)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
STRIP = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")

FUNDING = re.compile(
    r"\b(?:series [a-h]\b|seed round|pre-seed|raised \$[\d.]+\s*(?:m|b|million|billion))",
    re.I)
FOUNDED = re.compile(r"\bfounded (?:in )?(20[0-2]\d|19\d\d)\b|\bsince (20[0-2]\d)\b", re.I)


def _clean(html: str) -> str:
    return SPACE.sub(" ", _html.unescape(STRIP.sub(" ", TAGS.sub(" ", html)))).strip()


# A homepage that renders client side often leaves nothing but class names
# and navigation behind the tag stripper. That text looks like a description
# to len() and to a regex, so it would be stored with the company's own URL
# as its citation, which is the most convincing kind of wrong fact.
MARKUP_NOISE = re.compile(r"[\[\]{}<>]|&[a-z]{2,6};|(?:^|\s)[.#][a-z-]{4,}", re.I)
NAV_WORDS = re.compile(
    r"\b(?:toggle sidebar|skip to (?:main )?content|cookie|privacy policy|"
    r"terms of service|all rights reserved|sign in|log in|menu)\b", re.I)


# "is a step forward with the launch of Krea 2 https://www." was accepted as
# a description of Krea. It starts mid sentence, which is what a scraper
# produces when it lands in the middle of a paragraph.
FRAGMENT = re.compile(r"^\s*(?:is|are|was|were|and|but|or|to|of|with|for|the)\b", re.I)


def _is_prose(text: str) -> bool:
    """Does this read like a sentence about a business, or like page furniture."""
    if not text or len(text) < 25:
        return False
    if FRAGMENT.match(text):
        return False
    words = text.split()
    if len(words) < 6:
        return False
    noise = len(MARKUP_NOISE.findall(text))
    if noise > max(2, len(words) // 12):
        return False
    if len(NAV_WORDS.findall(text)) >= 2:
        return False
    # Real prose has short words and few of them capitalised mid sentence.
    long_tokens = sum(1 for w in words if len(w) > 24)
    return long_tokens <= 1


# Some marketing sites refuse a bot-shaped user agent outright (Gamma
# answered 403 to hunter and 200 to a browser string) and some render
# everything client side, leaving the homepage empty of prose. Both were
# silently costing companies their category score, which is the one
# component without which nothing can be scored at all.
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/128.0 Safari/537.36",
              "Accept": "text/html,application/xhtml+xml"}
ABOUT_PATHS = ("/about", "/about-us", "/company", "/home")


def _fetch(url: str, timeout: int) -> tuple[str, str, str]:
    """(html, final_url, note). Tried politely first, then as a browser."""
    for headers in (UA, BROWSER_UA):
        try:
            r = requests.get(url, headers=headers, timeout=timeout,
                             allow_redirects=True)
        except Exception as e:
            return "", "", f"site fetch failed: {e.__class__.__name__}"
        if r.status_code < 400:
            return r.text, str(r.url), ""
        note = f"site answered {r.status_code}"
    return "", "", note


def site_facts(url: str, timeout: int = 20) -> tuple[dict, list[str]]:
    """What the company says about itself, read off its own page.

    The meta description is preferred because it is the one sentence a
    company writes to describe itself to strangers, which is exactly the
    question being asked. The page text is the fallback, and the page URL is
    the citation either way.
    """
    if not url:
        return {}, ["no site url"]
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    html, final, note = _fetch(url, timeout)
    if not html:
        return {}, [note or "site returned nothing"]
    out, notes = {}, []
    m = META.search(html) or META_REV.search(html)
    desc = _clean(m.group(1)) if m else ""
    title = _clean(TITLE.search(html).group(1)) if TITLE.search(html) else ""
    body = _clean(html)
    # A description shorter than this names the company and nothing else
    # ("Acme | Home"), which is not an observation about the business.
    if len(desc) < 25:
        desc = ""
    blurb = " ".join(x for x in (title, desc) if x).strip()
    if len(blurb) < 25:
        # No meta description, so fall back to the page text, but only if the
        # page text is prose. Krea's homepage yielded a Tailwind selector
        # string and Reddit's yielded its nav, and both were about to be
        # stored and scored as descriptions of the business.
        candidate = body[:400]
        if _is_prose(candidate):
            blurb = candidate
        else:
            notes.append("page carried no prose description")
    if len(blurb) >= 25 and _is_prose(blurb):
        out["what_it_does"] = Fact(blurb[:600], final)
    else:
        # A homepage that renders client side says nothing useful, but its
        # about page is usually plain server-rendered prose.
        root = "/".join(final.split("/")[:3]) or url
        for path in ABOUT_PATHS:
            alt_html, alt_url, _ = _fetch(root + path, timeout)
            if not alt_html:
                continue
            am = META.search(alt_html) or META_REV.search(alt_html)
            alt = _clean(am.group(1)) if am else _clean(alt_html)[:400]
            if len(alt) >= 25 and _is_prose(alt):
                out["what_it_does"] = Fact(alt[:600], alt_url)
                break
        if "what_it_does" not in out:
            notes.append("no prose description on the site or its about page")
    fm = FUNDING.search(body)
    if fm:
        out["stage"] = Fact(fm.group(0), final)
    dm = FOUNDED.search(body)
    if dm:
        out["founded"] = Fact(dm.group(0), final)
    return out, notes


# Domains worth trying for a company hunter has no domain for, in the order
# the real ones turned out to use. The verification matters more than the
# order: a page that does not name the company is somebody else's site, and
# scoring a company from a squatter's parking page would be worse than
# scoring it from nothing.
# Three, not six. A company hunter has never heard of costs one DNS round
# trip per candidate, and at a budget of 300 companies the tail of .app,
# .co and .dev turned a discovery pass into twenty five minutes of waiting
# for names that were never going to resolve. Real companies are on one of
# these or are reachable through the name they publish.
TLDS = (".com", ".ai", ".io")


def resolve_domain(name: str, timeout: int = 6) -> tuple[str, str]:
    """Find the company's own site, and prove the page is about them.

    Returns (url, note). The proof is that the page names the company; a
    parking page or an unrelated business at the same slug fails it and the
    company keeps an unknown rather than gaining a wrong fact.
    """
    base = slugify(name).replace("-", "")
    if not base or len(base) < 3:
        return "", "name too short to guess a domain"
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 1]
    candidates = []
    # A name that already carries its own domain ("ProRata.ai") is telling
    # you the answer; guessing "prorataai.com" from it finds a different
    # site that happens to mention the same word.
    dotted = re.sub(r"[^a-z0-9.\-]", "", name.lower().strip())
    if "." in dotted and not dotted.endswith("."):
        candidates.append("https://" + dotted)
    # "Lightning AI" lives at lightning.ai, not lightningai.com, and the
    # .com belongs to somebody else entirely. A trailing word is very often
    # the top level domain the company chose, which is why it is in the name.
    if len(words) > 1 and words[-1] in ("ai", "io", "dev", "app"):
        stem = "".join(w for w in words[:-1])
        if len(stem) >= 3:
            candidates.append(f"https://{stem}.{words[-1]}")
            candidates.append(f"https://{'-'.join(words[:-1])}.{words[-1]}")
    candidates += [f"https://{base}{tld}" for tld in TLDS]
    misses = []
    for url in candidates:
        try:
            r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        except Exception:
            continue
        if r.status_code >= 400:
            continue
        # The host hunter ended up on, after redirects, must still be the
        # company's own. Parked and for-sale domains redirect to a registrar
        # whose page is full of brandable name suggestions, so it mentions
        # the company and passes a text check: perplexity resolved to a
        # domain marketplace, gamma to an unrelated security vendor, and
        # krea to a Slovak IT consultancy, and all three were scored.
        host = re.sub(r"^www\.", "", (str(r.url).split("/")[2] if "//" in str(r.url)
                                       else "")).lower()
        # The whole host, not just its first label. "Lightning AI" lives at
        # lightning.ai, whose first label is "lightning" while the slug is
        # "lightningai", and comparing labels threw away the right answer
        # while a parked domain that redirects to a registrar still has to
        # fail.
        squashed = host.replace(".", "").replace("-", "")
        want = base.replace("-", "")
        if not (squashed.startswith(want) or want.startswith(host.split(".")[0])):
            misses.append(f"{url} redirected to {host}")
            continue
        text = _clean(r.text)[:4000].lower()
        if base in text.replace(" ", "") or any(w in text for w in words):
            return str(r.url), ""
        # Somebody else's site at the same slug. Keep looking rather than
        # stopping: the real domain is often a later candidate, and a wrong
        # site is worse than no site.
        misses.append(url)
    if misses:
        return "", f"answered but did not name {name}: {', '.join(misses[:3])}"
    return "", f"no site found for {name}"


# A domain arrived at by guessing the slug is right most of the time and
# spectacularly wrong the rest: gamma.ai is a security vendor, not Gamma,
# and krea.com is a Slovak IT consultancy, not Krea. Neither can be told
# apart from the real thing by string matching, so the answer is not to
# pretend otherwise but to carry the doubt all the way to the sheet, where
# Krish can settle it in one edit.
GUESS_CAVEAT = " (site matched by name only, worth checking)"


def mark_guessed(facts: dict) -> dict:
    out = {}
    for k, v in facts.items():
        out[k] = Fact(v.value + GUESS_CAVEAT, v.source) if k == "what_it_does" else v
    return out


def web_facts(cfg: Config, name: str, domain: str = "") -> tuple[dict, list[str]]:
    """One capped, cited web pass. Returns (facts, notes).

    Every returned field must carry a source URL or it is dropped here, so a
    model that answers confidently without a citation contributes nothing
    rather than contributing a guess.
    """
    notes: list[str] = []
    try:
        import anthropic
    except ImportError:
        return {}, ["anthropic sdk missing"]
    key = cfg.optional("hunter_anthropic_api_key")
    if not key:
        return {}, ["no hunter_anthropic_api_key in system_config"]
    client = anthropic.Anthropic(api_key=key)
    prompt = PROMPT.format(name=name,
                           domain_hint=f" (website {domain})" if domain else "")
    try:
        resp = client.messages.create(
            model=cfg.optional("hunter_anthropic_model", "claude-opus-5"),
            max_tokens=3000,
            tools=[{"type": "web_search_20250305", "name": "web_search",
                    "max_uses": 4}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {}, [f"web pass failed: {e.__class__.__name__}"]
    if getattr(resp, "stop_reason", "") == "max_tokens":
        notes.append("web pass truncated")
    raw = _text(resp).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}, notes + ["model returned no JSON object"]
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {}, notes + [f"model JSON did not parse: {e}"]
    out: dict = {}
    for key_name, attr in WEB_FIELDS.items():
        val = parsed.get(key_name)
        if not isinstance(val, dict):
            continue
        value = str(val.get("value") or "").strip()
        source = _source_of(val)
        if not value or NON_ANSWER.search(value):
            continue
        if not source:
            # An uncited claim is exactly what this module refuses to store.
            notes.append(f"dropped {key_name}: no source URL")
            continue
        out[attr] = Fact(value, source)
    return out, notes


def gather(cfg: Config, name: str, *, a16z_row: dict | None = None,
           board_titles: list[str] | None = None, board_url: str = "",
           domain: str = "", allow_web: bool = True) -> tuple[Facts, list[str]]:
    """Everything known about one company, cheap sources first."""
    from .company import MIN_EVIDENCED
    slug = slugify(name).replace("-", "")
    got: dict = {}
    notes: list[str] = []
    if a16z_row:
        got.update(from_a16z(a16z_row))
    if board_titles:
        got.update({k: v for k, v in from_board(board_titles, board_url).items()
                    if k not in got or k == "open_roles"})
    facts = Facts(slug=slug, name=name,
                  **{k: v for k, v in got.items() if k != "a16z_portfolio"},
                  a16z_portfolio=bool(got.get("a16z_portfolio")))
    if allow_web:
        # The web pass fills what the free sources left unknown, and never
        # overwrites a fact hunter observed directly.
        web, web_notes = web_facts(cfg, name, domain)
        notes += web_notes
        for attr, fact in web.items():
            if getattr(facts, attr, None) is None:
                setattr(facts, attr, fact)
    return facts, notes


def save(cfg: Config, scores: list) -> None:
    """One row per company, carrying every component's evidence and source."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for s in scores:
        rows.append({
            "slug": s.slug, "name": s.name, "total": s.total,
            "tier": s.tier, "status": s.status,
            "evidenced": s.evidenced_count,
            "components": json.dumps([{"name": c.name, "points": c.points,
                                       "evidenced": c.evidenced,
                                       "evidence": c.evidence,
                                       "source": c.source}
                                      for c in s.components]),
            "why": s.why(), "scored_at": now,
        })
    if rows:
        db_insert(cfg, TABLE, rows, on_conflict="slug", merge=True)


# When a score is worth establishing again.
#
# A company hunter could not read is not a permanent verdict, it is a page
# that was down, a site that refused a bot, or a description that had not
# been written yet. Without a retry those companies were frozen out for
# good, which quietly turns a temporary failure into a policy.
RETRY_UNSCORED_DAYS = 14
# A score that stands goes stale more slowly: a company raises, hires a CRO,
# or changes what it sells, and none of that happens weekly.
RESCORE_DAYS = 90


def is_stale(row: dict, *, now=None) -> bool:
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    scored_at = row.get("scored_at") or ""
    if not scored_at:
        return True
    days = RETRY_UNSCORED_DAYS if (row.get("status") or "") else RESCORE_DAYS
    return scored_at < (now - timedelta(days=days)).isoformat()


def load(cfg: Config) -> dict[str, dict]:
    return {r["slug"]: r for r in db_get(cfg, TABLE,
            {"select": "slug,name,total,tier,status,evidenced,why,components,"
                       "scored_at", "limit": ALL_ROWS})}


def restore(row: dict):
    """A CompanyScore from a saved row, evidence and all.

    Rebuilding one without its components produced a number with no working
    shown, which is the opposite of the promise this makes: every score
    lands on his tab next to the sentence and the URL it came from, so he
    can overrule it in one edit.
    """
    from .company import CompanyScore, Component
    raw = row.get("components")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    comps = [Component(name=c.get("name", ""), points=float(c.get("points") or 0),
                       evidenced=bool(c.get("evidenced")),
                       evidence=c.get("evidence", ""), source=c.get("source", ""))
             for c in (raw or []) if isinstance(c, dict)]
    return CompanyScore(slug=row.get("slug", ""), name=row.get("name") or "",
                        total=float(row.get("total") or 0), components=comps,
                        status=row.get("status") or "")


# The opening of a job description is almost always the company describing
# itself ("Glean is the enterprise AI platform that..."), it is already in
# hunter_seen_roles.jd_text for every company that has ever produced a
# posting, and the posting URL is its citation. For a company whose site
# refuses hunter or renders client side, this is the difference between a
# score and a needs-evidence.
# A posting talks about the role far more than about the company, and the
# sentences that do are easy to name.
ABOUT_THE_ROLE = re.compile(
    r"\b(?:you(?:'ll| will)|we(?:'re| are) (?:looking|seeking|hiring)|"
    r"this role|the role|reporting to|responsibilities|requirements|"
    r"about the role|your mission|the ideal candidate|qualifications)\b", re.I)

INTRO = re.compile(
    r"((?:[A-Z][\w.&'-]*\s+){0,4}(?:is|are|builds?|makes?|powers?|helps?|"
    r"provides?)\s+(?:the\s+|a\s+|an\s+)?[^.]{25,320}\.)")


def from_posting(jd_text: str, url: str, company: str = "") -> dict:
    """A description of the business, taken from its own job posting."""
    if not jd_text or not url:
        return {}
    head = SPACE.sub(" ", jd_text[:2500]).strip()
    best = ""
    for m in INTRO.finditer(head):
        sentence = m.group(1).strip()
        if ABOUT_THE_ROLE.search(sentence):
            # "You'll build and lead the tax function at Gamma" names the
            # company and describes the job. Storing it as what the business
            # does would score Gamma as a tax practice.
            continue
        if company and company.split()[0].lower() not in sentence.lower():
            continue
        best = sentence
        break
    if len(best) < 40 or not _is_prose(best):
        return {}
    return {"what_it_does": Fact(best[:600], url)}
