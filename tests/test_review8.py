"""The second round on the seventh review: what its checks found, each with
a test that goes red when the guard is taken out.

- Prepare refuses a Yes his score took back whatever built the package, and
  the bulk run asks router.wants of every row;
- a "SKIPPED:" line is counted as skipped by process and rebuild-packages;
- jd_mirror names are checked as whole words, his note's copy is refused,
  and a flag says a name was refused only when the gate refused one;
- Prepare's note says what happened, and no log line carries the token,
  his address, a field value, a letter's words or an exception's message;
- posting_key keeps only the parameters that name a posting;
- a build that raised is decided from his row as it reads now;
- an essay that did not draft waits a day, and a refusal waits on the bank.

Every company, contact and note here is invented. The repository is
public: no real company sits beside a score of his, and no reaction of his
is quoted, anywhere in this file (CLAUDE.md 3f).
"""
import datetime
import pathlib
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as run_mod  # noqa: E402
import hunter.sheet as sheet_mod  # noqa: E402
from hunter import actions, router  # noqa: E402
from hunter.package import his_view as hv  # noqa: E402
from hunter.package import tailor as tailor_mod  # noqa: E402
from hunter.package import voicegate  # noqa: E402
from test_his_view import (EVIDENCE, GH, REACTION, L, Plan, _commands,  # noqa: E402
                           approvals_world, grid_of, line, rec, rebuild_world, tailor_world)
from test_review7 import (FIELD_VALUE, OSTRAVIA, RECIPIENT, TOKEN, RuleCanon,  # noqa: E402
                          model_reply, tailor_with)
from test_score_builds import Cfg, FakeCanon, _stub_build  # noqa: E402
from test_sheet_rows import FakeSheet  # noqa: E402

R = run_mod
JOB = "ostravia:chief-of-staff"
BUILT = sorted(sheet_mod.BUILT_STATUSES)[0]


# ---------- A1: Prepare refuses a Yes his score took back, whatever built it ----------

def _prepare_world(monkeypatch, lines, *, trigger, canon):
    """cmd_approvals --job-id as far as his mailbox, which records that it
    was reached and stops the run there."""
    s = FakeSheet(grid_of(*lines))
    rows = [{"job_id": JOB, "company": "Ostravia", "title": "Chief of Staff",
             "url": OSTRAVIA, "package_status": "built", "package_trigger": trigger,
             "status": "staging"}]
    monkeypatch.setattr(R, "build_context", lambda: (Cfg(), canon))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(R, "Sheet", lambda token: s)
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [
        r for r in rows if not params.get("job_id") or params["job_id"] == f"eq.{r['job_id']}"])
    reached = []
    import hunter.notify as notify_mod

    def mailbox(cfg):
        reached.append(True)
        raise RuntimeError("stop here")
    monkeypatch.setattr(notify_mod, "mailbox", mailbox)
    return reached


def _yes(k, m=""):
    return line("Yes", "Ostravia", "Chief of Staff", OSTRAVIA, k=k, m=m)


@pytest.mark.parametrize("trigger", ["Krish Score 8", "withdrawn: K now 3"])
def test_a_package_his_score_built_is_refused_once_a_yes_row_is_taken_back(monkeypatch,
                                                                          capsys, trigger):
    """He scored it 8 and hunter built on that; he later typed Yes in A and
    lowered K to 3. The score-built branch asks build_trigger, which says Yes
    for any Yes, so it let the Prepare through."""
    reached = _prepare_world(monkeypatch, [_yes("3")], trigger=trigger, canon=RuleCanon())
    tally = {}
    assert R.cmd_approvals(apply=True, job_id=JOB, tally=tally) == 1
    assert reached == [] and tally == {R.TAKEN_BACK: 1}
    assert "Ostravia" not in capsys.readouterr().out


def test_before_canon_carries_the_rule_prepare_still_sends_a_yes_he_scored_3(monkeypatch):
    reached = _prepare_world(monkeypatch, [_yes("3")], trigger="column A Yes",
                             canon=FakeCanon())
    R.cmd_approvals(apply=True, job_id=JOB)
    assert reached == [True]


