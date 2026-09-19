"""Learning from his edits. The rule under test throughout: a difference
between what hunter recorded writing and what the sheet says now can only have
come from Krish, and hunter must never read its own output as his."""
import pytest

from hunter import amend
from hunter.sheet import COLS, SheetRow, make_row

EM_DASH = chr(0x2014)


def row(n, **over):
    cells = make_row(company="Acme", role="VP Strategy",
                     jd_url="https://job-boards.greenhouse.io/acme/jobs/1",
                     score=7, source="ats_sweep", jd_snippet="Does a thing.")
    for k, v in over.items():
        cells[COLS[k]] = v
    return SheetRow(row_number=n, cells=cells, verdict=cells[COLS["Verdict"]],
                    company="Acme", role=cells[COLS["Role"]],
                    jd_url="https://job-boards.greenhouse.io/acme/jobs/1")


def state_for(r, job_id="acme:vp"):
    return {job_id: {"job_id": job_id, "fields": amend.fields_of(r)}}


PAIRED = {3: {"job_id": "acme:vp"}}


# ---------- what counts as the same text ----------

def test_whitespace_is_not_an_amendment():
    assert amend.norm("  a   b \n") == amend.norm("a b")


def test_smart_quotes_are_not_an_amendment():
    assert amend.norm("he’s here") == amend.norm("he's here")


def test_a_dash_style_change_is_not_an_amendment():
    assert amend.norm("a " + EM_DASH + " b") == amend.norm("a - b")


def test_a_real_word_change_is_an_amendment():
    assert amend.norm("VP Strategy") != amend.norm("VP Corporate Strategy")


# ---------- detection ----------

def test_a_rewritten_cell_is_read_back_as_a_correction():
    before = row(3)
    after = row(3, **{"Why It Fits": "Actually it fits because of the data work."})
    found = amend.detect([after], PAIRED, state_for(before))
    assert len(found) == 1
    assert found[0]["field"] == "Why It Fits"
    assert found[0]["after_text"].startswith("Actually it fits")
    assert found[0]["lesson"] == amend.MEANING["Why It Fits"]


def test_an_untouched_row_produces_nothing():
    r = row(3)
    assert amend.detect([r], PAIRED, state_for(r)) == []


def test_a_row_hunter_never_recorded_produces_nothing():
    # the incumbent's rows, and anything he pasted in himself. Guessing here
    # would file an amendment for every inherited row on the sheet.
    assert amend.detect([row(3)], PAIRED, {}) == []


def test_a_row_with_no_job_id_produces_nothing():
    r = row(3)
    assert amend.detect([r], {}, state_for(r)) == []


def test_clearing_a_cell_is_not_treated_as_a_correction():
    before = row(3)
    after = row(3, **{"Comp": ""})
    assert amend.detect([after], PAIRED, state_for(before)) == []


def test_several_changed_cells_are_several_amendments():
    before = row(3)
    after = row(3, Role="Director of Strategy", Location="London",
                Score="9")
    found = amend.detect([after], PAIRED, state_for(before))
    assert {f["field"] for f in found} == {"Role", "Location", "Score"}


def test_the_verdict_is_never_an_amendment():
    # column A is his by definition, and learn.py owns it. Recording it here
    # would double count every rejection.
    assert "Verdict" not in amend.WATCHED
    before = row(3)
    after = row(3, Verdict="Declined - comp below bar")
    assert amend.detect([after], PAIRED, state_for(before)) == []


# ---------- documents ----------

def test_an_edited_cover_letter_is_an_amendment():
    from hunter.sheet import hyperlink
    r = row(3, **{"Cover Letter Doc": hyperlink(
        "https://docs.google.com/document/d/abc123", "CL")})
    state = {"acme:vp": {"job_id": "acme:vp", "fields": {},
                         "letter_text": "Dear team, I built the engine."}}
    found = amend.detect_docs(None, lambda url: "Dear team, I ran the P and L.",
                              [r], PAIRED, state)
    assert len(found) == 1 and found[0]["field"] == "cover letter"


def test_an_unreadable_document_is_not_an_amendment():
    from hunter.sheet import hyperlink
    r = row(3, **{"Cover Letter Doc": hyperlink(
        "https://docs.google.com/document/d/abc123", "CL")})
    state = {"acme:vp": {"job_id": "acme:vp", "fields": {},
                         "letter_text": "Dear team."}}
    assert amend.detect_docs(None, lambda url: "", [r], PAIRED, state) == []


def test_an_unchanged_document_is_not_an_amendment():
    from hunter.sheet import hyperlink
    r = row(3, **{"Cover Letter Doc": hyperlink(
        "https://docs.google.com/document/d/abc123", "CL")})
    state = {"acme:vp": {"job_id": "acme:vp", "fields": {},
                         "letter_text": "Dear team.  "}}
    assert amend.detect_docs(None, lambda url: "Dear team.", [r], PAIRED,
                             state) == []


# ---------- reading the pattern ----------

def test_three_corrections_in_one_direction_is_a_calibration_signal():
    amendments = [{"field": "Score", "before_text": "6", "after_text": "9"},
                  {"field": "Score", "before_text": "5", "after_text": "8"},
                  {"field": "Score", "before_text": "7", "after_text": "9"}]
    out = amend.score_calibration(amendments)
    assert "higher" in out and "scorer needs recalibrating" in out


def test_two_corrections_are_not_yet_a_signal():
    amendments = [{"field": "Score", "before_text": "6", "after_text": "9"},
                  {"field": "Score", "before_text": "5", "after_text": "8"}]
    assert amend.score_calibration(amendments) == ""


def test_corrections_that_cancel_out_say_nothing():
    amendments = [{"field": "Score", "before_text": "6", "after_text": "8"},
                  {"field": "Score", "before_text": "8", "after_text": "6"},
                  {"field": "Score", "before_text": "7", "after_text": "7"}]
    assert amend.score_calibration(amendments) == ""


def test_one_amendment_is_evidence_not_a_rule():
    assert amend.proposals([{"field": "Why It Fits", "company": "Acme",
                             "before_text": "a", "after_text": "b"}]) == []


def test_a_repeated_correction_earns_a_question_and_nothing_more():
    group = [{"field": "Why It Fits", "company": f"C{i}",
              "before_text": "a", "after_text": "b"} for i in range(3)]
    props = amend.proposals(group)
    assert len(props) == 1
    assert props[0]["count"] == 3
    assert props[0]["question"].startswith("You corrected Why It Fits on 3")
    # nothing here applies anything
    assert "apply" not in props[0]


# ---------- writing the snapshot ----------

def test_the_snapshot_replaces_rather_than_inserting(monkeypatch):
    """A row hunter has written to before already has a snapshot. A plain
    insert answers 409 and the whole amendment layer silently stops working,
    which is exactly what happened on the first live pass."""
    calls = []
    monkeypatch.setattr(amend, "db_insert",
                        lambda cfg, table, rows, **kw: calls.append(kw))
    amend.save_fields(None, "acme:vp", {"Role": "VP Strategy"})
    assert calls and calls[0].get("merge") is True
    assert calls[0].get("on_conflict") == "job_id"


def test_syncing_a_whole_pass_also_merges(monkeypatch):
    calls = []
    monkeypatch.setattr(amend, "db_insert",
                        lambda cfg, table, rows, **kw: calls.append(kw))
    amend.sync_after(None, [row(3)], PAIRED)
    assert calls and all(c.get("merge") is True for c in calls)
