"""The dummy rehearsal, run offline against a synthetic answer bank.

The live version of this caught three real gaps on 2026-09-14: the demographic
and consent cells were still empty in the sheet, and an unrecognised quote intro
line was leaking into feedback text. Keeping it in the suite means a regression in
any part of the chain shows up here rather than in a real application.
"""
from __future__ import annotations

from hunter.apply import simulate
from hunter.apply.infobank import load_bank

INFO = [
    ["APPLICATION INFO BANK"],
    ["SECTION A - PERSONAL & CONTACT"],
    ["Field", "Value", "Status", "Notes"],
    ["Full legal name", "Krish Raja", "\U0001F7E2 LOCKED", ""],
    ["Preferred name", "Krish", "\U0001F7E2 LOCKED", ""],
    ["Email (job search)", "hello@krishraja.com", "\U0001F7E2 LOCKED", ""],
    ["Phone", "+1 (347) 665-8225", "\U0001F7E2 LOCKED", ""],
    ["LinkedIn URL", "https://www.linkedin.com/in/krish-raja/", "\U0001F7E2 LOCKED", ""],
    ["SECTION B - WORK AUTHORIZATION"],
    ["Authorized to work in US?", "Yes", "\U0001F7E2 LOCKED", ""],
    ["Will you require sponsorship in US?", "No", "\U0001F7E2 LOCKED", ""],
    ["SECTION C - COMPENSATION & LOGISTICS"],
    ["Salary expectations (verbal answer if asked)",
     "$250K+ base + meaningful equity", "\U0001F7E2 LOCKED", ""],
    ["Earliest start date", "Immediately available", "\U0001F7E2 LOCKED", ""],
    ["SECTION E - REFERRAL / SOURCE"],
    ["How did you hear about us (default)", "LinkedIn", "\U0001F7E2 LOCKED", ""],
    ["SECTION G - DEMOGRAPHIC / EEO"],
    ["Race / ethnicity", "Two or more races", "\U0001F534 SENSITIVE", ""],
    ["Gender", "Male", "\U0001F534 SENSITIVE", ""],
    ["Veteran status", "Not a veteran", "\U0001F534 SENSITIVE", ""],
    ["Disability status", "No disability", "\U0001F534 SENSITIVE", ""],
    ["Consent to recruiting privacy policies and acknowledgements", "Yes",
     "\U0001F534 SENSITIVE", ""],
]
PROFILE = [
    ["PROFILE"], ["CORE FACTS"], ["Field", "Value", "Notes"],
    ["Full name", "Krish Raja", ""],
    ["VOICE & TONE RULES"], ["Rule"],
    ["No corporate filler", "Banned: leverage, synergy", ""],
]
INTERVIEW = [["INTERVIEW ANSWERS"], ["CORE ANSWERS"],
             ["Greatest strength", "Range with depth.", ""]]


def bank():
    tabs = {"Application Info Bank": INFO, "Profile": PROFILE,
            "Interview Answers": INTERVIEW}
    return load_bank(lambda tab: tabs[tab])


def test_the_whole_rehearsal_passes_with_a_complete_bank():
    result = simulate.run(bank(), to="krish@themindmaker.ai")
    assert result.ok, [f"{n}: {d}" for n, _, d in result.failures]
    assert len(result.checks) >= 25


def test_the_rehearsal_fails_loudly_when_the_demographics_are_empty():
    """Exactly the failure the live run reported before the sheet was seeded. The
    rehearsal has to notice, or it is decoration."""
    rows = [r[:] for r in INFO]
    for r in rows:
        if r and r[0] == "Race / ethnicity":
            r[1] = ""
    tabs = {"Application Info Bank": rows, "Profile": PROFILE,
            "Interview Answers": INTERVIEW}
    result = simulate.run(load_bank(lambda tab: tabs[tab]),
                          to="krish@themindmaker.ai")
    assert not result.ok
    assert any("race" in n for n, _, _ in result.failures)


def test_the_rehearsal_touches_no_real_company():
    """The posting is a literal, so no fetch can happen and no employer exists."""
    assert "simulation" in simulate.DUMMY_COMPANY.lower()
    assert ".invalid" in simulate.DUMMY_URL
    assert simulate.DUMMY_SPEC.slug.endswith("simulation")


def test_the_rehearsal_covers_every_field_kind_the_harvest_found():
    kinds = {f.kind for f in simulate.DUMMY_FIELDS}
    for expected in ("name", "email", "phone", "location", "file_resume",
                     "file_cover", "url", "boolean", "single_select",
                     "number", "date", "long_text", "consent", "demographic"):
        assert expected in kinds, expected
