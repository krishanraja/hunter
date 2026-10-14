"""The third round on the seventh review: what its checks found after the
second, each with a test that goes red when the guard is taken out.

- jd_mirror's first word and a name with a letter outside ASCII are read;
- an email no channel took is not counted sent, and one that went with no
  record of its approval says so, on the run and on the Prepare note;
- a failed send frees its posting for a twin row of the same posting;
- a build that raised is counted as what its record and row say now, on the
  hourly score pass too, and a reset to build again takes its links with it;
- no error message on these paths quotes his cells or an exception's text.

Every company, contact and note here is invented. The repository is
public: no real company sits beside a score of his, and no reaction of his
is quoted, anywhere in this file (CLAUDE.md 3f).
"""
import pathlib
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as run_mod  # noqa: E402
import hunter.sheet as sheet_mod  # noqa: E402
from hunter import actions  # noqa: E402
from test_his_view import L, Plan, grid_of, line, rec  # noqa: E402
from test_review7 import RECIPIENT, TOKEN  # noqa: E402
from test_review8 import SCORED, _press  # noqa: E402
from test_score_builds import Cfg, FakeCanon, _stub_build  # noqa: E402
from test_sheet_rows import FakeSheet  # noqa: E402

R = run_mod


# ---------- the send: what reached him, said as it was ----------

def test_an_email_no_channel_took_is_not_counted_sent(monkeypatch, capsys):
    """send_email does not raise when gmail.send is not granted: it prints a
    length and says sent False. The token used to be recorded anyway, so
    every later Prepare said an application was waiting for an email that
    did not exist."""
    import hunter.notify as notify_mod
    from hunter.apply import approval
    _press(monkeypatch, SCORED, [rec("ardent")], Plan())
    monkeypatch.setattr(notify_mod, "send_email", lambda *a, **kw: {
        "sent": False, "channel": "stdout fallback"})
    recorded = []
    monkeypatch.setattr(approval, "record_sent", lambda cfg, **kw: recorded.append(1))
    tally = {}
    assert R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally) == 1
    assert tally.get(R.NOT_SENT) == 1 and not tally.get("sent") and recorded == []
    ok, note = actions.prepare_note(1, tally, [])
    assert ok is False and note.startswith("the email could not be sent")


def test_an_email_that_went_with_no_record_says_it_went(monkeypatch):
    """The email is in his inbox and its approval row is not: its open link
    finds nothing. The note used to say nothing was sent."""
    from hunter.apply import approval
    _press(monkeypatch, SCORED, [rec("ardent")], Plan())

    def record_sent(cfg, **kw):
        raise requests.HTTPError("503")
    monkeypatch.setattr(approval, "record_sent", record_sent)
    ok, note = actions._prepare(rec("ardent")["job_id"])
    assert ok is False and note.startswith("the Apply email went, but its approval was not "
                                           "recorded"), note


def test_documents_no_channel_took_are_not_counted_or_marked(monkeypatch):
    _, mailed, _ = _press(monkeypatch, SCORED, [rec("ardent")], Plan(readable=False))
    monkeypatch.setattr(R, "send_apply_by_hand", lambda cfg, **kw: {"sent": False})
    marked = []
    monkeypatch.setattr(R, "mark_prepared", lambda cfg, row, outcome: marked.append(outcome) or "")
    tally = {}
    assert R.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally) == 1
    assert tally.get(R.NOT_SENT) == 1 and not tally.get(R.BY_HAND) and marked == []


