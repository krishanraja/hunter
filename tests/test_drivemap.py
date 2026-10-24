"""Every file hunter makes in his Drive has a registered home (drivemap.py).

His words, 2026-10-18: "ensure it is impossible to save files randomly in
sprawl in my Drive - they must go in specific folders, subfolders, always".
Before it, DocBuild.folder defaulted to the top of his Drive and copy_master
filed a copy wherever Drive put it. Every folder id here is invented.
"""
import ast
import importlib.util
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fake_docs import FakeDocBuild  # noqa: E402
from hunter import docbuild as docbuild_mod  # noqa: E402
from hunter import drivemap  # noqa: E402

SRC = pathlib.Path(drivemap.__file__).parent
ROOT = SRC.parent.parent
MASTER = {"body": {"content": [{"startIndex": 0, "endIndex": 2, "paragraph": {
    "elements": [{"startIndex": 0, "endIndex": 2, "textRun": {"content": "x\n"}}]}}]}}


def fake_map(**over):
    base = dict(destinations={"hunter.applications": "apps", "hunter.masters": "masters",
                              "hunter.previews": "prev"},
                roots={"Ventures": "cur"}, protected={"FamilyContentLibrary": "cl"},
                top_level={"cur": "Ventures", "cl": "FamilyContentLibrary", "inbox": "Inbox from My AI"}, inbox="inbox")
    base.update(over)
    return drivemap.DriveMap(**base)


# ---------- the guard in DocBuild ----------

def test_nothing_is_created_before_the_registry_is_loaded():
    db = FakeDocBuild({"m": MASTER}, allowed=None)
    for make in (lambda: db.copy_master("m", "t", "apps"),
                 lambda: db.upload_pdf("t.pdf", "apps", b"%PDF"),
                 lambda: db.folder("x", "apps")):
        with pytest.raises(docbuild_mod.NoHome):
            make()
    assert db._copies == 0 and db.uploaded_pdfs == [] and db.folders == {}


def test_there_is_no_default_parent_anywhere_in_docbuild():
    """The old default was "root", the top of his Drive."""
    tree = ast.parse((SRC / "docbuild.py").read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name in ("copy_master", "upload_pdf", "folder"):
            names = [a.arg for a in fn.args.args]
            assert "parent_id" in names
            assert len(fn.args.defaults) <= len(names) - names.index("parent_id") - 1, \
                f"{fn.name} gives parent_id a default"
    consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)]
    assert "root" not in consts


def test_drive_writes_happen_only_in_docbuilds_guarded_helpers():
    """A Drive create anywhere else in src/ would skip the guard."""
    helpers = {"_copy", "_make_folder", "_upload", "_reparent"}
    tree = ast.parse((SRC / "docbuild.py").read_text())
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        src = ast.get_source_segment((SRC / "docbuild.py").read_text(), fn) or ""
        writes = ("requests.post(DRIVE" in src or "requests.post(f\"{DRIVE}" in src
                  or "requests.patch(" in src or "DRIVE_UPLOAD" in src.split(":", 1)[-1])
        if writes and fn.name != "DocBuild":
            assert fn.name in helpers, f"{fn.name} writes to Drive outside the guarded helpers"
    for path in SRC.rglob("*.py"):
        if path.name in ("docbuild.py",):
            continue
        text = path.read_text()
        for marker in ("drive/v3/files", "upload/drive", "DRIVE_UPLOAD", "/copy\""):
            assert marker not in text, f"{path.relative_to(ROOT)} talks to Drive directly"


def test_a_copy_lands_in_its_application_folder_and_is_read_back():
    db = FakeDocBuild({"m": MASTER}, allowed=None)
    drivemap.allow(db, dmap=fake_map())
    home = drivemap.application_folder(db, "Ostravia", "Chief of Staff")
    new = db.copy_master("m", "KrishRaja_CV_Ostravia", home)
    assert db.parents_of(new) == [home] and db.parents_of(home) == ["apps"]
    name = [k for k, v in db.folders.items() if v == home][0][1]
    assert name.endswith(" Ostravia - Chief of Staff") and name[:7].count("-") == 1


