"""The letter written for the posting, in his house style (package/compose.py).

Built on the emulator from a v5 master made out of a letter shaped like his
edited Sonos letter. Every word, figure and name here is invented."""
import copy
import json

import pytest

from contact_bases import BASES, UK_BASE, US_BASE
from fake_docs import FakeDocBuild
from hunter import drafts
from hunter.apply.merge import page_count
from hunter.package import compose, house, masters
from test_house import letter as specimen
from test_masters import cv_doc

COMPANY = "Zentrox, Inc."
JD = ("Zentrox builds wireless speakers and a home audio platform. You will lead platform "
      "partnerships and licensing deals, report to the COO, and build a small team.")
EVIDENCE = ("Quorra: grew a partner engine from $2M to $9M. Velmont: licensed the stack to "
            "three businesses. Halcyon: closed a $1.5M partnership. I build AI native sales "
            "engines. " + JD).lower()


def answer(**over):
    a = {"hiring_lead": "Hiring Team",
         "hook": "Zentrox is turning a home audio platform into a partner business, and the "
                 "deals that unlock it are the ones I have spent a decade closing.",
         "identity": "I'm a commercial leader with technical roots, and I build AI native "
                     "sales engines.",
         "bullets": [{"label": "Quorra:", "text": "grew a partner engine from $2M to $9M."},
                     {"label": "Velmont:", "text": "licensed the stack to three businesses."},
                     {"label": "Halcyon:", "text": "closed a $1.5M partnership."}],
         "why_now": "I am looking for a platform business where partnerships are the growth line.",
         "close": "I'd rather show you the partner engines I have built than describe them.",
         "cv_summary": 'I build platform partnerships and licensing deals: at Quorra I grew a partner engine from $2M to $9M, at Velmont I licensed the stack to three businesses, and at Halcyon I closed a $1.5M partnership. I want to lead the platform partnerships of a home audio business, reporting to the COO, with a small team built around licensing deals.',
         "asks": [{"ask": "partnerships", "quote": "lead platform partnerships and licensing deals",
                   "answered_by": "Velmont"}],
         "reader": READER,
         "map": [{"part": p, "belief": b} for p, b in MAP],
         "review": [{"part": p, "feeds_doubt": ""} for p, _ in MAP]}
    a.update(over)
    return a


READER = {"who": "the COO", "accountable_for": "platform partnerships and licensing",
          "must_believe": [
              {"belief": "he closes platform deals", "quote": "lead platform partnerships and licensing deals"},
              {"belief": "he works for an operator", "quote": "report to the COO"},
              {"belief": "he builds teams", "quote": "build a small team"}],
          "doubts": [{"doubt": "media, not audio", "answered_by": "Velmont"},
                     {"doubt": "never built a team", "answered_by": "Quorra"}],
          "register": "an operator"}
MAP = [("hook", 0), ("identity", 1), ("bullet 1", 2), ("bullet 2", 0), ("bullet 3", 0),
       ("why_now", 1), ("close", 2), ("cv_summary", 0)]


META = {"company": COMPANY, "short_name": "Zentrox", "base": "us", "needs": [], "entries": [],
        "bank": [], "banned": ["world-class"], "jd": JD}


def probs(a, **meta):
    return compose.problems(a, evidence=EVIDENCE, meta=dict(META, **meta))


# ---------- whether an answer may be used ----------

def test_a_letter_in_his_register_from_his_evidence_passes():
    assert probs(answer()) == []


