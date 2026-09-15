"""The payload the extension fills from.

Its selectors come from the drivers rather than a second list: there is one idea
of where a field lives on an Ashby form and it is AshbyDriver's.
"""
from __future__ import annotations

import base64

from hunter.apply import payload as P
from hunter.apply.fill import FillPlan, FilledField


def plan(fields=(), ats="ashby", style=""):
    return FillPlan(ats=ats, slug="harvey", posting_id="p1", company="Harvey",
                    role="Head of GTM", fields=tuple(fields),
                    attachment_style=style)


def f(**kw):
    base = dict(key="k", label="L", kind="short_text", required=False,
                value="v", source="s")
    base.update(kw)
    return FilledField(**base)


def test_the_capability_key_is_not_guessable():
    keys = {P.new_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(len(k) == 32 and int(k, 16) >= 0 for k in keys)


def test_playwright_only_selectors_are_dropped():
    """`:text-is()` is real to Playwright and a syntax error to
    document.querySelectorAll, so it is dead weight in a payload a browser has
    to read. The extension finds those controls by their label instead."""
    kept = P.css_only([
        'input[name="x"]',
        '.entry:has(label:text-is("Location")) input[role="combobox"]',
        'div >> text=Yes',
    ])
    assert kept == ['input[name="x"]']


def test_every_field_carries_a_kind_the_script_knows():
    p = P.build(plan([
        f(kind="short_text", label="Employer"),
        f(kind="single_select", label="Sponsorship", value="No"),
        f(kind="location", label="Location", value="New York, United States"),
        f(kind="boolean", label="Authorised", value="Yes"),
    ]))
    kinds = {x["label"]: x["kind"] for x in p["fields"]}
    assert kinds == {"Employer": "text", "Sponsorship": "choice",
                     "Location": "typeahead", "Authorised": "choice"}
    assert all(k in P.FILLABLE for k in kinds.values())


def test_a_field_with_no_answer_is_left_out():
    p = P.build(plan([f(label="Blank", value=""), f(label="Filled", value="x")]))
    assert [x["label"] for x in p["fields"]] == ["Filled"]


def test_the_documents_travel_as_bytes_under_their_proper_names():
    p = P.build(plan([f(key="_systemfield_resume", label="Resume",
                        kind="file_resume", value="the built PDF")]),
                attachments={"file_resume": b"%PDF-1.4"},
                names={"file_resume": "KrishRaja_Application_Harvey.pdf"})
    assert len(p["files"]) == 1
    doc = p["files"][0]
    assert doc["name"] == "KrishRaja_Application_Harvey.pdf"
    assert base64.b64decode(doc["b64"]) == b"%PDF-1.4"
    assert any("_systemfield_resume" in s for s in doc["selectors"])


def test_a_file_field_with_no_bytes_is_not_announced():
    """Telling the extension about a document that is not there would have it
    report a missing attachment rather than simply not mentioning one."""
    p = P.build(plan([f(key="r", label="Resume", kind="file_resume", value="x")]))
    assert p["files"] == []


def test_the_url_is_the_drivers_own():
    p = P.build(plan([f()]))
    assert p["url"] == "https://jobs.ashbyhq.com/harvey/p1/application"


# ---------- the questions no board API reports ----------

class Entry:
    def __init__(self, value, usable=True):
        self.value, self.usable = value, usable


class Bank:
    def __init__(self, **rows): self.rows = rows
    def get(self, name): return self.rows.get(name)


def test_his_recorded_demographics_travel_with_the_payload():
    """OpenAI's form asks gender, race, veteran and disability. Ashby's API
    reports none of them, so no field named them and every one went out blank
    on a form where Krish's own answers were already on record."""
    d = P.demographics(Bank(**{
        "Gender": Entry("Male"),
        "Race / ethnicity": Entry("Two or more races"),
        "Veteran status": Entry("Not a veteran"),
        "Disability status": Entry("No disability"),
    }))
    by = {x["label"]: x for x in d}
    assert set(by) == {"Gender", "Race or ethnicity", "Veteran status",
                       "Disability status"}
    assert "male" in by["Gender"]["phrases"]
    # The wording a real form uses, not the wording his bank uses.
    assert any("not a protected veteran" in p for p in by["Veteran status"]["phrases"])
    assert any("do not have a disability" in p
               for p in by["Disability status"]["phrases"])


def test_an_answer_with_no_mapping_is_never_guessed_at():
    assert P.demographics(Bank(**{"Gender": Entry("Genderqueer")})) == []


def test_a_row_he_has_not_filled_is_left_alone():
    assert P.demographics(Bank(**{"Gender": Entry("")})) == []
    assert P.demographics(Bank(**{"Gender": Entry("Male", usable=False)})) == []
    assert P.demographics(None) == []


def test_male_and_female_are_not_the_same_answer():
    """"male" is inside "female". A substring match picks the wrong radio on the
    very first question, which is why the extension matches on word boundaries."""
    d = {x["label"]: x for x in P.demographics(Bank(**{"Gender": Entry("Female")}))}
    assert d["Gender"]["phrases"] == ["female"]
