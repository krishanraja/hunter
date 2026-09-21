"""Offline tests for the apply layer. No network, no credentials.

The fixtures are captured live responses, written with ensure_ascii=True so a
literal em dash never lands in the tree (test_repo_guards.py scans every .json
with no fixture exemption; the Aptos response really does contain em dashes).
"""
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from hunter.apply import ashby_form, greenhouse_form
from hunter.apply.audit import (ATT_BOTH, ATT_CV, AUT_ACCOUNT, AUT_DEAD,
                                AUT_ESSAY, AUT_FULL, CPX_HIGH, CPX_UNKNOWN,
                                FMT_ASHBY, FMT_GREENHOUSE, audit)
from hunter.apply.fetch import account_required, form_for
from hunter.apply.infobank import (LOCKED, NEEDS_INPUT, SENSITIVE, AnswerBank,
                                   BankEntry, load_bank, norm_label,
                                   parse_info_bank, parse_interview,
                                   parse_profile, parse_status)
from hunter.apply.model import KINDS, FormField, FormSpec
from hunter.apply.resolve import Answer, Resolver, Unanswered

FIX = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------- Ashby adapter ----------------

def test_ashby_parses_every_field_and_maps_kinds():
    spec = ashby_form.parse(fixture("ashby_form_harvey.json"),
                            slug="harvey", posting_id="b85907be")
    assert spec.readable and spec.ats == "ashby"
    assert len(spec.fields) == 15, "Harvey had 15 fields in the 2026-09-13 harvest"
    assert all(f.kind in KINDS for f in spec.fields)
    by_key = {f.key: f for f in spec.fields}
    assert by_key["_systemfield_name"].kind == "name"
    assert by_key["_systemfield_email"].kind == "email"
    assert by_key["_systemfield_resume"].kind == "file_resume"
    # Every select that exists carries its options, or we cannot pick one.
    for f in spec.fields:
        if f.kind in ("single_select", "multi_select"):
            assert f.options, f"{f.label!r} is a select with no options"


def test_ashby_dead_posting_is_unreadable_not_empty():
    spec = ashby_form.parse(fixture("ashby_form_dead.json"),
                            slug="centari", posting_id="5f366ad6")
    assert spec.readable is False
    assert "dead" in spec.note.lower()
    assert spec.fields == ()


def test_ashby_unmapped_type_fails_loudly():
    payload = {"data": {"jobPosting": {"title": "t", "applicationForm": {
        "sections": [{"title": None, "fieldEntries": [
            {"isRequired": True,
             "field": {"path": "x", "title": "Q", "type": "SomethingNew"}}]}]}}}}
    with pytest.raises(ValueError, match="unmapped Ashby field type"):
        ashby_form.parse(payload, slug="s", posting_id="p")


def test_ashby_cover_letter_file_is_not_a_resume():
    payload = {"data": {"jobPosting": {"title": "t", "applicationForm": {
        "sections": [{"title": None, "fieldEntries": [
            {"isRequired": False,
             "field": {"path": "abc", "title": "Cover Letter", "type": "File"}}]}]}}}}
    spec = ashby_form.parse(payload, slug="s", posting_id="p")
    assert spec.fields[0].kind == "file_cover"


# ---------------- Greenhouse adapter ----------------

def test_greenhouse_parses_cloudflare_and_detects_consent():
    spec = greenhouse_form.parse(fixture("greenhouse_form_cloudflare.json"),
                                 slug="cloudflare", posting_id="8076815")
    assert spec.readable and spec.ats == "greenhouse"
    labels = {f.label for f in spec.fields}
    assert any("Candidate Privacy Policy" in x for x in labels)
    consents = [f for f in spec.fields if f.kind == "consent"]
    assert consents, "the privacy acknowledgement must classify as consent"
    assert all(f.required for f in consents)


def test_greenhouse_drops_the_paste_the_text_twin_of_a_file_question():
    spec = greenhouse_form.parse(fixture("greenhouse_form_cloudflare.json"),
                                 slug="cloudflare", posting_id="8076815")
    keys = [f.key for f in spec.fields]
    assert "resume" in keys
    assert "resume_text" not in keys, "the textarea twin must be dropped"
    assert "cover_letter_text" not in keys


def test_greenhouse_aptos_fixture_has_no_literal_em_dash_on_disk():
    # The em dash is written as an escape here on purpose: a literal one in this
    # file would itself trip test_no_em_dash_anywhere, which is the whole point.
    em_dash = "\u2014"
    raw = (FIX / "greenhouse_form_aptos.json").read_text(encoding="utf-8")
    assert em_dash not in raw, "fixtures must be dumped with ensure_ascii=True"
    # ...but the parsed value still carries it, so the fixture stays faithful.
    parsed = json.dumps(fixture("greenhouse_form_aptos.json"), ensure_ascii=False)
    assert em_dash in parsed, "the Aptos response really does contain em dashes"