@pytest.mark.parametrize("over,code", [
    ({"close": "Would rather show you the engines than describe them."}, "A13 the close"),
    ({"hook": "Happy to share how the deals get done. Zentrox is a home audio platform."},
     "A13 the hook"),
    ({"identity": "A commercial leader with technical roots who builds AI native sales engines."},
     "A13 the identity paragraph"),
    ({"bullets": [{"label": "Quorra:", "text": "grew a partner engine from $2M to $11M."},
                  {"label": "Velmont:", "text": "licensed the stack."},
                  {"label": "Halcyon:", "text": "closed a partnership."}]}, "A15 the bullet 1"),
    ({"bullets": [{"label": "Brightwater:", "text": "licensed the stack."},
                  {"label": "Velmont:", "text": "licensed the stack."},
                  {"label": "Halcyon:", "text": "closed a partnership."}]}, "A15 the bullet 1"),
    ({"close": "I'm a world-class closer."}, "A15 the close"),
    ({"asks": [{"ask": "x", "quote": "manage a sales team of fifty", "answered_by": "gap"}]},
     "asks: 1 quote(s)"),
    ({"asks": []}, "asks: none"),
    ({"hiring_lead": "Dana Whitfield"}, "shape: the letter is addressed"),
    ({"bullets": [{"label": "Quorra:", "text": "grew a partner engine."}]}, "shape: 1 bullets"),
    ({"hook": "Zentrox is a home audio platform. " * 30}, "shape: the hook is over"),
])
def test_each_rule_refuses_its_own_break(over, code):
    got = probs(answer(**over))
    assert any(f.startswith(code) for f in got), got


def test_a_failure_never_quotes_a_figure_or_a_name():
    got = probs(answer(bullets=[{"label": "Brightwater:", "text": "made $77M."}] * 3))
    assert got and not any("77" in f or "Brightwater" in f for f in got)


def test_his_line_must_be_carried_where_the_business_is_his_kind():
    line = "I have played in bands for twenty years and still record at home."
    meta = {"needs": ["voice_audio_speech"], "entries": [line], "bank": [line]}
    assert "A14 his personal line for this business is missing" in probs(answer(), **meta)
    a = answer(hook=answer()["hook"] + " " + line)
    assert probs(a, **meta) == []


def test_his_line_never_reaches_a_business_it_is_not_for():
    line = "I have played in bands for twenty years and still record at home."
    a = answer(close="I'd rather show you. " + line)
    assert "A14 a personal line of his is in a letter to a business it is not for" in \
        probs(a, bank=[line], entries=[line])


def test_the_routine_runs_the_same_check_through_drafts():
    assert drafts.problems_for("letter", answer(), prompt=JD, evidence=EVIDENCE, meta=META) == []
    assert drafts.problems_for("letter", "not json", prompt=JD, evidence=EVIDENCE, meta=META) == [
        "the answer is not the letter schema: JSONDecodeError"]


def test_settle_never_sends_a_letter_to_the_api(monkeypatch):
    rows = [{"id": 1, "kind": "letter", "ref": "z:vp", "status": "queued",
             "requested_at": "2026-01-01T00:00:00+00:00", "prompt": "p"}]
    monkeypatch.setattr(drafts, "db_get", lambda cfg, t, p: rows)

    def api(*a):
        raise AssertionError("a letter was sent to the API")
    lines = drafts.settle(None, apply=True, api_case=api, api_observation=api)
    assert lines[0].startswith("drafts: 0 used, 0 failed, 0 from the API fallback, 0 waiting")


# ---------- spelling ----------

def test_uk_roles_get_uk_spelling_and_names_keep_theirs():
    got = compose.spell("Optimized pricing. We will optimize the World Health Organization's "
                        "commercialization plan.", "uk")
    assert got == ("Optimised pricing. We will optimise the World Health Organization's "
                   "commercialisation plan.")
    assert compose.spell("We optimise the programme.", "us") == "We optimize the programme."
    assert compose.spelling_problems("we optimize it", "uk") == [
        "A13 1 word(s) spelled the US way for a UK role"]
    assert compose.spelling_problems("we optimise it", "us") == [
        "A13 1 word(s) spelled the UK way for a US role"]
    assert compose.spelling_problems("World Health Organization", "uk") == []


# ---------- the build ----------

def master_db(pages=(1,)):
    db = FakeDocBuild({"spec": specimen()})
    db.export_pages = [1]
    out = masters.build_v5(db, "spec", folder_id="f", page_count=page_count, apply=True)
    assert out["problems"] == []
    db.export_pages = list(pages)
    return db, out["made"]


def build(a=None, *, base=US_BASE, pages=(1,), needs=(), entries=()):
    db, master = master_db(pages)
    lt = compose.parse(a or answer())
    got = compose.build_letter(db, lt, master_id=master, title="t", folder_id="f", base=base,
                               bases=BASES, date_text="October 16, 2026", company=COMPANY,
                               role="Vice President, Partnerships", page_count=page_count,
                               needs=list(needs), entries=list(entries),
                               bank_words=list(entries))
    return db, got


