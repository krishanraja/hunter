"""Form answers written by the routine on his subscription, never the API.

His answer, 2026-10-26, asked whether to move them as letters were moved:
"Yes, move them". They cost about $7 a month on the API. These hold what the
move must keep: no model call, one request per form, the same checks the API
path held each answer to, the form read whole, and an Apply email that goes
on its own once the answers pass, with no second press.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from hunter import actions, config, drafts, run
from hunter.apply import essays
from hunter.package import voicegate

EVIDENCE = voicegate.build_evidence(
    "Krish Raja took Nine Entertainment's data and automation line from $9m to "
    "$61m over three years. He ran an AI practice at mind/make. He worked at "
    "Microsoft. Nine was the employer.")
WHY_US = ("I took Nine's data and automation line from $9m to $61m, which is the closest "
          "thing I have to the job you are describing and the reason I am writing at all "
          "about this particular role. I ran an AI practice at mind/make afterwards, so the "
          "operating half of this is familiar ground rather than something I would be "
          "learning on your time.")
HARD = ("The hardest problem I have had was at Nine, growing a line of business while the "
        "market around it was shifting every quarter; the answer was to price the data, not "
        "the media, and to staff for that before the revenue arrived, which is what I would "
        "bring to this seat from the first week.")
QUESTIONS = ["Why us?", "A hard problem?"]


class Cfg:
    def __init__(self, **kw):
        self.raw = dict(kw)

    def optional(self, key, default=""):
        return self.raw.get(key) or default


class FakeDB:
    def __init__(self):
        self.rows = {"hunter_drafts": [], "hunter_draft_contexts": [], "hunter_actions": []}
        self.next = 1

    @staticmethod
    def _match(row, params):
        for k, v in params.items():
            if k in ("select", "order", "limit"):
                continue
            op, _, val = v.partition(".")
            if op == "eq" and str(row.get(k)) != val:
                return False
            if op == "in" and str(row.get(k)) not in val.strip("()").split(","):
                return False
        return True

    def get(self, cfg, table, params):
        out = [dict(r) for r in self.rows.setdefault(table, []) if self._match(r, params)]
        if params.get("order", "").startswith("id.desc"):
            out.sort(key=lambda r: -r["id"])
        if params.get("limit", "").isdigit():
            out = out[:int(params["limit"])]
        return out

    def insert(self, cfg, table, rows, **kw):
        for r in rows:
            r = dict(r)
            r.setdefault("id", self.next)
            r.setdefault("status", "queued")
            self.next += 1
            self.rows.setdefault(table, []).append(r)

    def patch(self, cfg, table, match, values):
        for r in self.rows.setdefault(table, []):
            if all(str(r.get(k)) == str(v) for k, v in match.items()):
                r.update(values)

    def patch_where(self, cfg, table, filters, values):
        n = 0
        for r in self.rows.setdefault(table, []):
            if self._match(r, filters):
                r.update(values)
                n += 1
        return n


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    for mod in (config, drafts):
        monkeypatch.setattr(mod, "db_get", d.get)
        monkeypatch.setattr(mod, "db_insert", d.insert)
        monkeypatch.setattr(mod, "db_patch", d.patch)
        monkeypatch.setattr(mod, "db_patch_where", d.patch_where)
    return d


@pytest.fixture
def no_model(monkeypatch):
    """Any attempt to reach a model fails the test."""
    class Client:
        def __init__(self, **kw):
            raise AssertionError("a model was called for a form answer")
    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    from hunter import llm
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a model was called for a form answer")))


def ask(notes=None, questions=QUESTIONS):
    return essays.draft_all(Cfg(), list(questions), company="Nine", role="GM",
                            jd_text="the posting", evidence=EVIDENCE, notes=notes,
                            ref="nine:gm")


def answer(db, answers, status="drafted"):
    r = db.rows["hunter_drafts"][-1]
    r.update(status=status, output={"answers": answers}, drafted_by="routine")
    return r


# ---------- Prepare asks the routine ----------

def test_a_form_is_one_request_and_no_model_is_called(db, no_model):
    notes = []
    assert ask(notes) == {}
    reqs = db.rows["hunter_drafts"]
    assert len(reqs) == 1 and reqs[0]["kind"] == "essay" and reqs[0]["ref"] == "nine:gm"
    assert reqs[0]["meta"]["questions"] == QUESTIONS
    assert "1. Why us?" in reqs[0]["prompt"] and "2. A hard problem?" in reqs[0]["prompt"]
    assert len(notes) == 2 and all(essays.WAITING in n for n in notes)


def test_a_form_already_asked_for_is_not_asked_again(db, no_model):
    ask()
    ask()
    assert len(db.rows["hunter_drafts"]) == 1


def test_the_routine_answers_go_on_the_form_once_they_pass(db, no_model):
    ask()
    r = answer(db, {"Why us?": WHY_US, "A hard problem?": HARD})
    notes = []
    assert ask(notes) == {"Why us?": WHY_US, "A hard problem?": HARD}
    assert r["status"] == "drafted", "reading is not sending: the drafts step still asks"
    assert all("drafted" in n for n in notes)


def test_a_dry_run_queues_and_changes_nothing(db, no_model):
    notes = []
    assert essays.draft_all(Cfg(), QUESTIONS, company="Nine", role="GM",
                            jd_text="the posting", evidence=EVIDENCE, notes=notes,
                            ref="nine:gm", write=False) == {}
    assert db.rows["hunter_drafts"] == []
    assert all("dry run: nothing queued" in n for n in notes)
    ask()
    answer(db, {"Why us?": WHY_US.replace("$61m", "$90m"), "A hard problem?": HARD})
    essays.draft_all(Cfg(), QUESTIONS, company="Nine", role="GM", jd_text="the posting",
                     evidence=EVIDENCE, ref="nine:gm", write=False)
    assert [r["status"] for r in db.rows["hunter_drafts"]] == ["drafted"]


def test_an_invented_figure_is_refused_and_the_routine_asked_again(db, no_model):
    ask()
    answer(db, {"Why us?": WHY_US.replace("$61m", "$90m"), "A hard problem?": HARD})
    assert ask() == {}
    statuses = [r["status"] for r in db.rows["hunter_drafts"]]
    assert statuses == ["failed", "queued"]


def test_a_form_refused_three_times_is_not_asked_a_fourth(db, no_model):
    for _ in range(essays.FAILS):
        ask()
        answer(db, {"Why us?": WHY_US.replace("$61m", "$90m"), "A hard problem?": HARD})
    notes = []
    assert ask(notes) == {}
    assert all(r["status"] == "failed" for r in db.rows["hunter_drafts"])
    assert len(db.rows["hunter_drafts"]) == essays.FAILS
    assert essays.STUCK in notes[0] and "3 times" in notes[0]


def test_a_new_question_is_a_new_request_whatever_the_old_record(db, no_model):
    for _ in range(essays.FAILS):
        ask()
        answer(db, {"Why us?": WHY_US.replace("$61m", "$90m"), "A hard problem?": HARD})
    ask()
    assert [r["status"] for r in db.rows["hunter_drafts"]] == ["failed"] * essays.FAILS
    ask(questions=QUESTIONS + ["Anything else?"])
    assert db.rows["hunter_drafts"][-1]["status"] == "queued"
    assert db.rows["hunter_drafts"][-1]["meta"]["questions"] == QUESTIONS + ["Anything else?"]


def test_a_form_that_now_asks_more_is_asked_for_whole(db, no_model):
    ask(questions=["Why us?"])
    ask(questions=QUESTIONS)
    statuses = [(r["status"], r["meta"]["questions"]) for r in db.rows["hunter_drafts"]]
    assert statuses == [("expired", ["Why us?"]), ("queued", QUESTIONS)]


def test_the_api_path_is_kept_behind_his_setting():
    assert essays.writer(Cfg()) == "routine"
    assert essays.writer(Cfg(hunter_essay_writer="api")) == "api"


# ---------- the checks ----------

def meta():
    return {"questions": QUESTIONS, "posting": "the posting", "banned": [], "family": "",
            "ruled_out": []}


def test_the_routine_runs_the_very_check_hunter_runs():
    out = {"answers": {"Why us?": WHY_US}}
    assert drafts.problems_for("essay", out, prompt="", evidence=EVIDENCE, meta=meta()) == \
        essays.form_check(out, prompt="", evidence=EVIDENCE, meta=meta())


def test_an_unanswered_question_is_refused():
    got = essays.form_check({"answers": {"Why us?": WHY_US}}, prompt="",
                            evidence=EVIDENCE, meta=meta())
    assert got == ["question 2 has no answer"]


def test_the_same_story_twice_on_one_form_is_refused():
    got = essays.form_check({"answers": {"Why us?": WHY_US, "A hard problem?": WHY_US}},
                            prompt="", evidence=EVIDENCE, meta=meta())
    assert any("retells a story" in p for p in got)


def test_a_reply_without_the_schema_is_refused():
    assert essays.form_check("not json", prompt="", evidence=EVIDENCE, meta=meta()) == \
        ["the answer is not the essay schema: no answers object"]


# ---------- the Apply email follows on its own ----------

def test_a_passing_form_asks_for_prepare_once(db):
    essays.draft_all(Cfg(), QUESTIONS, company="Nine", role="GM", jd_text="the posting",
                     evidence=EVIDENCE, ref="nine:gm")
    answer(db, {"Why us?": WHY_US, "A hard problem?": HARD})
    lines = drafts.essays_ready(Cfg(), db.rows["hunter_drafts"], {}, apply=True)
    assert [(a["kind"], a["job_id"]) for a in db.rows["hunter_actions"]] == \
        [("prepare", "nine:gm")]
    assert "1 form(s) answered by the routine and passed" in lines[0]
    db.rows["hunter_actions"][0]["status"] = "done"     # the drain applied it
    drafts.essays_ready(Cfg(), db.rows["hunter_drafts"], {}, apply=True)
    assert len(db.rows["hunter_actions"]) == 1, "asked once, not every hour"


def test_a_failing_form_is_asked_of_the_routine_again_not_prepared(db):
    essays.draft_all(Cfg(), QUESTIONS, company="Nine", role="GM", jd_text="the posting",
                     evidence=EVIDENCE, ref="nine:gm")
    answer(db, {"Why us?": WHY_US.replace("$61m", "$90m"), "A hard problem?": HARD})
    drafts.essays_ready(Cfg(), list(db.rows["hunter_drafts"]), {}, apply=True)
    assert db.rows["hunter_actions"] == []
    assert [r["status"] for r in db.rows["hunter_drafts"]] == ["failed", "queued"]


def test_the_hourly_drafts_step_asks_for_prepare(db):
    """settle itself, not only the helper: removing the call must fail."""
    ask()
    answer(db, {"Why us?": WHY_US, "A hard problem?": HARD})
    lines = drafts.settle(Cfg(), apply=True)
    assert any("Prepare asked for each" in l for l in lines)
    assert [(a["kind"], a["requested_by"]) for a in db.rows["hunter_actions"]] == \
        [("prepare", "hunter")]


def test_one_form_that_raises_does_not_stop_the_step(db, monkeypatch):
    ask()
    answer(db, {"Why us?": WHY_US, "A hard problem?": HARD})
    monkeypatch.setattr(drafts, "_essay_ready",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    lines = drafts.settle(Cfg(), apply=True)
    assert lines[0].startswith("drafts: "), "the cases and observations still settled"
    assert any("not handled (RuntimeError)" in l for l in lines)


def test_a_prepare_hunter_asked_for_is_held_to_his_row(db, monkeypatch):
    seen = []
    db.rows["hunter_seen_roles"] = [{"job_id": "nine:gm", "company": "Nine", "title": "GM"}]
    db.insert(None, "hunter_actions", [{"kind": "prepare", "job_id": "nine:gm",
                                        "requested_by": actions.HUNTER, "requested_at": "1"}])
    db.insert(None, "hunter_actions", [{"kind": "prepare", "job_id": "nine:gm",
                                        "requested_by": "krish", "requested_at": "2"}])
    monkeypatch.setattr(actions, "db_get", db.get)
    monkeypatch.setattr(actions, "db_patch", db.patch)
    actions.apply_queued(Cfg(), None, [], apply=True,
                         prepare=lambda j, **kw: seen.append(kw) or (True, "sent"))
    assert seen == [{"require_wanted": True}, {}]


def test_essays_never_fall_back_to_the_api():
    assert "essay" in drafts.NO_API and "essay" in drafts.KINDS


def test_his_card_says_the_email_follows_on_its_own():
    ok, note = actions.prepare_note(0, {run.ESSAY_QUEUED: 1}, [])
    assert not ok, "nothing was sent yet"
    assert "writing routine" in note and "follows on its own" in note
