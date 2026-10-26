"""The screen: the judge's record, encoded, so a plainly wrong seat costs no call.

His words, 2026-10-26: "encode as much as possible into followable, replicable
steps rather than as we go, so the cost should keep coming down". These hold
the parts that make it safe: his own record above the judge's, a line drawn
so it would have cut nothing he wanted, shadow before gate, the exceptions the
company bar keeps, and a measurement that has to be re-run when the screen
changes.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from hunter import screen
from hunter.judge_stage import Candidate

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "screen_eval.json"


class Cfg:
    def __init__(self, **kw):
        self.raw = dict(kw)

    def optional(self, key, default=""):
        return self.raw.get(key) or default


class Role:
    def __init__(self, title, company="Acme", posting="", source="greenhouse:acme"):
        self.title, self.company, self.jd_text, self.source = title, company, posting, source
        self.job_id = f"{company}:{title}".lower()


def cand(title, company="Acme", posting=""):
    return Candidate(role=Role(title, company, posting), result=None, row={}, rank=0.0)


ENGINEER = "build backend services in python and kubernetes, on call rotation, code review"
GTM = "own revenue, partnerships and go to market, report to the founder, build the team"


def synthetic():
    rows = []
    for i in range(40):
        rows.append({"title": f"Senior Software Engineer {i % 4}", "posting": ENGINEER,
                     "company": f"Eng{i}", "label": 0})
    for i in range(20):
        rows.append({"title": f"Head of GTM {i % 3}", "posting": GTM,
                     "company": f"Gtm{i}", "label": 1})
    return rows


@pytest.fixture(scope="module")
def model():
    return screen.build(synthetic())


# ---------- the record ----------

def test_the_measured_screen_cut_nothing_he_wanted_and_nothing_the_judge_presented():
    """Measured 2026-10-26 on the live record, fitting on what was judged
    before each window and screening what came after, with every role he
    wanted as a positive whether the judge read it or not (86 it never did):
    it cut 0.8 to 2.5 percent of the judge's calls, more as the record grew,
    and on no window a role the judge presented or one he wanted. A change
    to screen.py must re-run screen-eval (python -m hunter.run screen-eval)
    and do no worse."""
    rec = json.loads(FIXTURE.read_text())
    assert rec["version"] == screen.VERSION, "screen.py changed: re-run screen-eval"
    assert len(rec["windows"]) >= 3
    for w in rec["windows"]:
        assert w["presents_lost"] == 0 and w["wants_lost"] == 0, w
        assert w["screened"] == w["judge_rejects_cut"]
    assert rec["windows"][-1]["share"] > 0, "a screen that cuts nothing saves nothing"


def test_the_fixture_carries_counts_only():
    """The repo is public: no weight, title or company goes in it."""
    text = FIXTURE.read_text()
    rec = json.loads(text)
    assert "w" not in rec and "bias" not in rec
    for w in rec["windows"]:
        assert all(isinstance(v, (int, float, str)) for v in w.values())
        assert set(w) == {"from", "trained_on", "screened_of", "screened", "share",
                          "judge_rejects_cut", "presents_lost", "wants_lost"}


# ---------- labels ----------

def test_his_want_wins_over_a_judge_rejection():
    assert screen.label(judge_verdict="reject", his="want") == 1


def test_his_decline_does_not_turn_a_presented_seat_into_a_rejection():
    """His declines are mostly about the business; the screen reads the seat."""
    assert screen.label(judge_verdict="present", his="no") == 1


def test_labels_from_the_judge():
    assert screen.label(judge_verdict="reject", his=None) == 0
    assert screen.label(judge_verdict="present", his=None) == 1
    assert screen.label(judge_verdict=None, his=None) is None


def test_the_record_reads_his_applied_and_his_scores_above_the_judge(monkeypatch):
    from hunter import config
    seen = []

    def fake(cfg, table, params):
        seen.append(table)
        if table == "hunter_seen_roles":
            return [{"job_id": "a", "title": "GM", "jd_text": "", "company": "A",
                     "judge_verdict": "reject", "judge_at": "2026-10-20"},
                    {"job_id": "b", "title": "GM", "jd_text": "", "company": "B",
                     "judge_verdict": "reject", "judge_at": "2026-10-20"},
                    {"job_id": "c", "title": "GM", "jd_text": "", "company": "C",
                     "judge_verdict": "reject", "judge_at": "2026-10-20"}]
        if table == "hunter_verdict_events":
            return [{"job_id": "a"}]
        if table == "hunter_krish_ratings":
            return [{"job_id": "b", "kind": "scored", "score": 8},
                    {"job_id": "c", "kind": "scored", "score": 4}]
        raise KeyError(table)
    monkeypatch.setattr(config, "db_get", fake)
    rows = {r["company"]: r for r in screen.record(Cfg())}
    assert rows["A"]["label"] == 1 and rows["A"]["want"], "he applied: never cut"
    assert rows["B"]["label"] == 1, "he scored it 8: never cut"
    assert rows["C"]["label"] == 0, "a 4 is not a want; the judge's rejection stands"


def test_a_role_he_wanted_that_the_judge_never_read_is_a_positive(monkeypatch):
    """The line must sit under every role he wanted, not only those the judge
    read: his Yes from before the judge, a posting he sent, one he applied to."""
    from hunter import config

    def fake(cfg, table, params):
        if table == "hunter_seen_roles" and "judge_at" in params:
            return [{"job_id": "j", "title": "GM", "company": "J", "judge_verdict": "reject",
                     "judge_at": "2026-10-20"}]
        if table == "hunter_seen_roles" and "application_state" in params:
            assert "Interviewing" in params["application_state"]
            return [{"job_id": "a", "title": "CRO", "company": "A",
                     "application_state": "Interviewing", "scanned_at": "2026-09-01"}]
        if table == "hunter_seen_roles":
            assert params["job_id"] == 'in.("y")'
            return [{"job_id": "y", "title": "Head of GTM", "company": "Y",
                     "presented_at": "2026-09-10"}]
        if table == "hunter_verdict_events":
            return [{"job_id": "y"}]
        return []
    monkeypatch.setattr(config, "db_get", fake)
    rows = {r["company"]: r for r in screen.record(Cfg())}
    assert rows["Y"]["label"] == 1 and rows["Y"]["want"] and rows["Y"]["at"] == "2026-09-10"
    assert rows["A"]["label"] == 1, "after applying, whatever happened next, he wanted it"
    assert rows["J"]["label"] == 0


# ---------- the model and the line ----------

def test_a_seat_the_judge_always_rejected_falls_under_the_line(model):
    under, _ = screen.screened(model, title="Staff Software Engineer", posting=ENGINEER)
    assert under


def test_a_seat_of_his_kind_is_never_under_the_line(model):
    under, _ = screen.screened(model, title="VP Revenue and Partnerships", posting=GTM)
    assert not under


def test_the_line_sits_below_every_positive_scored_out_of_fold():
    rows = synthetic()
    docs = [screen.features(title=r["title"], posting=r["posting"], company=r["company"])
            for r in rows]
    ys = [r["label"] for r in rows]
    oof = screen.out_of_fold(docs, ys)
    cut = screen.line(oof, ys)
    assert all(s > cut for s, y in zip(oof, ys) if y == 1)


def test_too_little_record_means_no_screen():
    assert screen.build(synthetic()[:12]) == {}
    assert screen.screened({}, title="Software Engineer", posting=ENGINEER) == (False, 1.0)


# ---------- in the run ----------

def test_shadow_cuts_nothing_and_names_what_it_would_have(model):
    cs = [cand("Staff Software Engineer", posting=ENGINEER), cand("Head of GTM", posting=GTM)]
    kept, under, lines = screen.apply(Cfg(), cs, top_keys=set(), mark=True, model=model)
    assert kept == cs, "shadow sends every role to the judge"
    assert [c.role.title for c in under] == ["Staff Software Engineer"]
    assert not cs[0].row, "shadow writes nothing on the row"
    assert "all still judged" in lines[0]


def test_gate_cuts_with_no_judge_call_and_says_why(model):
    cs = [cand("Staff Software Engineer", posting=ENGINEER), cand("Head of GTM", posting=GTM)]
    kept, under, _ = screen.apply(Cfg(hunter_screen_mode="gate",
                                      hunter_screen_audit_per_run="0"), cs, top_keys=set(),
                                  mark=True, model=model)
    assert [c.role.title for c in kept] == ["Head of GTM"]
    assert cs[0].row["status"] == "blocked"
    assert cs[0].row["rejection_reason"].startswith("SCREEN:")


def test_gate_still_sends_a_sample_of_what_it_cuts_to_the_judge(model):
    """CLAUDE.md 3b: the audit sample measures what the gate hides."""
    cs = [cand(f"Staff Software Engineer {i}", company=f"E{i}", posting=ENGINEER)
          for i in range(3)]
    kept, report, lines = screen.apply(Cfg(hunter_screen_mode="gate",
                                           hunter_screen_audit_per_run="1"),
                                       cs, top_keys=set(), mark=True, model=model)
    assert len(kept) == 1 and kept[0].screen_audit and not kept[0].row
    assert report == kept, "the audited role is what the run reports on"
    assert sum(1 for c in cs if c.row.get("status") == "blocked") == 2
    assert "1 of them sent to the judge as an audit" in lines[1]


def test_gate_does_not_cut_while_the_judge_itself_is_not_deciding(model):
    cs = [cand("Staff Software Engineer", posting=ENGINEER)]
    kept, _, _ = screen.apply(Cfg(hunter_screen_mode="gate"), cs, top_keys=set(),
                              mark=False, model=model)
    assert kept == cs and not cs[0].row


def test_his_list_an_ai_seat_a_recruiter_and_a_posting_he_sent_are_never_screened(model):
    from hunter import universe
    sent = cand("Senior Software Engineer", company="Other", posting=ENGINEER)
    sent.role.source = "sent by Krish"
    cs = [cand("Senior Software Engineer", company="Listed Co", posting=ENGINEER),
          cand("Software Engineer, AI Transformation", posting=ENGINEER),
          cand("Senior Software Engineer", company="Acme Executive Search", posting=ENGINEER),
          sent]
    _, under, _ = screen.apply(Cfg(), cs, top_keys={universe.key_of("Listed Co")},
                               mark=True, model=model)
    assert under == []


def test_off_means_off(model):
    cs = [cand("Staff Software Engineer", posting=ENGINEER)]
    assert screen.apply(Cfg(hunter_screen_mode="off"), cs, top_keys=set(), mark=True,
                        model=model) == (cs, [], [])


def test_the_shadow_report_names_a_role_the_gate_would_have_hidden(model):
    from types import SimpleNamespace
    a = cand("Staff Software Engineer", company="A", posting=ENGINEER)
    b = cand("Staff Software Engineer", company="B", posting=ENGINEER)
    a.judgement = SimpleNamespace(verdict="reject")
    b.judgement = SimpleNamespace(verdict="present")
    lines = screen.shadow_report([a, b])
    assert "the judge rejected 1, presented or held 1" in lines[0]
    assert lines[1] == "  screen would have hidden: B / Staff Software Engineer"


def test_a_failed_refit_falls_back_to_the_stored_screen(monkeypatch):
    from hunter import config
    stored = {"bias": 0.0, "w": {"t:x": 1.0}, "line": 0.1, "fitted_at": "then"}

    def fake(cfg, table, params):
        if table == "system_config":
            return [{"value": json.dumps(stored)}]
        raise RuntimeError("database down")
    monkeypatch.setattr(config, "db_get", fake)
    model, how = screen.refresh(Cfg())
    assert model == stored and how.startswith("stored")


def test_the_refit_is_stored_and_read_back(monkeypatch):
    from hunter import config
    rows = synthetic()
    store = {}
    monkeypatch.setattr(screen, "record", lambda cfg: rows)
    monkeypatch.setattr(config, "db_get", lambda cfg, table, params: (
        [{"key": screen.CONFIG_KEY, "value": store["v"]}] if store else []))
    monkeypatch.setattr(config, "db_insert",
                        lambda cfg, table, rs: store.update(v=rs[0]["value"]))
    model, how = screen.refresh(Cfg())
    assert how == "refitted" and json.loads(store["v"])["line"] == model["line"]


def test_staging_screens_after_the_company_bar_and_before_the_judge():
    import inspect
    from hunter import run as R
    src = inspect.getsource(R.stage_postings)
    bar, scr, judged = (src.index("companybar.apply("), src.index("screen.apply("),
                        src.index("judge_stage.run("))
    assert bar < scr < judged
    assert 'top_keys=top_keys, mark=judge_mode == "gate")' in src[scr:judged]
    assert "screen.shadow_report(under)" in src[judged:]