def texts(db, doc_id):
    return [house.norm(p.text) for p in house.snapshot(db.get(doc_id))]


def test_the_letter_is_his_layout_with_the_role_written_in():
    db, got = build()
    assert got.problems == [] and got.pages == 1 and got.notes == []
    t = texts(db, got.doc_id)
    assert t[2] == house.norm(US_BASE.header_line())
    assert "Re: Vice President, Partnerships" in t and "Dear Hiring Team," in t
    assert t[-1] == house.SIGNATURE and house.SIGN_OFF in t
    paras = house.snapshot(db.get(got.doc_id))
    bullets = [p for p in paras if p.bullet]
    assert len(bullets) == 3
    first = [r for r in bullets[0].runs if r.text.strip()]
    assert first[0].text == "Quorra:" and first[0].bold and not first[1].bold
    assert all((r.font, r.size) == ("Arial", 9.0) for p in paras[7:] for r in p.runs if r.text.strip())


def test_a_uk_role_takes_london_and_uk_spelling():
    a = answer(identity="I'm a commercial leader with technical roots, and I build AI native "
                        "sales engines that optimize pipeline.")
    db, got = build(a, base=UK_BASE)
    assert got.problems == []
    t = " ".join(texts(db, got.doc_id))
    assert house.norm(UK_BASE.header_line()) in t and "optimise pipeline" in t and "optimize" not in t


def test_the_ladder_brings_the_margins_in_then_cuts_the_fourth_bullet():
    four = answer(bullets=answer()["bullets"] + [{"label": "Orrin:", "text": "built a team."}])
    db, got = build(four, pages=(2, 2, 1))
    assert got.notes == ["margins at the floor", "the fourth bullet cut to fit one page"]
    assert got.problems == [] and got.pages == 1
    assert sum(1 for p in house.snapshot(db.get(got.doc_id)) if p.bullet) == 3
    assert db.get(got.doc_id)["documentStyle"]["marginLeft"]["magnitude"] == masters.MARGIN_FLOOR


def test_a_letter_that_never_fits_one_page_holds():
    db, got = build(pages=(2,))
    assert "A9 the letter renders 2 pages; the limit is 1" in got.problems
    assert got.notes == ["margins at the floor", "why-now cut to fit one page"]


def test_his_line_is_read_back_from_the_built_letter():
    line = "I have played in bands for twenty years and still record at home."
    a = answer(hook=answer()["hook"] + " " + line)
    db, got = build(a, needs=["voice_audio_speech"], entries=[line])
    assert got.problems == []
    db, got = build(answer(), needs=["voice_audio_speech"], entries=[line])
    assert "A14 his personal line is not in the letter as built" in got.problems


def test_a_bold_body_is_refused_on_read_back(monkeypatch):
    real = compose.fill_requests

    def bolder(doc, lt, vals):
        reqs = real(doc, lt, vals)
        for r in reqs:
            st = r.get("updateTextStyle")
            if st and st["fields"] == "*" and st["textStyle"].get("bold") is None:
                st["textStyle"] = dict(st["textStyle"], bold=True)
        return reqs
    monkeypatch.setattr(compose, "fill_requests", bolder)
    db, got = build()
    assert "A11 the opening paragraph is bold" in got.problems


# ---------- the CV ----------

def cv_master():
    db = FakeDocBuild({"cv": cv_doc(), "spec": specimen()})
    db.export_pages = [2]
    out = masters.build_cv_master(db, "cv", "spec", folder_id="f", page_count=page_count,
                                  apply=True)
    assert out["problems"] == []
    return db, out["made"]


def heading_sizes(db, doc_id):
    return masters._cv_heading_sizes(house.snapshot(db.get(doc_id)))


