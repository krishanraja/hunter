"""Every role Krish has ruled on, as evidence a judge can be measured against.

His sheet is the record of what he decided: column A on Pipeline and on the
Applied tab, in his own words. The database supplies what the sheet does not
hold: when the role was put in front of him, the posting's full text, and its
URL. A row is paired to its database row with the same match_rows reconcile
uses, so the pairing rules exist once.

Kept out on purpose:
- hunter's own codes (dead posting, duplicate row, already applied), which
  record hunter failing rather than his taste;
- the rows he cleared with "sourced before the bar was fixed", which were a
  bulk clean-up, not a ruling on each role;
- anything with no verdict.

A role with no date cannot be placed before or after a cutoff, so it is
counted and left out of any time split rather than guessed into one.
"""
from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass

from . import verdicts
from .config import ALL_ROWS, Config, db_get

CLEARING = "sourced before the bar was fixed"


@dataclass
class Ruled:
    job_id: str
    company: str
    title: str
    label: str              # "yes" or "no"
    words: str              # column A, verbatim
    code: str | None        # his decline code, when he gave one
    comp: str
    location: str
    url: str
    presented_at: str | None
    jd_text: str
    snippet: str            # the sheet's JD Snippet, when the posting is gone
    why: str                # the sheet's Why It Fits at the time
    source: str
    archived: bool

    @property
    def when(self) -> datetime.datetime | None:
        if not self.presented_at:
            return None
        try:
            got = datetime.datetime.fromisoformat(str(self.presented_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        return got if got.tzinfo else got.replace(tzinfo=datetime.timezone.utc)

    @property
    def has_jd(self) -> bool:
        return len(self.jd_text or "") >= 400

    def to_dict(self) -> dict:
        return asdict(self)


def label_of(text: str) -> tuple[str | None, str | None]:
    """('yes'|'no'|None, code) from column A."""
    text = (text or "").strip()
    if not text or CLEARING in text.lower():
        return None, None
    kind, code = verdicts.parse(text)
    if kind in ("go", "applied"):
        return "yes", None
    if kind == "rejection":
        if verdicts.is_system_code(code):
            return None, None
        return "no", code
    return None, None


def _clean(cell: str, default: str) -> str:
    v = (cell or "").strip()
    return "" if v == default else v


def collect(cfg: Config, sheet, canon) -> tuple[list[Ruled], dict]:
    """(every ruled role, counts of what was left out and why)."""
    from .run import match_rows
    rows = list(sheet.read_pipeline(canon.sheet_headers)) + list(sheet.read_archive())
    db = db_get(cfg, "hunter_seen_roles", {
        "select": "job_id,company,title,url,job_url,comp,location,presented_at,"
                  "jd_text,why_it_fits,source,status", "limit": ALL_ROWS})
    first_event: dict[str, str] = {}
    for e in db_get(cfg, "hunter_verdict_events",
                    {"select": "job_id,recorded_at", "order": "recorded_at.asc",
                     "limit": ALL_ROWS}):
        first_event.setdefault(e["job_id"], e["recorded_at"])
    pairs, _, _, _ = match_rows(rows, db)
    # Keyed on the row object: Pipeline row 7 and Applied row 7 are different rows.
    paired = {id(s): d for s, d in pairs}

    out: list[Ruled] = []
    skipped = {"no_verdict": 0, "system_or_clearing": 0, "duplicate": 0}
    seen: set[str] = set()
    for s in rows:
        label, code = label_of(s.verdict)
        if label is None:
            key = "no_verdict" if not (s.verdict or "").strip() else "system_or_clearing"
            skipped[key] += 1
            continue
        d = paired.get(id(s)) or {}
        from .sources import job_id as mint
        jid = d.get("job_id") or mint(s.company, s.role)
        if jid in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(jid)
        out.append(Ruled(
            job_id=jid, company=s.company.strip(), title=s.role.strip(), label=label,
            words=s.verdict.strip(), code=code,
            comp=_clean(s.cell("Comp"), "Not disclosed") or (d.get("comp") or ""),
            location=_clean(s.cell("Location"), "Not stated") or (d.get("location") or ""),
            url=s.jd_url or d.get("job_url") or d.get("url") or "",
            presented_at=d.get("presented_at") or first_event.get(jid),
            jd_text=d.get("jd_text") or "",
            snippet=_clean(s.cell("JD Snippet"), "Not captured"),
            why=_clean(s.cell("Why It Fits"), "Not assessed"),
            source=d.get("source") or s.cell("Source") or "",
            archived=s.archived))
    return out, skipped


def split(rows: list[Ruled], cutoff: datetime.datetime
          ) -> tuple[list[Ruled], list[Ruled], list[Ruled]]:
    """(examples ruled before the cutoff, holdout presented after it, undated)."""
    before, after, undated = [], [], []
    for r in rows:
        w = r.when
        if w is None:
            undated.append(r)
        elif w < cutoff:
            before.append(r)
        else:
            after.append(r)
    return before, after, undated


def fill_jd(r: Ruled) -> Ruled:
    """The posting's text, fetched again when the database never stored it.
    Only from the posting itself: a board's JSON for a known ATS, the page
    otherwise. What cannot be fetched stays empty and the row says so."""
    if r.has_jd or not r.url.startswith("http"):
        return r
    from .run import _resolve_for_build, ats_key, fetch_jd_plain
    text = ""
    try:
        if ats_key(r.url):
            role = _resolve_for_build({"url": r.url, "job_url": r.url, "title": r.title,
                                       "company": r.company})
            text = role.jd_text or ""
        else:
            live, page = fetch_jd_plain(r.url)
            if live is not False and len(page) >= 400 and r.title.split()[0].lower() in page.lower():
                text = page[:12000]
    except Exception:
        text = ""
    if len(text) >= 400:
        r.jd_text = text
    return r


def posting_for(r: Ruled) -> str:
    """What the judge reads for a ruled role: the posting when it exists, the
    sheet's own description of it when it does not, labelled as such."""
    if r.has_jd:
        return r.jd_text
    parts = [p for p in (r.snippet, r.why) if p]
    return ("[The full posting is no longer available. What the sheet recorded "
            "about it:]\n" + "\n".join(parts)) if parts else ""