def test_another_row_of_the_posting_that_still_asks_holds_it(monkeypatch):
    """Two rows link this posting: the Yes he scored 3, and a row he scored
    8. The posting is still one he wants an application for."""
    lines = [_yes("3"), line("New", "Ostravia", "Chief of Staff", OSTRAVIA, k="8")]
    reached = _prepare_world(monkeypatch, lines, trigger="column A Yes", canon=RuleCanon())
    R.cmd_approvals(apply=True, job_id=JOB)
    assert reached == [True]


# ---------- A2: the bulk run asks router.wants of every row ----------

def _bulk(monkeypatch, db, roles):
    real = db.get

    def get(cfg, table, params):
        if table == "hunter_seen_roles" and params.get("package_status") == "eq.built":
            return [dict(r) for r in roles
                    if not params.get("job_id") or params["job_id"] == f"eq.{r['job_id']}"]
        return real(cfg, table, params)
    monkeypatch.setattr(R, "db_get", get)


def test_bulk_approvals_skips_a_row_that_no_longer_asks(monkeypatch, capsys):
    roles = [rec("ardent", package_trigger="column A Yes"),
             rec("brisk", package_trigger="column A Yes")]
    db, mailed, reached = approvals_world(
        monkeypatch, [L("ardent", verdict="Yes", k="3"), L("brisk", verdict="Yes", k="")],
        roles, Plan())
    _bulk(monkeypatch, db, roles)
    tally = {}
    assert R.cmd_approvals(apply=True, prefill=False, tally=tally) == 0
    assert reached == ["brisk:vp"] and mailed["approval"] == [1]
    assert tally == {"no longer asked for": 1, "sent": 1}
    out = capsys.readouterr().out
    assert "skip 1 package(s) whose row no longer asks" in out and "Ardent" not in out


# ---------- A3: a "SKIPPED:" line is counted as skipped ----------

def _process(monkeypatch, sheet, todo, *, db=None):
    """process_step with every phase but the builds stubbed or failing
    offline. db, when given, is the record the build loop reads and patches."""
    import hunter.ats.discover as disc
    monkeypatch.setattr(R, "archive_columns_step", lambda sheet, apply: "ok")
    monkeypatch.setattr(R, "amendment_step", lambda *a: 0)
    monkeypatch.setattr(R, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(R, "reconcile", lambda *a, **k: R.ReconcileLedger())
    monkeypatch.setattr(R, "sync_applied_state", lambda *a: {"written": 0, "from_sheet": 0,
                                                              "from_ledger": 0})

    def off(*a, **k):
        raise ValueError("offline")
    monkeypatch.setattr(R, "learning_step", off)
    monkeypatch.setattr(R, "ratings_step", lambda *a, **k: {})
    monkeypatch.setattr(R, "leave_step", lambda *a, **k: {})
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(R, "select_for_build", lambda *a, **k: [dict(r) for r in todo])
    for name in ("yes_db_rows", "archive_decided", "snapshot_step", "funnel_batches"):
        monkeypatch.setattr(R, name, off)
    monkeypatch.setattr(R.invariants, "enforce", off)
    monkeypatch.setattr(R.layout, "apply_layout", off)
    monkeypatch.setattr(sheet, "sort_by_score", off)
    if db is not None:
        monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [dict(db)])
        monkeypatch.setattr(R, "db_patch", lambda cfg, table, match, values: db.update(values))
    summary = []
    counts = R.process_step(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), sheet, summary)
    return counts, summary


ROW = {"job_id": JOB, "company": "Ostravia", "title": "Chief of Staff", "url": OSTRAVIA,
       "status": "staging", "package_status": "none", "build_trigger": "column A Yes",
       "sheet_link": OSTRAVIA, "sheet_row": 3}


def test_process_counts_a_skipped_line_as_skipped(monkeypatch):
    def build_one(cfg, canon, sheet, row, summary, **kw):
        summary.append("SKIPPED: a Yes his score has since taken back; nothing fetched or built")
        return False
    monkeypatch.setattr(R, "build_one", build_one)
    counts, _ = _process(monkeypatch, FakeSheet(grid_of(_yes("3"))), [ROW], db=dict(ROW))
    assert counts.get("skipped") == 1 and counts["blocked"] == 0