def test_a_failed_send_frees_its_posting_for_a_twin_row(monkeypatch):
    """Two records of one posting, the first send refused: the second is the
    same application and may still go."""
    import hunter.notify as notify_mod
    roles = [rec("ardent"), dict(rec("ardent"), job_id="ardent:vp-old")]
    _press(monkeypatch, [L("ardent", k="9")], roles, Plan())
    calls = []

    def send(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise notify_mod.NotifyError("403")
        return {"id": "m2", "sent": True}
    monkeypatch.setattr(notify_mod, "send_email", send)
    tally = {}
    R.cmd_approvals(apply=True, prefill=False, chosen=roles, tally=tally)
    assert len(calls) == 2 and tally.get("sent") == 1


def test_the_retired_note_claims_nothing_it_did_not_read():
    ok, note = actions.prepare_note(0, {"retired": 1}, [])
    assert (ok, note) == (False, "the posting is gone; nothing sent")


# ---------- a build that raised, on the hourly score pass ----------

URL = "https://job-boards.greenhouse.io/ostravia/jobs/7"


def _score_pass(monkeypatch, update_package_cells):
    import hunter.ats.discover as disc
    from hunter import router
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", URL, k="8")))
    known = [{"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
              "title": "Chief of Staff", "url": URL, "package_status": "none",
              "status": "staging"}]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    rec_ = _stub_build(monkeypatch)
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(s, "update_package_cells", update_package_cells(s))
    detail = []
    out = R.score_build_step(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s,
                             apply=True, cap=3, detail=detail, masters=lambda: "m")
    return out, detail, rec_


def test_a_score_build_whose_row_write_never_landed_is_built_again(monkeypatch):
    """A 429 on the batchUpdate POST: the record said built beside a row with
    no links, and select_for_build skips a built record, so the role he
    scored 8 was never built or sent. Read back, it is set to be built again
    and its links to documents that never reached his row go with it."""
    def never(s):
        def boom(*a, **kw):
            raise sheet_mod.SheetError("429 on the write")
        return boom
    out, detail, rec_ = _score_pass(monkeypatch, never)
    assert out["failed"] == 1 and out["built"] == 0
    last = [v for _, _, v in rec_["patches"] if "package_status" in v][-1]
    assert last["package_status"] == R.RETRY_STATUS
    assert last["package_cv_url"] is None and last["package_letter_url"] is None
    assert any("set to be built again" in x for x in detail)


# ---------- nothing quotes his cells or an exception's text ----------

def test_a_change_in_his_cells_is_never_quoted_by_the_assessment_guard():
    """update_assessment's own guard used to put 40 characters of his K and M,
    before and after, into its message, and that message reaches logs."""
    words = "Invented words he typed in his own cell"

    class Racing(FakeSheet):
        def _post(self, path, body):
            out = super()._post(path, body)
            if path == "/values:batchUpdate":
                self.grid[2][sheet_mod.COLS[sheet_mod.KRISH_REACTION]] = "A better argument."
            return out
    s = Racing(grid_of(line("New", "Ostravia", "Chief of Staff", URL, k="8", m=words)))
    with pytest.raises(sheet_mod.SheetError) as err:
        s.update_assessment(3, score=7, why_it_fits="A better argument.")
    msg = str(err.value)
    assert "changed across" in msg and "Invented" not in msg and "better argument" not in msg


def test_a_skipped_amendment_pass_prints_the_class_only():
    """amend.record's insert error quotes the failing row, letter words and
    all; process printed it after the class."""
    import inspect
    src = inspect.getsource(R.process_step)
    line_ = next(x for x in src.splitlines() if "amendment pass skipped" in x)
    assert "{e}" not in line_ and "__class__.__name__" in line_


def test_the_live_path_formats_no_exception_message():
    """A static guard over the functions today's live steps run: none of them
    puts an exception's own text into a line, because the text of a Gmail,
    PostgREST or Docs error quotes what it was given, and the log is public."""
    import ast
    import inspect
    live = ("build_one", "score_build_step", "cmd_score_builds", "cmd_rebuild_packages",
            "rebuild_targets", "cmd_approvals", "cmd_approvals_wanted", "cmd_approvals_drain",
            "wanted_for_approval", "build_error_outcome", "record_his_view",
            "send_apply_by_hand", "amendment_step", "retire_dead_posting")
    bad = []
    for name in live:
        tree = ast.parse(inspect.getsource(getattr(R, name)).lstrip())
        for node in ast.walk(tree):
            if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name) \
                    and node.value.id in ("e", "err", "exc", "x"):
                bad.append((name, node.lineno))
    assert bad == [], bad
    assert "{e}" not in inspect.getsource(actions)


# ---------- plain text into the Doc ----------

def test_markdown_the_model_writes_never_reaches_the_doc(monkeypatch):
    """The tailor prompt carries Markdown bold, and the model answered in kind:
    a CV went out with a phrase between double asterisks."""
    from hunter.package import his_view as hv
    from hunter.package import voicegate
    from test_his_view import SUMMARY_OK, tailor_world
    from test_review7 import model_reply, tailor_with
    marked = SUMMARY_OK.replace("partner engine", "**partner engine**")
    tailor_world(monkeypatch, [model_reply(summary=marked)])
    tr = tailor_with(hv.HisView(score=9, reaction="Invented: the shape I want"))
    assert tr.summary and "*" not in tr.summary and "partner engine" in tr.summary
    assert voicegate.plain_prose("a __b__ and `c` with 3 * 4 and a $1.5M deal") == \
        "a b and c with 3 * 4 and a $1.5M deal"


def test_an_essay_answer_is_plain_text():
    from hunter.apply import essays
    got, why = essays._parse('{"answer": "I would **rebuild** the channel first."}')
    assert (got, why) == ("I would rebuild the channel first.", "")
    got, why = essays._parse("I would **rebuild** the channel first.")
    assert got == "I would rebuild the channel first."


def test_the_section_says_plain_text_and_no_third_person():
    from hunter.package import his_view as hv
    from test_review7 import model_reply  # noqa: F401
    import hunter.package.tailor as tailor_mod
    text = (pathlib.Path(tailor_mod.__file__).parent.parent / "prompts" / "tailor.md").read_text()
    start, end = text.index("[[HIS_VIEW_START]]"), text.index("[[HIS_VIEW_END]]")
    section = text[start:end]
    assert '"he", "his" or "him"' in section and "no asterisks" in section
    assert hv  # the section is his view's; with no view it is cut, byte for byte