def test_the_cv_takes_the_roles_header_and_spelling():
    db, m = cv_master()
    sizes = heading_sizes(db, m)
    db.docs[m].apply({"replaceAllText": {"containsText": {"text": "Built an invented region",
                                                          "matchCase": True},
                                         "replaceText": "Optimized an invented region"}})
    db.docs[m].apply({"replaceAllText": {"containsText": {"text": "Grew an invented line",
                                                          "matchCase": True},
                                         "replaceText": "Grew and optimized an invented line"}})
    db.export_pages = [2]
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=UK_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=sizes)
    assert got.problems == [], got.problems
    t = " ".join(texts(db, got.doc_id))
    assert house.norm(UK_BASE.header_line()) in t and "optimised an invented line" in t
    # A capitalised word is left as it is: it may be a name.
    assert "Optimized an invented region" in t


def test_the_banner_goes_before_the_cv_runs_over_two_pages():
    db, m = cv_master()
    db.export_pages = [3, 2]
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=US_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=heading_sizes(db, m))
    assert got.problems == [] and got.notes == ["banner taken off to keep the CV to two pages"]
    db, m = cv_master()
    db.export_pages = [3]
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=US_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=heading_sizes(db, m))
    assert "A9 the cv renders 3 pages; the limit is 2" in got.problems


def test_the_cv_summary_is_written_for_the_role_when_given():
    db, m = cv_master()
    anchor, count = compose.cv_summary_span(house.snapshot(db.get(m)))
    assert count == 1
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=US_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=heading_sizes(db, m),
                           summary="I build partner engines.", summary_anchor=anchor,
                           summary_count=count)
    assert got.problems == [] and "I build partner engines." in texts(db, got.doc_id)


# ---------- the request the routine reads ----------

def test_the_model_letter_reaches_the_routine_without_his_personal_lines():
    line = "I have played in bands for twenty years and still record at home."
    model = "Zentrox is busy. " + line + " I'm a commercial leader."
    ctx = compose.shared_context(model_letter=model, evidence_text="facts", bank_words=[line])
    assert "played in bands" not in ctx and "I'm a commercial leader." in ctx
    assert json.loads(ctx.split("SCHEMA\n", 1)[1].split("\n\n", 1)[0]) == compose.SCHEMA


def test_the_role_prompt_carries_the_line_and_the_base():
    p = compose.role_prompt(company=COMPANY, title="VP", location="London", base_key="uk",
                            jd_text=JD, judge_answers={"business": {"quote": "home audio"}},
                            his_view="", entries=["A line of his."], needs=["voice_audio_speech"])
    assert "BASE: UK (UK spelling)" in p and "- A line of his." in p and "POSTING\n" + JD in p


def test_a_word_the_fill_missed_is_caught_on_read_back(monkeypatch):
    monkeypatch.setattr(compose, "respelled", lambda lt, base_key: lt)
    a = answer(identity="I'm a commercial leader with technical roots, and I build AI native "
                        "sales engines that optimize pipeline.")
    db, got = build(a, base=UK_BASE)
    assert "A13 1 word(s) spelled the US way for a UK role" in got.problems


def test_bold_anywhere_in_the_body_is_refused_on_read_back(monkeypatch):
    real = compose.fill_requests
    close = answer()["close"]

    def bold_close(doc, lt, vals):
        reqs = real(doc, lt, vals)
        for i, r in enumerate(reqs):
            ins = r.get("insertText")
            if ins and ins["text"] == close:
                at = ins["location"]["index"]
                # Right after the close is written: the requests run from the
                # end of the letter backwards, so its index holds here.
                reqs.insert(i + 2, {"updateTextStyle": {
                    "range": {"startIndex": at, "endIndex": at + 4},
                    "textStyle": {"bold": True}, "fields": "bold"}})
                break
        return reqs
    monkeypatch.setattr(compose, "fill_requests", bold_close)
    db, got = build()
    assert got.problems == ["A11 a body paragraph carries bold"]


def test_the_date_is_written_the_way_the_roles_country_writes_it():
    import datetime
    day = datetime.date(2026, 10, 5)
    assert compose.date_for("uk", day) == "5 October 2026"
    assert compose.date_for("us", day) == "October 5, 2026"



# ---------- written for this role (A16) ----------

