"""Which header a role takes, and the place and phone that go with it
(package/contact.py). His ruling, 2026-10-15: US and US-remote roles take
Brooklyn and his US number; the UK, any European country, Europe and EMEA
take London and his UK number; anywhere else, or both, holds. Every number
here is in a range reserved for fiction."""
import re

import pytest

from contact_bases import BASES, UK_BASE, US_BASE, raw_bases
from hunter import actions
from hunter.apply.fill import FilledField, FillPlan
from hunter.apply.model import FormField, Option
from hunter.apply.resolve import NEEDS_BASE, NEEDS_KRISH, Answer, Resolver, Unanswered
from hunter.package import contact
from test_apply_forms import build_bank


def field(label, kind="short_text", required=True, options=()):
    return FormField(key=label.lower().replace(" ", "_"), label=label, kind=kind,
                     required=required, options=tuple(options))


class Cfg:
    def __init__(self, **raw):
        self.raw = raw


# ---------- which base ----------

@pytest.mark.parametrize("location,jd,want", [
    ("Paris, France", "", "uk"), ("Berlin", "", "uk"), ("Dublin, Ireland", "", "uk"),
    ("Amsterdam", "", "uk"), ("Remote (EMEA)", "", "uk"), ("Europe", "", "uk"),
    ("London, UK", "", "uk"), ("Remote, United Kingdom", "", "uk"),
    ("New York, NY", "", "us"), ("Remote - US", "", "us"), ("San Francisco, CA", "", "us"),
    ("Austin, TX", "", "us"),
    ("New York or London", "", ""), ("Toronto, Canada", "", ""), ("Singapore", "", ""),
    ("Remote", "", ""), ("Tel Aviv", "", ""),
    # The posting is read only for what it says in so many words.
    ("", "Join us. Help teams like us ship.", ""),
    ("", "Location: Berlin, Germany. Join us.", "uk"),
    ("", "This role is Remote (US).", "us"),
    ("", "We are based in London and we love working with us.", "uk"),
    # The location field wins: the posting is not read when it names a place.
    ("Paris", "Location: New York", "uk"),
])
def test_base_for_follows_his_rule(location, jd, want):
    got, why = contact.base_for(location, jd)
    assert got == want, why
    assert not re.search(r"\d", why)


def test_country_of_alone_would_have_read_join_us_as_the_us():
    """The trap this rule exists to avoid, kept as a measurement."""
    from hunter.sources import country_of
    assert "us" in country_of("join us")


# ---------- the bases, from system_config ----------

def test_bases_are_read_from_system_config():
    got, note = contact.load_bases(Cfg(**{contact.BASES_KEY: raw_bases()}))
    assert got == BASES and note == "2 contact base(s) read"


@pytest.mark.parametrize("raw", [None, "", "not json", "[]",
                                 '{"us": {"place": "Brooklyn, NY"}}'])
def test_a_missing_or_incomplete_base_is_left_out_never_filled_in(raw):
    cfg = Cfg(**({} if raw is None else {contact.BASES_KEY: raw}))
    got, note = contact.load_bases(cfg)
    assert got == {} and not re.search(r"\d\d", note), "counts at most, never a number"


def test_the_header_line_is_place_then_phone():
    assert UK_BASE.header_line() == "London  ·  07700 900123"


# ---------- the form ----------

def test_a_phone_field_gets_the_role_bases_number():
    for base in (US_BASE, UK_BASE):
        r = Resolver(build_bank(), role_location="London", contact=base)
        for f in (field("Phone", kind="phone"), field("Mobile phone number"),
                  field("Telephone")):
            got = r.resolve(f)
            assert isinstance(got, Answer) and got.value == base.phone_international


def test_without_a_base_the_phone_is_never_guessed():
    got = Resolver(build_bank(), role_location="New York, NY").resolve(
        field("Phone", kind="phone"))
    assert isinstance(got, Unanswered) and got.reason.startswith(NEEDS_BASE)


def test_a_phone_box_that_mentions_country_gets_a_phone_not_a_country():
    """_location ran before both phone paths and answers anything naming a
    country with the role's country."""
    got = Resolver(build_bank(), role_location="London", contact=UK_BASE).resolve(
        field("Phone number (with country code)"))
    assert isinstance(got, Answer) and got.value == UK_BASE.phone_international


def test_a_question_about_a_phone_screen_is_not_a_phone_field():
    assert not contact.is_phone_field("boolean", "Are you available for a phone screen?")