def test_a_destination_outside_a_registered_root_is_refused():
    db = FakeDocBuild({}, allowed=None)
    db.parent_of["loose"] = ["root"]
    with pytest.raises(drivemap.DriveMapError):
        drivemap.allow(db, dmap=fake_map(destinations={"hunter.applications": "loose",
                                                       "hunter.masters": "masters",
                                                       "hunter.previews": "prev"}))
    db.parent_of["inside_cl"] = ["cl"]
    with pytest.raises(drivemap.DriveMapError):
        drivemap.allow(db, keys=("hunter.applications",),
                       dmap=fake_map(destinations={"hunter.applications": "inside_cl"}))
    with pytest.raises(drivemap.DriveMapError):
        drivemap.allow(db, dmap=fake_map(destinations={"hunter.applications": "apps"}))
    assert db.allowed is None


def test_ancestry_reads_the_chain_up_to_a_root():
    parents = {"a": ["b"], "b": ["cur"], "cur": ["root"], "x": ["root"], "p": ["vault"],
               "vault": ["cur"], "loop1": ["loop2"], "loop2": ["loop1"]}
    m = fake_map(protected={"ContentLibrary": "cl", "Vault": "vault"})
    get = lambda f: parents.get(f, [])
    assert drivemap.ancestry(get, "a", m) == "Ventures"
    assert drivemap.ancestry(get, "cur", m) is None, "a root itself is not a home"
    assert drivemap.ancestry(get, "x", m) is None, "the top of his Drive is not a home"
    assert drivemap.ancestry(get, "p", m) is None, "nothing inside a protected folder"
    assert drivemap.ancestry(get, "loop1", m) is None
    assert drivemap.ancestry(get, "", m) is None


def test_application_names_are_safe_on_his_windows_mirror():
    import datetime
    n = drivemap.application_name('Acme/Co: "AI"', "VP, Growth | EMEA", datetime.date(2026, 10, 1))
    assert n == "2026-10 Acme-Co- -AI - VP, Growth - EMEA"
    assert not any(c in n for c in '\\/:*?"<>|')


def test_the_registry_must_be_whole():
    with pytest.raises(drivemap.DriveMapError):
        drivemap.parse('{"destinations": {"hunter.applications": "a"}}')
    with pytest.raises(drivemap.DriveMapError):
        drivemap.parse("not json")
    assert drivemap.parse(fake_map().to_json()).inbox == "inbox"


# ---------- the hourly sweep ----------

def _drive_with_stray():
    db = FakeDocBuild({}, allowed=None)
    db.parent_of.update({"cl": ["root"], "mine": ["root"], "stray": ["root"],
                         "stray_folder": ["root"]})
    db.meta = {"stray": {"name": "Untitled document", "mimeType": "x"},
               "stray_folder": {"name": "agent output", "mimeType": docbuild_mod.FOLDER_MIME},
               "inbox": {"name": "Inbox from My AI", "mimeType": docbuild_mod.FOLDER_MIME}}
    return db


def test_the_sweep_files_only_new_strays_into_his_inbox():
    db = _drive_with_stray()
    m = fake_map(top_level={"cur": "Ventures", "cl": "FamilyContentLibrary", "mine": "START-HERE",
                            "inbox": "Inbox from My AI"})
    dry = drivemap.sweep(db, m, apply=False)
    assert dry["strays"] == 2 and dry["moved"] == [] and db.moves == []
    out = drivemap.sweep(db, m, apply=True)
    assert sorted(f["id"] for f in out["moved"]) == ["stray", "stray_folder"]
    assert db.parents_of("stray") == ["inbox"] and db.parents_of("stray_folder") == ["inbox"]
    assert db.parents_of("mine") == ["root"] and db.parents_of("cl") == ["root"]
    again = drivemap.sweep(db, m, apply=True)
    assert again["strays"] == 0 and again["moved"] == []


def test_the_sweep_never_touches_a_protected_folder_even_if_unregistered():
    db = _drive_with_stray()
    m = fake_map(top_level={"cur": "Ventures", "mine": "START-HERE", "inbox": "Inbox from My AI"})
    drivemap.sweep(db, m, apply=True)
    assert db.parents_of("cl") == ["root"]