def test_greenhouse_url_hints_classify_linkedin_and_website():
    spec = greenhouse_form.parse(fixture("greenhouse_form_aptos.json"),
                                 slug="aptoslabs", posting_id="4645494005")
    kinds = {f.label: f.kind for f in spec.fields}
    assert kinds.get("LinkedIn Profile") == "url"
    assert kinds.get("Website") == "url"


# ---------------- answer bank ----------------

def test_parse_status_reads_the_real_legend():
    assert parse_status("\U0001F7E2 LOCKED") == LOCKED
    assert parse_status("\U0001F7E1 NEEDS YOUR INPUT") == NEEDS_INPUT
    assert parse_status("\U0001F534 SENSITIVE") == SENSITIVE
    assert parse_status("\U0001F7E1 EDIT TO TASTE") == NEEDS_INPUT


def test_norm_label_handles_nbsp_and_punctuation():
    # Ashby labels really do carry a trailing non breaking space.
    assert norm_label("What is your current Right To Work Status ") == \
        norm_label("what is your current right to work status")
    assert norm_label("Authorized to work in US?") == "authorized to work in us"


INFO_ROWS = [
    ["APPLICATION INFO BANK", "Updated 2026-09-13"],
    ["SECTION A - PERSONAL & CONTACT"],
    ["Field", "Value", "Status", "Notes"],
    ["Full legal name", "Krish Raja", "\U0001F7E2 LOCKED", ""],
    ["Preferred name", "Krish", "\U0001F7E2 LOCKED", ""],
    ["Phone", "+1 (347) 665-8225", "\U0001F7E2 LOCKED", ""],
    ["Email (job search)", "hello@krishraja.com", "\U0001F7E2 LOCKED", ""],
    ["ZIP", "11222", "\U0001F7E1 NEEDS INPUT", ""],
    ["LinkedIn URL", "https://www.linkedin.com/in/krish-raja/", "\U0001F7E2 LOCKED", ""],
    ["SECTION B - WORK AUTHORIZATION"],
    ["Field", "Value", "Status", "Notes"],
    ["Authorized to work in US?", "Yes", "\U0001F7E2 LOCKED", ""],
    ["Will you require sponsorship in US?", "No", "\U0001F7E2 LOCKED", ""],
    ["Authorized to work in UK?", "Yes", "\U0001F7E2 LOCKED", ""],
    ["Will you require sponsorship in UK?", "No", "\U0001F7E2 LOCKED", ""],
    ["Authorized to work in Australia?", "Yes", "\U0001F7E2 LOCKED", ""],
    ["SECTION E - REFERRAL / SOURCE"],
    ["How did you hear about us (default)", "LinkedIn", "\U0001F7E2 LOCKED", ""],
    ["SECTION C - COMPENSATION & LOGISTICS"],
    ["Salary expectations (verbal answer if asked)",
     "$250K+ base + meaningful equity", "\U0001F7E2 LOCKED", ""],
    ["SECTION G - DEMOGRAPHIC / EEO"],
    ["Race / ethnicity", "", "\U0001F534 SENSITIVE", "Optional"],
    ["SECTION H - REFERENCES"],
    ["Reference 1 - name", "", "\U0001F7E1 NEEDS INPUT", "fill me"],
]

PROFILE_ROWS = [
    ["KRISH RAJA - MASTER PROFILE", "Updated 2026-09-13"],
    ["CORE FACTS"],
    ["Field", "Value", "Notes"],
    ["Full name", "Krish Raja", ""],
    ["Right to work", "USA, UK, Australia - full rights, no sponsorship required", ""],
    ["VOICE & TONE RULES"],
    ["Rule"],
    ["No em dashes", "Use hyphens, semicolons, or brackets", ""],
    ["No AI sentence patterns",
     'Banned: "It is not just X, it is Y", "at the intersection of", '
     '"uniquely positioned to"', ""],
    ["No corporate filler", "Banned: leverage, synergy, empower, cutting-edge", ""],
    ["HARD POSITIONING RULES"],
    ["Rule"],
    ["Never apply below VP/Director", "No exceptions without explicit Krish approval", ""],
]

INTERVIEW_ROWS = [
    ["INTERVIEW ANSWERS"],
    ["CORE ANSWERS"],
    ["Greatest strength", "My strength is range with depth.", ""],
    ["Management philosophy", "I manage to outcomes.", ""],
    ["WHY THIS COMPANY (template - assemble fresh every time)"],
    ["Slot 1 - the specific thing", "One true, non-obvious observation.", ""],
    ["Slot 2 - the mechanism", "Where the range maps onto the bottleneck.", ""],
    ["Slot 3 - why now, why me", "Why this version of the problem, next.", ""],
]


def build_bank() -> AnswerBank:
    tabs = {"Application Info Bank": INFO_ROWS, "Profile": PROFILE_ROWS,
            "Interview Answers": INTERVIEW_ROWS}
    return load_bank(lambda tab: tabs[tab])


def test_info_bank_parses_sections_and_statuses():
    bank = build_bank()
    assert bank.value("Full legal name") == "Krish Raja"
    assert bank.get("Authorized to work in US?").section == "B"
    assert bank.get("ZIP").usable is True, "a filled NEEDS INPUT cell is usable"
    assert bank.get("Reference 1 - name").blocking is True
    assert bank.get("Race / ethnicity").usable is False, "SENSITIVE is never auto filled"


