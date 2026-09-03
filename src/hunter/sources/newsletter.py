"""a16zjobs.substack.com, read every time a post goes out.

Krish asked for it on 2026-09-03. The newsletter is not a job list; it is
where the clues are. Each post names people who have just joined companies,
with their LinkedIn profiles ("Mike Myer has joined Sierra's GTM team after
8+ years at Snowflake"), founders who are hiring with apply links, and the
occasional "DM him". A named person who joined a target company last week is
the warmest, most timely bridge there is, and until now none of it reached
hunter.

Mechanics. The feed carries full post bodies. Each post is processed exactly
once (hunter_newsletter_posts, keyed on the link) by one structured-outputs
call in the same shape package/rationale.py uses. Every URL the model returns
must appear verbatim in the post, or the item is dropped: the model reads,
it does not invent. The extraction is stored on the post row, so every
signal downstream can be traced to the sentence it came from.

This module reads a feed and calls a model. It can reach nobody.
"""
from __future__ import annotations

import html as html_mod
import json
import re

import requests

from ..config import Config, db_get, db_insert
from ..people.ingest import li_slug
from ..sheet import plain_text

FEED = "https://a16zjobs.substack.com/feed"
UA = {"User-Agent": "Mozilla/5.0 (compatible; hunter/1.0)"}
TABLE = "hunter_newsletter_posts"

SCHEMA = {
    "type": "object",
    "properties": {
        "talent_moves": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "linkedin_url": {"type": "string"},
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    "previous_company": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["person", "linkedin_url", "company", "title",
                             "previous_company", "quote"],
                "additionalProperties": False,
            },
        },
        "hiring": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "roles": {"type": "array", "items": {"type": "string"}},
                    "founder": {"type": "string"},
                    "founder_linkedin_url": {"type": "string"},
                    "apply_url": {"type": "string"},
                    "comp": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["company", "roles", "founder", "founder_linkedin_url",
                             "apply_url", "comp", "quote"],
                "additionalProperties": False,
            },
        },
        "reach_out_advice": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string"},
                    "how": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["who", "how", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["talent_moves", "hiring", "reach_out_advice"],
    "additionalProperties": False,
}

PROMPT = """You are reading one issue of the a16z jobs newsletter for Krish Raja,
who is looking for a senior commercial role (GM, chief commercial or strategy,
corporate development, chief of staff) at an AI-native company.

Extract, as JSON and nothing else:

talent_moves: every person the post says has JOINED or is MOVING TO a company,
with the LinkedIn URL the post links for them, the company they joined, the
title or team if stated, the company they came from if stated, and the exact
sentence from the post. Leave a field empty if the post does not say.

hiring: every company the post says is hiring, with the roles named, the
founder or hiring lead if named, that person's LinkedIn URL if linked, the
apply URL if linked, comp if stated, and the exact sentence.

reach_out_advice: every place the post tells the reader how to contact
someone ("DM him", "email her", "reach out to"), with who and how, and the
exact sentence.

Rules: copy URLs exactly as they appear in the post. Never invent a URL, a
name, a company or a figure. Quotes are verbatim. Empty arrays are fine.

THE POST:
{text}

LINKS IN THE POST (the only URLs you may use):
{links}"""


# ---------- feed ----------

def parse_feed(xml: str) -> list[dict]:
    """Every item in the feed: link, title, published, and the body as HTML."""
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        body = re.search(r"<content:encoded><!\[CDATA\[(.*?)\]\]></content:encoded>", item, re.S)
        if not link:
            continue
        out.append({
            "link": link.group(1).strip(),
            "title": html_mod.unescape(title.group(1).strip()) if title else "",
            "published": pub.group(1).strip() if pub else "",
            "html": body.group(1) if body else "",
        })
    return out


