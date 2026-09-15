"""The browser extension, as a file on disk.

Krish is on Windows with no Python and no terminal. A page may not read his disk,
but it MAY put bytes it already has into a file input, so a content script can
fill the whole form and attach the CV, which is what finally makes his one-click
loop possible. Verified against the live Harvey form: 14 of 14 fields.

These are the properties that must not drift, checked against the shipped files
rather than a description of them.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

EXT = pathlib.Path(__file__).resolve().parents[1] / "extension"
FILL = (EXT / "fill.js").read_text()
RUN = (EXT / "run.js").read_text()


def code_only(src: str) -> str:
    """The source with comments stripped, so prose cannot satisfy a check."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


def test_the_extension_cannot_press_submit():
    """The one property that matters. The last click on a job application is his,
    and an extension that could press would be a submission he never saw."""
    for src in (FILL, RUN):
        body = code_only(src).lower()
        assert "submit application" not in body
        assert "submit()" not in body
        assert 'type="submit"' not in body
        assert not re.search(r"querySelector[^\n]*submit", body)


def test_the_capability_travels_in_the_fragment():
    """A fragment is never sent to a server, so the employer's site never
    receives the key and neither does anything in between."""
    assert "location.hash" in RUN
    assert "location.search" not in RUN


def test_it_waits_for_the_form_before_filling():
    """Ashby fetches its form after the page loads. A script that fills on
    document_idle finds nothing."""
    assert "querySelector('input, textarea, select')" in RUN