MODEL = ("Zentrox is clearly busy. I'm a commercial leader born of technical roots, who has "
         "scaled a business to nine figures and carried a P&L. Worth saying why I am looking. "
         "I'd rather show you the system than describe it.")
CONTEXT = "RULES\n\nMODEL LETTER (his own)\n" + MODEL + "\n\nEVIDENCE (x)\nfacts"


def role(a, **meta):
    return [f for f in compose.problems(a, evidence=EVIDENCE, meta=dict(META, **meta),
                                        context=CONTEXT) if f.startswith("A16")]


def written_for_zentrox(**over):
    a = answer(
        identity="I'm a commercial leader with technical roots, and I build the platform "
                 "partnerships and licensing deals a home audio platform needs.",
        why_now="I am looking for a platform business like Zentrox, where partnerships are "
                "the growth line.",
        close="I'd rather show you the partner engines I have built, starting with Zentrox's "
              "licensing deals.",
        bullets=[{"label": "Quorra:", "text": "grew a partner engine from $2M to $9M, the "
                                               "platform partnerships work this seat leads."},
                 {"label": "Velmont:", "text": "licensed the stack to three businesses, the "
                                                "licensing deals Zentrox runs."},
                 {"label": "Halcyon:", "text": "closed a $1.5M partnership."}])
    a.update(over)
    return a


def test_a_letter_written_for_the_role_passes():
    assert role(written_for_zentrox()) == []


def test_a_paragraph_copied_from_his_model_letter_is_refused():
    a = written_for_zentrox(identity="I'm a commercial leader born of technical roots, who has "
                                     "scaled a business to nine figures and carried a P&L.")
    assert "A16 the identity copies his model letter; write it for this role" in role(a)


def test_a_generic_paragraph_is_refused_and_says_which():
    a = written_for_zentrox(close="I'd rather show you what I have built than describe it to you.")
    got = role(a)
    assert "A16 the close says nothing about this company or this seat" in got
    a = written_for_zentrox(why_now="I am looking for a platform business with growth ahead.")
    assert "A16 the why_now does not name the company" in role(a)


def test_bullets_must_say_what_they_mean_for_the_role():
    a = written_for_zentrox(bullets=[{"label": "Quorra:", "text": "grew a partner engine."},
                                     {"label": "Velmont:", "text": "licensed the stack."},
                                     {"label": "Halcyon:", "text": "closed a $1.5M deal."}])
    assert "A16 fewer than two bullets say what they mean for this role" in role(a)


def test_his_personal_line_never_counts_as_a_copy():
    line = "I'm a commercial leader born of technical roots, who has scaled a business to nine figures."
    a = written_for_zentrox(hook=written_for_zentrox()["hook"] + " " + line)
    assert role(a, entries=[line], needs=["voice_audio_speech"], bank=[line]) == []


def test_without_the_context_the_role_check_is_not_run():
    assert not [f for f in compose.problems(answer(close="I'd rather show you the system than "
                                                         "describe it."),
                                            evidence=EVIDENCE, meta=META) if f.startswith("A16")]



# ---------- the CV summary, written for the role ----------

CV_CONTEXT = CONTEXT + "\nPROFESSIONAL SUMMARY\nWith 16 years commercializing data and tech inside " \
    "content and media enterprises, I have scaled business to nine figures and ship AI into production " \
    "myself.\nCAREER HIGHLIGHTS\nx"


def summary_role(a):
    return [f for f in compose.problems(a, evidence=EVIDENCE, meta=META, context=CV_CONTEXT)
            if "CV summary" in f]


def test_a_summary_written_for_the_role_passes():
    assert summary_role(written_for_zentrox()) == []


def test_an_empty_summary_is_refused():
    got = compose.problems(answer(cv_summary=""), evidence=EVIDENCE, meta=META)
    assert "shape: the CV summary is missing or too short to be written for the role" in got


def test_his_own_cv_summary_repeated_is_refused():
    a = written_for_zentrox(cv_summary=(
        "With 16 years commercializing data and tech inside content and media enterprises, I have "
        "scaled business to nine figures and ship AI into production myself. I want to lead platform "
        "partnerships and licensing deals for a home audio business."))
    assert "A16 the CV summary repeats his CV's own summary; write it for this role" in summary_role(a)


