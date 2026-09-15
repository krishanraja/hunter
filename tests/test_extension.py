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
    assert "location.href !== startedAt" in body, "leaving the form counts too"


def test_it_watches_without_pressing_anything():
    """Watching for his click must not become clicking for him."""
    body = code_only(RUN).lower()
    assert ".click(" not in body


def test_it_gives_up_watching_rather_than_running_for_ever():
    assert "deadline" in RUN and "30 * 60 * 1000" in RUN


def test_a_failure_to_report_is_told_to_him():
    """Silently failing to record it is the same silence in a new place."""
    assert "could not be told" in RUN