def test_blocking_and_sensitive_are_reported_separately():
    bank = build_bank()
    assert [e.field_name for e in bank.blocking] == ["Reference 1 - name"]
    assert [e.field_name for e in bank.sensitive] == ["Race / ethnicity"]


def test_profile_voice_rules_are_machine_readable():
    bank = build_bank()
    assert "at the intersection of" in bank.banned_phrases
    assert "leverage" in bank.banned_phrases
    assert any("VP/Director" in r for r in bank.positioning_rules)
    assert len(bank.why_company_slots) == 3


# ---------------- resolver ----------------

def field(label, kind="boolean", required=True, key="k", options=()):
    return FormField(key=key, label=label, kind=kind, required=required,
                     options=options)


@pytest.mark.parametrize("label,expected", [
    ("Are you eligible to work in the United States indefinitely without any "
     "visa sponsorship?", "Yes"),
    ("Are you authorized to work in the country where the job is located?", "Yes"),
    ("Are you legally authorized to work in the country where this role is "
     "located, for any employer?", "Yes"),
    ("Are you currently legally authorized to work in the United States?", "Yes"),
    ("Are you authorized to work in the country you currently reside in?", "Yes"),
    ("Will you now or in the future require sponsorship for employment visa "
     "status in this country?", "No"),
    ("Will you now or in the future require visa sponsorship to work in the "
     "United States?", "No"),
    ("Do you now or will you in the future require immigration sponsorship to "
     "work at Cloudflare?", "No"),
    ("Will you now or will you in the future require employment visa "
     "sponsorship?", "No"),
    ("Do you currently require, or will you in the future require, visa "
     "sponsorship (e.g., H-1B, O-1) to work in the United States?", "No"),
])
def test_the_seventeen_authorisation_phrasings_collapse_onto_four_facts(label, expected):
    """This is the cluster that makes the whole layer worth building: the
    2026-09-13 harvest asked work authorisation 17 times in 8 wordings."""
    got = Resolver(build_bank()).resolve(field(label))
    assert isinstance(got, Answer), f"{label!r} came back {got!r}"
    assert got.value == expected


def test_canada_or_usa_question_is_satisfied_by_us_authorisation():
    got = Resolver(build_bank()).resolve(
        field("Are you legally eligible to work in Canada or the USA?"))
    assert isinstance(got, Answer) and got.value == "Yes"
    assert "Canada or USA" in got.source


def test_canada_only_question_is_not_guessed():
    got = Resolver(build_bank()).resolve(
        field("Are you authorized to work in Canada?"))
    assert isinstance(got, Unanswered)


def test_office_and_relocation_questions_defer_to_the_posting():
    r = Resolver(build_bank())
    for label in ("Are you willing and able to work from our NYC office 5 days "
                  "a week?",
                  "Are you comfortable working 4 days in the office in Shoreditch?",
                  "We have a in person office culture, if required would you "
                  "relocate to NYC to work in person with us?"):
        got = r.resolve(field(label))
        assert isinstance(got, Unanswered), f"{label!r} must not be auto answered"
        assert "NYC or London" in got.note or "posting" in got.note


def test_consent_and_demographic_are_never_auto_filled():
    r = Resolver(build_bank())
    consent = r.resolve(field("Applicant Arbitration Agreement Acknowledgement",
                              kind="consent"))
    demo = r.resolve(field("Gender", kind="demographic"))
    assert isinstance(consent, Unanswered) and "Krish" in consent.reason
    assert isinstance(demo, Unanswered) and "Krish" in demo.reason


def test_a_required_salary_field_gets_the_floor_and_is_flagged():
    """Krish's ruling 2026-09-14. Fleek and Trulioo both require this field, so
    refusing outright made those two applications impossible to complete."""
    got = Resolver(build_bank()).resolve(
        field("What are your salary expectations?", kind="number", required=True))
    assert isinstance(got, Answer) and got.value == "250000"
    assert got.flagged, "a salary must always reach the approval email"


def test_an_optional_salary_field_is_still_left_to_krish():
    got = Resolver(build_bank()).resolve(
        field("What are your salary expectations?", kind="number",
              required=False))
    assert isinstance(got, Unanswered) and "optional" in got.note


def test_the_comp_floor_is_parsed_from_the_recorded_text():
    from hunter.apply.resolve import comp_floor
    assert comp_floor(build_bank()) == "250000"


def test_essays_are_not_resolved_here():
    got = Resolver(build_bank()).resolve(
        field("What makes you excited about the ElevenLabs mission?",
              kind="long_text"))
    assert isinstance(got, Unanswered) and "essay" in got.reason


def test_linkedin_and_contact_fields_resolve_from_the_bank():
    r = Resolver(build_bank())
    assert isinstance(r.resolve(field("LinkedIn URL", kind="url")), Answer)
    assert r.resolve(field("Phone Number", kind="phone")).value.startswith("+1")
    assert r.resolve(field("What is your home zip code?",
                           kind="short_text")).value == "11222"
    assert r.resolve(field("How did you hear about us?",
                           kind="short_text")).value == "LinkedIn"