def test_a_summary_about_nothing_in_particular_is_refused():
    a = written_for_zentrox(cv_summary=(
        "I am a commercial leader with technical roots who builds AI native sales engines, and I have "
        "spent a long career growing revenue, building teams and taking businesses into new markets, "
        "and I would bring all of it to the next thing I do, with energy and care for the people."))
    assert "A16 the CV summary says nothing about this role" in summary_role(a)


def test_the_summary_bolds_its_figures_and_his_own_bold_phrases():
    got = compose.summary_bold("I grew it from $2M to $9M and ran a 14-agent autonomous AI operating "
                               "system; 30% came from partners.",
                               ["14-agent autonomous AI operating system", "a phrase it lost"])
    assert got == ["$2M to $9M", "30%", "14-agent autonomous AI operating system"]


def test_the_built_cv_carries_the_role_summary_with_its_figures_bold():
    db, m = cv_master()
    anchor, count = compose.cv_summary_span(house.snapshot(db.get(m)))
    text = "I grew a partner engine from $2M to $9M and licensed the stack to three businesses."
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=US_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=heading_sizes(db, m),
                           summary=text, summary_anchor=anchor, summary_count=count)
    assert got.problems == []
    para = next(p for p in house.snapshot(db.get(got.doc_id)) if house.norm(p.text) == text)
    bold = [r.text for r in para.runs if r.bold]
    assert bold == ["$2M to $9M"]
    assert all((r.font, r.size) == ("Arial", 9.0) for r in para.runs if r.text.strip())


def test_a_money_range_is_bolded_whole():
    t = "I took the region from $0 to $12M ARR and closed deals up to $600K, 30% from partners."
    assert compose.summary_bold(t, []) == ["$0 to $12M ARR", "$600K", "30%"]


# ---------- his CV highlights, in the role's order (canon 9.12) ----------

def test_a_letter_must_order_every_highlight_once_when_his_cv_has_them():
    def shape(a):
        return [f for f in probs(a, highlights=3) if "highlight" in f]
    assert shape(answer()) == ["shape: no order for his CV highlights; put them in this role's order"]
    bad = "shape: the CV highlight order must use each of 0 to 2 once"
    assert shape(answer(cv_highlight_order=[0, 0, 1])) == [bad]
    assert shape(answer(cv_highlight_order=[0, 1])) == [bad]
    assert shape(answer(cv_highlight_order=["2", "0", "1"])) == [bad]
    assert shape(answer(cv_highlight_order=[2, 0, 1])) == []
    assert [f for f in probs(answer()) if "highlight" in f] == [], "no highlights asked, none owed"


def test_the_writer_sees_his_highlights_numbered_in_his_order():
    db, m = cv_master()
    got = compose.cv_highlights(house.snapshot(db.get(m)))
    assert got == ["Built an invented region from $0 to $5M", "Grew an invented line $2M to $8M"]
    ctx = compose.shared_context(model_letter="", evidence_text="", bank_words=[], highlights=got)
    assert "0. Built an invented region from $0 to $5M\n1. Grew an invented line $2M to $8M" in ctx


def highlight_texts(db, doc_id):
    paras = house.snapshot(db.get(doc_id))
    return [(house.norm(p.text), [r.text for r in p.runs if r.bold and r.text.strip()])
            for p in paras if p.text.startswith(("Built an invented", "Grew an invented"))]


def test_the_built_cv_puts_his_highlights_in_the_roles_order_with_his_bold():
    db, m = cv_master()
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=US_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=heading_sizes(db, m),
                           highlight_order=[1, 0])
    assert got.problems == [] and "highlights in this role's order" in got.notes
    assert highlight_texts(db, got.doc_id) == [
        ("Grew an invented line $2M to $8M", ["$2M to $8M"]),
        ("Built an invented region from $0 to $5M", ["$0 to $5M"])]