def test_rebuild_packages_counts_a_skipped_line_as_skipped(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")])
    _commands(monkeypatch, s)

    def build_one(cfg, canon, sheet, row, detail, **kw):
        detail.append("SKIPPED: a rebuild for a row that no longer asks for an application; "
                      "nothing fetched or built")
        return False
    monkeypatch.setattr(R, "build_one", build_one)
    assert R.cmd_rebuild_packages(apply=True) == 0
    out = capsys.readouterr().out
    assert "1 row changed before its turn" in out and "refused at a gate" not in out


# ---------- B1: jd_mirror names as whole words; his note is never copied ----------

KING_JD = ("Quillfield needs commercial strategy across regions, as the board asked, "
           "and a market view of working partners. ") * 10
KING_NOTE = "Mark King runs sales there and asked me to apply"


def test_a_contact_hidden_inside_other_words_is_still_refused(monkeypatch):
    mirror = "commercial strategy across regions, as Mark King asked"
    hay = voicegate.build_evidence(KING_JD, EVIDENCE)
    assert voicegate.check(mirror, evidence=hay).ok, \
        "the gate's substring test passes it: 'mark' in 'market', 'king' in 'working'"
    got, why = tailor_mod.gate_mirror(mirror, KING_JD, evidence=EVIDENCE,
                                      company="Quillfield",
                                      his_view=hv.HisView(score=9, reaction=KING_NOTE))
    assert (got, why) == ("", tailor_mod.MIRROR_DROPPED)
    # end to end: the letter keeps the block's clause
    tailor_world(monkeypatch, [model_reply(jd_mirror=mirror)])
    tr = tailor_with(hv.HisView(score=9, reaction=KING_NOTE), jd=KING_JD)
    assert tr.jd_mirror == "" and tailor_mod.MIRROR_DROPPED in tr.flags
    assert not any("Mark" in f or "King" in f for f in tr.flags)


def test_a_name_the_jd_carries_in_another_case_is_kept():
    jd = KING_JD + " You will work with the APAC and EMEA teams."
    got, why = tailor_mod.gate_mirror("commercial strategy across Apac and Emea", jd,
                                      evidence=EVIDENCE, company="Quillfield")
    assert why == "" and got == "commercial strategy across Apac and Emea"


def test_a_mirror_that_copies_his_note_is_dropped():
    note = "i would rebuild the partner channel from the ground up within a quarter"
    mirror = "rebuild the partner channel from the ground up within a quarter"
    view = hv.HisView(score=8, reaction=note)
    got, why = tailor_mod.gate_mirror(mirror, KING_JD, evidence=EVIDENCE,
                                      company="Quillfield", his_view=view)
    assert (got, why) == ("", tailor_mod.MIRROR_DROPPED)
    assert tailor_mod.gate_mirror(mirror, KING_JD, evidence=EVIDENCE,
                                  company="Quillfield") == (mirror, ""), \
        "with no note of his, the same clause stands"


def test_a_name_as_the_mirrors_first_word_is_refused():
    """The mirror completes "[[COMPANY]] is ...", so its first word never
    opens a sentence. Skipping it let a contact from his note through."""
    note = "Dana runs sales there and asked me to apply"
    view = hv.HisView(score=9, reaction=note)
    got, why = tailor_mod.gate_mirror("Dana's push for commercial strategy across regions",
                                      KING_JD, evidence=EVIDENCE, company="Quillfield",
                                      his_view=view)
    assert (got, why) == ("", tailor_mod.MIRROR_DROPPED)


def test_a_name_with_an_accented_first_letter_is_refused():
    """ASCII-only words cut "Émilie" to "milie", a lowercase piece no check read."""
    note = "Émilie runs sales there and asked me to apply"
    view = hv.HisView(score=9, reaction=note)
    got, why = tailor_mod.gate_mirror("commercial strategy across regions, as Émilie asked",
                                      KING_JD, evidence=EVIDENCE, company="Quillfield",
                                      his_view=view)
    assert (got, why) == ("", tailor_mod.MIRROR_DROPPED)
    assert tailor_mod.unsupported_names("as Émilie asked", "as asked", "Quillfield") == ["Émilie"]


# ---------- B2: a name or a figure only in the gate's own formats ----------

def test_an_empty_hiring_lead_is_never_called_a_refused_name(monkeypatch):
    msg = "hiring_lead is empty; return 'Hiring Team' when no name is found"
    view = hv.HisView(score=9, reaction="Invented: Corvantis offered me more, and asked")
    assert hv.redact([msg], view) == [msg]
    bad = model_reply(hiring_lead="")
    tailor_world(monkeypatch, [bad, bad])
    flags = "; ".join(tailor_with(view).flags)
    assert "hiring_lead is empty" in flags and hv.NAME_LEFT_OUT not in flags


def test_the_gates_own_formats_are_still_redacted():
    view = hv.HisView(score=9, reaction="Invented: Zentrix offered 310k")
    got = hv.redact(["number '$310k' is not in the recorded evidence",
                     "name 'Zentrix' is not in the recorded evidence",
                     "jd_mirror carries a number not in the JD verbatim: '310k'"], view)
    assert got == [hv.FIGURE_LEFT_OUT, hv.NAME_LEFT_OUT]


# ---------- B3: gate_lead matches whole words ----------

def test_a_lead_whose_words_sit_only_inside_other_words_is_refused():
    assert tailor_mod.gate_lead("Mark King", KING_JD) == ("Hiring Team",
                                                          tailor_mod.LEAD_DROPPED)
    assert tailor_mod.gate_lead("Mark King", KING_JD + " Report to Mark King.") == (
        "Mark King", "")


# ---------- C1: Prepare's note says what happened ----------

def _press(monkeypatch, lines, roles, plan, *, canon=None, live=None, fill=None):
    db, mailed, reached = approvals_world(monkeypatch, lines, roles, plan)
    _bulk(monkeypatch, db, roles)
    if canon is not None:
        monkeypatch.setattr(R, "build_context", lambda: (Cfg(), canon))
    if live is not None:
        monkeypatch.setattr(R, "live_postings", lambda cfg: live)
    if fill is not None:
        monkeypatch.setattr(R, "build_fill_plan", fill)
    return db, mailed, reached


class Blocked(Plan):
    def __init__(self, *reasons):
        super().__init__(blocking=tuple(f"Question {i}" for i in range(len(reasons))))
        for f, why in zip(self.blocking, reasons):
            f.reason = why


def _essay():
    from hunter.apply.resolve import NEEDS_ESSAY
    return NEEDS_ESSAY + ": drafted per posting"


def _boom(*a, **kw):
    raise ValueError(f"the plan quoted {FIELD_VALUE}")


SCORED = [L("ardent", k="9")]
OUTCOMES = {
    "sent": dict(plan=Plan(), want=(True, "sent: the Apply email is on its way")),
    "taken": dict(plan=Plan(), live=True, want=(True, "already has an application waiting")),
    "refused": dict(plan=Blocked("no answer in the bank"),
                    want=(False, "refused: a required question has no answer")),
    "essay": dict(plan=Blocked(_essay()), want=(False, "an essay did not draft")),
    "by hand": dict(plan=Plan(readable=False), want=(True, "sent as by hand")),
    "off sheet": dict(plan=Plan(), lines=[L("brisk", k="9")],
                      want=(False, "not on Pipeline any more")),
    "taken back": dict(plan=Plan(), lines=[L("ardent", verdict="Yes", k="3")],
                       trigger="column A Yes", canon=RuleCanon(),
                       want=(False, "your score took this Yes back")),
    "failed": dict(plan=Plan(), fill=_boom, want=(False, "failed (ValueError)")),
}


@pytest.mark.parametrize("name", list(OUTCOMES))
def test_each_prepare_outcome_reaches_the_note(monkeypatch, capsys, name):
    o = OUTCOMES[name]
    role = rec("ardent", package_trigger=o.get("trigger", "Krish Score 8"))
    live = {R.posting_key(role): (TOKEN, "awaiting")} if o.get("live") else None
    _press(monkeypatch, o.get("lines", SCORED), [role], o["plan"], canon=o.get("canon"),
           live=live, fill=o.get("fill"))
    ok, note = actions._prepare(role["job_id"])
    assert ok is o["want"][0] and note.startswith(o["want"][1]), note
    printed = capsys.readouterr()
    for leak in (TOKEN, "him@example.com", FIELD_VALUE, "Ardent"):
        assert leak not in note and leak not in printed.out and leak not in printed.err


def test_an_error_out_of_approvals_is_a_class_on_the_note(monkeypatch):
    import hunter.run
    monkeypatch.setattr(hunter.run, "cmd_approvals", _boom)
    assert actions._prepare(JOB) == (False, "failed (ValueError); nothing was sent")


def test_a_run_that_counted_nothing_claims_nothing():
    ok, note = actions.prepare_note(0, {}, [])
    assert ok is False and "sent" not in note.replace("nothing is known to be sent", "")


# ---------- C2: the token, his address and a message stay out of the log ----------

def test_the_taken_skip_names_the_state_never_the_token(monkeypatch, capsys):
    role = rec("ardent")
    _press(monkeypatch, SCORED, [role], Plan(),
           live={R.posting_key(role): (TOKEN, "awaiting")})
    tally = {}
    R.cmd_approvals(apply=True, prefill=False, chosen=[role], tally=tally)
    out = capsys.readouterr().out
    assert tally == {"taken": 1} and "an approval is already awaiting" in out
    assert TOKEN not in out


def test_the_by_hand_branch_never_prints_his_address(monkeypatch, capsys):
    import hunter.notify as notify_mod
    _, mailed, _ = _press(monkeypatch, SCORED, [rec("ardent")], Plan(readable=False))
    monkeypatch.setattr(notify_mod, "mailbox", lambda cfg: RECIPIENT)
    R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")])
    out = capsys.readouterr().out
    assert mailed["by_hand"] == ["Ardent"] and "documents emailed to him" in out
    assert RECIPIENT not in out


def test_a_payload_error_is_printed_by_class(monkeypatch, capsys):
    from hunter.apply import payload
    _press(monkeypatch, SCORED, [rec("ardent")], Plan())
    monkeypatch.setattr(payload, "build", _boom)
    R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")])
    out = capsys.readouterr().out
    assert "no fill payload for this one (ValueError)" in out and FIELD_VALUE not in out


# ---------- C3: the send, the token's record and the drain, by class ----------

def test_a_send_that_fails_is_a_class_and_the_next_posting_still_goes(monkeypatch, capsys):
    import hunter.notify as notify_mod
    roles = [rec("ardent"), rec("brisk")]
    _, mailed, _ = _press(monkeypatch, [L("ardent", k="9"), L("brisk", k="8")], roles, Plan())
    calls = []

    def send(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise notify_mod.NotifyError(f"403 {RECIPIENT} token {TOKEN}")
        return {"id": "m2", "sent": True}
    monkeypatch.setattr(notify_mod, "send_email", send)
    tally = {}
    assert R.cmd_approvals(apply=True, prefill=False, chosen=roles, tally=tally) == 1
    assert tally == {"failed": 1, "sent": 1}
    printed = capsys.readouterr()
    assert "the Apply email was not sent (NotifyError)" in printed.out
    for leak in (RECIPIENT, TOKEN):
        assert leak not in printed.out and leak not in printed.err


def test_a_token_record_that_fails_is_a_class(monkeypatch, capsys):
    from hunter.apply import approval
    _press(monkeypatch, SCORED, [rec("ardent")], Plan())
    monkeypatch.setattr(approval, "new_token", lambda job_id: TOKEN)

    def record_sent(cfg, **kw):
        raise requests.HTTPError(f"409 Conflict: Failing row contains ({TOKEN}, ardent:vp)")
    monkeypatch.setattr(approval, "record_sent", record_sent)
    tally = {}
    assert R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally) == 1
    printed = capsys.readouterr()
    assert "its approval was not recorded (HTTPError)" in printed.out
    assert TOKEN not in printed.out and TOKEN not in printed.err


def test_the_drain_handles_a_failing_reply_by_class_and_reads_the_next(monkeypatch, capsys):
    from hunter.apply import approval, inbox
    monkeypatch.setattr(R, "build_context", lambda: (Cfg(), RuleCanon()))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(R, "Sheet", lambda token: None)
    monkeypatch.setattr(inbox, "fetch_replies", lambda cfg: [
        {"token": TOKEN, "message_id": "m1"}, {"token": "tok-two", "message_id": "m2"}])

    def get_row(cfg, token):
        if token == TOKEN:
            raise requests.HTTPError(f"503 for url: https://db.example/rest/v1/"
                                     f"hunter_application_approvals?token=eq.{TOKEN}")
        return {"job_id": "zentrox:b"}
    monkeypatch.setattr(approval, "get_row", get_row)
    monkeypatch.setattr(inbox, "classify", lambda r, row: ("skip", "already processed"))
    monkeypatch.setattr(R, "report_stalled_approvals", lambda cfg, apply=False: [])
    assert R.cmd_approvals_drain(apply=False) == 1
    printed = capsys.readouterr()
    assert "this reply stopped at an error (HTTPError)" in printed.out
    assert "zentrox:b" in printed.out, "the next reply is still read"
    assert TOKEN not in printed.out and TOKEN not in printed.err


# ---------- C4: process prints counts and classes, never his words ----------

def test_amendments_print_field_counts_and_no_text(monkeypatch):
    from hunter import amend
    s = FakeSheet(grid_of(L("ardent")))
    monkeypatch.setattr(R, "_pair_sheet_to_db", lambda cfg, rows: {})
    monkeypatch.setattr(amend, "load_state", lambda cfg: {"ardent:vp": {}})
    monkeypatch.setattr(amend, "detect", lambda rows, paired, state: [
        {"company": "Zentrix", "field": "cover letter",
         "before_text": "Invented letter words alpha", "after_text": "Invented words beta"},
        {"company": "Zentrix", "field": "cover letter",
         "before_text": "Invented gamma", "after_text": "Invented delta"}])
    monkeypatch.setattr(amend, "detect_docs", lambda *a: [])
    monkeypatch.setattr(amend, "record", lambda cfg, found: len(found))
    monkeypatch.setattr(amend, "proposals", lambda found: [])
    monkeypatch.setattr(R, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": "x"})())
    monkeypatch.setattr(R, "DocBuild", lambda token: object())
    summary = []
    assert R.amendment_step(Cfg(), FakeCanon(), s, summary) == 2
    assert "  cover letter: 2 edit(s)" in summary
    joined = "\n".join(summary)
    assert "Invented" not in joined and "Zentrix" not in joined


def test_build_failed_prints_the_class_only(monkeypatch):
    def build_one(*a, **kw):
        raise LookupError("paragraph anchor 'Invented hook words he' not found")
    monkeypatch.setattr(R, "build_one", build_one)
    counts, summary = _process(monkeypatch, FakeSheet(grid_of(_yes(""))), [ROW],
                               db=dict(ROW))
    assert f"BUILD FAILED {JOB}: LookupError" in summary
    assert not any("Invented" in x for x in summary)
    assert counts["blocked"] == 1, "a build that raised before it built is blocked, as before"


# ---------- D1, D2: posting_key keeps only what names a posting ----------

LI = "https://www.linkedin.com/jobs/view/vp-partnerships-at-quillfield-4470000001"


def test_two_linkedin_links_to_one_job_are_one_posting():
    a, b = {"url": LI + "?position=3&pageNum=0"}, {"url": LI + "/?position=9&pageNum=2"}
    assert R.posting_key(a) == R.posting_key(b) == "linkedin|4470000001"
    assert R.posting_key({"url": "https://www.linkedin.com/jobs/view/4470000002"}) \
        == "linkedin|4470000002"


def test_linkedin_twins_on_two_scored_rows_are_prepared_once(monkeypatch):
    urls = [LI + "?position=3&pageNum=0", LI + "?position=9&pageNum=2"]
    lines = [line("New", "Quillfield", "VP Partnerships", urls[0], k="9"),
             line("New", "Quillfield", "VP Partnerships", urls[1], k="8")]
    roles = [dict(rec("quillfield"), url=urls[0]),
             dict(rec("quillfield"), job_id="quillfield:vp-old", url=urls[1])]
    s, _ = rebuild_world(monkeypatch, lines, roles)
    rep = {}
    got = R.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep)
    assert len(got) == 1, rep


def test_two_id_postings_on_one_careers_path_stay_two():
    base = "https://zentrox.example/careers/job/?id={}&position=4&utm_source=x"
    a, b = R.posting_key({"url": base.format(101)}), R.posting_key({"url": base.format(202)})
    assert a == "zentrox.example/careers/job?id=101" and a != b


def test_a_workday_link_with_and_without_a_source_is_one_posting():
    wd = "https://quillfield.wd5.myworkdayjobs.com/en-US/Careers/job/London/Head-of-X_R123"
    assert R.posting_key({"url": wd + "?source=LinkedIn"}) == R.posting_key({"url": wd}) \
        == R.posting_key({"url": wd + "/#apply"})


# ---------- E1: a build that raised is decided from his row ----------

def _real_build(monkeypatch, fail):
    """process_step with the real build_one onto a Yes row, its sheet write
    failing at the POST (nothing landed) or at the read-back (all landed)."""
    s = FakeSheet(grid_of(_yes("")))
    rec_ = _stub_build(monkeypatch)
    real_post, real_cells = s._post, s.update_package_cells

    def post(path, body):
        if path == "/values:batchUpdate":
            raise sheet_mod.SheetError("429 on the batchUpdate POST")
        return real_post(path, body)

    def cells(*a, **kw):
        real_cells(*a, **kw)
        raise sheet_mod.SheetError("429 on the read-back")
    if fail == "post":
        monkeypatch.setattr(s, "_post", post)
    else:
        monkeypatch.setattr(s, "update_package_cells", cells)
    stored = {}
    monkeypatch.setattr(R, "db_patch", lambda cfg, table, match, values: (
        rec_["patches"].append((table, dict(match), dict(values))), stored.update(values)))
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [dict(ROW, **stored)])
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    real_select = router.select_for_build
    counts, summary = _process(monkeypatch, s, [ROW])
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: [dict(ROW, **stored)])
    again = real_select(Cfg(), s, FakeCanon().sheet_headers, cap=0)
    return counts, summary, stored, again, s