# ---------------- audit ----------------

DAY = datetime.date(2026, 9, 13)


def test_audit_writes_all_six_cells_and_never_blanks_one():
    spec = ashby_form.parse(fixture("ashby_form_harvey.json"),
                            slug="harvey", posting_id="b85907be")
    result = audit(spec, build_bank(), today=DAY)
    cells = result.cells()
    from hunter.sheet import Sheet
    assert tuple(cells) == Sheet.AUDIT_NAMES
    assert all(str(v).strip() for v in cells.values()), "canon 9.13 has no blanks"
    assert cells["Application Format"] == FMT_ASHBY
    assert cells["Form Audit Date"] == "2026-09-13"


def test_audit_grades_a_required_essay_as_high_complexity():
    spec = ashby_form.parse(fixture("ashby_form_harvey.json"),
                            slug="harvey", posting_id="b85907be")
    result = audit(spec, build_bank(), today=DAY)
    assert result.form_complexity == CPX_HIGH
    assert "custom" in result.additional_questions


def test_audit_marks_a_dead_posting_dead_not_full():
    spec = ashby_form.parse(fixture("ashby_form_dead.json"),
                            slug="centari", posting_id="5f366ad6")
    result = audit(spec, build_bank(), today=DAY)
    assert result.autonomy_score == AUT_DEAD
    assert result.form_complexity == CPX_UNKNOWN
    assert result.additional_questions == "not readable"


def test_audit_marks_an_account_gated_posting_partial_account():
    spec = form_for(
        "https://www.google.com/about/careers/applications/jobs/results/1-x/",
        lambda url: None)
    assert spec.readable is False and account_required(spec)
    result = audit(spec, build_bank(), today=DAY,
                   account_required=account_required(spec))
    assert result.autonomy_score == AUT_ACCOUNT


def test_audit_attachment_style_follows_the_real_file_fields():
    both = FormSpec(ats="ashby", slug="s", posting_id="p", title="t", fields=(
        field("Resume", kind="file_resume", key="_systemfield_resume"),
        field("Cover letter", kind="file_cover", key="cl")))
    cv_only = FormSpec(ats="ashby", slug="s", posting_id="p", title="t", fields=(
        field("Resume", kind="file_resume", key="_systemfield_resume"),))
    bank = build_bank()
    assert audit(both, bank, today=DAY).attachment_style == ATT_BOTH
    assert audit(cv_only, bank, today=DAY).attachment_style == ATT_CV


def test_audit_is_full_only_when_nothing_is_unresolved():
    easy = FormSpec(ats="ashby", slug="s", posting_id="p", title="t", fields=(
        field("Name", kind="name", key="_systemfield_name"),
        field("Email", kind="email", key="_systemfield_email"),
        field("Resume", kind="file_resume", key="_systemfield_resume"),
        field("Are you authorized to work in the United States?", kind="boolean"),
    ))
    result = audit(easy, build_bank(), today=DAY)
    assert result.autonomy_score == AUT_FULL
    assert result.unresolved == ()

    with_essay = FormSpec(ats="ashby", slug="s", posting_id="p", title="t",
                          fields=easy.fields + (
                              field("Why do you want to work here?",
                                    kind="long_text"),))
    assert audit(with_essay, build_bank(), today=DAY).autonomy_score == AUT_ESSAY


def test_greenhouse_audit_reports_greenhouse_format():
    spec = greenhouse_form.parse(fixture("greenhouse_form_cloudflare.json"),
                                 slug="cloudflare", posting_id="8076815")
    result = audit(spec, build_bank(), today=DAY)
    assert result.application_format == FMT_GREENHOUSE
    assert result.flagged, "the privacy acknowledgement must be flagged for Krish"


# ---------------- dispatcher ----------------

def test_form_for_routes_by_ats_key_and_names_account_gates():
    seen = {}

    def fake_key(url):
        seen["url"] = url
        return None

    spec = form_for("https://www.linkedin.com/jobs/view/x-123", fake_key)
    assert spec.ats == "linkedin" and not spec.readable
    assert account_required(spec)
    spec = form_for("https://example.com/careers/1", fake_key)
    assert spec.ats == "unknown" and not account_required(spec)


# ---------------- the residence rule ----------------

def test_without_a_role_location_every_residence_question_stays_unanswered():
    """The safe default. Absent the posting's location, nothing guesses where
    Krish is sitting."""
    r = Resolver(build_bank())
    got = r.resolve(field("Where are you currently located?", kind="short_text"))
    assert isinstance(got, Unanswered)
    assert "not supplied" in got.note


@pytest.mark.parametrize("role_location,expected", [
    ("New York, NY", "New York, United States"),
    ("Brooklyn, NY", "New York, United States"),
    ("London, UK", "London, United Kingdom"),
    ("London", "London, United Kingdom"),
    ("Remote US", "New York, United States"),
])
def test_residence_resolves_to_the_roles_city(role_location, expected):
    """Krish's rule 2026-09-13: he lives between NYC and London, and for any role
    he applies to he is resident in that role's city."""
    r = Resolver(build_bank(), role_location=role_location)
    got = r.resolve(field("Where are you currently located?", kind="short_text"))
    assert isinstance(got, Answer) and got.value == expected
    assert got.flagged, "a residence answer must still reach the approval email"


