#!/usr/bin/env python3
"""Claude Code PreToolUse hook: no session files anything loose in his Drive.

His words, 2026-10-18: "ensure it is impossible to save files randomly in
sprawl in my Drive - they must go in specific folders, subfolders, always".

The Google Drive tools file a new file at the top of his Drive when no parent
is named (create_file) or next to the original (copy_file). So a create or a
copy must name a parent folder, and that folder may not be the top of his
Drive, one of his top-level areas itself (Ventures, --ARCHIVE, Family Admin:
files go in their subfolders), or a protected folder (FamilyContentLibrary,
KeePass: "KeePass and FamilyContentLibrary are immovable", 2026-10-24). A
move (update_file with a parentId) is held to the same rule.

The registry is system_config drive_destinations (src/hunter/drivemap.py),
read with the session's Supabase key. When it cannot be read, a call naming a
parent is refused: unknown is not allowed. Standard library only, so the same
file runs in the control-center repo, which does not install hunter.

Exit 2 with the reason on stderr blocks the call; exit 0 lets it through.
"""
import json
import os
import sys
import urllib.request

ACTIONS = ("create_file", "copy_file", "update_file")
INBOX_HINT = "Inbox from My AI (at the top of his Drive) when unsure where it belongs"


def is_drive_write(tool: str) -> str:
    """The action of a Google Drive write tool, or "" for anything else. The
    server's prefix differs by surface (mcp__Google_Drive__, mcp__google-drive__)."""
    head, _, action = tool.rpartition("__")
    return action if action in ACTIONS and "drive" in head.lower() else ""


def registry():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return None
    req = urllib.request.Request(
        f"{url}/rest/v1/system_config?select=value&key=eq.drive_destinations",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = json.load(r)
        return json.loads(rows[0]["value"]) if rows else None
    except Exception:
        return None


def decide(tool: str, tool_input: dict, load=registry) -> str:
    """The reason to refuse, or "" to allow."""
    action = is_drive_write(tool)
    if not action:
        return ""
    parent = str((tool_input or {}).get("parentId") or "").strip()
    if action == "update_file" and not parent:
        return ""        # a rename or a description: nothing moves
    if not parent:
        return (f"Drive {action} names no folder, so it would land loose in his Drive. "
                f"Pass parentId: a subfolder of Ventures (Active Ventures, Inspiration, Bugs & Issues), "
                f"--ARCHIVE or Family Admin, or {INBOX_HINT}.")
    if parent.lower() == "root":
        return f"the top of his Drive is never a home. Use a subfolder, or {INBOX_HINT}."
    reg = load()
    if not reg:
        return ("the Drive map (system_config drive_destinations) could not be read, so "
                "nothing is filed into his Drive from this session.")
    if parent in set((reg.get("protected") or {}).values()):
        return "that folder is protected (FamilyContentLibrary or KeePass); nothing is filed there."
    if parent in set(reg.get("top_level") or {}) | set((reg.get("roots") or {}).values()):
        return (f"that is one of his top-level areas itself; files go in its subfolders, "
                f"or {INBOX_HINT}.")
    return ""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    why = decide(data.get("tool_name", ""), data.get("tool_input") or {})
    if why:
        print(f"Refused by the Drive guard: {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