def test_the_sweep_refuses_an_inbox_that_is_no_longer_at_the_top():
    db = _drive_with_stray()
    db.parent_of["inbox"] = ["cur"]
    with pytest.raises(drivemap.DriveMapError):
        drivemap.sweep(db, fake_map(), apply=True)
    assert db.moves == []


def _his_drive():
    """His Drive as he laid it out on 2026-10-24 (filearchives D-033)."""
    db = FakeDocBuild({}, allowed=None)
    F = docbuild_mod.FOLDER_MIME
    db.parent_of = {"ven": ["root"], "arc": ["root"], "fam": ["root"], "fcl": ["root"],
                    "kp": ["root"], "ai": ["root"], "krish": ["fam"], "js": ["krish"],
                    "hunter": ["js"], "a": ["hunter"], "m": ["hunter"], "p": ["hunter"]}
    names = {"ven": "Ventures", "arc": "--ARCHIVE", "fam": "Family Admin",
             "fcl": "FamilyContentLibrary", "kp": "KeePass", "ai": "Inbox from My AI",
             "krish": "Krish", "js": "Job-Search", "hunter": "Hunter",
             "a": "Applications", "m": "Masters", "p": "Previews"}
    db.meta = {k: {"name": v, "mimeType": F} for k, v in names.items()}
    return db


def test_the_map_is_read_off_his_drive_as_he_laid_it_out():
    m = drivemap.build_map(_his_drive(), apply=True, now="2026-10-24T00:00:00Z")
    assert m.roots == {"Ventures": "ven", "--ARCHIVE": "arc", "Family Admin": "fam"}
    assert m.protected == {"FamilyContentLibrary": "fcl", "KeePass": "kp"}
    assert m.inbox == "ai"
    assert m.destinations == {"hunter.applications": "a", "hunter.masters": "m",
                              "hunter.previews": "p"}


def test_a_rebuild_keeps_registered_folders_by_id_after_he_moves_or_renames_them():
    db = _his_drive()
    first = drivemap.build_map(db, apply=True)
    # He renames the inbox and moves Hunter's folder elsewhere inside Ventures.
    db.meta["ai"]["name"] = "AI drops"
    db.meta["hunter"]["name"] = "Jobs bot"
    db.parent_of["hunter"] = ["ven"]
    again = drivemap.build_map(db, apply=True, previous=first)
    assert again.inbox == "ai"
    assert again.destinations == first.destinations
    assert db.folders == {}, "nothing was made again by name"


def test_a_map_with_no_inbox_at_the_top_is_refused():
    db = _his_drive()
    db.parent_of["ai"] = ["ven"]
    with pytest.raises(drivemap.DriveMapError):
        drivemap.build_map(db, apply=True)


# ---------- filing what hunter made before ----------

def test_the_old_files_are_matched_to_their_roles():
    roles = [{"company": "Ostravia", "title": "Chief of Staff", "package_built_at": "2026-10-02T10:00",
              "package_cv_url": "https://docs.google.com/document/d/cv1/edit",
              "package_letter_url": "https://docs.google.com/document/d/cl1/edit"},
             {"company": "Ostravia", "title": "VP Growth", "package_built_at": "2026-09-10T10:00",
              "package_cv_url": "", "package_letter_url": ""}]
    files = [{"id": "cv1", "name": "KrishRaja_CV_Ostravia", "createdTime": "2026-10-02T10:01"},
             {"id": "p1", "name": "KrishRaja_CV_Ostravia.pdf", "createdTime": "2026-09-10T10:02"},
             {"id": "old", "name": "KrishRaja_CoverLetter_Ostravia_vp-growth",
              "createdTime": "2026-09-11T09:00"},
             {"id": "mst", "name": "CV master", "createdTime": "2026-09-01"},
             {"id": "zz", "name": "notes from a call", "createdTime": "2026-09-01"},
             {"id": "orph", "name": "KrishRaja_CV_Gone Co", "createdTime": "2026-09-01"}]
    plan = {p["id"]: p for p in drivemap.plan_moves(files, roles, {"mst"})}
    assert plan["cv1"]["title"] == "Chief of Staff"
    assert plan["p1"]["title"] == "VP Growth", "a PDF goes to the role built nearest in time"
    assert plan["old"]["title"] == "VP Growth"
    assert plan["mst"]["to"] == "masters" and plan["orph"]["to"] == "unmatched"
    assert plan["zz"]["to"] == "leave", "his own documents in that folder are never moved"