def test_office_booleans_answer_yes_for_a_resolved_base_but_stay_flagged():
    r = Resolver(build_bank(), role_location="New York, NY")
    got = r.resolve(field("Are you willing and able to work from our NYC office "
                          "5 days a week?"))
    assert isinstance(got, Answer) and got.value == "Yes" and got.flagged


def test_a_location_select_picks_only_an_option_the_form_offers():
    from hunter.apply.model import Option
    fleek = field("What is your current/intended working location?",
                  kind="single_select",
                  options=(Option("London", "London"),
                           Option("Remote UK", "Remote UK"),
                           Option("Remote ROW", "Remote ROW")))
    got = Resolver(build_bank(), role_location="London, UK").resolve(fleek)
    assert isinstance(got, Answer) and got.value == "London"

    # A NYC role against a London-only option list must refuse, not invent.
    got = Resolver(build_bank(), role_location="New York, NY").resolve(fleek)
    assert isinstance(got, Unanswered)


def test_an_unresolvable_posting_location_says_so_rather_than_guessing():
    r = Resolver(build_bank(), role_location="Singapore")
    got = r.resolve(field("Where are you currently located?", kind="short_text"))
    assert isinstance(got, Unanswered) and "Singapore" in got.note


def test_preferred_first_and_last_name_resolve():
    r = Resolver(build_bank())
    assert r.resolve(field("Preferred First Name", kind="name")).value == "Krish"
    assert r.resolve(field("Preferred Last Name", kind="name")).value == "Raja"


def test_an_essay_typed_as_a_short_string_is_still_an_essay():
    """Profound asks "Why do you want to work at Profound?" as an Ashby String."""
    got = Resolver(build_bank()).resolve(
        field("Why do you want to work at Profound?", kind="short_text"))
    assert isinstance(got, Unanswered) and "essay" in got.reason


# ---------------- answers must be one of the form's own options ----------------

def test_a_yes_no_answer_maps_onto_a_sentence_option():
    """Harvey does not offer a bare "No". It offers "No, I do not require
    sponsorship to work in the country where this role is located". An answer
    that is not one of the offered labels is not an answer."""
    from hunter.apply.model import Option
    harvey = field(
        "Will you now or will you in the future require employment visa "
        "sponsorship?", kind="single_select",
        options=(Option("Yes, I will require Harvey to sponsor my employment",
                        "y"),
                 Option("No, I do not require sponsorship to work in the "
                        "country where this role is located", "n")))
    got = Resolver(build_bank()).resolve(harvey)
    assert isinstance(got, Answer)
    assert got.value.startswith("No, I do not require sponsorship")
    assert "matched to the form's option" in got.source


def test_an_answer_that_matches_no_option_is_refused_not_forced():
    from hunter.apply.model import Option
    odd = field("Are you authorized to work in the United States?",
                kind="single_select",
                options=(Option("Citizen", "c"), Option("Green card", "g")))
    got = Resolver(build_bank()).resolve(odd)
    assert isinstance(got, Unanswered)
    assert "not one of this form's options" in got.note


def test_a_sentence_office_select_resolves_via_the_residence_rule():
    from hunter.apply.model import Option
    harvey_office = field(
        "This role is tied to the office location listed in the job posting. "
        "Are you currently based in the listed location and able to work in "
        "person 3 days per week?", kind="single_select",
        options=(Option("Yes, I am based in this location and able to work "
                        "from the office 3 days per week", "a"),
                 Option("No, I am not based in this location but willing to "
                        "relocate", "b"),
                 Option("No, I am only able to work remotely", "c"),
                 Option("Other (optional context)", "d")))
    got = Resolver(build_bank(), role_location="New York, NY").resolve(harvey_office)
    assert isinstance(got, Answer)
    assert got.value.startswith("Yes, I am based in this location")
    assert got.flagged


def test_free_text_fields_are_untouched_by_option_matching():
    r = Resolver(build_bank(), role_location="London, UK")
    got = r.resolve(field("Where are you currently located?", kind="short_text"))
    assert isinstance(got, Answer) and got.value == "London, United Kingdom"


# ---------------- membership questions must be tested, not assumed ----------------

def test_a_state_list_that_excludes_new_york_answers_no():
    """Socure asks "Do you currently reside in any of the following states: DE,
    HI, IA, KY, MS, NE, NM, SD, VT, WV, WY?". A New York resident answers No.
    The residence rule answered Yes before this guard existed, which would have
    put a false statement on a real application."""
    got = Resolver(build_bank(), role_location="New York, NY").resolve(
        field("Do you currently reside in any of the following states: DE, HI, "
              "IA, KY, MS, NE, NM, SD, VT, WV, WY?"))
    assert isinstance(got, Answer) and got.value == "No"
    assert "does not appear" in got.source