def test_a_row_write_that_never_landed_is_built_again(monkeypatch):
    counts, summary, stored, again, s = _real_build(monkeypatch, "post")
    assert stored["package_status"] == R.RETRY_STATUS
    assert counts["built"] == 0 and counts["blocked"] == 0 and counts["failed"] == 1
    assert [d["job_id"] for d in again] == [JOB], "select_for_build tries it again"
    assert any("row write did not land: the record is set to be built again" in x
               for x in summary)
    assert "1 failed and tried again next pass" in R._process_line(counts)


def test_a_read_back_that_failed_after_the_write_landed_stays_built(monkeypatch):
    counts, summary, stored, again, s = _real_build(monkeypatch, "readback")
    assert stored["package_status"] == "built"
    assert counts["built"] == 1 and counts["blocked"] == 0 and not counts.get("failed")
    assert again == [], "its row carries the links"
    assert any("reads back with the new links; left as built" in x for x in summary)


def test_a_rebuild_whose_write_never_landed_keeps_the_package_his_row_links(monkeypatch):
    stored = {"package_status": "built", "package_cv_url": "https://docs.example/cv-new",
              "package_letter_url": "https://docs.example/cl-new"}
    s = FakeSheet(grid_of(_yes("")))
    old = ["https://docs.example/cv-old", "https://docs.example/cl-old",
           "https://docs.example/cvp-old", "https://docs.example/clp-old"]
    s.update_package_cells(3, cv_url=old[0], letter_url=old[1], cv_pdf_url=old[2],
                           letter_pdf_url=old[3], package_status=BUILT,
                           built_date="2026-10-01")
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [dict(stored)])
    monkeypatch.setattr(R, "db_patch", lambda cfg, table, match, values: stored.update(values))
    outcome, _ = R.build_error_outcome(Cfg(), JOB, sheet_mod.SheetError("429"), sheet=s,
                                       canon=FakeCanon(), row=ROW)
    assert outcome == R.BUILD_RETRY and stored["package_status"] == "built"
    assert (stored["package_cv_url"], stored["package_letter_url"]) == tuple(old[:2])