def test_filing_moves_each_file_and_reads_it_back_once():
    db = FakeDocBuild({}, allowed=None)
    db.parent_of.update({"old_cv_folder": ["arch"], "arch": ["root"], "cv1": ["old_cv_folder"],
                         "zz": ["old_cv_folder"], "mst": ["old_cv_folder"]})
    plan = [{"id": "cv1", "to": "application", "company": "Ostravia",
             "title": "Chief of Staff", "when": "2026-10-02"},
            {"id": "zz", "to": "unmatched"}, {"id": "mst", "to": "masters"},
            {"id": "his", "to": "leave"}]
    db.parent_of["his"] = ["old_cv_folder"]
    counts = drivemap.apply_moves(db, fake_map(), plan)
    assert db.parents_of("his") == ["old_cv_folder"]
    assert counts == {"application": 1, "masters": 1, "unmatched": 1}
    app = db.parents_of("cv1")[0]
    assert db.parents_of(app) == ["apps"] and db.parents_of("mst") == ["masters"]
    assert db.parents_of(db.parents_of("zz")[0]) == ["apps"]
    moved = len(db.moves)
    drivemap.apply_moves(db, fake_map(), plan)
    assert len(db.moves) == moved, "a file already in its folder is left as it is"


# ---------- the guard for Claude sessions ----------

def _guard():
    spec = importlib.util.spec_from_file_location("drive_guard",
                                                  ROOT / ".claude" / "hooks" / "drive_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REG = {"top_level": {"cur": "Ventures", "arch": "--ARCHIVE"}, "roots": {"Ventures": "cur"},
       "protected": {"FamilyContentLibrary": "cl", "KeePass": "kp"}}


@pytest.mark.parametrize("tool", ["mcp__Google_Drive__create_file", "mcp__google-drive__copy_file"])
def test_the_session_hook_refuses_a_loose_file(tool):
    g = _guard()
    ok = lambda: REG
    assert "names no folder" in g.decide(tool, {"title": "x"}, ok)
    assert "top of his Drive" in g.decide(tool, {"parentId": "root"}, ok)
    assert "top-level areas" in g.decide(tool, {"parentId": "cur"}, ok)
    assert "protected" in g.decide(tool, {"parentId": "kp"}, ok)
    assert "could not be read" in g.decide(tool, {"parentId": "sub"}, lambda: None)
    assert g.decide(tool, {"parentId": "sub"}, ok) == ""


def test_the_session_hook_lets_everything_else_through():
    g = _guard()
    assert g.decide("mcp__Google_Drive__update_file", {"fileId": "f", "title": "x"},
                    lambda: None) == ""
    assert "top of his Drive" in g.decide("mcp__Google_Drive__update_file",
                                          {"fileId": "f", "parentId": "root"}, lambda: REG)
    assert g.decide("mcp__Google_Drive__read_file_content", {}, lambda: None) == ""
    assert g.decide("Write", {"file_path": "/tmp/x"}, lambda: None) == ""


def test_both_repos_register_the_hook_for_drive_writes():
    import json
    import re
    hooks = json.loads((ROOT / ".claude" / "settings.json").read_text())["hooks"]["PreToolUse"]
    pattern = hooks[0]["matcher"]
    assert "drive_guard.py" in hooks[0]["hooks"][0]["command"]
    for tool in ("mcp__Google_Drive__create_file", "mcp__google-drive__copy_file",
                 "mcp__Google_Drive__update_file"):
        assert re.fullmatch(pattern, tool)
    assert not re.fullmatch(pattern, "mcp__Google_Drive__read_file_content")