def test_a_state_list_that_includes_new_york_answers_yes():
    got = Resolver(build_bank(), role_location="New York, NY").resolve(
        field("Do you currently reside in any of the following states: NY, NJ, CT?"))
    assert isinstance(got, Answer) and got.value == "Yes"
    assert "appears" in got.source


def test_how_did_you_hear_maps_onto_an_option_that_names_the_channel():
    from hunter.apply.model import Option
    eleven = field("How did you hear about ElevenLabs?", kind="single_select",
                   options=(Option("I'm a user", "1"),
                            Option("News article", "2"),
                            Option("Job board ", "3"),
                            Option("Social media (LinkedIn, Instagram, X etc)", "4")))
    got = Resolver(build_bank()).resolve(eleven)
    assert isinstance(got, Answer)
    assert got.value.startswith("Social media")


def test_how_did_you_hear_refuses_when_no_option_names_the_channel():
    from hunter.apply.model import Option
    phantom = field("How did you hear about Phantom?", kind="single_select",
                    options=(Option("Phantom User", "1"),
                             Option("Phantom Employee", "2"),
                             Option("Cryptocurrency Jobs", "3"),
                             Option("Other", "4")))
    got = Resolver(build_bank()).resolve(phantom)
    assert isinstance(got, Unanswered), "picking Other is Krish's call, not ours"


def test_three_way_sponsorship_picks_the_option_that_negates_the_future_too():
    from hunter.apply.model import Option
    trulioo = field("Will you now or in the future require Trulioo to sponsor "
                    "your employment authorization to work in this location?",
                    kind="single_select",
                    options=(Option("Yes, I currently require sponsorship", "1"),
                             Option("No, I do not currently require sponsorship, "
                                    "but I will require sponsorship in the "
                                    "future.", "2"),
                             Option("No, I do not require sponsorship now or in "
                                    "the future.", "3")))
    got = Resolver(build_bank()).resolve(trulioo)
    assert isinstance(got, Answer)
    assert got.value == "No, I do not require sponsorship now or in the future."


# ---------------- LinkedIn liveness ----------------

def test_linkedin_closed_posting_is_dead(monkeypatch):
    """A removed LinkedIn posting still answers 200. Both signals below are what
    an anonymous reader actually gets, and both were missed before."""
    from hunter.ats import linkedin

    class R:
        status_code = 200
        url = "https://www.linkedin.com/jobs/view/chief-revenue-officer-at-denodo-1"
        text = "<html>No longer accepting applications</html>"

    monkeypatch.setattr(linkedin.requests, "get", lambda *a, **k: R())
    live, why = linkedin.posting_state(R.url)
    assert live is False and "no longer accepting" in why.lower()


def test_linkedin_search_redirect_is_dead(monkeypatch):
    from hunter.ats import linkedin

    class R:
        status_code = 200
        url = "https://www.linkedin.com/jobs/vice-president-corporate-development-jobs"
        text = "<html>lots of jobs</html>"

    monkeypatch.setattr(linkedin.requests, "get", lambda *a, **k: R())
    live, why = linkedin.posting_state(
        "https://www.linkedin.com/jobs/view/head-of-corporate-development-1")
    assert live is False and "search page" in why


def test_a_live_linkedin_posting_stays_unknown_not_live(monkeypatch):
    """Unknown is not live. Conflating them is how a dead role sat on the sheet."""
    from hunter.ats import linkedin

    class R:
        status_code = 200
        url = "https://www.linkedin.com/jobs/view/some-real-role-123"
        text = "<html>Apply now</html>"

    monkeypatch.setattr(linkedin.requests, "get", lambda *a, **k: R())
    live, why = linkedin.posting_state(R.url)
    assert live is None and "not assertable" in why


# ---------------- sensitive answers: stored, and always shown ----------------

def sensitive_bank() -> AnswerBank:
    """The Info Bank as it will read once Krish's 2026-09-14 answers are in."""
    rows = [r[:] for r in INFO_ROWS]
    filled = {
        "Race / ethnicity": "Two or more races",
        "Gender": "Male",
        "Veteran status": "Not a veteran",
        "Disability status": "No disability",
    }
    out = []
    for r in rows:
        if r and r[0] in filled:
            r = [r[0], filled[r[0]], "\U0001F534 SENSITIVE", ""]
        out.append(r)
    for name, value in filled.items():
        if not any(r and r[0] == name for r in out):
            out.append([name, value, "\U0001F534 SENSITIVE", ""])
    out.append(["Consent to recruiting privacy policies and acknowledgements",
                "Yes", "\U0001F534 SENSITIVE", ""])
    tabs = {"Application Info Bank": out, "Profile": PROFILE_ROWS,
            "Interview Answers": INTERVIEW_ROWS}
    return load_bank(lambda tab: tabs[tab])


def test_a_sensitive_row_with_a_value_is_usable_and_always_flagged():
    bank = sensitive_bank()
    race = bank.get("Race / ethnicity")
    assert race.usable is True, "Krish supplied the value, so the field can fill"
    assert race.always_flagged is True, "and it must still reach every email"