def test_address_fields_come_from_the_base():
    r = Resolver(build_bank(), role_location="London", contact=UK_BASE)
    assert r.resolve(field("Zip/Postal Code")).value == UK_BASE.postal
    assert r.resolve(field("City")).value == "London"
    for label in ("Address Line 1", "State/Province"):
        got = r.resolve(field(label))
        assert isinstance(got, Unanswered) and got.reason.startswith(NEEDS_KRISH), label
    us = Resolver(build_bank(), role_location="New York, NY", contact=US_BASE)
    assert us.resolve(field("Address Line 1")).value == US_BASE.street
    assert us.resolve(field("State/Province")).value == "New York"


def test_a_phone_select_is_answered_from_its_own_options():
    f = field("Phone", kind="single_select", options=(
        Option("+1 212 555 0142", "a"), Option("+44 7700 900123", "b")))
    got = Resolver(build_bank(), contact=UK_BASE).resolve(f)
    assert isinstance(got, Answer) and got.value == "+44 7700 900123"


# ---------- read back before anything is sent ----------

def plan_of(*fields, essays=None):
    return FillPlan(ats="ashby", slug="s", posting_id="p", company="c", role="r",
                    fields=tuple(FilledField(key=l, label=l, kind=k, required=True, value=v)
                                 for l, k, v in fields), essays=essays or {})


def test_a_clean_plan_passes():
    plan = plan_of(("Phone", "phone", UK_BASE.phone_international),
                   ("City", "short_text", "London"))
    assert contact.check_plan(plan, UK_BASE, BASES) == []


def test_a_london_form_with_the_us_number_is_refused():
    plan = plan_of(("Phone", "phone", US_BASE.phone_international),
                   ("City", "short_text", "Brooklyn"),
                   essays={"Why us?": f"Call me on {US_BASE.phone_display}."})
    problems = contact.check_plan(plan, UK_BASE, BASES)
    assert problems == ["a field carries the US number",
                        "a phone field does not carry the UK number",
                        "a place field names the US place"]
    for p in problems:
        assert not re.search(r"\d", p)


def test_an_essay_may_name_london_for_a_us_role():
    """Places in prose are his history, not his address."""
    plan = plan_of(("Phone", "phone", US_BASE.phone_international),
                   essays={"Why us?": "I built the team in London."})
    assert contact.check_plan(plan, US_BASE, BASES) == []


# ---------- the header button ----------

def test_the_header_button_records_the_base_and_reads_it_back(monkeypatch):
    closed, patched = [], []

    def get(cfg, table, params):
        if table == actions.TABLE:
            return [{"id": 1, "kind": "header", "job_id": "acme:vp", "payload": {"base": "UK"}}]
        if params.get("select") == "header_base":
            return [{"header_base": patched[-1][1]["header_base"]}] if patched else []
        return [{"job_id": "acme:vp", "company": "Acme", "title": "VP"}]

    def patch(cfg, table, match, body):
        (closed if table == actions.TABLE else patched).append((match, body))
    monkeypatch.setattr(actions, "db_get", get)
    monkeypatch.setattr(actions, "db_patch", patch)
    actions.apply_queued(None, None, ["h"], apply=True)
    assert patched == [({"job_id": "acme:vp"}, {"header_base": "uk"})]
    assert closed[-1][1]["status"] == "done"
    assert closed[-1][1]["result"] == "recorded the UK header"


def test_a_header_hunter_does_not_know_is_refused(monkeypatch):
    closed = []
    monkeypatch.setattr(actions, "db_get", lambda cfg, table, params: (
        [{"id": 1, "kind": "header", "job_id": "a:b", "payload": {"base": "fr"}}]
        if table == actions.TABLE else [{"job_id": "a:b"}]))
    monkeypatch.setattr(actions, "db_patch", lambda cfg, table, match, body: closed.append(body))
    actions.apply_queued(None, None, ["h"], apply=True)
    assert closed[-1]["status"] == "failed"


def test_prepare_says_which_header_when_the_form_needs_one():
    from hunter import run
    ok, note = actions.prepare_note(1, {run.NO_BASE: 1}, [])
    assert ok is False and note.startswith("which header, US or UK?")


# ---------- inside the send path ----------

