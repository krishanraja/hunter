"""drivemap.py - every file hunter makes in his Drive has a registered home.

His words, 2026-10-18: "ensure it is impossible to save files randomly in
sprawl in my Drive - they must go in specific folders, subfolders, always".

What made it possible before: `DocBuild.folder(name, parent_id="root")` made
folders at the top of his Drive (the scratch preview used it), and
`copy_master(..., parent_id=None)` filed a copy wherever Drive put it. The live
CVs and letters went to a folder his estate clean-up of 4 to 11 October had
filed under ARCHIVE, and nothing ever read back where a file landed.

The rule now:

- The registry is `system_config drive_destinations`: the folder ids of his
  top-level roots, the protected folders, his top-level items as they stood,
  his inbox, and one destination per writer. It is in Supabase, never in this
  public repo. The folder names follow his estate policy
  (krishanraja/filearchives, policy/estate-policy-v1.json); this file only
  finds their ids.
- A DocBuild creates nothing until `allow` has loaded the registry and walked
  each destination's parents up to a registered root. Every create names a
  parent that `allow` approved or that hunter made inside one, and the new
  file's parents are read back (docbuild.DocBuild._landed).
- His applications live one folder each, `Applications/<YYYY-MM> <Company> -
  <Role>`, under his Hunter folder (his choice, 2026-10-18), which he moved to
  Family Admin/Krish/Job-Search/Hunter in October 2026.
- Anything that still lands at the top of his Drive is filed into
  `Inbox from My AI` by the hourly sweep, and he is told in one line. Nothing
  is ever deleted, and FamilyContentLibrary and KeePass are never touched.

His Drive as he laid it out, 2026-10-24 (krishanraja/filearchives D-033):
"Ventures is where everything now lives", with Active Ventures, Inspiration,
Bugs & Issues and _Archive inside; `--ARCHIVE` and `Family Admin` beside it;
`Inbox from My AI` at the top for anything an AI cannot place ("just
reinstate that in the root folder then"); "KeePass and FamilyContentLibrary
are immovable". CURRENT and its 00_INBOX are gone (filearchives D-031).

Folders are found by ID, never by path, wherever the API allows it (his
ruling, 2026-10-24): a rebuild keeps every destination and the inbox at the
id the registry already holds while it still sits where it must, and finds a
folder by its name only when the registry has none, so a folder he renames
or moves inside a registered area is not lost.
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field

KEY = "drive_destinations"
FOLDER_MIME = "application/vnd.google-apps.folder"

# His top-level areas a file may sit under (filearchives D-033): files go in
# their subfolders, never loose in the area itself.
ROOT_NAMES = ("Ventures", "--ARCHIVE", "Family Admin")
# Never written to, moved or listed into: his immovables. FamilyContentLibrary
# was ContentLibrary until 2026-10-23; the old name is kept in case it returns.
PROTECTED_NAMES = ("FamilyContentLibrary", "ContentLibrary", "KeePass")
# Where Hunter's three folders are made when the registry has none.
HUNTER_HOME = ("Family Admin", "Krish", "Job-Search", "Hunter")
# The folder at the top of his Drive for what an AI cannot place.
INBOX_NAME = "Inbox from My AI"
HUNTER_FOLDERS = {"hunter.applications": "Applications",
                  "hunter.masters": "Masters",
                  "hunter.previews": "Previews"}
# Areas inside his roots that other systems write into, by the registry key
# they read (filearchives D-033). Found under their root by name when the
# registry has no id, and never made: they are his folders. Bugs & Issues is
# where a feedback video's stills and feedback go, one folder per venture
# (content-engine api/ux-feedback; Krish, 2026-10-28).
AREA_FOLDERS = {"bugs_and_issues": ("Ventures", "Bugs & Issues")}
UNMATCHED = "_unmatched"
MAX_DEPTH = 40


class DriveMapError(RuntimeError):
    """The registry is missing or a destination is not where it must be."""


@dataclass
class DriveMap:
    destinations: dict[str, str]
    roots: dict[str, str]
    protected: dict[str, str]
    top_level: dict[str, str]
    inbox: str
    written_at: str = ""
    raw: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"destinations": self.destinations, "roots": self.roots,
                           "protected": self.protected, "top_level": self.top_level,
                           "inbox": self.inbox, "written_at": self.written_at},
                          sort_keys=True)


def parse(value) -> DriveMap:
    """The registry from its system_config value. Missing parts raise: a map
    with no roots or no inbox would let the guard pass what it cannot judge."""
    try:
        d = json.loads(value) if isinstance(value, str) else dict(value or {})
    except ValueError as e:
        raise DriveMapError(f"{KEY} is not readable JSON") from e
    missing = [k for k in ("destinations", "roots", "protected", "top_level", "inbox")
               if not d.get(k)]
    if missing:
        raise DriveMapError(f"{KEY} lacks {', '.join(missing)}")
    return DriveMap(destinations=dict(d["destinations"]), roots=dict(d["roots"]),
                    protected=dict(d["protected"]), top_level=dict(d["top_level"]),
                    inbox=d["inbox"], written_at=d.get("written_at", ""), raw=d)


def load(cfg) -> DriveMap:
    from .config import db_get
    rows = db_get(cfg, "system_config", {"select": "value", "key": f"eq.{KEY}"})
    if not rows:
        raise DriveMapError(f"no {KEY} in system_config: nothing is created in his Drive "
                            f"until it is written (python -m hunter.run drive-map --apply)")
    return parse(rows[0]["value"])


def ancestry(parents_of, folder_id: str, m: DriveMap) -> str | None:
    """The name of the registered root this folder sits under, walking its
    parents up; None when the chain reaches the top of his Drive without one,
    passes a protected folder, or is longer than any real tree."""
    roots = {v: k for k, v in m.roots.items()}
    protected = set(m.protected.values())
    seen, cur = 0, folder_id
    if cur in roots:
        return None          # a root itself is not a home: files go in its subfolders
    while cur and seen < MAX_DEPTH:
        if cur in protected:
            return None
        parents = parents_of(cur) or []
        if not parents:
            return None
        cur = parents[0]
        if cur in roots:
            return roots[cur]
        seen += 1
    return None


def allow(db, cfg=None, keys=tuple(HUNTER_FOLDERS), *, dmap: DriveMap | None = None) -> DriveMap:
    """Load the registry, prove each of `keys` sits inside a registered root,
    and let `db` create into those folders and nothing else."""
    m = dmap or load(cfg)
    approved = set()
    for k in keys:
        fid = m.destinations.get(k)
        if not fid:
            raise DriveMapError(f"{KEY} has no destination {k}")
        if ancestry(db.parents_of, fid, m) is None:
            raise DriveMapError(f"{k} is not inside a registered root of his Drive")
        approved.add(fid)
    db.allowed = approved
    db.drive_map = m
    return m


def destination(db, key: str) -> str:
    m = getattr(db, "drive_map", None)
    fid = (m.destinations.get(key) if m else None) or ""
    if not fid or fid not in (db.allowed or ()):
        raise DriveMapError(f"{key} is not an approved destination for this run")
    return fid


_UNSAFE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")


def application_name(company: str, role: str, when: datetime.date | None = None) -> str:
    """Applications/<YYYY-MM> <Company> - <Role>, as he chose. Characters a
    folder name cannot carry on his Windows machine are replaced, and the name
    is kept short enough for his Drive's desktop mirror."""
    when = when or datetime.date.today()
    def clean(s):
        return re.sub(r"\s+", " ", _UNSAFE.sub("-", s or "")).strip(" .-") or "unknown"
    return f"{when:%Y-%m} {clean(company)[:60]} - {clean(role)[:80]}"