def test_a_sensitive_row_without_a_value_stays_unanswered():
    bank = build_bank()
    assert bank.get("Race / ethnicity").usable is False


def test_demographics_resolve_from_the_bank_and_stay_flagged():
    r = Resolver(sensitive_bank())
    for label, expected in (
            ("Race / ethnicity", "Two or more races"),
            ("Gender", "Male"),
            ("Veteran status", "Not a veteran"),
            ("Disability status", "No disability")):
        got = r.resolve(field(label, kind="demographic"))
        assert isinstance(got, Answer), f"{label} came back {got!r}"
        assert got.value == expected and got.flagged


def test_a_demographic_select_is_matched_to_the_forms_own_option():
    from hunter.apply.model import Option
    gh = field("Race / Ethnicity", kind="demographic", options=(
        Option("Asian (Not Hispanic or Latino)", "1"),
        Option("Two or More Races (Not Hispanic or Latino)", "2"),
        Option("Decline To Self Identify", "3")))
    got = Resolver(sensitive_bank()).resolve(gh)
    assert isinstance(got, Answer)
    assert got.value.startswith("Two or More Races")


def test_consent_resolves_from_the_bank_and_stays_flagged():
    got = Resolver(sensitive_bank()).resolve(
        field("Applicant Arbitration Agreement Acknowledgement", kind="consent"))
    assert isinstance(got, Answer) and got.value == "Yes" and got.flagged


def test_consent_without_a_bank_row_still_refuses():
    got = Resolver(build_bank()).resolve(
        field("Applicant Arbitration Agreement Acknowledgement", kind="consent"))
    assert isinstance(got, Unanswered) and "Krish" in got.reason


# ---------------- em dashes in the bank's own cells ----------------

def test_an_em_dash_in_a_stored_answer_is_substituted_not_shipped():
    """The education row really does carry one. Left alone it would break Krish's
    own rule on a live application, and notify.py refuses to send an email
    containing one, so it would block the application outright."""
    from hunter.apply.infobank import strip_em_dash
    em = "\u2014"
    raw = f"MA Design Strategy (Distinction) {em} University for the Creative Arts"
    out = strip_em_dash(raw)
    assert em not in out
    assert out == ("MA Design Strategy (Distinction), University for the "
                   "Creative Arts")


def test_clean_text_is_left_exactly_alone():
    from hunter.apply.infobank import strip_em_dash
    for text in ("Krish Raja", "+1 (347) 665-8225", "$250K+ base", ""):
        assert strip_em_dash(text) == text


def test_the_bank_reports_which_cells_need_fixing_at_source():
    em = "\u2014"
    rows = [r[:] for r in INFO_ROWS]
    rows.append(["Highest degree completed",
                 f"MA Design Strategy {em} University for the Creative Arts",
                 "\U0001F7E2 LOCKED", ""])
    tabs = {"Application Info Bank": rows, "Profile": PROFILE_ROWS,
            "Interview Answers": INTERVIEW_ROWS}
    bank = load_bank(lambda tab: tabs[tab])
    offenders = bank.em_dash_cells
    assert [e.field_name for e in offenders] == ["Highest degree completed"]
    assert em in offenders[0].raw_value, "the original is kept for reporting"
    assert em not in offenders[0].value, "the used value is clean"


def test_a_resolved_answer_never_carries_an_em_dash():
    em = "\u2014"
    rows = [r[:] for r in INFO_ROWS]
    rows.append(["Highest degree completed", f"MA Design Strategy {em} UCA",
                 "\U0001F7E2 LOCKED", ""])
    tabs = {"Application Info Bank": rows, "Profile": PROFILE_ROWS,
            "Interview Answers": INTERVIEW_ROWS}
    bank = load_bank(lambda tab: tabs[tab])
    got = Resolver(bank).resolve(
        field("University or School Attended", kind="short_text"))
    assert isinstance(got, Answer) and em not in got.value


def test_the_employer_field_obeys_the_naming_law():
    """00_NORTH_STAR.md: the business is Mindmake, never Mindmaker. This resolver
    answered "Mindmaker" until 2026-09-15, and that value went into the employer
    field of real application forms.
    """
    from hunter.apply.gtmseed import NAME_VARIANTS_BANNED

    got = Resolver(build_bank()).resolve(field("Current or Most Recent Employer"))
    assert isinstance(got, Answer)
    assert got.value == "Mindmake"
    for variant in NAME_VARIANTS_BANNED:
        assert variant.lower() != got.value.lower()
    assert "mindmaker" not in got.value.lower()


@pytest.mark.parametrize("label,expected", [
    ("First Name", "Krish"),
    ("Last Name", "Raja"),
    ("Given Name", "Krish"),
    ("Surname", "Raja"),
    # Ashby asks for both in one box, and that label contains "last name".
    ("Legal First and Last Name", "Krish Raja"),
    ("Full legal name", "Krish Raja"),
])
def test_a_split_name_field_gets_its_own_half(label, expected):
    """Greenhouse asks for the given name and the surname separately. Both carry
    kind "name", so the bank's Full legal name went into each and the live form
    read "Krish Raja" twice."""
    r = Resolver(build_bank())
    got = r.resolve(field(label, kind="name"))
    assert getattr(got, "value", None) == expected