def _fill_world(monkeypatch, *, essays=None):
    import hunter.run as run_mod
    from hunter.apply import fetch
    from hunter.apply.model import FormSpec
    from hunter.sources import ResolvedRole
    spec = FormSpec(ats="ashby", slug="acme", posting_id="1", title="VP", fields=(
        FormField("phone", "Phone", "phone", True),
        FormField("why", "Why do you want to work here?", "long_text", True)))
    monkeypatch.setattr(run_mod, "resolve_for_build", lambda cfg, row, cache: (
        ResolvedRole(company="Acme", title="VP", url="u", jd_url="u", jd_text="x",
                     location="London", live=True, source="t"), None, []))
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(fetch, "form_for", lambda url, key: spec)
    drafted = []

    def draft(cfg, sheet, role, questions, notes, view=None, job_id="", write=True):
        drafted.append(questions)
        return dict.fromkeys(questions, essays or "A plain answer.")
    monkeypatch.setattr(run_mod, "_draft_essays", draft)
    monkeypatch.setattr(run_mod, "his_view_for", lambda *a, **kw: None)
    return run_mod, drafted


def test_a_form_needing_his_phone_on_a_role_with_no_base_stops_before_any_essay(monkeypatch):
    run_mod, drafted = _fill_world(monkeypatch)
    plan, _au, _role = run_mod.build_fill_plan(Cfg(**{contact.BASES_KEY: raw_bases()}),
                                               None, {"job_id": "acme:vp"}, bank=build_bank())
    assert drafted == [], "no essay is drafted, so nothing is paid for"
    assert any(f.reason.startswith(NEEDS_BASE) for f in plan.blocking)


def test_a_plan_carrying_the_other_bases_number_is_never_sent(monkeypatch):
    run_mod, drafted = _fill_world(monkeypatch, essays=f"Ring {US_BASE.phone_display}.")
    with pytest.raises(run_mod.ContactMismatch) as e:
        run_mod.build_fill_plan(Cfg(**{contact.BASES_KEY: raw_bases()}), None,
                                {"job_id": "acme:vp", "header_base": "uk"}, bank=build_bank())
    assert str(e.value) == "a field carries the US number"
    assert drafted, "the essays were drafted, and their read-back caught it"


def test_the_role_base_is_the_recorded_one_never_worked_out_again(monkeypatch):
    run_mod, _ = _fill_world(monkeypatch)
    plan, _au, _role = run_mod.build_fill_plan(Cfg(**{contact.BASES_KEY: raw_bases()}), None,
                                               {"job_id": "acme:vp", "header_base": "us"},
                                               bank=build_bank())
    phone = next(f for f in plan.fields if f.kind == "phone")
    assert phone.value == US_BASE.phone_international, \
        "the letter was built with the US header, so the form follows it"


def test_cmd_approvals_counts_a_role_with_no_base_and_marks_nothing(monkeypatch):
    from test_his_view import L, Plan, approvals_world, rec
    import hunter.run as run_mod

    class NeedsBase(Plan):
        def __init__(self):
            super().__init__()
            self.blocking = [type("F", (), {"label": "Phone", "reason": NEEDS_BASE})()]
    db, mailed, reached = approvals_world(monkeypatch, [L("ardent", k="9")],
                                          [rec("ardent")], NeedsBase())
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {run_mod.NO_BASE: 1}
    assert mailed == {"by_hand": [], "approval": []}
    assert db.tables.get("hunter_alerts", []) == [], "never marked against his answer bank"


def test_doctor_reads_both_headers_in_counts_only():
    from hunter import doctor
    good = doctor.check_contact_bases(Cfg(**{contact.BASES_KEY: raw_bases()}))
    none = doctor.check_contact_bases(Cfg())
    assert good.state == doctor.OK and none.state == doctor.FAIL
    for c in (good, none):
        assert "555" not in c.detail + c.fix and "7700" not in c.detail + c.fix


def test_the_us_base_falls_back_to_his_info_bank_rows_and_the_uk_base_never_does():
    from hunter.package.contact import Base
    bare_us = Base(key="us", place="Brooklyn, NY", phone_display=US_BASE.phone_display,
                   phone_international=US_BASE.phone_international, country="United States")
    bare_uk = Base(key="uk", place="London", phone_display=UK_BASE.phone_display,
                   phone_international=UK_BASE.phone_international, country="United Kingdom")
    us = Resolver(build_bank(), role_location="New York, NY", contact=bare_us)
    got = us.resolve(field("What is your home zip code?"))
    assert isinstance(got, Answer) and got.value == "11222" and "US base" in got.source
    uk = Resolver(build_bank(), role_location="London", contact=bare_uk)
    got = uk.resolve(field("What is your home zip code?"))
    assert isinstance(got, Unanswered) and "UK base" in got.note
