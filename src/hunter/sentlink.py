"""A posting he sends as a link, made into a stage-sent item.

His message on 2026-11-15 was a LinkedIn link and two words. `stage-sent`
took only a JSON file, so the scheduled job could not stage it and a session
had to hold the database key to do it. Now `stage-sent LINK --apply` reads the
link itself and the job runs it.

The employer's own board is preferred over LinkedIn every time (section 3k):
a board link is read off its board; a LinkedIn link is read for the company
and title, then the company's board is looked up and the same title found on
it. Only when no board holds it does the LinkedIn text stand in, and then it
is recorded checked only if LinkedIn itself says the posting is open.

Nothing here writes. Every reader is injected so the tests run offline.
"""
from __future__ import annotations

import html as html_mod
import re

LINKEDIN_JOB = re.compile(r"linkedin\.com/jobs/(?:view/(?:[^/?]*-)?|.*[?&]currentJobId=)(\d{8,})")
GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{num}"


def linkedin_number(link: str) -> str:
    m = LINKEDIN_JOB.search(link or "")
    return m.group(1) if m else ""


def _text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>|</(?:p|li|div|h\d)>", "\n", fragment or "", flags=re.I)
    s = html_mod.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _first(pattern: str, page: str) -> str:
    m = re.search(pattern, page, re.S)
    return _text(m.group(1)) if m else ""


def parse_linkedin(page: str) -> dict:
    """Title, company, location, pay and posting text off LinkedIn's signed-out
    posting page. Empty strings where the page did not say."""
    return {
        "title": _first(r'class="[^"]*topcard__title[^"]*"[^>]*>(.*?)</', page),
        "company": _first(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</', page),
        "location": _first(r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</', page),
        "comp": _first(r'class="[^"]*compensation__salary[^"]*"[^>]*>(.*?)</', page),
        "jd_text": _first(r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page),
    }


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _readable(slug: str) -> str:
    return " ".join(p.capitalize() for p in re.split(r"[-_]+", slug or "") if p)


def _board_item(posting, company: str) -> dict:
    raw = posting.raw or {}
    # A first-published date is a posting date; an updated date is not, and is
    # never called one (section 3j).
    posted = raw.get("first_published") or ""
    return {"url": posting.url, "company": company, "title": posting.title,
            "location": posting.location or "", "comp": posting.comp_text or "",
            "posted_at": posted[:10], "ats": posting.ats, "slug": posting.ats_slug,
            "posting_id": str(posting.ats_posting_id)}


def item_for(link: str, *, board_key, boards: dict, board_name, get,
             find_board, linkedin_open, company: str = "") -> tuple[dict | None, str]:
    """(stage-sent item, '') or (None, why not). `company` overrides the
    name read off the board or the page."""
    key = board_key(link)
    if key and key[0] in boards:
        ats, slug, pid = key
        on_board = [p for p in boards[ats](slug) if str(p.ats_posting_id) == str(pid)]
        if not on_board:
            return None, "the posting is not on its board"
        name = company or board_name(ats, slug) or _readable(slug)
        return _board_item(on_board[0], name), ""
    num = linkedin_number(link)
    if not num:
        return None, "not a link hunter can read: a Greenhouse, Ashby, Lever or LinkedIn posting"
    page = get(GUEST.format(num=num))
    if not page:
        return None, "LinkedIn did not answer for that posting"
    li = parse_linkedin(page)
    name = company or li["company"]
    if not (li["title"] and name):
        return None, "LinkedIn's page named no title or company"
    found = find_board(name)
    if found and found[0] in boards:
        ats, slug = found
        same = [p for p in boards[ats](slug) if _norm(p.title) == _norm(li["title"])]
        if same:
            item = _board_item(same[0], name)
            item["comp"] = item["comp"] or li["comp"]
            return item, ""
    view = f"https://www.linkedin.com/jobs/view/{num}/"
    item = {"url": view, "company": name, "title": li["title"],
            "location": li["location"], "comp": li["comp"], "posted_at": "",
            "jd_text": li["jd_text"]}
    if linkedin_open(view) is True:
        item["open_read"] = True
    return item, ""


def live_readers(cfg) -> dict:
    """The real readers, for item_for. Network: called only from the command."""
    import requests
    from .ats import ashby, discover, greenhouse, lever
    from .ats import linkedin as li_mod
    from .run import board_key

    def get(url: str) -> str:
        try:
            r = requests.get(url, headers=li_mod.UA, timeout=35)
        except requests.RequestException:
            return ""
        return r.text if r.ok else ""

    def board_name(ats: str, slug: str) -> str:
        if ats != "greenhouse":
            return ""
        try:
            r = requests.get(f"{greenhouse.API}/{slug}", timeout=20)
            return (r.json() or {}).get("name", "") if r.ok else ""
        except Exception:
            return ""

    def find_board(company: str):
        cache = discover.load_cache(cfg) if cfg is not None else None
        return discover.discover(cfg, company, cache)

    return {"board_key": board_key,
            "boards": {"greenhouse": greenhouse.board, "ashby": ashby.board,
                       "lever": lever.board},
            "board_name": board_name, "get": get, "find_board": find_board,
            "linkedin_open": lambda url: li_mod.posting_state(url)[0]}
