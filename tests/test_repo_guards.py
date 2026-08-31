"""Repo-wide guards: no em dash anywhere in the tree, no secret-shaped values.
These enforce two of the brief's ground rules mechanically."""
import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "hunter.egg-info", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".json", ".cfg", ".ini", ".yaml", ".yml"}

SECRET_PATTERNS = [
    re.compile(r"sbp_[A-Za-z0-9]{30,}"),
    re.compile(r"apify_api_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    re.compile(r"ya29\.[A-Za-z0-9_-]{30,}"),
    re.compile(r"eyJhbGciOi[A-Za-z0-9_-]{20,}"),
]


def tracked_files():
    for path in sorted(ROOT.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            yield path


EM_DASH = "\u2014"


def test_no_em_dash_anywhere():
    offenders = []
    for path in tracked_files():
        text = path.read_text(errors="replace")
        if EM_DASH in text:
            line = next(i + 1 for i, l in enumerate(text.split("\n")) if EM_DASH in l)
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert not offenders, f"em dash found in: {offenders}"


def test_no_secret_shaped_values():
    offenders = []
    for path in tracked_files():
        text = path.read_text(errors="replace")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {pat.pattern}")
    assert not offenders, f"secret-shaped value found: {offenders}"


def test_no_outbound_messaging_outside_notify():
    """The draft-only posture is structural: nothing in the tree may import a
    send-capable channel except notify.py, which can only message Krish."""
    banned = re.compile(r"\b(smtplib|sendmail|instantly|slack_sdk)\b", re.I)
    offenders = []
    for path in tracked_files():
        if path.suffix != ".py" or path.name in ("notify.py", "test_repo_guards.py"):
            continue
        if banned.search(path.read_text(errors="replace")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"outbound-messaging reference outside notify.py: {offenders}"