def test_the_manifest_reaches_only_the_boards_it_fills():
    manifest = json.loads((EXT / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    matches = manifest["content_scripts"][0]["matches"]
    assert matches == ["https://jobs.ashbyhq.com/*",
                       "https://job-boards.greenhouse.io/*"]
    # No <all_urls>, no broad tabs or history permission: it can read the two
    # job boards it fills and nothing else of his browsing.
    assert manifest["permissions"] == ["storage"]
    for host in manifest["host_permissions"]:
        assert host.startswith("https://")
        assert "<all_urls>" not in host


def test_it_acts_then_reads_back_rather_than_assuming():
    """React updates its state after a click, so a synchronous read straight
    afterwards sees the old value: the work authorisation question was set
    correctly on the live form and reported unfilled. One pass acts, then it
    waits, then a second pass reads the DOM."""
    body = code_only(FILL)
    assert "async function run(" in body
    assert "await sleep(" in body
    for reader in ("holdsText", "holdsChoice", "holdsFile", "holdsCombo"):
        assert f"function {reader}(" in body


def test_an_unmatched_typeahead_leaves_the_box_empty():
    """A half typed place reads on the page as an answer and is not one."""
    assert "setNative(el, '');" in FILL


def test_a_required_field_it_could_not_fill_is_reported_loudly():
    assert "required_missed" in FILL and "required_missed" in RUN
    assert "DO THESE YOURSELF" in RUN


def test_a_no_answer_on_a_yes_no_control_reads_as_answered():
    """A segmented Yes/No answers "No" by pressing the No button, which
    correctly leaves the mirror checkbox FALSE. Reading only the checkbox called
    the OpenAI sponsorship question unfilled and told him to do it himself."""
    body = code_only(FILL)
    where = body[body.index("function holdsChoice"):]
    where = where[:where.index("function holdsFile")]
    assert "aria-pressed" in where, "the pressed button is the answer, not the mirror"


def test_a_document_never_lands_in_a_slot_another_already_took():
    """The cover letter's own selector missed, fell through to the generic
    input[type=file], and landed on top of the CV: the Resume box held
    KrishRaja_CoverLetter.pdf and the Cover letter box was empty."""
    body = code_only(FILL)
    assert "usedInputs" in body
    where = body[body.index("function actFile"):]
    where = where[:where.index("function ") + where[where.index("function ") + 8:].index("function ")]
    assert "usedInputs.has(el)" in where


def test_demographics_match_on_word_boundaries():
    """"male" is inside "female"."""
    body = code_only(FILL)
    assert "function wordMatch" in body
    assert "actDemographic" in body
    where = body[body.index("function wordMatch"):body.index("function actDemographic")]
    assert "/[a-z0-9]/" in where, "a substring test picks Female for Male"


def test_an_ambiguous_demographic_is_left_for_him():
    body = code_only(FILL)
    where = body[body.index("function actDemographic"):]
    assert "hits.length !== 1" in where, "none, or more than one, is not an answer"


def test_a_superseded_link_explains_itself():
    """Krish opened an older email and got "the server said 404"."""
    assert "410" in RUN and "replaced by a newer one" in RUN


def test_the_extension_watches_for_the_submit():
    """Krish: "can you confirm that when I click the submit button, the extension
    can read that I successfully submitted, and make the appropriate changes in
    the google sheet?" It could not: it filled the form and stopped, so the sheet
    kept saying "Not applied" on a role that was applied for."""
    body = code_only(RUN)
    assert "SUBMITTED_MARKS" in body
    assert "/submitted" in body


def test_only_the_form_saying_so_counts_as_a_submission():
    """A navigation is not evidence.

    An earlier version also treated any move off the /application path as a
    submission, so clicking back to the job description recorded an application
    that was never sent: the role moved to Applied on his sheet, the receipt email
    claimed the form had acknowledged it, and hunter then skipped his real APPROVE
    for that role because the token already read submitted.

    Nothing is lost. A real submission that only redirects is caught when the new
    page carries one of the marks, or by hunter's watcher on the employer's own
    "thanks for applying" email, which is better evidence than anything a script
    in the page can see.
    """
    body = code_only(RUN)
    assert "location.href !== startedAt" not in body
    assert "startedAt" not in body, "the navigation test is gone, not commented out"
    # And the evidence quotes the words it matched, so the receipt email can quote
    # something real rather than asserting a press nobody observed.
    assert "the form said" in body
    # A phrase already on the page is the baseline and never counts. Without this,
    # "Application complete" as a step label on a multi step form reports a
    # submission 1.5 seconds after the fill, before he has read anything.
    assert "baseline" in body
    # What the baseline actually does is checked by running the file, in
    # test_what_the_extension_decides_when_it_actually_runs.


def test_a_refused_write_is_not_reported_as_success():
    """fetch does not throw on a 4xx or 5xx, so the previous version painted the
    green "Hunter has it" banner over a write that never happened. That is the
    same lie as a silent failure, pointing the other way."""
    body = code_only(RUN)
    post = body[body.index("'/submitted'"):]
    assert "res.ok" in post, "the status is read"
    assert "res.status === 410" in post, "a replaced application says so"
    # The green banner cannot be reached without passing the status check.
    ok_at = post.index("res.ok")
    green_at = post.index("Hunter has it")
    assert ok_at < green_at


def test_it_watches_without_pressing_anything():
    """Watching for his click must not become clicking for him."""
    body = code_only(RUN).lower()
    assert ".click(" not in body


def test_it_gives_up_watching_rather_than_running_for_ever():
    """And the watch it stored has to expire too, or one left behind could fire on
    a page he opens days later."""
    assert "30 * 60 * 1000" in RUN
    body = code_only(RUN)
    assert "Date.now() < watch.until" in body, "the loop has an end"
    assert "Date.now() > w.until" in body, "and a stored watch is checked against it"


def test_a_failure_to_report_is_told_to_him():
    """Silently failing to record it is the same silence in a new place."""
    assert "could not be told" in RUN


def test_the_manifest_version_matches_the_payload_floor():
    """The two numbers that decide whether Krish is told his copy is stale.

    payload.MIN_EXTENSION is the oldest build that can fill what hunter now
    sends. If the shipped manifest were below it, every freshly downloaded
    extension would open every application under an out-of-date warning; if the
    manifest were raised without the floor, a stale copy would stay silent. They
    move together.
    """
    from hunter.apply import payload as P
    shipped = json.loads((EXT / "manifest.json").read_text())["version"]
    assert shipped == P.MIN_EXTENSION


def test_a_stale_extension_says_so_rather_than_claiming_success():
    """The failure this exists to stop: Krish opened an application whose payload
    carried his equal opportunity answers, his extension predated the code that
    reads them, the section came up empty, and the banner said every field was
    filled. A version he cannot see is a silent failure."""
    body = code_only(RUN)
    assert "getManifest()" in body            # it knows its own version
    assert "needs_extension" in body          # and what the payload requires
    assert "older(MINE" in body               # and compares them
    # The warning has to reach him before the reassurance does.
    stale = body.index("older(MINE, payload.needs_extension)")
    green = body.index("Filled all ")
    assert stale < green
    # And it must say what to do, not merely that something is wrong.
    assert "main.zip" in RUN
    assert "chrome://extensions" in RUN
    assert "reload" in RUN.lower()


def test_the_version_compare_gets_the_awkward_cases_right():
    """1.10.0 is newer than 1.9.0, and a missing segment is a zero. Run the real
    function rather than a description of it, offline, through node."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    fn = re.search(r"function older\(mine, need\) \{.*?\n  \}", RUN, re.S)
    assert fn, "older() is no longer in run.js"
    cases = [("1.0.0", "1.1.0", True), ("1.1.0", "1.1.0", False),
             ("1.2.0", "1.1.0", False), ("1.9.0", "1.10.0", True),
             ("1.10.0", "1.9.0", False), ("1.1", "1.1.0", False),
             ("1", "1.1.0", True), ("", "1.1.0", True)]
    script = fn.group(0).replace("\n  ", "\n") + "\n" + "\n".join(
        f"if (older({m!r}, {n!r}) !== {str(w).lower()}) "
        f"throw new Error({m!r} + ' vs ' + {n!r});" for m, n, w in cases)
    subprocess.run([node, "-e", script], check=True, timeout=30)


def test_what_the_extension_decides_when_it_actually_runs():
    """The six behaviours that decide whether a role is recorded as applied.

    Reading the source for the right strings cannot answer any of them, so
    tests/extension_behaviour.mjs builds a page, a chrome API and a fetch, runs the
    real file, and checks what it does:

      a confirmation phrase already on the page is the baseline, never a report.
        "Application complete" is an ordinary step label on a multi step form, and
        the first tick runs 1.5 seconds after the fill, before he has read anything

      a mark that APPEARS is reported once, quoting the words it matched

      a submission that navigates is still reported. A content script dies with its
        document, Greenhouse loads its own confirmation page, and the re-injected
        copy finds no capability in the new URL

      a watch does not fire on a different job on the same board

      an expired watch is dropped rather than fired

      the watch is stored BEFORE the page can navigate, which is the half that
        makes the resume reachable in a real browser rather than only in a harness
        that pre-seeded storage
    """
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    root = EXT.parent
    out = subprocess.run(
        [node, str(root / "tests" / "extension_behaviour.mjs"),
         str(EXT / "run.js")],
        capture_output=True, text=True, timeout=180, cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "all six hold" in out.stdout, out.stdout