# ---------- E2: an essay that did not draft waits a day ----------

def _two_postings(monkeypatch):
    lines = [L("ardent", k="10"), L("brisk", k="8")]
    roles = [rec("ardent"), rec("brisk")]
    db, mailed, reached = approvals_world(monkeypatch, lines, roles, Plan())
    stub = R.build_fill_plan

    def fill(cfg, sheet, row_, bank=None, view=None):
        got = stub(cfg, sheet, row_, bank=bank, view=view)
        return (Blocked(_essay()) if row_["job_id"] == "ardent:vp" else Plan(),) + got[1:]
    monkeypatch.setattr(R, "build_fill_plan", fill)
    s = R.Sheet("x")
    _commands(monkeypatch, s)
    return db, mailed, reached, s


def test_two_batches_in_one_run_do_not_both_take_a_failing_essay(monkeypatch):
    db, mailed, reached, s = _two_postings(monkeypatch)
    R.cmd_approvals_wanted(apply=True, max_n=1)
    R.cmd_approvals_wanted(apply=True, max_n=1)
    assert reached == ["ardent:vp", "brisk:vp"]
    assert mailed["approval"] == [1]
    marks = db.tables["hunter_alerts"]
    assert [(m["kind"], m["detail"]["outcome"]) for m in marks] == [
        ("trouble", R.ESSAY_NOT_DRAFTED)]
    assert marks[0]["fingerprint"] == R.prepared_fingerprint(rec("ardent"),
                                                             R.ESSAY_NOT_DRAFTED)
    assert marks[0]["fingerprint"].startswith(R.PREPARED_MARK)