def test_an_order_for_other_highlights_leaves_his_order_and_says_so():
    db, m = cv_master()
    got = compose.build_cv(db, master_id=m, title="c", folder_id="f", base=US_BASE, bases=BASES,
                           page_count=page_count, heading_sizes=heading_sizes(db, m),
                           highlight_order=[2, 0, 1])
    assert got.problems == []
    assert "highlights kept in his order (the CV's highlights changed)" in got.notes
    assert [t for t, _ in highlight_texts(db, got.doc_id)] == [
        "Built an invented region from $0 to $5M", "Grew an invented line $2M to $8M"]


# ---------- his company, written his way ----------

def test_a_letter_naming_his_company_any_other_way_is_refused():
    """His ruling 2026-10-15: "mind/make", as his CV writes it."""
    close = "I'd rather show you the partner engines I built at {} than describe them."
    assert probs(answer(close=close.format("mind/make"))) == []
    got = probs(answer(close=close.format("Mindmake")))
    assert "A13 the close names his company 'Mindmake'; write 'mind/make'" in got
    summary = answer()["cv_summary"].replace("I build", "At Mind/Make I build", 1)
    assert "A13 the cv_summary names his company 'Mind/Make'; write 'mind/make'" in \
        probs(answer(cv_summary=summary))


# ---------- the reader first (A17) and altitude (A18), 2026-10-19 ----------

def _review(**fed):
    return [{"part": p, "feeds_doubt": fed.get(p.replace(" ", "_"), "")} for p, _ in MAP]


@pytest.mark.parametrize("over,code", [
    ({"reader": {}}, "A17 no reader brief"),
    ({"reader": dict(READER, must_believe=READER["must_believe"][:2])}, "A17 the reader brief has 2 beliefs"),
    ({"reader": dict(READER, must_believe=[dict(b, quote="run a sales team of fifty")
                                           for b in READER["must_believe"]])},
     "A17 3 belief(s) quote nothing"),
    ({"reader": dict(READER, doubts=READER["doubts"][:1])}, "A17 the reader brief names fewer than 2"),
    ({"map": [{"part": p, "belief": b} for p, b in MAP[:-1]]}, "A17 1 part(s) serve no belief"),
    ({"map": [{"part": p, "belief": 0} for p, _ in MAP]}, "A17 no part builds belief 1, 2"),
    ({"review": _review(close="the engines I have built")}, "A17 read as the reader, close"),
    ({"review": _review()[:-2]}, "A17 the review did not read 2"),
])
def test_each_reader_rule_refuses_its_own_break(over, code):
    got = probs(answer(**over))
    assert any(f.startswith(code) for f in got), got


def test_a_tool_the_posting_does_not_name_is_refused_and_named_by_label():
    a = answer(close="I'd rather show you the partner engines I built on n8n and Supabase.")
    got = probs(a)
    assert any(f.startswith("A18 the close says how it is built (n8n, Supabase)") for f in got), got


def test_a_tool_the_posting_names_may_be_said_back():
    jd = JD + " Our stack runs on n8n."
    a = answer(close="I'd rather show you the partner engines I built on n8n.")
    assert not [f for f in compose.problems(a, evidence=EVIDENCE + " n8n",
                                            meta=dict(META, jd=jd)) if f.startswith("A18")]


def test_his_ai_system_is_said_once_outside_an_ai_seat_and_freely_inside_one():
    a = answer(identity="I'm a commercial leader, and I run my firm on an AI operating system.",
               close="I'd rather show you the AI operating system than describe it.")
    got = probs(a, family="corp_dev_strategy")
    assert any("2 parts speak of his AI system" in f for f in got), got
    assert not [f for f in probs(a, family="ai_chief_of_staff_transformation")
                if "speak of his AI system" in f]


def test_the_writer_is_told_the_seat_and_reads_the_evidence_marked():
    p = compose.role_prompt(company=COMPANY, title="Head of Corporate Development", location="",
                            base_key="us", jd_text=JD, judge_answers=None, his_view="",
                            entries=[], needs=[])
    assert "ROLE TYPE" in p and "build, buy or partner" in p
    ctx = compose.shared_context(model_letter="", evidence_text="Ran a 14-agent system on n8n.",
                                 bank_words=[], his_rules='- "less specificity"')
    assert "mechanics, never in prose: n8n, an agent count" in ctx
    assert "HIS RULES FOR EVERY PACKAGE" in ctx