def application_folder(db, company: str, role: str, when: datetime.date | None = None) -> str:
    return db.folder(application_name(company, role, when), destination(db, "hunter.applications"))


# ---------- the registry, made from his Drive as it stands ----------

def _child(db, parent_id: str, name: str) -> str:
    got = [f for f in db.children(parent_id) if f["name"] == name
           and f.get("mimeType") == FOLDER_MIME]
    if len(got) != 1:
        raise DriveMapError(f"{'/'.join([name])}: {len(got)} folders of that name, need one")
    return got[0]["id"]


def _path(db, top: dict[str, str], names: tuple[str, ...]) -> str:
    if names[0] not in top:
        raise DriveMapError(f"his Drive has no {names[0]} at the top")
    cur = top[names[0]]
    for n in names[1:]:
        cur = _child(db, cur, n)
    return cur


def build_map(db, *, apply: bool, now: str = "", previous: DriveMap | None = None) -> DriveMap:
    """The registry read off his Drive: his top-level items as they stand, the
    roots and protected folders among them by name, his inbox, and Hunter's
    three folders (made under his Hunter folder with --apply, reused when
    there). A destination or inbox the previous registry holds keeps its id
    while it still sits where it must; a name is looked up only when it
    does not (his ruling, 2026-10-24: folders are found by ID)."""
    items = db.children("root")
    top = {f["name"]: f["id"] for f in items if f.get("mimeType") == FOLDER_MIME}
    top_ids = set(top.values())
    roots = {n: top[n] for n in ROOT_NAMES if n in top}
    if not roots:
        raise DriveMapError("his Drive has none of " + ", ".join(ROOT_NAMES) + " at the top")
    protected = {n: top[n] for n in PROTECTED_NAMES if n in top}
    probe = DriveMap(destinations={}, roots=roots, protected=protected,
                     top_level={}, inbox="")
    if previous and previous.inbox in top_ids:
        inbox = previous.inbox
    elif INBOX_NAME in top:
        inbox = top[INBOX_NAME]
    else:
        raise DriveMapError(f"his Drive has no {INBOX_NAME} at the top")
    dest, home = {}, None
    for key, name in HUNTER_FOLDERS.items():
        kept = (previous.destinations.get(key) if previous else "") or ""
        if kept and ancestry(db.parents_of, kept, probe):
            dest[key] = kept
            continue
        home = home or _path(db, top, HUNTER_HOME)
        have = [f for f in db.children(home) if f["name"] == name
                and f.get("mimeType") == FOLDER_MIME]
        if have:
            dest[key] = have[0]["id"]
        elif apply:
            db.allowed = (db.allowed or set()) | {home}
            dest[key] = db.folder(name, home)
        else:
            dest[key] = ""
    for key, names in AREA_FOLDERS.items():
        kept = (previous.destinations.get(key) if previous else "") or ""
        if kept and ancestry(db.parents_of, kept, probe):
            dest[key] = kept
            continue
        try:
            dest[key] = _path(db, top, names)
        except DriveMapError:
            pass  # not there: his to make, never ours
    # Another writer's destination the registry already holds keeps its id
    # while it still sits inside a registered area, as the docstring says.
    if previous:
        for key, fid in previous.destinations.items():
            if key not in dest and fid and ancestry(db.parents_of, fid, probe):
                dest[key] = fid
    m = DriveMap(destinations=dest, roots=roots, protected=protected,
                 top_level={f["id"]: f["name"] for f in items}, inbox=inbox,
                 written_at=now or datetime.datetime.now(datetime.timezone.utc)
                 .isoformat(timespec="seconds"))
    if apply:
        for key, fid in dest.items():
            if ancestry(db.parents_of, fid, m) is None:
                raise DriveMapError(f"{key} is not inside a registered area of his Drive")
    return m