def _opt(label):
    from hunter.apply.model import Option
    return Option(label=label, value=label)


def _bank_with_consent():
    from hunter.apply.infobank import load_bank
    rows = [r[:] for r in INFO_ROWS] + [
        ["Consent to recruiting privacy policies and acknowledgements", "Yes",
         "\U0001F7E2 LOCKED", ""]]
    tabs = {"Application Info Bank": rows, "Profile": PROFILE_ROWS,
            "Interview Answers": INTERVIEW_ROWS}
    return load_bank(lambda tab: tabs[tab])


def test_a_single_option_acknowledgement_takes_that_option():
    """"I acknowledge that I have read the Arbitration Agreement" is not a
    yes/no question, it is the only thing that can be said. His stored Yes did
    not match the wording, so this came back needing him on nearly every form."""
    r = Resolver(_bank_with_consent())
    f = field("Applicant Arbitration Agreement Acknowledgement", kind="consent",
              options=(_opt("I acknowledge that I have opened, read, and "
                            "understood the Arbitration Agreement."),))
    got = r.resolve(f)
    assert getattr(got, "value", "").startswith("I acknowledge")
    assert getattr(got, "flagged", False), "a consent is still his to read"


def test_a_real_choice_is_never_answered_for_him():
    """Two options is a question. Only one is a tick box."""
    r = Resolver(_bank_with_consent())
    f = field("Do you agree to the terms?", kind="consent",
              options=(_opt("I agree"), _opt("I do not agree")))
    got = r.resolve(f)
    assert getattr(got, "value", None) in (None, "I agree") or isinstance(got, Unanswered)


def test_a_dead_posting_is_never_ready():
    """Slingshot AI's posting was dead, Ashby returned no form, the plan had zero
    fields and therefore zero unanswered required fields, and an approval email
    went out for a role nobody can apply to. `ready` was vacuously true."""
    from hunter.apply.fill import build_payload
    from hunter.apply.model import FormSpec
    spec = FormSpec(ats="ashby", slug="x", posting_id="y", title="", fields=(),
                    readable=False, note="Ashby returned no posting; the posting is dead")
    plan = build_payload(spec, build_bank(), company="Slingshot AI", role="CoS")
    assert plan.readable is False
    assert plan.fields == ()
    assert plan.ready is False, "a form nobody can fill is not ready to send"


def test_a_readable_form_with_no_fields_is_not_ready_either():
    from hunter.apply.fill import build_payload
    from hunter.apply.model import FormSpec
    spec = FormSpec(ats="ashby", slug="x", posting_id="y", title="", fields=())
    plan = build_payload(spec, build_bank(), company="X", role="Y")
    assert plan.ready is False


# ---------- the repeating sub-forms Ashby sends, and the narrow carve out ----------

def _spec(*fields):
    from hunter.apply.model import FormSpec
    return FormSpec(ats="ashby", slug="s", posting_id="p", title="t",
                    fields=tuple(fields), readable=True)


def _field(kind, label, required=True):
    from hunter.apply.model import FormField
    return FormField(key=f"_systemfield_{kind}", label=label, kind=kind,
                     required=required)


def test_an_education_history_block_is_a_kind_rather_than_a_crash():
    """One Ashby form carrying an EducationHistory block raised through the
    whole approvals run and 24 built applications were never sent."""
    from hunter.apply.model import KINDS
    for kind in ("education_history", "work_history", "social_links"):
        assert kind in KINDS
        _field(kind, "Education")   # must not raise


def test_a_required_sub_form_does_not_stop_the_application_reaching_him():
    """Hunter cannot fill a repeating history from a flat answer bank and
    never will, so blocking on one means that application never reaches him
    at all. He presses submit himself, on the real form, where he can see
    the section and type into it."""
    from hunter.apply.fill import build_payload
    plan = build_payload(
        _spec(_field("education_history", "Education"),
              _field("name", "Name"), _field("email", "Email")),
        build_bank(), company="Acme", role="GM", jd_url="https://x/1")
    edu = next(f for f in plan.fields if f.kind == "education_history")
    assert edu.unresolved, "it is still unanswered, and must say so"
    assert not edu.blocking, "and it must not stop the email existing"
    assert any("browser" in (f.reason or "") for f in plan.fields)


def test_an_ordinary_required_field_with_no_answer_still_refuses():
    """The carve out is for repeating sub-forms alone. A half-filled
    application he approves from a photograph is the failure the whole apply
    layer exists to prevent."""
    from hunter.apply.fill import build_payload
    plan = build_payload(
        _spec(_field("short_text", "What is your employee number at Acme?"),
              _field("name", "Name")),
        build_bank(), company="Acme", role="GM", jd_url="https://x/1")
    unknown = next(f for f in plan.fields if "employee number" in f.label)
    assert unknown.unresolved and unknown.blocking
    assert not plan.ready