def test_after_a_day_the_failed_essay_is_offered_again(monkeypatch):
    db, mailed, reached, s = _two_postings(monkeypatch)
    R.cmd_approvals_wanted(apply=True, max_n=1)
    rep = {}
    ids = [d["job_id"] for d in R.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep)]
    assert "ardent:vp" not in ids and rep["skips"] == {
        R.PREPARED_SKIPS[R.ESSAY_NOT_DRAFTED]: 1}
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=25)
    monkeypatch.setattr(R, "_now_utc", lambda: later)
    ids = [d["job_id"] for d in R.wanted_for_approval(Cfg(), s, FakeCanon(), report={})]
    assert ids[0] == "ardent:vp"


def test_an_essay_failing_again_after_its_day_starts_a_new_day(monkeypatch):
    db, _, _ = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")], Plan())
    first = datetime.datetime(2026, 10, 14, 8, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(R, "_now_utc", lambda: first)
    assert R.mark_prepared(Cfg(), rec("ardent"), R.ESSAY_NOT_DRAFTED) == ""
    monkeypatch.setattr(R, "_now_utc", lambda: first + datetime.timedelta(hours=30))
    assert R.mark_prepared(Cfg(), rec("ardent"), R.ESSAY_NOT_DRAFTED) == ""
    marks = db.tables["hunter_alerts"]
    assert len(marks) == 1 and R.essay_wait_open(marks[0]["detail"])


# ---------- E3: a refusal waits on his answer bank ----------

def test_adding_the_missing_answer_reopens_the_posting(monkeypatch):
    db, _, reached = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")],
                                     Blocked("no answer in the bank"))
    tally = {}
    R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {R.REFUSED_BLOCKING: 1}
    assert db.tables["hunter_alerts"][0]["fingerprint"] == R.prepared_fingerprint(
        rec("ardent"), R.REFUSED_BLOCKING, bank=R.bank_hash({}))
    s = R.Sheet("x")
    rep = {}
    assert R.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep) == []
    assert rep["skips"] == {R.PREPARED_SKIPS[R.REFUSED_BLOCKING]: 1}
    monkeypatch.setattr(R, "_answer_bank", lambda sheet: {"work authorisation": "Yes"})
    got = R.wanted_for_approval(Cfg(), s, FakeCanon(), report={})
    assert [d["job_id"] for d in got] == ["ardent:vp"]


def test_an_old_refusal_mark_is_not_read_as_by_hand(monkeypatch):
    """A refusal marked before the bank was in its key sits under the
    by-hand key. It is not a by-hand delivery, and the posting is offered."""
    s, db = rebuild_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")])
    db.tables["hunter_alerts"] = [{"kind": "trouble", "id": 1,
                                   "fingerprint": R.prepared_fingerprint(rec("ardent")),
                                   "detail": {"outcome": R.REFUSED_BLOCKING}}]
    monkeypatch.setattr(R, "_answer_bank", lambda sheet: {})
    got = R.wanted_for_approval(Cfg(), s, FakeCanon(), report={})
    assert [d["job_id"] for d in got] == ["ardent:vp"]


# ---------- E4: an essay and a missing answer together are an essay ----------

def test_a_failed_essay_beside_a_missing_answer_writes_no_permanent_mark(monkeypatch):
    db, _, _ = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")],
                               Blocked(_essay(), "no answer in the bank"))
    tally = {}
    R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {R.ESSAY_NOT_DRAFTED: 1}
    assert [m["detail"]["outcome"] for m in db.tables["hunter_alerts"]] == [
        R.ESSAY_NOT_DRAFTED]