def write_map(cfg, m: DriveMap) -> DriveMap:
    """Store the registry and read it back; what comes back is what counts."""
    from .config import db_insert
    db_insert(cfg, "system_config", [{"key": KEY, "value": m.to_json()}],
              on_conflict="key", merge=True)
    back = load(cfg)
    if back.to_json() != m.to_json():
        raise DriveMapError(f"{KEY} read back different from what was written")
    return back


# ---------- the hourly sweep of the top of his Drive ----------

def sweep(db, m: DriveMap, *, apply: bool) -> dict:
    """Every item at the top of his Drive that was not there when the registry
    was written is filed into his inbox (Inbox from My AI) and read back. His
    own items, and the protected folders, are never touched; nothing is
    deleted. The inbox must still be a folder at the top of his Drive: one
    that has gone or moved is refused rather than filed into."""
    items = db.children("root")
    if not any(f["id"] == m.inbox and f.get("mimeType") == FOLDER_MIME for f in items):
        raise DriveMapError(f"the inbox is not a folder at the top of his Drive "
                            f"(run drive-map --apply after {INBOX_NAME} is back)")
    keep = set(m.top_level) | set(m.protected.values()) | set(m.roots.values()) | {m.inbox}
    strays = [f for f in items if f["id"] not in keep]
    moved = []
    if apply and strays:
        db.allowed = {m.inbox}
        for f in strays:
            db.move(f["id"], m.inbox)
            moved.append(f)
    return {"top": len(items), "strays": len(strays), "moved": moved}