def post_text(body_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", body_html)
    return html_mod.unescape(re.sub(r"\s+", " ", text)).strip()


def post_links(body_html: str) -> list[str]:
    seen, out = set(), []
    for u in re.findall(r'href="(https?://[^"]+)"', body_html):
        u = html_mod.unescape(u)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_feed(timeout: int = 60) -> list[dict]:
    r = requests.get(FEED, headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse_feed(r.text)


def new_posts(cfg: Config, posts: list[dict]) -> list[dict]:
    """Posts not yet recorded, oldest first, so a backlog lands in order."""
    seen = {r["link"] for r in db_get(cfg, TABLE, {"select": "link", "limit": "5000"})}
    fresh = [p for p in posts if p["link"] not in seen]
    return list(reversed(fresh))


# ---------- extraction ----------

def _text(resp) -> str:
    for block in resp.content:
        if getattr(block, "type", "") == "text":
            return block.text
    raise ValueError("no text block in the model response")


def ground(signals: dict, links: list[str]) -> tuple[dict, list[str]]:
    """Drop any item whose URL is not one the post actually carries. The
    dropped items are returned by name so the run report can say so."""
    allowed = set(links)
    dropped: list[str] = []

    def keep_url(u: str) -> str:
        u = (u or "").strip()
        return u if u in allowed else ""

    moves = []
    for m in signals.get("talent_moves") or []:
        url = keep_url(m.get("linkedin_url", ""))
        if m.get("linkedin_url") and not url:
            dropped.append(f"talent move {m.get('person')!r}: LinkedIn URL not in post")
            continue
        moves.append({**m, "linkedin_url": url})
    hiring = []
    for h in signals.get("hiring") or []:
        h = dict(h)
        for key in ("founder_linkedin_url", "apply_url"):
            if h.get(key) and not keep_url(h[key]):
                dropped.append(f"hiring {h.get('company')!r}: {key} not in post")
                h[key] = ""
        hiring.append(h)
    return ({"talent_moves": moves, "hiring": hiring,
             "reach_out_advice": signals.get("reach_out_advice") or []}, dropped)


def extract(cfg: Config, post: dict) -> tuple[dict | None, list[str]]:
    """(signals, flags). None when the model could not be used; the post is
    then recorded with the error so it is retried next time, not skipped."""
    text = post_text(post["html"])[:14000]
    links = post_links(post["html"])
    if len(text) < 200:
        return {"talent_moves": [], "hiring": [], "reach_out_advice": []}, ["thin post"]
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.require("hunter_anthropic_api_key"))
        resp = client.messages.create(
            model=cfg.optional("hunter_anthropic_model", "claude-opus-5"),
            # A post runs to 14k characters and the schema wants verbatim
            # sentences back, so the JSON alone can pass 4k tokens. Both of
            # the first two live posts stopped at max_tokens=4000.
            max_tokens=16000,
            messages=[{"role": "user", "content": PROMPT.format(
                text=text, links="\n".join(links[:150]))}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
        if getattr(resp, "stop_reason", "") in ("refusal", "max_tokens"):
            return None, [f"model stopped: {resp.stop_reason}"]
        raw = json.loads(_text(resp))
    except Exception as e:
        return None, [f"extraction failed: {e.__class__.__name__}"]
    signals, dropped = ground(raw, links)
    return signals, dropped


# ---------- landing ----------

def contacts_from(signals: dict, post: dict) -> list[dict]:
    """network_contacts rows for the people who moved. Strength is zero, the
    relationship is unknown; the evidence carries the post, the date and the
    sentence, which is what a bridge card needs to show."""
    rows = []
    for m in signals.get("talent_moves") or []:
        slug = li_slug(m.get("linkedin_url") or "")
        if not slug or not m.get("company"):
            continue
        name = plain_text(m.get("person") or "").strip()
        parts = name.split()
        rows.append({
            "contact_key": slug,
            "linkedin_url": m["linkedin_url"],
            "full_name": name,
            "first_name": parts[0] if parts else "",
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "current_company": plain_text(m["company"]).strip(),
            "current_title": plain_text(m.get("title") or "").strip(),
            "source": "a16z newsletter",
            "strength_score": 0,
            "strength_evidence": {
                "newsletter_post": post["link"],
                "newsletter_title": post.get("title", ""),
                "newsletter_date": post.get("published", ""),
                "quote": plain_text(m.get("quote") or "")[:400],
                "previous_company": plain_text(m.get("previous_company") or ""),
            },
        })
    return rows


def record_post(cfg: Config, post: dict, signals: dict | None, flags: list[str],
                model: str) -> None:
    db_insert(cfg, TABLE, [{
        "link": post["link"], "title": plain_text(post.get("title", ""))[:300],
        "published_at": _rfc822_to_iso(post.get("published", "")),
        "processed_at": None if signals is None else _now(),
        "signals": signals, "model": model,
        "error": "; ".join(flags)[:500] if flags else None,
    }], on_conflict="link", merge=True)


def _now() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _rfc822_to_iso(s: str) -> str | None:
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).isoformat()
    except Exception:
        return None
