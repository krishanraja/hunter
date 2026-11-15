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


# ---------- no phone number of his, or anyone's ----------
#
# His US number sat in three public test files until 2026-10-15. Strict on
# purpose: a loose "phone-shaped" pattern also matches Ashby UUIDs and the
# epoch timestamps in the fixtures. A phone here is international (+ and ten
# digits or more), UK national (0 then nine or ten digits, spaced or not), or
# US national with separators. The only ones allowed are the ranges reserved
# for fiction: US exchange 555, lines 0100 to 0199, and UK 07700 900xxx.
PHONE_PATTERNS = [
    re.compile(r"(?<![\w+])\+\d[\d\s().-]{8,18}\d(?!\w)"),
    re.compile(r"(?<![\w.-])0[1-9]\d{2,4}[\s-]?\d{3}[\s-]?\d{3,4}(?![\w-])"),
    re.compile(r"(?<![\w.-])\(?[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w-])"),
]
PHONE_SUFFIXES = TEXT_SUFFIXES | {".html", ".js", ".ts", ".csv"}


def invented_phone(digits: str) -> bool:
    if len(digits) in (10, 11) and digits[-7:-4] == "555" and digits[-4:-2] == "01":
        return True
    national = "0" + digits[2:] if digits.startswith("44") else digits
    return national.startswith("07700900")


def phones_in(text: str) -> list[str]:
    """Every phone number in text outside the invented ranges, masked."""
    out = []
    for pat in PHONE_PATTERNS:
        for m in pat.finditer(text):
            digits = re.sub(r"\D", "", m.group())
            if len(digits) < 10 or invented_phone(digits):
                continue
            out.append("*" * (len(digits) - 2) + digits[-2:])
    return out


def test_no_phone_number_outside_the_invented_ranges():
    offenders = []
    for path in sorted(ROOT.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not (path.is_file() and path.suffix in PHONE_SUFFIXES):
            continue
        for found in phones_in(path.read_text(errors="replace")):
            # Masked: the failure message is printed in a public log.
            offenders.append(f"{path.relative_to(ROOT)}: {found}")
    assert not offenders, f"phone number found: {offenders}"


def test_the_phone_guard_catches_a_number_in_any_shape():
    # Built at run time, so this file holds none of them whole.
    uk = "07" + "123456789"
    for shape in (uk, uk[:5] + " " + uk[5:], "+44 " + uk[1:5] + " " + uk[5:],
                  "+44" + uk[1:], "(415) " + "234-5678", "415." + "234.5678",
                  "+1 415 " + "234 5678", "+1" + "4152345678"):
        assert phones_in(f"call {shape} today"), shape


def test_the_phone_guard_leaves_ids_dates_and_invented_numbers_alone():
    for fine in ("123e4567-e89b-12d3-a456-426614174000",
                 "00000000-0000-0000-0000-000000000000",
                 "1727222400", "1727222400000", "2026-10-15", "2026101512",
                 '"startIndex": 2905, "endIndex": 2951',
                 "+1 (212) 555-0142", "(212) 555-0199", "07700 900123",
                 "+44 7700 900456", "0.3529412", "v1.2.3"):
        assert not phones_in(fine), fine


# ---------- no bot-detection evasion ----------
#
# Section 9 of CLAUDE.md. Only the code that ships or runs is scanned; docs and
# tests name these tools to record why they are absent.
def test_nothing_in_hunter_works_around_bot_detection():
    """CLAUDE.md section 9, made mechanical. Masking navigator.webdriver and a
    spoofed user agent were built once and reverted; Camoufox was measured on
    2026-11-15 (tests/test_browser_eval.py) and its only edge over Hunter's own
    Chromium was getting past Cloudflare challenges that had refused it. A site
    that refuses an automated reader is an answer, not an obstacle."""
    offenders = []
    for path in evasion_scope():
        for found in evasion_in(path.read_text(errors="replace")):
            offenders.append(f"{path.relative_to(ROOT)}: {found}")
    assert not offenders, f"bot-detection evasion found: {offenders}"


def test_the_evasion_guard_catches_each_shape_and_leaves_ordinary_words_alone():
    # Built at run time, so this file is not itself an offender.
    for shape in ("from " + "camoufox.sync_api import Camoufox",
                  '"@askjo/' + 'camofox-browser": "1.19.0"',
                  "import playwright" + "_stealth", "undetected" + "_chromedriver",
                  "cloud" + "scraper.create_scraper()", "from curl" + "_cffi import requests",
                  "--disable-blink-features=" + "AutomationControlled",
                  "Object.define" + "Property(navigator, 'webdriver', {get: () => false})"):
        assert evasion_in(shape), shape
    for fine in ("Stealth Vertical AI Startup", "stealth mode", "attempt_fingerprint",
                 "the fingerprint of a score build", "navigator.webdriver true, which "
                 "is what an invisible bot check scores"):
        assert not evasion_in(fine), fine


EVASION = re.compile(
    r"\b(?:camou?fox|playwright[_-]stealth|puppeteer-extra-plugin-stealth|"
    r"undetected[_-]chromedriver|selenium[_-]stealth|cloudscraper|curl_cffi|tls_client|"
    r"nodriver|botasaurus)\b|AutomationControlled|"
    r"defineProperty\(\s*navigator\s*,\s*['\"]webdriver", re.I)


def evasion_in(text: str) -> list[str]:
    return sorted({m.group(0) for m in EVASION.finditer(text)})


def evasion_scope():
    """Code and dependency files that ship or run: never docs or tests, which
    name these tools to say why they are absent."""
    roots = [ROOT / "src", ROOT / "extension", ROOT / ".github"]
    files = [ROOT / "pyproject.toml"]
    for root in roots:
        files += [p for p in sorted(root.rglob("*")) if p.is_file()
                  and p.suffix in {".py", ".js", ".ts", ".json", ".toml", ".yml", ".yaml", ".txt"}
                  and not any(part in SKIP_DIRS for part in p.parts)]
    return [p for p in files if p.exists()]