def tell(cfg, moved: list[dict]) -> bool:
    """One email line about what the sweep filed, to him only, once per set of
    files. The public log never carries the names; this email does, because
    they are his own files."""
    if not moved:
        return False
    import hashlib
    from . import alerts
    from .notify import send_email
    fp = hashlib.sha256(",".join(sorted(f["id"] for f in moved)).encode()).hexdigest()[:16]
    if alerts.already_sent(cfg, "drive_sweep", fp):
        return False
    names = ", ".join(f.get("name", "?") for f in moved[:10])
    more = f" and {len(moved) - 10} more" if len(moved) > 10 else ""
    line = (f"{len(moved)} file(s) landed at the top of your Drive and are now in "
            f"{INBOX_NAME}: {names}{more}. Nothing was deleted.")
    out = send_email(cfg, "Hunter filed stray files into your inbox folder",
                     f"<p>{alerts._esc(line)}</p>", text=line)
    if out.get("sent"):
        alerts.mark_sent(cfg, "drive_sweep", fp, {"count": len(moved)})
        return True
    return False


# ---------- filing what hunter made before the registry ----------

def _month(stamp: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat((stamp or "")[:10])
    except ValueError:
        return datetime.date.today()


def _doc_id(url: str) -> str:
    m = re.search(r"/d/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else ""


def plan_moves(files: list[dict], roles: list[dict], masters: set[str]) -> list[dict]:
    """Where each file hunter made before 2026-10-18 belongs. A document is
    matched to its role by the link on the role; a PDF, which no record links,
    by its name (KrishRaja_CV_<company>.pdf) to the role at that company built
    closest in time; a superseded document the same way. Masters go to
    Masters/; a file hunter named (KrishRaja_...) that no role claims goes to
    Applications/_unmatched. Everything else in those folders is his own
    (his CV versions, his positioning documents, there before hunter) and is
    left exactly where it is."""
    by_doc = {}
    by_company: dict[str, list[dict]] = {}
    for r in roles:
        for col in ("package_cv_url", "package_letter_url"):
            d = _doc_id(r.get(col) or "")
            if d:
                by_doc[d] = r
        by_company.setdefault((r.get("company") or "").strip().lower(), []).append(r)
    out = []
    for f in files:
        name, created = f.get("name") or "", f.get("createdTime") or ""
        if f["id"] in masters:
            out.append({"id": f["id"], "to": "masters"})
            continue
        role = by_doc.get(f["id"])
        if role is None:
            m = re.match(r"(?:PREVIEW .*?)?KrishRaja_(?:CV|CoverLetter)_(.+?)(?:_.*)?(?:\.pdf)?$",
                         name)
            cands = by_company.get((m.group(1) if m else "").strip().lower(), [])
            if cands:
                role = min(cands, key=lambda r: abs(
                    (_month(r.get("package_built_at") or created) - _month(created)).days))
        if role is None:
            out.append({"id": f["id"], "to": "unmatched" if name.startswith("KrishRaja_")
                        else "leave"})
        else:
            out.append({"id": f["id"], "to": "application", "company": role.get("company"),
                        "title": role.get("title"),
                        "when": (role.get("package_built_at") or created)[:10]})
    return out


def apply_moves(db, m: DriveMap, plan: list[dict]) -> dict:
    """Each move made and read back (DocBuild.move). Idempotent: a file already
    in its folder is left as it is."""
    allow(db, dmap=m)
    counts = {"application": 0, "masters": 0, "unmatched": 0}
    for p in plan:
        if p["to"] == "leave":
            continue
        if p["to"] == "masters":
            to = destination(db, "hunter.masters")
        elif p["to"] == "unmatched":
            to = db.folder(UNMATCHED, destination(db, "hunter.applications"))
        else:
            to = application_folder(db, p["company"], p["title"], _month(p["when"]))
        db.move(p["id"], to)
        counts[p["to"]] += 1
    return counts
