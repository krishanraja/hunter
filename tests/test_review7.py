"""The seventh review of his-view packages: what it found, each with a test.

- build_one, the last read before paying, holds a Yes to "my score wins",
  and a rebuild to router.wants, read off his row now;
- what leaves tailor or the essays never quotes a refused figure or name
  when the view carries his words;
- hiring_lead and jd_mirror are gated like the rest of the letter;
- Prepare prints counts and neutral outcomes, never the Apply email, the
  open link, the token or a field value;
- posting_key is the posting's identity, so two postings on one careers
  path are two applications and one's AMEND words never reach the other;
- a spent month or a failed essay is never marked as refused for good;
- process never overwrites a built package as blocked;
- a score-built row whose posting is gone leaves Pipeline;
- an unreadable baseline stops rebuild-packages with counts only;
- a curly apostrophe does not hide a copy of his note.

Every company and every reaction here is invented. The repository is public:
no real company sits beside a score of his, and no reaction of his is
quoted, anywhere in this file (CLAUDE.md 3f).
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as run_mod  # noqa: E402
import hunter.sheet as sheet_mod  # noqa: E402
from hunter import leave, router  # noqa: E402
from hunter.package import tailor as tailor_mod  # noqa: E402
from test_his_view import (GH, MARK, REACTION, L, _commands, grid_of, line, rec,  # noqa: E402
                           rebuild_world)
from test_score_builds import Cfg, FakeCanon, _stub_build  # noqa: E402
from test_sheet_rows import FakeSheet  # noqa: E402


class RuleCanon(FakeCanon):
    """Canon v11: 9.13 carries his ruling that his score wins over a Yes."""
    v913 = FakeCanon.v913 + " " + leave.CANON_RULE + ", with K and M kept."


OSTRAVIA = "https://job-boards.greenhouse.io/ostravia/jobs/7"


def _yes_build(monkeypatch, *, k, m, canon=None, rebuild=False):
    s = FakeSheet(grid_of(line("Yes", "Ostravia", "Chief of Staff", OSTRAVIA, k=k, m=m)))
    rec_ = _stub_build(monkeypatch)
    summary = []
    row_ = {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
            "title": "Chief of Staff", "url": OSTRAVIA, "status": "staging",
            "package_status": "built", "package_trigger": "column A Yes",
            "build_trigger": "column A Yes", "sheet_link": OSTRAVIA, "sheet_row": 3}
    ok = run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), canon or RuleCanon(), s,
                           row_, summary, cache={}, rebuild=rebuild)
    return ok, rec_, summary


# ---------- F1: build_one holds a Yes to "my score wins" ----------

@pytest.mark.parametrize("rebuild", [False, True])
@pytest.mark.parametrize("k,m", [("3", REACTION), ("", REACTION), ("2", "")])
def test_build_one_never_pays_for_a_yes_his_score_took_back(monkeypatch, k, m, rebuild):
    """Chosen as a Yes; by its turn he had scored it 3, or answered it with
    only a reaction. Nothing is fetched or paid for, and the line says so
    without the role."""
    ok, rec_, summary = _yes_build(monkeypatch, k=k, m=m, rebuild=rebuild)
    assert ok is False
    assert rec_["resolved"] == 0 and rec_["tailored"] == 0 and rec_["patches"] == []
    assert summary == ["SKIPPED: a Yes his score has since taken back; nothing fetched or built"]
    assert not any("Ostravia" in x or "ostravia" in x or MARK in x for x in summary)


def test_before_canon_carries_the_rule_a_yes_still_builds(monkeypatch):
    ok, rec_, _ = _yes_build(monkeypatch, k="3", m=REACTION, canon=FakeCanon())
    assert ok is True and rec_["tailored"] == 1


@pytest.mark.parametrize("k,m", [("6", ""), ("6", REACTION), ("", "Duplicate")])
def test_a_rebuild_needs_a_row_that_still_asks_for_an_application(monkeypatch, k, m):
    """A Yes he scored 6 builds as it always did, but a paid rebuild is for
    a role he wants an application for (router.wants)."""
    ok, rec_, summary = _yes_build(monkeypatch, k=k, m=m, rebuild=True)
    assert ok is False and rec_["tailored"] == 0 and rec_["resolved"] == 0
    assert summary[0].startswith("SKIPPED: a rebuild")
    ok, rec_, _ = _yes_build(monkeypatch, k=k, m=m, rebuild=False)
    assert ok is True and rec_["tailored"] == 1, "an ordinary Yes build is unchanged"


def test_rebuild_packages_reads_his_row_again_before_each_target(monkeypatch, capsys):
    """Two Yes targets, K 9 first. While the first is rebuilt he scores the
    second a 3: it is not rebuilt, and the count says why without naming it."""
    s, _ = rebuild_world(monkeypatch, [L("brisk", verdict="Yes", k="9"),
                                       L("cobalt", verdict="Yes", k="8")],
                         [rec("brisk", package_trigger="column A Yes"),
                          rec("cobalt", package_trigger="column A Yes")])
    _commands(monkeypatch, s)
    built = []

    def build_one(cfg, canon, sheet, row, detail, **kw):
        built.append(row["job_id"])
        for r in sheet.grid[2:]:
            if r[sheet_mod.COLS["Business"]] == "Cobalt":
                r[sheet_mod.COLS[sheet_mod.KRISH_SCORE]] = "3"
        return True
    monkeypatch.setattr(run_mod, "build_one", build_one)
    assert run_mod.cmd_rebuild_packages(apply=True) == 0
    assert built == ["brisk:vp"]
    out = capsys.readouterr().out
    assert "1 no longer asked for since it was chosen" in out
    assert "Cobalt" not in out and "cobalt:vp" not in out


def _prepare_world(monkeypatch, *, k, m, canon=None):
    s = FakeSheet(grid_of(line("Yes", "Ostravia", "Chief of Staff", OSTRAVIA, k=k, m=m)))
    rows = [{"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
             "title": "Chief of Staff", "url": OSTRAVIA, "package_status": "built",
             "package_trigger": "column A Yes", "status": "staging"}]
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), canon or RuleCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [
        r for r in rows if not params.get("job_id") or params["job_id"] == f"eq.{r['job_id']}"])
    reached = []
    import hunter.notify as notify_mod

    def mailbox(cfg):
        reached.append(True)
        raise RuntimeError("stop here: the refusal let it through")
    monkeypatch.setattr(notify_mod, "mailbox", mailbox)
    return reached


@pytest.mark.parametrize("k,m", [("3", ""), ("", REACTION)])
def test_prepare_refuses_a_yes_his_score_took_back(monkeypatch, capsys, k, m):
    reached = _prepare_world(monkeypatch, k=k, m=m)
    assert run_mod.cmd_approvals(apply=True, job_id="ostravia:chief-of-staff") == 1
    assert reached == []
    out = capsys.readouterr().out
    assert "REFUSING" in out and "Ostravia" not in out and MARK not in out


@pytest.mark.parametrize("k,m", [("6", ""), ("", ""), ("8", REACTION)])
def test_prepare_still_sends_a_yes_he_scored_6_or_has_not_scored(monkeypatch, k, m):
    """Prepare is his explicit press: a Yes with K 6 or K blank is his to send."""
    reached = _prepare_world(monkeypatch, k=k, m=m)
    # The stub mailbox stops it there, and the stop is counted by class.
    tally = {}
    assert run_mod.cmd_approvals(apply=True, job_id="ostravia:chief-of-staff",
                                 tally=tally) == 1
    assert reached == [True] and tally == {"failed": 1}


# ---------- F2: what leaves tailor and the essays quotes no refused figure or name ----------

from hunter.package import his_view as hv  # noqa: E402
from test_his_view import (BLOCKS, COMPETENCIES, EVIDENCE, SUMMARY_OK, EssayCfg,  # noqa: E402
                           essay_world, reply, tailor_world)

# Invented: a note to himself with an offer elsewhere, written the way he
# types figures, and a contact by name.
NOTE = ("Corvantis offered me 310k base, and Mirela Ostrander runs revenue there "
        "and asked me to apply")
JD = "Quillfield needs commercial strategy across regions, as the board asked. " * 20


def model_reply(**over):
    data = json.loads(reply())
    data.update(over)
    return json.dumps(data)


def tailor_with(view, jd=JD):
    return tailor_mod.tailor(
        Cfg({"hunter_anthropic_api_key": "k"}), None, company="Quillfield",
        title="VP Commercial Strategy", jd_text=jd, master_competencies=COMPETENCIES,
        letter_blocks=BLOCKS, evidence=EVIDENCE, his_view=view)


def test_a_figure_from_his_note_in_another_form_is_not_quoted(monkeypatch):
    """His note says 310k; the model wrote $310K. The gate's failure quoted
    '$310k', which matched nothing in his note, and the flag went into the
    BUILT line in the public log. Now no refused figure is quoted at all."""
    noted = SUMMARY_OK.replace("into revenue", "past a $310K offer")
    sent = tailor_world(monkeypatch, [model_reply(summary=noted)])
    tr = tailor_with(hv.HisView(score=9, reaction=NOTE))
    flags = "; ".join(tr.flags)
    assert tr.summary == "" and hv.FIGURE_LEFT_OUT in flags
    assert "310" not in flags
    assert "$310k" in sent[1][1]["text"], "only the retry message names it"


def test_with_no_view_the_flag_still_names_the_figure(monkeypatch):
    noted = SUMMARY_OK.replace("into revenue", "past a $310K offer")
    tailor_world(monkeypatch, [model_reply(summary=noted)])
    tr = tailor_with(None)
    assert any("number '$310k' is not in the recorded evidence" in f for f in tr.flags)


def test_a_hard_validation_failure_is_redacted_before_the_fallback(monkeypatch):
    """A jd_mirror carrying his figure failed validate() twice, and the
    fallback flag quoted it whole."""
    bad = model_reply(jd_mirror="commercial strategy across regions at 310k")
    tailor_world(monkeypatch, [bad, bad])
    tr = tailor_with(hv.HisView(score=9, reaction=NOTE))
    flags = "; ".join(tr.flags)
    assert flags.startswith("tailor fallback: validation failed twice")
    assert hv.FIGURE_LEFT_OUT in flags and "310" not in flags
    tailor_world(monkeypatch, [bad, bad])
    plain = "; ".join(tailor_with(None).flags)
    assert "'310k'" in plain, "with no view the specific failure stands"


def test_an_essay_reason_quotes_no_figure_and_the_retry_names_it(monkeypatch):
    from hunter.apply import essays
    from test_his_view import ANSWER
    essay_world(monkeypatch, ANSWER + " I turned down a $310K offer to do this.")
    seen = []
    from hunter import llm
    real = llm.complete

    def complete(cfg, prompt, **kw):
        seen.append(prompt)
        return real(cfg, prompt, **kw)
    monkeypatch.setattr(llm, "complete", complete)
    got, why = essays.draft_one(EssayCfg(), question="Why us?", company="Quillfield",
                                role="VP", jd_text="jd", evidence=EVIDENCE,
                                his_view=hv.HisView(score=8, reaction=NOTE))
    assert got == "" and hv.FIGURE_LEFT_OUT in why and "310" not in why
    assert any("$310k" in p for p in seen[1:]), "the retry tells the model what failed"


# ---------- F3: hiring_lead and jd_mirror are gated ----------

def test_a_contact_only_his_note_names_never_addresses_the_letter(monkeypatch):
    tailor_world(monkeypatch, [model_reply(hiring_lead="Mirela Ostrander")])
    tr = tailor_with(hv.HisView(score=9, reaction=NOTE))
    assert tr.hiring_lead == "Hiring Team"
    assert tailor_mod.LEAD_DROPPED in tr.flags
    assert not any("Mirela" in f or "Ostrander" in f for f in tr.flags)
    assert tr.summary == SUMMARY_OK, "the rest of the result stands"


def test_a_hiring_lead_the_jd_names_is_kept(monkeypatch):
    jd = JD + " Report to Tamsin Reyhill, Chief Revenue Officer."
    tailor_world(monkeypatch, [model_reply(hiring_lead="Tamsin Reyhill")])
    tr = tailor_with(hv.HisView(score=9, reaction=NOTE), jd=jd)
    assert tr.hiring_lead == "Tamsin Reyhill" and tailor_mod.LEAD_DROPPED not in tr.flags


def test_a_jd_mirror_naming_his_contact_falls_back_to_the_block_clause(monkeypatch):
    mirror = "commercial strategy across regions, as Mirela Ostrander asked"
    assert tailor_mod.validate_jd_mirror(mirror, JD) == [], "shape alone let it through"
    tailor_world(monkeypatch, [model_reply(jd_mirror=mirror)])
    tr = tailor_with(hv.HisView(score=9, reaction=NOTE))
    assert tr.jd_mirror == "" and tailor_mod.MIRROR_DROPPED in tr.flags
    assert not any("Mirela" in f or "Ostrander" in f for f in tr.flags)
    tailor_world(monkeypatch, [model_reply(jd_mirror="commercial strategy across regions")])
    ok = tailor_with(hv.HisView(score=9, reaction=NOTE))
    assert ok.jd_mirror == "commercial strategy across regions"
    assert tailor_mod.MIRROR_DROPPED not in ok.flags


# ---------- F4: Prepare prints counts and neutral outcomes only ----------

TOKEN, OPEN_KEY = "TOKZEBRA-9f3c", "KEYQUOKKA77"
FORM = "https://jobs.example/apply/ostravia-form"
RECIPIENT = "zorbic.him@example.com"
ESSAY = "Zorbic essay sentence: I would build the partner engine in my first month."
FIELD_VALUE = "Value-Quorrafield-42"


def _real_render_world(monkeypatch, capsys_mailed):
    from hunter.apply import approval, fill, payload
    from hunter.apply import submit as submit_mod
    import hunter.docbuild as docbuild
    import hunter.notify as notify_mod
    s = FakeSheet(grid_of(line("Yes", "Ostravia", "Chief of Staff", OSTRAVIA, k="8", m=REACTION)))
    rows = [{"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
             "title": "Chief of Staff", "url": OSTRAVIA, "package_status": "built",
             "package_trigger": "column A Yes", "status": "staging",
             "package_cv_url": "https://docs.google.com/document/d/cvid0123456789/edit",
             "package_letter_url": "https://docs.google.com/document/d/clid0123456789/edit"}]
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), RuleCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "GoogleOAuth",
                        lambda cfg: type("O", (), {"access_token": lambda self=None: "t"})())
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [
        r for r in rows if not params.get("job_id") or params["job_id"] == f"eq.{r['job_id']}"])
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    monkeypatch.setattr(run_mod, "live_postings", lambda cfg: {})
    monkeypatch.setattr(run_mod, "_answer_bank", lambda sheet: {})
    monkeypatch.setattr(docbuild, "DocBuild", lambda tok: type(
        "D", (), {"export_pdf": lambda self, doc_id: b"%PDF"})())
    monkeypatch.setattr(notify_mod, "mailbox", lambda cfg: RECIPIENT)
    monkeypatch.setattr(notify_mod, "send_email",
                        lambda cfg, subject, html, **kw: capsys_mailed.append(kw["text"])
                        or {"id": "m1", "sent": True})
    monkeypatch.setattr(approval, "new_token", lambda job_id: TOKEN)
    monkeypatch.setattr(approval, "record_sent", lambda cfg, **kw: None)
    monkeypatch.setattr(payload, "new_key", lambda: OPEN_KEY)
    monkeypatch.setattr(payload, "build", lambda plan, **kw: {"url": FORM})
    monkeypatch.setattr(submit_mod, "preview", lambda plan, **kw: {
        "filled": ["a"], "missed": [], "notes": [f"Location offered {FIELD_VALUE}"],
        "png": b"", "blocker": f"captcha after {FIELD_VALUE}", "error": ""})
    plan = fill.FillPlan(
        ats="greenhouse", slug="ostravia", posting_id="7", company="Ostravia",
        role="Chief of Staff", jd_url=OSTRAVIA,
        fields=(fill.FilledField(key="q1", label="Current company", kind="text",
                                 required=True, value=FIELD_VALUE, source="bank"),),
        essays={"Why Ostravia?": ESSAY}, notes=[f"drafted an answer: {ESSAY}"])
    au = type("A", (), {"attachment_style": "", "autonomy_score": "1"})()
    role = type("R", (), {"company": "Ostravia", "title": "Chief of Staff",
                          "jd_url": OSTRAVIA})()
    monkeypatch.setattr(run_mod, "build_fill_plan", lambda *a, **kw: (plan, au, role))


SECRETS = (TOKEN, OPEN_KEY, FORM, RECIPIENT, ESSAY, "Zorbic", FIELD_VALUE, MARK)


@pytest.mark.parametrize("apply", [True, False])
def test_prepare_prints_no_part_of_the_apply_email(monkeypatch, capsys, apply):
    """Through the real approval.render: the email carries the open link,
    the token, his address, the essay and the field values. None of it
    reaches stdout or stderr, sent or dry run."""
    mailed = []
    _real_render_world(monkeypatch, mailed)
    code = run_mod.cmd_approvals(apply=apply, job_id="ostravia:chief-of-staff")
    printed = capsys.readouterr()
    assert code == 0
    if apply:
        assert len(mailed) == 1, "the email went"
        assert f"{FORM}#hunter={TOKEN}.{OPEN_KEY}" in mailed[0] and ESSAY in mailed[0], \
            "the email itself carries what the log must not"
        assert "Apply email sent: 1, fields 1, essays 1" in printed.out
    for leak in SECRETS:
        assert leak not in printed.out and leak not in printed.err, leak


def test_the_prepare_press_keeps_stdout_out_of_the_drain_log(monkeypatch, capsys):
    from hunter import actions

    def approvals(apply=False, job_id="", tally=None, **kw):
        print(f"{FORM}#hunter={TOKEN}.{OPEN_KEY} {ESSAY}")
        tally["sent"] = 1
        return 0
    monkeypatch.setattr(run_mod, "cmd_approvals", approvals)
    ok, note = actions._prepare("ostravia:chief-of-staff")
    printed = capsys.readouterr()
    assert ok is True
    for leak in SECRETS:
        assert leak not in printed.out and leak not in printed.err and leak not in note, leak


def test_the_mail_fallback_prints_no_part_of_the_email(monkeypatch, capsys):
    import hunter.notify as notify_mod
    monkeypatch.setattr(notify_mod, "_check_recipient", lambda to, cfg=None: to)
    monkeypatch.setattr(notify_mod, "GoogleOAuth",
                        lambda cfg: type("O", (), {"access_token": lambda self=None: "t"})())

    class R:
        status_code = 403
        text = "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
    monkeypatch.setattr(notify_mod.requests, "post", lambda *a, **kw: R())
    out = notify_mod.send_email(Cfg(), f"Apply: Ostravia [{TOKEN}]", "<p>x</p>",
                                to=RECIPIENT, text=f"{FORM}#hunter={TOKEN}.{OPEN_KEY} {ESSAY}")
    printed = capsys.readouterr()
    assert out["sent"] is False and "went nowhere" in printed.out
    for leak in SECRETS:
        assert leak not in printed.out, leak


# ---------- F5: posting_key is the posting's identity ----------

from test_his_view import Plan, approvals_world, doc_links  # noqa: E402

CAREERS = "https://zentrox.example/careers/job/?gh_jid={}"


def zentrox(n, title, jid):
    return dict({"job_id": f"zentrox:{n}", "company": "Zentrox", "title": title,
                 "url": CAREERS.format(jid), "package_status": "built",
                 "package_trigger": "Krish Score 8", "status": "staging",
                 "presented_at": "2026-10-10", "package_built_at": "2026-10-11T08:00:00Z"},
                **doc_links(f"zentrox{n}"))


ROLE_A, ROLE_B = zentrox("a", "VP Partnerships", 1111111), zentrox("b", "Head of Alliances", 2222222)
LINES = [line("New", "Zentrox", "VP Partnerships", CAREERS.format(1111111), k="8"),
         line("New", "Zentrox", "Head of Alliances", CAREERS.format(2222222), k="9")]


def test_two_postings_on_one_careers_path_are_two_postings():
    assert run_mod.posting_key(ROLE_A) != run_mod.posting_key(ROLE_B)
    assert run_mod.posting_key(ROLE_A) == "greenhouse-jid|1111111"
    # only where a click came from is taken off
    tracked = {"url": "https://zentrox.example/careers/job/?id=77&utm_source=x&ref=y"}
    other = {"url": "http://Zentrox.example/careers/job/?id=78#apply"}
    assert run_mod.posting_key(tracked) == "zentrox.example/careers/job?id=77"
    assert run_mod.posting_key(tracked) != run_mod.posting_key(other)
    # one posting under two job ids is still one (the Harvey case)
    twin = dict(ROLE_A, job_id="zentrox:a-old")
    assert run_mod.posting_key(twin) == run_mod.posting_key(ROLE_A)
    board = {"url": "https://job-boards.greenhouse.io/zentrox/jobs/55?gh_src=abc"}
    assert run_mod.posting_key(board) == "greenhouse|zentrox|55"


def test_both_are_wanted_and_both_get_their_own_email(monkeypatch):
    real_live = run_mod.live_postings
    db, mailed, reached = approvals_world(monkeypatch, LINES, [ROLE_A, ROLE_B], Plan())
    rep = {}
    got = run_mod.wanted_for_approval(Cfg(), run_mod.Sheet("x"), FakeCanon(), report=rep)
    assert sorted(d["job_id"] for d in got) == ["zentrox:a", "zentrox:b"], rep
    # A is sent, and its token is waiting on him: B is still its own posting
    monkeypatch.setattr(run_mod, "live_postings", real_live)
    by_id = {d["job_id"]: d for d in got}
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[by_id["zentrox:a"]], tally=tally)
    db.tables["hunter_application_approvals"].append(
        {"token": "ta", "job_id": "zentrox:a", "state": "awaiting", "sent_at": "x"})
    rep = {}
    left = run_mod.wanted_for_approval(Cfg(), run_mod.Sheet("x"), FakeCanon(), report=rep)
    assert [d["job_id"] for d in left] == ["zentrox:b"], rep
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=left, tally=tally)
    assert tally == {"sent": 2} and mailed["approval"] == [1, 1]
    assert reached == ["zentrox:a", "zentrox:b"]


def test_one_postings_amend_words_never_reach_the_others_rebuild(monkeypatch):
    tokens = [{"token": "ta", "job_id": "zentrox:a", "state": "cancelled", "sent_at": "x",
               "decided_at": "2026-10-12T09:00:00Z",
               "feedback": "Invented: lead with the Ostravia channel."}]
    s, _ = rebuild_world(monkeypatch, LINES, [ROLE_A, ROLE_B], tokens)
    got = run_mod.rebuild_targets(Cfg(), s, FakeCanon(), report={})
    feedback = {d["job_id"]: d["amend_feedback"] for d in got}
    assert feedback == {"zentrox:a": "Invented: lead with the Ostravia channel.",
                        "zentrox:b": ""}


def test_a_twin_record_of_the_same_posting_still_carries_his_amend_words():
    known = [ROLE_A, dict(ROLE_A, job_id="zentrox:a-old")]
    toks = [{"token": "t0", "job_id": "zentrox:a-old", "state": "cancelled",
             "decided_at": "2026-10-12", "feedback": "Invented: shorter."}]
    tokens = run_mod._tokens_by_posting(None, known, toks)
    assert run_mod.amend_feedback(ROLE_A, tokens) == "Invented: shorter."
    assert run_mod.amend_feedback(ROLE_B, tokens) == ""


# ---------- F6: a transient failure is never marked as refused for good ----------

def test_with_the_month_at_its_ceiling_prepare_all_waits_before_paying(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")])
    _commands(monkeypatch, s)
    from hunter import spend
    monkeypatch.setattr(spend, "over_budget", lambda cfg: "the month's ceiling of $150 is spent")
    monkeypatch.setattr(run_mod, "wanted_for_approval",
                        lambda *a, **kw: pytest.fail("read and chose with the month spent"))
    monkeypatch.setattr(run_mod, "cmd_approvals", lambda *a, **kw: pytest.fail("paid"))
    assert run_mod.cmd_approvals_wanted(apply=True) == 0
    assert "prepare: waiting, the month's ceiling of $150 is spent" in capsys.readouterr().out


class EssayPlan(Plan):
    """A form whose one missing answer is an essay that did not draft."""

    def __init__(self, reason):
        super().__init__(blocking=("Why Ardent?",))
        for f in self.blocking:
            f.reason = reason


def test_an_essay_that_did_not_draft_is_tried_again_next_batch(monkeypatch):
    from hunter.apply.resolve import NEEDS_ESSAY
    db, mailed, _ = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")],
                                    EssayPlan(NEEDS_ESSAY + ": drafted per posting"))
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {run_mod.ESSAY_NOT_DRAFTED: 1}
    marks = db.tables["hunter_alerts"]
    assert [m["detail"]["outcome"] for m in marks] == [run_mod.ESSAY_NOT_DRAFTED], \
        "marked for a day only (test_review8 holds the day)"
    assert mailed == {"by_hand": [], "approval": []}


def test_an_answer_only_his_bank_can_give_is_still_marked(monkeypatch):
    db, _, _ = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")],
                               EssayPlan("no answer in the bank"))
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {run_mod.REFUSED_BLOCKING: 1}
    assert [m["detail"]["outcome"] for m in db.tables["hunter_alerts"]] == [
        run_mod.REFUSED_BLOCKING]


def test_prepare_all_says_what_the_essays_cost(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")])
    _commands(monkeypatch, s)
    from hunter import spend
    monkeypatch.setattr(spend, "summary", lambda: ["model spend this run: $0.42 in 3 call(s)"])
    flushed = []
    monkeypatch.setattr(spend, "flush", lambda cfg=None: flushed.append(1) or "spend: 3 written")

    def approvals(apply=False, job_id="", prefill=True, *, chosen=None, tally=None):
        tally["sent"] = tally.get("sent", 0) + 1
        return 0
    monkeypatch.setattr(run_mod, "cmd_approvals", approvals)
    assert run_mod.cmd_approvals_wanted(apply=True) == 0
    out = capsys.readouterr().out
    assert "model spend this run: $0.42 in 3 call(s)" in out and flushed == [1]


# ---------- F7: process never overwrites a built package as blocked ----------

def test_process_leaves_a_built_package_built_when_the_row_write_raises(monkeypatch):
    """build_one wrote 'built', then the link read-back on his row raised (a
    Sheets 429). process used to patch the record to blocked, and every path
    that sends an application skips a blocked package."""
    import hunter.ats.discover as disc
    R = run_mod
    record = {"job_id": "ostravia:chief-of-staff", "package_status": "none"}
    patches = []

    def patch(cfg, table, match, values):
        patches.append(dict(values))
        record.update(values)
    monkeypatch.setattr(R, "db_patch", patch)
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [dict(record)])

    class Stop(BaseException):
        """Past every except Exception in process_step, after the builds."""
    monkeypatch.setattr(R, "archive_columns_step", lambda sheet, apply: "ok")
    monkeypatch.setattr(R, "amendment_step", lambda *a: 0)
    monkeypatch.setattr(R, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(R, "reconcile", lambda *a, **k: R.ReconcileLedger())
    monkeypatch.setattr(R, "sync_applied_state", lambda *a: {"written": 0, "from_sheet": 0,
                                                              "from_ledger": 0})
    monkeypatch.setattr(R, "learning_step", lambda cfg, apply: {
        "roles": [], "company_declines": {}, "recorded": 0, "findings": [], "fixed": [],
        "unexplained": []})
    monkeypatch.setattr(R, "ratings_step", lambda *a, **k: {})
    monkeypatch.setattr(R, "leave_step", lambda *a, **k: {})
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(R, "select_for_build", lambda *a, **k: [dict(record)])

    def build_one(cfg, canon, sheet, row, summary, **kw):
        R.db_patch(cfg, "hunter_seen_roles", {"job_id": row["job_id"]},
                   {"package_status": "built"})
        raise sheet_mod.SheetError("429 on the read-back")
    monkeypatch.setattr(R, "build_one", build_one)
    monkeypatch.setattr(R, "yes_db_rows", lambda *a: (_ for _ in ()).throw(Stop()))
    summary = []
    with pytest.raises(Stop):
        R.process_step(Cfg(), RuleCanon(), object(), summary)
    assert record["package_status"] == "built"
    assert [p.get("package_status") for p in patches] == ["built"]
    # No row could be read back (the sheet here reads nothing): the record is
    # left as it was, and said to be not known to have reached his row.
    assert any("left as it was" in x for x in summary)


# ---------- M1: a score-built row whose posting is gone leaves ----------

from test_leave import rating, r_  # noqa: E402
from hunter import ratings as ratings_mod  # noqa: E402

DEAD = {"Package Status": sheet_mod.PKG_DEAD}


@pytest.mark.parametrize("k", ["9", "7", "6"])
def test_a_row_his_score_built_leaves_once_its_posting_is_gone(k):
    """rebuild-packages re-checks liveness on packages his score built. One
    it found gone was marked Posting dead and, being score-built, kept on
    Pipeline for good, against his ruling "Move the gone ones"."""
    rr = rating(ratings_mod.SCORED, score_built=True)
    assert leave.should_leave(r_(k=k, **DEAD), rr) == leave.GONE
    assert leave.should_leave(r_(k=k), rr) is None, "a live one stays, as before"


def test_a_yes_marked_dead_still_stays_whatever_built_it():
    rr = rating(ratings_mod.SCORED, score_built=True)
    assert leave.should_leave(r_("Yes", k="9", **DEAD), rr) is None


# ---------- M2: an unreadable baseline stops rebuild-packages with counts only ----------

@pytest.mark.parametrize("apply", [False, True])
def test_a_supabase_error_in_the_selection_names_no_role(monkeypatch, capsys, apply):
    import requests
    s, db = rebuild_world(monkeypatch, [L("ardent"), L("brisk")], [rec("ardent"), rec("brisk")])
    _commands(monkeypatch, s)
    real = db.get

    def get(cfg, table, params):
        if table == "hunter_row_state":
            raise requests.HTTPError(
                "503 Server Error: Service Unavailable for url: https://db.example/rest/v1/"
                "hunter_row_state?job_id=in.%28ardent%3Avp%2Cbrisk%3Avp%29")
        return real(cfg, table, params)
    monkeypatch.setattr(run_mod, "db_get", get)
    monkeypatch.setattr(run_mod, "build_one", lambda *a, **kw: pytest.fail("paid"))
    assert run_mod.cmd_rebuild_packages(apply=apply) == 1
    printed = capsys.readouterr()
    assert "rebuild: the targets could not be read (HTTPError); nothing built" in printed.out
    for leak in ("ardent", "brisk", "Ardent", "Brisk", "hunter_row_state?"):
        assert leak not in printed.out and leak not in printed.err, leak


# ---------- M3: a curly apostrophe does not hide a copy of his note ----------

@pytest.mark.parametrize("mark", ["’", "‘", "ʼ"])
def test_a_note_typed_with_curly_apostrophes_is_still_caught(mark):
    note = (f"I{mark}ve run this exact Quorra motion and I{mark}d do it again "
            f"tomorrow")
    prose = ("I've run this exact Quorra motion and I'd do it again tomorrow, "
             "starting with the partners.")
    view = hv.HisView(score=8, reaction=note)
    assert hv.quotes_note(prose, view) is True
    assert hv.quotes_note(prose.replace("'", "’"), hv.HisView(
        score=8, reaction=note.replace(mark, "'"))) is True, "and the other way round"


def test_curly_double_quotes_are_read_as_straight_ones():
    assert hv.normalise_quotes("“Zorbic”") == '"Zorbic"'


def test_the_reply_drain_prints_neither_the_token_nor_his_words(monkeypatch, capsys):
    from hunter.apply import approval, inbox
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), RuleCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: None)
    monkeypatch.setattr(inbox, "fetch_replies", lambda cfg: [
        {"token": TOKEN, "message_id": "m1", "from": RECIPIENT}])
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: {"job_id": "zentrox:a"})
    monkeypatch.setattr(inbox, "classify",
                        lambda r, row, cfg=None: ("amend", "Invented Zorbic words: lead on partners."))
    monkeypatch.setattr(run_mod, "report_stalled_approvals", lambda cfg, apply=False: [])
    assert run_mod.cmd_approvals_drain(apply=False) == 0
    out = capsys.readouterr().out
    assert "AMEND" in out and "zentrox:a" in out and "characters of feedback" in out
    for leak in (TOKEN, "Zorbic", RECIPIENT):
        assert leak not in out, leak


def test_amend_feedback_takes_only_this_postings_tokens_whatever_the_key_holds():
    """The filter itself: even under a key that holds another posting's
    token (the old prefix key did), only a token filed under this record or
    a twin of this posting is read."""
    key_a, key_b = run_mod.posting_key(ROLE_A), run_mod.posting_key(ROLE_B)
    other = {"token": "tb", "job_id": "zentrox:b", "_posting": key_b, "state": "cancelled",
             "decided_at": "2026-10-13", "feedback": "Invented: B's words."}
    mine = {"token": "ta", "job_id": "zentrox:a-old", "_posting": key_a, "state": "cancelled",
            "decided_at": "2026-10-12", "feedback": "Invented: A's words."}
    assert run_mod.amend_feedback(ROLE_A, {key_a: [other, mine]}) == "Invented: A's words."
    assert run_mod.amend_feedback(ROLE_A, {key_a: [other]}) == ""
