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
  <Role>`, under CURRENT/02_MINDMAKE-OS/Agents/Hunter (his choice, 2026-10-18).
- Anything that still lands at the top of his Drive is filed into
  CURRENT/00_INBOX by the hourly sweep, and he is told in one line. Nothing is
  ever deleted, and ContentLibrary is never touched.
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field

KEY = "drive_destinations"
FOLDER_MIME = "application/vnd.google-apps.folder"

# His top-level areas a file may sit under (00-START-HERE.md and the estate
# policy). Ventures is his live venture tree, made the same week.
ROOT_NAMES = ("CURRENT", "ARCHIVE", "FAMILY-ADMIN", "Ventures")
# Never written to, moved or listed into. ContentLibrary is the estate
# policy's protected boundary; KeePass holds his password store.
PROTECTED_NAMES = ("ContentLibrary", "KeePass")
HUNTER_HOME = ("CURRENT", "02_MINDMAKE-OS", "Agents", "Hunter")
INBOX_PATH = ("CURRENT", "00_INBOX")
HUNTER_FOLDERS = {"hunter.applications": "Applications",
                  "hunter.masters": "Masters",
                  "hunter.previews": "Previews"}
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


def build_map(db, *, apply: bool, now: str = "") -> DriveMap:
    """The registry read off his Drive: his top-level items as they stand, the
    roots and protected folders among them by name, his inbox, and Hunter's
    three folders (made under Agents/Hunter with --apply, reused when there).
    The folder names are the estate policy's; only the ids are found here."""
    items = db.children("root")
    top = {f["name"]: f["id"] for f in items if f.get("mimeType") == FOLDER_MIME}
    roots = {n: top[n] for n in ROOT_NAMES if n in top}
    if "CURRENT" not in roots:
        raise DriveMapError("his Drive has no CURRENT at the top")
    protected = {n: top[n] for n in PROTECTED_NAMES if n in top}
    home = _path(db, top, HUNTER_HOME)
    inbox = _path(db, top, INBOX_PATH)
    dest = {}
    for key, name in HUNTER_FOLDERS.items():
        have = [f for f in db.children(home) if f["name"] == name
                and f.get("mimeType") == FOLDER_MIME]
        if have:
            dest[key] = have[0]["id"]
        elif apply:
            db.allowed = (db.allowed or set()) | {home}
            dest[key] = db.folder(name, home)
        else:
            dest[key] = ""
    m = DriveMap(destinations=dest, roots=roots, protected=protected,
                 top_level={f["id"]: f["name"] for f in items}, inbox=inbox,
                 written_at=now or datetime.datetime.now(datetime.timezone.utc)
                 .isoformat(timespec="seconds"))
    if apply:
        for key, fid in [*dest.items(), ("inbox", inbox)]:
            if ancestry(db.parents_of, fid, m) != "CURRENT":
                raise DriveMapError(f"{key} is not inside CURRENT")
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
    was written is filed into CURRENT/00_INBOX and read back. His own items,
    and the protected folders, are never touched; nothing is deleted."""
    if ancestry(db.parents_of, m.inbox, m) != "CURRENT":
        raise DriveMapError("the inbox is not inside CURRENT")
    keep = set(m.top_level) | set(m.protected.values()) | set(m.roots.values())
    items = db.children("root")
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
            f"CURRENT/00_INBOX: {names}{more}. Nothing was deleted.")
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
