"""PR6: his reasons are in every package he wants.

His words, 2026-10-14: "Make sure that, when you're creating all the
packages, you include all the rationales that I've put in the new column, as
well as the combined rationale. Increased understanding of who I am and what
I want is what makes it a more compelling personalised pitch for the
packages: more human without sounding arrogant, but sounding confident and
like I can deliver ASAP."

What has to hold, each with a test here:

- his view (package.his_view) is K, M and L off his row as it reads now, the
  record only when there is no row, and empty stays empty;
- the tailor prompt carries his reaction and hunter's case when he has said
  something, and is byte for byte today's prompt when he has not;
- the gate traces against his CV and proof points alone, never his note: a
  figure, a name, an offer or a salary only his note gives is refused, and
  the failure never quotes him; his note is never copied;
- the arrogance phrases are named in the prompt and refused by the gate, from
  one list of generic phrases;
- the essays get the same section and the same gate;
- rebuild-packages picks only packages built without his view, for a role he
  scores 7 or more, that he has not acted on, and says why for the rest;
- approvals --wanted picks one built package per posting for a role he
  wants, and skips the ones in play, dead or off Pipeline;
- both commands print counts, never a role and never a word of his.

Every company and every reaction here is invented. The repository is public:
no real company sits beside a score of his, and no reaction of his is quoted,
anywhere in this file (CLAUDE.md 3f).
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as run_mod  # noqa: E402
import hunter.sheet as sheet_mod  # noqa: E402
from hunter import router  # noqa: E402
from hunter.package import his_view as hv  # noqa: E402
from hunter.package import tailor as tailor_mod  # noqa: E402
from hunter.package import voicegate  # noqa: E402
from hunter.sheet import AI_WHY, COLS, HEADERS, KRISH_REACTION, KRISH_SCORE, N_COLS, SheetRow  # noqa: E402
from test_score_builds import Cfg, FakeCanon, _stub_build  # noqa: E402
from test_sheet_rows import FakeSheet, data_row  # noqa: E402

C = COLS
FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Invented words, unique enough that a leak of them anywhere is unambiguous.
REACTION = ("Zorbic note: I have scaled four studios past $4.2M on Quorra agents, "
            "and this seat is the partner engine I keep building")
CASE = "Hunter case for Quillfield: mandate is a partner engine, risk is quota."
MARK = "Zorbic"


def row(verdict="New", k="", m="", why="", n=3, company="Quillfield",
        role="VP Partnerships", url="https://job-boards.greenhouse.io/quillfield/jobs/11",
        **over):
    cells = data_row(verdict, company, role, url, **over)
    cells[C[KRISH_SCORE]], cells[C[KRISH_REACTION]] = k, m
    if why:
        cells[C[AI_WHY]] = why
    return SheetRow(row_number=n, cells=cells, verdict=verdict, company=company,
                    role=role, jd_url=url)


def line(verdict, company, role, url, k="", m="", **over):
    cells = data_row(verdict, company, role, url, **over)
    cells[C[KRISH_SCORE]], cells[C[KRISH_REACTION]] = k, m
    return cells


def grid_of(*rows):
    return [list(HEADERS), [""] * N_COLS] + [list(r) for r in rows]


# ---------- 1. his view ----------

def test_his_view_is_k_m_and_l_off_his_row():
    v = hv.his_view(None, "quillfield:vp", row(k="8", m=REACTION, why=CASE))
    assert (v.score, v.reaction, v.ai_case, v.source) == (8, REACTION, CASE, hv.SHEET)
    assert not v.empty and v.fingerprint.startswith("v1:")
    assert MARK not in v.fingerprint


def test_the_sheet_wins_over_a_stale_record():
    """The record is an hour behind the sheet at best. A reaction he edited,
    or cleared, is what he means now."""
    stale = {"quillfield:vp": {"kind": "scored", "score": 7,
                               "reaction": "Old invented words he has since replaced"}}
    v = hv.his_view(None, "quillfield:vp", row(k="9", m="New invented words"), latest=stale)
    assert (v.score, v.reaction) == (9, "New invented words")
    cleared = hv.his_view(None, "quillfield:vp", row(k="", m=""), latest=stale)
    assert cleared.empty and cleared.reaction == "" and cleared.score is None


def test_the_record_speaks_only_when_there_is_no_row():
    latest = {"quillfield:vp": {"kind": "scored", "score": 8, "reaction": REACTION}}
    v = hv.his_view(None, "quillfield:vp", None, latest=latest,
                    record={"why_it_fits": CASE})
    assert (v.score, v.reaction, v.ai_case, v.source) == (8, REACTION, CASE, hv.RECORD)
    unreadable = {"quillfield:vp": {"kind": "score_unreadable", "score_text": "7.5"}}
    assert hv.his_view(None, "quillfield:vp", None, latest=unreadable).empty


@pytest.mark.parametrize("k,m,why", [("", "", ""), ("", "", "Not assessed"),
                                     ("7.5", "", ""), ("8/10", "Duplicate", "")])
def test_empty_stays_empty(k, m, why):
    v = hv.his_view(None, "x", row(k=k, m=m, why=why or "Not assessed"))
    assert v.empty and v.score is None and v.reaction == "" and v.ai_case == ""
    assert v.fingerprint == ""


def test_a_yes_row_with_only_a_reaction_has_a_view():
    v = hv.his_view(None, "x", row(verdict="Yes", m=REACTION))
    assert not v.empty and v.score is None and v.reaction == REACTION


def test_a_record_that_cannot_be_read_is_no_view(monkeypatch):
    import hunter.ratings as ratings
    monkeypatch.setattr(ratings, "latest", lambda cfg: (_ for _ in ()).throw(RuntimeError()))
    assert hv.his_view(None, "x", None).empty


# ---------- 2. the tailor prompt ----------

COMPETENCIES = [f"Invented competency {i}" for i in range(11)]
BLOCKS = {
    "commercial_strategy": {"text": "[[COMPANY]] is [[JD_MIRROR]] and I have run that build.",
                            "default_mirror": "building its engine"},
    "gm_market_builder": {"text": "[[COMPANY]] is [[JD_MIRROR]], a market I have opened.",
                          "default_mirror": "opening a market"},
}


def golden_prompt(**extra):
    """The inputs tests/fixtures/tailor_prompt_no_view.txt was rendered from,
    by the code on origin/main before his view existed."""
    return tailor_mod._prompt(
        None, "Quillfield", "VP Strategy and Revenue",
        "Quillfield is hiring a VP to own revenue strategy across three regions. " * 6,
        COMPETENCIES, ["commercial_strategy", "gm_market_builder"], BLOCKS,
        highlights=["Opened a market", "Built a partner engine", "Ran a P&L"],
        evidence_view="invented evidence: opened 3 markets, ran a $9M p&l at fernwick.",
        voice_rules="- never write 'synergy'\n- never write 'leverage'",
        angle="", keep_verbatim=("opened three markets",), **extra)


def test_with_no_view_the_prompt_is_todays_byte_for_byte():
    today = (FIXTURES / "tailor_prompt_no_view.txt").read_text()
    assert golden_prompt() == today
    assert golden_prompt(his_view=hv.HisView()) == today
    assert golden_prompt(his_view=hv.his_view(None, "x", row(why=CASE))) == today, \
        "hunter's case alone is not his view: nothing he said, nothing added"


def test_the_prompt_carries_his_reaction_and_the_case():
    v = hv.his_view(None, "x", row(k="8", m=REACTION, why=CASE))
    p = golden_prompt(his_view=v)
    assert "## Why he wants this role, in his own words" in p
    assert REACTION in p and CASE in p
    assert "His score for this role: 8 out of 10" in p and "never mention it" in p
    section = p.split("## Why he wants this role")[1].split("## Hard rules on both pieces")[0]
    assert "[[" not in section, "every slot in the section, its markers included, is filled"
    assert "[[HIS_VIEW" not in p
    assert f"at most {tailor_mod.HOOK_MAX_CHARS} characters" in p
    # the rules travel with it
    for rule in ("never mention his score", "Never arrogant", "people he",
                 "Honest beats impressive", "first moves in the first weeks"):
        assert rule in p
    # and nothing else moved: the prompt is today's with the section added
    today = (FIXTURES / "tailor_prompt_no_view.txt").read_text()
    head = today.split("## Hard rules on both pieces")[0]
    assert p.startswith(head)
    assert p.endswith(today[len(head):])


def test_his_words_are_never_read_as_a_slot():
    v = hv.HisView(score=8, reaction="I would bring [[JD]] and [[COMPANY]] thinking")
    p = golden_prompt(his_view=v)
    assert "I would bring [[JD]] and [[COMPANY]] thinking" in p


# ---------- 3. the gate, end to end through tailor() ----------

EVIDENCE = voicegate.build_evidence(
    "Krish opened 3 markets for Fernwick and ran a $9M P&L there. He builds "
    "partner programmes that partners want to sell.")

SUMMARY_OK = (
    "Commercial operator who opens markets and builds the partner engine that keeps "
    "them open. At Fernwick he opened 3 markets and ran a $9M P&L, and he has since "
    "taken businesses into revenue on agents he builds himself. The first moves are the "
    "same every time: find the partners who already own the customer, price the "
    "motion so they want to sell it, and land the first deal within the first month. "
    "That is the work this seat needs at Quillfield.")
HOOK_OK = ("Quillfield is opening regions faster than its partners can sell it. I have "
           "built that partner engine before and would start on it in the first weeks.")


def tailor_world(monkeypatch, replies):
    """tailor() offline: a fake model that answers from `replies` in turn and
    records every prompt it was sent."""
    sent = []

    class Block:
        type = "text"

        def __init__(self, t):
            self.text = t

    class Usage:
        output_tokens = 10

    class Resp:
        stop_reason = "end_turn"
        usage = Usage()
        model = "m"

        def __init__(self, t):
            self.content = [Block(t)]

    class Messages:
        def create(self, **kw):
            sent.append(kw["messages"][0]["content"])
            return Resp(replies[min(len(sent), len(replies)) - 1])

    class Client:
        def __init__(self, **kw):
            self.messages = Messages()

    import types
    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    mod.APIError = RuntimeError
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    from hunter import llm, spend
    monkeypatch.setattr(spend, "over_budget", lambda cfg: None)
    monkeypatch.setattr(spend, "record", lambda *a, **kw: 0.0)
    monkeypatch.setattr(spend, "usage_of", lambda resp: {})
    monkeypatch.setattr(llm, "model_for", lambda cfg, purpose: ("m", ""))
    return sent


def reply(summary=SUMMARY_OK, hook=HOOK_OK):
    return json.dumps({"competency_order": COMPETENCIES, "letter_bullet_to_cut": 3,
                       "block_key": "commercial_strategy", "jd_mirror": "",
                       "hiring_lead": "Hiring Team", "highlight_order": [],
                       "summary": summary, "hook": hook})


def run_tailor(view, banned=()):
    return tailor_mod.tailor(
        Cfg({"hunter_anthropic_api_key": "k"}), None, company="Quillfield",
        title="VP Commercial Strategy", jd_text="Quillfield needs commercial strategy. " * 20,
        master_competencies=COMPETENCIES, letter_blocks=BLOCKS, evidence=EVIDENCE,
        banned_phrases=banned, his_view=view)


def test_prose_written_from_his_view_passes_the_gate(monkeypatch):
    sent = tailor_world(monkeypatch, [reply()])
    tr = run_tailor(hv.HisView(score=8, reaction=REACTION))
    assert tr.summary == SUMMARY_OK and tr.hook == HOOK_OK, tr.flags
    assert REACTION in sent[0][0]["text"]


def test_a_figure_only_his_note_gives_is_refused_and_not_quoted(monkeypatch):
    """His note is not evidence. A figure he gives about himself and his CV
    does not carry is refused like any invention, and the failure says what
    kind of thing it was without the figure: flags reach the run summary."""
    noted = SUMMARY_OK.replace("into revenue", "past $4.2M")
    tailor_world(monkeypatch, [reply(summary=noted)])
    tr = run_tailor(hv.HisView(score=8, reaction=REACTION))
    assert tr.summary == "" and tr.hook == ""
    flags = "; ".join(tr.flags)
    assert hv.FIGURE_LEFT_OUT in flags
    assert "4.2" not in flags


def test_without_his_note_the_same_figure_is_refused_by_name(monkeypatch):
    noted = SUMMARY_OK.replace("into revenue", "past $4.2M")
    tailor_world(monkeypatch, [reply(summary=noted)])
    tr = run_tailor(hv.HisView(score=8))
    assert tr.summary == "" and any("$4.2m" in f for f in tr.flags)


def test_a_number_in_neither_his_record_nor_his_note_is_still_refused(monkeypatch):
    """Refused all the same, and, with a reaction of his on the row, not
    quoted either: the gate cannot tell which other form of his note's
    figure the model wrote, so no refused figure is printed at all. The
    retry message, which is not printed, names it."""
    bad = SUMMARY_OK.replace("into revenue", "past $7.7M")
    sent = tailor_world(monkeypatch, [reply(summary=bad)])
    tr = run_tailor(hv.HisView(score=8, reaction=REACTION))
    assert tr.summary == "" and hv.FIGURE_LEFT_OUT in "; ".join(tr.flags)
    assert not any("7.7" in f for f in tr.flags)
    assert "$7.7m" in sent[1][1]["text"], "the model is told what it got wrong"
    assert tr.hook == ""   # the whole generated pair falls back together


# Invented: an offer elsewhere, the pay he wants, a person he knows and the
# employer they work for, as a note to himself might carry them.
PRIVATE_NOTE = ("Corvantis offered me $310K base last week, and I want at least "
                "$280K here. Mirela Ostrander runs revenue there and asked me to apply.")
PRIVATE_WORDS = ("310", "280", "Corvantis", "Mirela", "Ostrander")


@pytest.mark.parametrize("what,sentence", [
    ("an offer elsewhere", "He turned down a $310K offer to do it. "),
    ("the pay he wants", "At a $280K base the motion pays for itself. "),
    ("a person he knows", "Mirela Ostrander has seen him build it. "),
    ("another employer", "He has watched the same motion stall at Corvantis. "),
])
def test_nothing_only_his_note_carries_reaches_the_letter(monkeypatch, what, sentence):
    """The prompt says leave these out; the gate is what holds the model to
    it. Each is refused, and no word of his note is in what is reported."""
    leak = SUMMARY_OK.replace("That is the work", sentence + "That is the work")
    tailor_world(monkeypatch, [reply(summary=leak)])
    tr = run_tailor(hv.HisView(score=9, reaction=PRIVATE_NOTE))
    assert tr.summary == "", what
    flags = "; ".join(tr.flags)
    assert "the evidence does not support was left out" in flags, what
    for word in PRIVATE_WORDS:
        assert word.lower() not in flags.lower(), (what, word)


def test_with_a_reaction_no_refused_name_or_figure_is_quoted():
    """With a reaction of his, every refused name and figure is printed in
    its generic form, his or not: a shortened name or a reworded figure of
    his note does not match it. With no reaction, the failures stand."""
    view = hv.HisView(score=9, reaction=PRIVATE_NOTE)
    fails = ["name 'Zentrox' is not in the recorded evidence",
             "name 'Ostrander' is not in the recorded evidence",
             "number '$310k' is not in the recorded evidence",
             "too short (9 chars, min 350)"]
    assert hv.redact(fails, view) == [hv.NAME_LEFT_OUT, hv.FIGURE_LEFT_OUT,
                                      "too short (9 chars, min 350)"]
    assert hv.redact(fails, hv.HisView(score=9)) == fails
    assert hv.redact(fails, None) == fails


def test_his_note_copied_word_for_word_is_refused_without_quoting_it(monkeypatch):
    copied = SUMMARY_OK.replace(
        "That is the work",
        "This seat is the partner engine I keep building. That is the work")
    tailor_world(monkeypatch, [reply(summary=copied)])
    tr = run_tailor(hv.HisView(score=8, reaction=REACTION))
    assert tr.summary == ""
    assert any("copies his note" in f for f in tr.flags)
    assert not any("partner engine I keep" in f for f in tr.flags)


def test_the_arrogance_phrases_are_named_in_the_prompt_and_refused_by_the_gate(monkeypatch):
    banned = voicegate.package_banned(("synergy",))
    for phrase in ("perfect fit", "no-brainer", "second to none"):
        assert phrase in voicegate.ARROGANCE_BANNED and phrase in banned
    boast = SUMMARY_OK.replace("That is the work", "Frankly he is a perfect fit. "
                               "That is the work")
    sent = tailor_world(monkeypatch, [reply(summary=boast)])
    tr = run_tailor(hv.HisView(score=8, reaction=REACTION), banned=banned)
    prompt = sent[0][0]["text"]
    for phrase in voicegate.ARROGANCE_BANNED:
        assert f"- never write {phrase!r}" in prompt
    assert tr.summary == ""
    assert any("banned phrase 'perfect fit'" in f for f in tr.flags)


def test_the_gate_refuses_arrogance_on_its_own():
    v = voicegate.check("Taking this seat is a no-brainer, honestly.",
                        evidence=EVIDENCE, banned_phrases=voicegate.package_banned())
    assert not v.ok and any("no-brainer" in f for f in v.failures)


def test_build_one_reads_his_view_and_the_one_banned_list(monkeypatch, capsys):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="8", m=REACTION)))
    _stub_build(monkeypatch)
    seen = {}

    def tailor(*a, **kw):
        seen.update(kw)
        return type("TR", (), {"block_key": "b", "flags": [], "summary": "s", "hook": ""})()
    monkeypatch.setattr(tailor_mod, "tailor", tailor)
    summary = []
    row_ = {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
            "title": "Chief of Staff", "url": url, "status": "staging",
            "build_trigger": "Krish Score 8", "krish_score": 8, "attempt": "a" * 16}
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row_,
                             summary, cache={}) is True
    assert seen["his_view"].reaction == REACTION and seen["his_view"].score == 8
    assert set(voicegate.ARROGANCE_BANNED) <= set(seen["banned_phrases"])
    assert not any(MARK in x for x in summary), "his words never reach the run summary"
    printed = capsys.readouterr()
    assert MARK not in printed.out and MARK not in printed.err, "nor the public log"


# ---------- 4. what the record says the package carries ----------

def _build(monkeypatch, *, k="8", m=REACTION, tr_summary="s", rebuild=False,
           verify_fails=0, field_fails=False, made=None, flags=("gate said no",),
           sheet_cls=None):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = (sheet_cls or FakeSheet)(grid_of(line("New", "Ostravia", "Chief of Staff", url,
                                              k=k, m=m)))
    rec = _stub_build(monkeypatch)
    rec["verify_fails"] = verify_fails
    if made is not None:
        import hunter.package.build as build_mod
        real = build_mod.build_package

        def counting(*a, **kw):
            made.append(1)
            return real(*a, **kw)
        monkeypatch.setattr(build_mod, "build_package", counting)

    def tailor(*a, **kw):
        rec["tailored"] += 1
        return type("TR", (), {"block_key": "b", "flags": list(flags),
                               "summary": tr_summary, "hook": ""})()
    monkeypatch.setattr(tailor_mod, "tailor", tailor)
    if field_fails:
        real = run_mod.db_patch

        def patch(cfg, table, match, values):
            if hv.FIELD in values:
                raise RuntimeError("400: column does not exist")
            return real(cfg, table, match, values)
        monkeypatch.setattr(run_mod, "db_patch", patch)
    summary = []
    row_ = {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
            "title": "Chief of Staff", "url": url, "status": "staging",
            "package_status": "built", "package_trigger": "Krish Score 8",
            "build_trigger": f"Krish Score {k}" if k else "column A Yes",
            "krish_score": int(k) if k else None,
            "package_built_at": "2026-10-11T08:00:00Z", "sheet_link": url, "sheet_row": 3}
    ok = run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row_,
                           summary, cache={}, rebuild=rebuild)
    return ok, rec, summary, s


def test_a_package_written_from_his_view_records_its_fingerprint(monkeypatch):
    ok, rec, summary, _ = _build(monkeypatch)
    assert ok
    marks = [v[hv.FIELD] for _, _, v in rec["patches"] if hv.FIELD in v]
    want = hv.his_view(None, "x", row(k="8", m=REACTION,
                                      company="Ostravia", role="Chief of Staff")).fingerprint
    assert marks == [want] and want


def test_a_package_whose_prose_fell_back_records_none(monkeypatch):
    made = []
    ok, rec, _, _ = _build(monkeypatch, tr_summary="", made=made)
    assert ok and made == [1], "an ordinary build still builds on the blocks"
    assert [v[hv.FIELD] for _, _, v in rec["patches"] if hv.FIELD in v] == [None]


def test_without_the_column_the_build_still_stands_and_says_so(monkeypatch):
    ok, rec, summary, _ = _build(monkeypatch, field_fails=True)
    assert ok
    assert any(v.get("package_status") == "built" for _, _, v in rec["patches"])
    assert any("was not recorded" in x for x in summary)


def test_a_rebuild_whose_prose_did_not_come_through_makes_no_document(monkeypatch):
    made = []
    ok, rec, summary, _ = _build(monkeypatch, tr_summary="", rebuild=True, made=made)
    assert ok is False and made == []
    assert any(x.startswith("HELD ostravia:chief-of-staff") for x in summary)
    assert not any("package_status" in v for _, _, v in rec["patches"]), \
        "the package built before stays recorded as it was"


def test_a_rebuild_that_fails_verification_leaves_the_package_built(monkeypatch):
    ok, rec, summary, _ = _build(monkeypatch, rebuild=True, verify_fails=1)
    assert ok is False
    assert any(x.startswith("REBUILD FAILED ostravia:chief-of-staff") for x in summary)
    assert not any(v.get("package_status") == "blocked" for _, _, v in rec["patches"])


def test_a_rebuild_the_gate_held_is_recorded_as_tried(monkeypatch):
    from hunter.package.tailor import GATE_FELL_BACK
    ok, rec, summary, _ = _build(monkeypatch, tr_summary="", rebuild=True,
                                 flags=("summary rejected: too long", GATE_FELL_BACK))
    assert ok is False
    marks = [v[hv.FIELD] for _, _, v in rec["patches"] if hv.FIELD in v]
    view = hv.his_view(None, "x", row(k="8", m=REACTION, company="Ostravia",
                                      role="Chief of Staff"))
    assert marks == [hv.held_mark(view.fingerprint, "2026-10-11T08:00:00Z",
                                  "https://job-boards.greenhouse.io/ostravia/jobs/7")]
    assert not any("not recorded" in x or "did not read back" in x for x in summary)


def test_a_rebuild_held_for_a_missing_key_is_tried_again(monkeypatch):
    """A spent month or a missing key says nothing about the inputs."""
    ok, rec, _, _ = _build(monkeypatch, tr_summary="", rebuild=True,
                           flags=("hunter_anthropic_api_key missing from system_config",))
    assert ok is False
    assert not any(hv.FIELD in v for _, _, v in rec["patches"])


def test_a_rebuild_that_failed_verification_is_recorded_as_tried(monkeypatch):
    ok, rec, _, _ = _build(monkeypatch, rebuild=True, verify_fails=1)
    assert ok is False
    marks = [v[hv.FIELD] for _, _, v in rec["patches"] if hv.FIELD in v]
    assert len(marks) == 1 and marks[0].startswith(hv.HELD)


class LandsThenFails(FakeSheet):
    """The four links land on the row and the read-back raises (a Sheets 429)."""

    def update_package_cells(self, row_number, **kw):
        super().update_package_cells(row_number, **kw)
        raise sheet_mod.SheetError("read back failed")


class FailsBeforeWriting(FakeSheet):
    def update_package_cells(self, row_number, **kw):
        raise sheet_mod.SheetError("429")


@pytest.mark.parametrize("sheet_cls,landed", [(LandsThenFails, True),
                                              (FailsBeforeWriting, False)])
def test_a_failed_row_write_records_nothing_and_keeps_the_baseline_true(
        monkeypatch, sheet_cls, landed):
    """Which of his reasons a package carries is recorded only once his row
    reads back with its links, so a failed write is tried again. And what
    amend reads his edits against is the text of the documents the row links
    now: hunter's new letter where the write landed, the old baseline where
    it did not."""
    saved, recs = [], []
    _stub = _stub_build

    def stub(mp, **kw):
        rec = _stub(mp, **kw)
        recs.append(rec)
        mp.setattr(run_mod.amend, "save_docs", lambda cfg, jid, **t: saved.append(t))
        mp.setattr(run_mod, "doc_plain_text", lambda docs, doc_id: f"text of {doc_id}")
        return rec
    monkeypatch.setattr(sys.modules[__name__], "_stub_build", stub)
    with pytest.raises(sheet_mod.SheetError):
        _build(monkeypatch, sheet_cls=sheet_cls)
    assert saved == ([{"letter_text": "text of L1", "cv_text": "text of V1"}] if landed
                     else [])
    assert not any(hv.FIELD in v for _, _, v in recs[0]["patches"]), \
        "not recorded as carrying his view: the next run tries it again"


def test_the_new_links_are_read_back_off_the_row():
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"

    class Dropping(FakeSheet):
        def _post(self, path, body):
            if path == "/values:batchUpdate":
                for block in body["data"]:
                    if block["range"].split("!")[1].startswith(sheet_mod.col_letter(C["CV Doc"])):
                        block["values"][0][0] = "Not built"
            return super()._post(path, body)

    links = dict(cv_url="https://docs.example/cv", letter_url="https://docs.example/cl",
                 cv_pdf_url="https://docs.example/cvp", letter_pdf_url="https://docs.example/clp",
                 package_status=sheet_mod.PKG_BUILT_DIRECT, built_date="2026-10-14")
    FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url))).update_package_cells(3, **links)
    with pytest.raises(sheet_mod.SheetError, match="did not read back"):
        Dropping(grid_of(line("New", "Ostravia", "Chief of Staff", url))).update_package_cells(
            3, **links)


# ---------- 5. essays ----------

def essay_world(monkeypatch, answer):
    class Block:
        type = "text"

        def __init__(self, t):
            self.text = t

    class Resp:
        def __init__(self, t):
            self.content = [Block(t)]

    class Messages:
        seen = []

        def create(self, **kw):
            Messages.seen.append(kw)
            return Resp(json.dumps({"answer": answer}))

    class Client:
        def __init__(self, **kw):
            self.messages = Messages()

    import types
    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    from hunter import spend
    monkeypatch.setattr(spend, "over_budget", lambda cfg: None)
    return Messages


def system_text(call: dict) -> str:
    system = call["system"]
    if isinstance(system, list):
        return "".join(b.get("text", "") for b in system)
    return system


class EssayCfg:
    def optional(self, name, default=None):
        return "k" if name == "hunter_anthropic_api_key" else (default or "claude-opus-5")


ANSWER = ("At Fernwick I opened 3 markets and ran a $9M P&L, and since then I have taken "
          "businesses into revenue on agents I build myself. Here the first move is the "
          "partner engine: find the partners who already own the customer and price the "
          "motion so they want to sell it, within the first month.")


def test_the_essays_get_the_same_section(monkeypatch):
    from hunter.apply import essays
    seen = essay_world(monkeypatch, ANSWER)
    v = hv.HisView(score=8, reaction=REACTION, ai_case=CASE)
    got, why = essays.draft_one(EssayCfg(), question="Why us?", company="Quillfield",
                                role="VP", jd_text="jd", evidence=EVIDENCE, his_view=v)
    assert why == "" and got == ANSWER
    system = system_text(seen.seen[0])
    assert "Why he wants this role, in his own words" in system
    assert REACTION in system and CASE in system and "Quillfield" in system
    assert "[[" not in system
    assert system.index("Why he wants") < system.index("EVIDENCE (everything")


def test_essays_without_a_view_are_as_they_were(monkeypatch):
    from hunter.apply import essays
    seen = essay_world(monkeypatch, ANSWER)
    got, why = essays.draft_one(EssayCfg(), question="Why us?", company="Quillfield",
                                role="VP", jd_text="jd", evidence=EVIDENCE,
                                his_view=hv.HisView())
    assert got == ANSWER and why == ""
    want = essays.SYSTEM + f"\n\nEVIDENCE (everything you may draw on):\n{EVIDENCE[:120000]}"
    assert system_text(seen.seen[0]) == want, "byte for byte the system text of before"


def test_an_essay_copying_his_note_is_refused_without_quoting_it(monkeypatch):
    from hunter.apply import essays
    copied = ANSWER + " This seat is the partner engine I keep building."
    essay_world(monkeypatch, copied)
    got, why = essays.draft_one(EssayCfg(), question="Why us?", company="Quillfield",
                                role="VP", jd_text="jd", evidence=EVIDENCE,
                                his_view=hv.HisView(score=8, reaction=REACTION))
    assert got == "" and "copies his note" in why and "keep building" not in why


def test_an_essay_carrying_what_only_his_note_says_is_refused_without_quoting_it(
        monkeypatch):
    from hunter.apply import essays
    essay_world(monkeypatch, ANSWER + " Mirela Ostrander asked me to apply at $280K.")
    got, why = essays.draft_one(EssayCfg(), question="Why us?", company="Quillfield",
                                role="VP", jd_text="jd", evidence=EVIDENCE,
                                his_view=hv.HisView(score=8, reaction=PRIVATE_NOTE))
    assert got == "" and "the evidence does not support was left out" in why
    for word in PRIVATE_WORDS:
        assert word.lower() not in why.lower(), word


def test_an_unreadable_reply_is_reported_without_its_words():
    """The reply can carry his note: the model has it. A reason is printed in
    the approval run, so it says how long the reply was and nothing it said."""
    from hunter.apply import essays
    raw = ('{"answer": "As I put it, "this seat is the partner engine I keep '
           'building" and I would start on it in the first weeks."}')
    got, why = essays._parse(raw)
    assert got == "" and "could not find an answer" in why
    assert "partner engine" not in why and "keep building" not in why


def test_the_section_carries_his_rules_for_every_piece():
    """The wording the section must carry, pinned: the essays read it as well
    as the letter, so the rules the letter's hard rules hold sit inside it."""
    v = hv.HisView(score=8, reaction=REACTION)
    for text in (golden_prompt(his_view=v), hv.section(v, company="Quillfield")):
        flat = " ".join(text.split())
        for rule in (
                'nothing like "a perfect fit", "a no-brainer" or "second to none"',
                "Never say or imply he is looking for work or free to start; he is "
                "choosing this seat while running an active portfolio.",
                "Never carry from his note anything about where he wants to live or "
                "work, pay, other offers, people he knows, or the pedigree of anyone "
                "at Quillfield. Where an application question itself asks about "
                "location or pay, answer only from the evidence.",
                "Every number and every name you write still comes from the evidence "
                "below. If his note gives a figure, a product or a name the evidence "
                "does not, leave it out. Never name a person from his note at all.",
                "US spelling, as in his CV and letter."):
            assert rule in flat, rule
        assert "checked against his note" not in flat
        assert "anyone's pedigree" not in flat


def test_the_approval_path_drafts_its_essays_from_his_view(monkeypatch):
    from hunter.apply import essays, infobank
    import hunter.docbuild as docbuild
    import hunter.package.build as build_mod
    got = {}
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type(
        "O", (), {"access_token": lambda self=None: "t"})())
    monkeypatch.setattr(docbuild, "DocBuild", lambda tok: object())
    monkeypatch.setattr(build_mod, "read_master_facts",
                        lambda db: type("F", (), {"cv_master_text": "krish opened 3 markets"})())
    bank = type("B", (), {"profile": {}, "interview": {}, "entries": {},
                          "banned_phrases": ("synergy",)})()
    monkeypatch.setattr(infobank, "load_bank", lambda read: bank)

    def draft_all(cfg, questions, **kw):
        got.update(kw)
        return {}
    monkeypatch.setattr(essays, "draft_all", draft_all)
    role = type("R", (), {"company": "Quillfield", "title": "VP", "jd_text": "jd"})()
    v = hv.HisView(score=9, reaction=REACTION)
    run_mod._draft_essays(Cfg(), None, role, ["Why us?"], [], view=v)
    assert got["his_view"] is v
    assert set(voicegate.ARROGANCE_BANNED) <= set(got["banned_phrases"])


# ---------- 6. rebuild-packages: which packages ----------

class FakeDb:
    """hunter_seen_roles and the approvals ledger, filtered the way PostgREST
    would for the handful of operators these paths use."""

    def __init__(self, roles, tokens=(), view_column=True):
        self.tables = {"hunter_seen_roles": [dict(r) for r in roles],
                       "hunter_application_approvals": [dict(t) for t in tokens],
                       "hunter_row_state": []}
        self.view_column = view_column
        self.patches = []
        # The Google Docs he can open: link -> what the document says now.
        self.documents: dict[str, str] = {}
        self.reads: list[str] = []
        # Read in full only by load_state, which a rebuild must not call: the
        # table holds every letter and CV hunter ever wrote.
        self.full_state_reads = 0

    def read_doc(self, url):
        self.reads.append(url)
        return self.documents.get(url, "")

    def insert(self, cfg, table, rows, on_conflict="", ignore_duplicates=False,
               merge=False, **kw):
        t = self.tables.setdefault(table, [])
        keys = [k for k in on_conflict.split(",") if k]
        for r in rows:
            same = [x for x in t if keys and all(x.get(k) == r.get(k) for k in keys)]
            if same and ignore_duplicates:
                continue
            if same and merge:
                same[0].update(r)
                continue
            t.append(dict(r, id=len(t) + 1))

    @staticmethod
    def _match(r, key, cond):
        v = r.get(key)
        if cond.startswith("eq."):
            return str(v) == cond[3:]
        if cond.startswith("neq."):
            return str(v) != cond[4:]
        if cond.startswith("in.("):
            return str(v) in cond[4:-1].split(",")
        if cond == "not.is.null":
            return v is not None
        if cond.startswith("like."):
            return str(v or "").startswith(cond[5:].rstrip("*"))
        raise NotImplementedError(cond)

    def get(self, cfg, table, params):
        if (table == "hunter_seen_roles" and not self.view_column
                and hv.FIELD in params.get("select", "")):
            raise RuntimeError("42703 column does not exist")
        if table == "hunter_row_state" and "job_id" not in params:
            self.full_state_reads += 1
        out = []
        for r in self.tables.get(table, []):
            if all(self._match(r, k, c) for k, c in params.items()
                   if k not in ("select", "limit", "order")):
                out.append(dict(r))
        return out


GH = "https://job-boards.greenhouse.io/{}/jobs/{}"


def doc_links(slug):
    return {"package_cv_url": f"https://docs.google.com/document/d/{slug}-cv-0123456789abc/edit",
            "package_letter_url": f"https://docs.google.com/document/d/{slug}-cl-0123456789abc/edit"}


def rec(slug, n=1, **extra):
    return dict({"job_id": f"{slug}:vp", "company": slug.title(), "title": "VP Partnerships",
                 "url": GH.format(slug, n), "package_status": "built",
                 "package_trigger": "Krish Score 8", "status": "staging",
                 "presented_at": "2026-10-10", "package_built_at": "2026-10-11T08:00:00Z",
                 **doc_links(slug)}, **extra)


def rebuild_world(monkeypatch, lines, roles, tokens=(), view_column=True, *,
                  edited=(), unreadable=(), no_baseline=()):
    """Every built package has its two documents, and hunter_row_state holds
    what hunter wrote into them, unless the test says otherwise: `edited`
    roles' letters carry a paragraph of his, `unreadable` ones read as
    nothing, and `no_baseline` ones have no record of what hunter wrote."""
    s = FakeSheet(grid_of(*lines))
    db = FakeDb(roles, tokens, view_column=view_column)
    for r in roles:
        jid = r["job_id"]
        if jid not in no_baseline:
            db.tables["hunter_row_state"].append(
                {"job_id": jid, "letter_text": f"Letter hunter wrote for {jid}.",
                 "cv_text": f"CV hunter wrote for {jid}."})
        letter = f"Letter hunter wrote for {jid}."
        if jid in edited:
            letter += " A paragraph he wrote himself."
        db.documents.setdefault(r["package_letter_url"], "" if jid in unreadable else letter)
        db.documents.setdefault(r["package_cv_url"],
                                "" if jid in unreadable else f"CV hunter wrote for {jid}.")
    monkeypatch.setattr(run_mod, "db_get", db.get)
    monkeypatch.setattr(run_mod, "db_insert", db.insert)
    monkeypatch.setattr(run_mod, "_safe_doc_reader", lambda cfg: db.read_doc)
    return s, db


def target_ids(s, **kw):
    rep = {}
    got = run_mod.rebuild_targets(Cfg(), s, FakeCanon(), report=rep, **kw)
    return [d["job_id"] for d in got], rep


def L(slug, k="8", verdict="New", n=1, **over):
    return line(verdict, slug.title(), "VP Partnerships", GH.format(slug, n), k=k, **over)


def test_rebuild_picks_only_unacted_packages_built_without_his_view(monkeypatch):
    lines = [L("ardent"), L("brisk", verdict="Yes", k="9"), L("cobalt"),
             L("dune", verdict="Applied", k="9"), L("ember", **{"Application Status": "Applied"}),
             L("fable"), L("grove"), L("harbor"), L("isle"), L("jolt"), L("kite"),
             L("lumen", **{"Package Status": sheet_mod.PKG_DEAD}), L("moss", k="6"),
             L("nimbus", verdict="Declined - company", k="9"), L("orbit", verdict="Yes", k=""),
             L("pike"), L("pike", n=1), L("quarry"),
             L("rill", verdict="Yes", k="", m=REACTION), L("sable", verdict="Yes", k="5", m=REACTION),
             L("tarn"), L("umber"), L("vale")]
    roles = [rec("ardent"), rec("brisk", package_trigger="column A Yes"),
             rec("cobalt", **{hv.FIELD: "v1:" + "a" * 16}), rec("dune"), rec("ember"),
             rec("fable", application_state="Applied"), rec("grove"), rec("harbor"),
             rec("isle"), rec("jolt"), rec("kite", package_status="blocked"),
             rec("lumen"), rec("moss"), rec("nimbus"), rec("orbit"),
             rec("pike"), dict(rec("pike"), job_id="pike:vp-old"), rec("quarry"),
             rec("rill", package_trigger="column A Yes"),
             rec("sable", package_trigger="column A Yes"),
             rec("tarn"), rec("umber"), rec("vale")]
    tokens = [{"token": "t1", "job_id": "grove:vp", "state": "cancelled",
               "opened_at": "2026-10-13T10:00:00Z", "sent_at": "2026-10-12"},
              {"token": "t2", "job_id": "harbor:vp", "state": "approved", "sent_at": "x"},
              {"token": "t3", "job_id": "isle:vp", "state": "awaiting",
               "sent_at": "2026-10-13"},
              {"token": "t4", "job_id": "jolt:vp", "state": "submitted", "sent_at": "x"},
              # He replied AMEND twice: approvals-drain cancelled each token
              # and kept his words on it. Nothing records an AMEND as a state.
              {"token": "t5", "job_id": "quarry:vp", "state": "cancelled", "sent_at": "x",
               "decided_at": "2026-10-12T09:00:00Z", "feedback": "Newer invented amend words."},
              {"token": "t6", "job_id": "quarry:vp", "state": "cancelled", "sent_at": "x",
               "decided_at": "2026-10-09T09:00:00Z", "feedback": "Older invented amend words."}]
    s, db = rebuild_world(monkeypatch, lines, roles, tokens, edited=("tarn:vp",),
                          no_baseline=("umber:vp",), unreadable=("vale:vp",))
    rep = {}
    got = run_mod.rebuild_targets(Cfg(), s, FakeCanon(), report=rep)
    ids = [d["job_id"] for d in got]
    # A Yes he answered with only a reaction (rill) is one his score took
    # back (his ruling 2026-10-13, "My score wins"): it leaves Pipeline and is
    # not rebuilt. A Yes with nothing of his (orbit) has nothing to add, and a
    # role he scored 6 (moss) he never wanted.
    # A package he amended is rebuilt with his newest amend in it; one whose
    # letter he edited, or that cannot be read against what hunter wrote, is not.
    assert ids == ["brisk:vp", "ardent:vp", "pike:vp", "quarry:vp"], ids
    assert rep["skips"] == {
        "carries his view": 1, "column A Applied": 1, "application status": 1,
        "applied on record": 1, "form opened": 1, "approved": 1,
        "awaiting, email sent": 1, "submitted": 1, "not built yet": 1,
        "posting dead": 1, "not wanted now": 3, "same posting": 1,
        run_mod.DOCS_EDITED: 1, run_mod.DOCS_NO_BASELINE: 1, run_mod.DOCS_UNREAD: 1}
    assert rep["view_column"] is True and rep["with_amend"] == 1
    feedback = {d["job_id"]: d["amend_feedback"] for d in got}
    assert feedback["quarry:vp"] == "Newer invented amend words."
    assert feedback["ardent:vp"] == ""
    assert db.full_state_reads == 0, "hunter_row_state is read by id, never whole"


def test_rebuild_job_id_names_one_target(monkeypatch):
    """Named, a package already written from his view is rebuilt from the view
    he holds now; one he has acted on still is not."""
    carried = {hv.FIELD: "v1:" + "b" * 16}
    s, _ = rebuild_world(monkeypatch, [L("ardent"), L("brisk"), L("cobalt")],
                         [rec("ardent"), rec("brisk", **carried),
                          rec("cobalt", application_state="Applied", **carried)])
    assert target_ids(s, job_id="brisk:vp")[0] == ["brisk:vp"]
    assert target_ids(s, job_id="cobalt:vp")[0] == []
    assert target_ids(s)[0] == ["ardent:vp"]


def test_without_the_column_every_built_package_counts_as_without_his_view(monkeypatch):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")], view_column=False)
    ids, rep = target_ids(s)
    assert ids == ["ardent:vp"] and rep["view_column"] is False


def test_rebuild_refuses_to_pay_without_the_column(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")], view_column=False)
    _commands(monkeypatch, s)
    monkeypatch.setattr(run_mod, "build_one", lambda *a, **kw: pytest.fail("paid"))
    assert run_mod.cmd_rebuild_packages(apply=True) == 1
    assert "REFUSING" in capsys.readouterr().out


def test_a_form_opened_after_the_selection_stops_that_rebuild(monkeypatch, capsys):
    s, db = rebuild_world(monkeypatch, [L("ardent"), L("brisk")], [rec("ardent"), rec("brisk")])
    _commands(monkeypatch, s)
    built = []

    def build_one(cfg, canon, sheet, row, detail, **kw):
        assert kw.get("rebuild") is True
        built.append(row["job_id"])
        # his extension opens the brisk form while ardent is being rebuilt
        db.tables["hunter_application_approvals"].append(
            {"token": "late", "job_id": "brisk:vp", "state": "awaiting",
             "opened_at": "2026-10-14T09:00:00Z", "sent_at": "2026-10-14"})
        detail.append(f"BUILT {row['job_id']}")
        return True
    monkeypatch.setattr(run_mod, "build_one", build_one)
    assert run_mod.cmd_rebuild_packages(apply=True) == 0
    assert built == ["ardent:vp"]
    assert "1 acted on since it was chosen" in capsys.readouterr().out


def test_a_letter_he_edits_after_the_selection_stops_that_rebuild(monkeypatch, capsys):
    s, db = rebuild_world(monkeypatch, [L("ardent"), L("brisk")], [rec("ardent"), rec("brisk")])
    _commands(monkeypatch, s)
    built = []

    def build_one(cfg, canon, sheet, row, detail, **kw):
        built.append(row["job_id"])
        # he opens the brisk letter from his row and rewrites a paragraph
        db.documents[doc_links("brisk")["package_letter_url"]] += " His own words."
        return True
    monkeypatch.setattr(run_mod, "build_one", build_one)
    assert run_mod.cmd_rebuild_packages(apply=True) == 0
    assert built == ["ardent:vp"]
    assert "1 documents changed since it was chosen" in capsys.readouterr().out


def test_a_rebuild_carries_his_amend_instructions(monkeypatch, capsys):
    tokens = [{"token": "t1", "job_id": "ardent:vp", "state": "cancelled", "sent_at": "x",
               "decided_at": "2026-10-12T09:00:00Z", "feedback": "Invented: lead on partners."}]
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")], tokens)
    _commands(monkeypatch, s)
    seen = {}

    def build_one(cfg, canon, sheet, row, detail, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(run_mod, "build_one", build_one)
    assert run_mod.cmd_rebuild_packages(apply=True) == 0
    assert seen["feedback"] == "Invented: lead on partners." and seen["rebuild"] is True
    out = capsys.readouterr().out
    assert "1 of them carry his AMEND instructions" in out
    assert "lead on partners" not in out, "his amend words are never printed"


def test_a_held_rebuild_is_not_paid_for_again_until_something_changes(monkeypatch):
    k8 = L("ardent", m=REACTION)
    view = hv.his_view(None, "x", row(k="8", m=REACTION, company="Ardent",
                                      role="VP Partnerships", url=GH.format("ardent", 1)))
    held = hv.held_mark(view.fingerprint, "2026-10-11T08:00:00Z", GH.format("ardent", 1))
    s, _ = rebuild_world(monkeypatch, [k8], [rec("ardent", **{hv.FIELD: held})])
    ids, rep = target_ids(s)
    assert ids == [] and rep["skips"] == {"held before, nothing changed since": 1}
    # a new reaction is a new try, and so is --job-id
    s2, _ = rebuild_world(monkeypatch, [L("ardent", m="Different invented words")],
                          [rec("ardent", **{hv.FIELD: held})])
    assert target_ids(s2)[0] == ["ardent:vp"]
    s3, _ = rebuild_world(monkeypatch, [k8], [rec("ardent", **{hv.FIELD: held})])
    assert target_ids(s3, job_id="ardent:vp")[0] == ["ardent:vp"]
    # and a package rebuilt since is a different package
    s4, _ = rebuild_world(monkeypatch, [k8], [rec("ardent", package_built_at="2026-10-14",
                                                  **{hv.FIELD: held})])
    assert target_ids(s4)[0] == ["ardent:vp"]


# ---------- 7. approvals --wanted: which applications ----------

def test_prepare_all_picks_one_built_package_per_posting_he_wants(monkeypatch):
    lines = [L("ardent"), L("brisk", verdict="Yes", k=""), L("cobalt", verdict="Yes", k="5"),
             L("dune", verdict="Yes", k="7.5"), L("ember"), L("fable"),
             L("grove", **{"Package Status": sheet_mod.PKG_DEAD}), L("harbor"),
             L("isle", **{"Application Status": "Applied"}), L("jolt"), L("jolt", n=1),
             L("kite", k="6")]
    roles = [rec("ardent"), rec("brisk", package_trigger="column A Yes"),
             rec("cobalt"), rec("dune"), rec("ember"),
             dict(rec("fable"), url="", job_url=""), rec("grove"),
             rec("harbor", package_status="none"), rec("isle"), rec("jolt"),
             dict(rec("jolt"), job_id="jolt:vp-old"), rec("kite"),
             rec("zephyr"), rec("yarrow")]   # built, rows gone from Pipeline
    tokens = [{"token": "t1", "job_id": "ember:vp", "state": "awaiting", "sent_at": "x"},
              {"token": "t2", "job_id": "fable:vp", "state": "approved", "sent_at": "x"}]
    s, _ = rebuild_world(monkeypatch, lines, roles, tokens)
    rep = {}
    got = run_mod.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep)
    assert [d["job_id"] for d in got] == ["brisk:vp", "ardent:vp", "jolt:vp"]
    assert all(d.get("package_status") == "built" for d in got), "the full record"
    assert rep["skips"] == {
        "Yes, scored below 7 or unreadable": 2, "built, off Pipeline": 2,
        "approval already waiting or approved": 2, "posting dead": 1,
        "not built yet": 1, "applied": 1, "same posting": 1}


def test_prepare_all_sends_each_posting_once_and_prints_counts_only(monkeypatch, capsys):
    lines = [L("ardent", m=REACTION), L("brisk", verdict="Yes", m=REACTION),
             L("jolt", m=REACTION), L("jolt", n=1, m=REACTION)]
    roles = [rec("ardent"), rec("brisk"), rec("jolt"), dict(rec("jolt"), job_id="jolt:vp-old")]
    s, _ = rebuild_world(monkeypatch, lines, roles)
    _commands(monkeypatch, s)
    calls = []

    def approvals(apply=False, job_id="", prefill=True, *, chosen=None, tally=None):
        assert apply and len(chosen) == 1
        calls.append(chosen[0]["job_id"])
        # what the real one prints: the application itself
        print(f"Apply: {chosen[0]['company']} VP Partnerships\n{REACTION}")
        tally["sent"] = tally.get("sent", 0) + 1
        return 0
    monkeypatch.setattr(run_mod, "cmd_approvals", approvals)
    assert run_mod.cmd_approvals_wanted(apply=True) == 0
    assert sorted(calls) == ["ardent:vp", "brisk:vp", "jolt:vp"]
    out = capsys.readouterr().out
    assert "prepare: outcome: 3 sent" in out
    for leak in (MARK, "Ardent", "Brisk", "Jolt", "ardent:vp", "jolt:vp"):
        assert leak not in out, leak


def test_prepare_all_dry_run_prepares_nothing(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")])
    _commands(monkeypatch, s)
    monkeypatch.setattr(run_mod, "cmd_approvals", lambda *a, **kw: pytest.fail("prepared"))
    assert run_mod.cmd_approvals_wanted(apply=False) == 0
    assert "dry run" in capsys.readouterr().out


def test_prepare_all_one_failure_does_not_stop_the_rest(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent"), L("brisk")], [rec("ardent"), rec("brisk")])
    _commands(monkeypatch, s)
    calls = []

    def approvals(apply=False, job_id="", prefill=True, *, chosen=None, tally=None):
        calls.append(chosen[0]["job_id"])
        if len(calls) == 1:
            raise RuntimeError(f"export failed for {chosen[0]['company']}")
        tally["sent"] = tally.get("sent", 0) + 1
        return 0
    monkeypatch.setattr(run_mod, "cmd_approvals", approvals)
    assert run_mod.cmd_approvals_wanted(apply=True) == 1
    out = capsys.readouterr().out
    assert len(calls) == 2 and "1 RuntimeError" in out and "Ardent" not in out


def test_cmd_approvals_with_chosen_rows_sends_a_score_built_one_and_reads_his_view(monkeypatch):
    """Bulk approvals hold back a package his score built; the roles he asked
    for all at once are sent whatever built them, with his view."""
    url = GH.format("ardent", 1)
    s = FakeSheet(grid_of(line("New", "Ardent", "VP Partnerships", url, k="9", m=REACTION)))
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": lambda self=None: "t"})())
    import hunter.notify as notify_mod
    monkeypatch.setattr(notify_mod, "mailbox", lambda cfg: "him@example.com")
    monkeypatch.setattr(run_mod, "_answer_bank", lambda sheet: {})
    monkeypatch.setattr(run_mod, "live_postings", lambda cfg: {})
    seen = {}

    def plan(cfg, sheet, row, bank=None, view=None):
        seen["view"] = view
        raise RuntimeError("stop here")
    monkeypatch.setattr(run_mod, "build_fill_plan", plan)
    tally = {}
    run_mod.cmd_approvals(apply=True, chosen=[rec("ardent")], tally=tally)
    assert seen["view"].reaction == REACTION and seen["view"].score == 9
    assert tally == {"failed": 1}


def test_a_chosen_posting_with_a_live_approval_is_not_prepared_again(monkeypatch):
    """A Prepare pressed in Control Center between prepare-all's selection and
    its turn: the posting already holds a token, so it is skipped before any
    essay is paid for, and he never gets the application twice."""
    url = GH.format("ardent", 1)
    s = FakeSheet(grid_of(line("New", "Ardent", "VP Partnerships", url, k="9", m=REACTION)))
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": lambda self=None: "t"})())
    import hunter.notify as notify_mod
    monkeypatch.setattr(notify_mod, "mailbox", lambda cfg: "him@example.com")
    monkeypatch.setattr(run_mod, "_answer_bank", lambda sheet: {})
    chosen = rec("ardent")
    monkeypatch.setattr(run_mod, "live_postings",
                        lambda cfg: {run_mod.posting_key(chosen): ("tok-1", "awaiting")})
    monkeypatch.setattr(run_mod, "build_fill_plan",
                        lambda *a, **kw: pytest.fail("a posting with a live token was prepared"))
    tally = {}
    run_mod.cmd_approvals(apply=True, chosen=[chosen], tally=tally)
    assert tally == {"taken": 1}


def test_a_yes_he_answered_with_only_a_reaction_is_not_asked_for():
    """His score wins over an older Yes, and a reaction with no score is his
    no: the row leaves Pipeline on the next pass, and nothing is mailed for
    it before then. A Yes with K and M blank is still asked for."""
    words = row(verdict="Yes", k="", m="Not the shape I want")
    assert router.build_trigger(words) == router.YES_TRIGGER
    assert router.wants(words) is None
    assert router.wants(row(verdict="Yes", k="", m="")) == router.YES_TRIGGER
    assert router.wants(row(verdict="Yes", k="8", m="Not the shape I want")) == router.YES_TRIGGER


def test_a_yes_he_called_a_duplicate_is_not_sent_twice(monkeypatch):
    """The twin he did not mark is the role. A Yes still builds whatever M
    says; asking him to send it is another matter."""
    dup = row(verdict="Yes", m="Duplicate")
    assert router.build_trigger(dup) == router.YES_TRIGGER and router.wants(dup) is None
    lines = [L("ardent", verdict="Yes", k="", m="Duplicate"), L("ardent", n=2, verdict="Yes", k="")]
    roles = [rec("ardent"), dict(rec("ardent", n=2), job_id="ardent:vp-2")]
    s, _ = rebuild_world(monkeypatch, lines, roles)
    rep = {}
    got = run_mod.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep)
    assert [d["job_id"] for d in got] == ["ardent:vp-2"]
    assert rep["skips"] == {"Yes, called a duplicate": 1}


def test_prepare_all_skips_a_posting_dealt_with_until_its_package_changes(monkeypatch):
    s, db = rebuild_world(monkeypatch, [L("ardent"), L("brisk"), L("cobalt")],
                          [rec("ardent"), rec("brisk"), rec("cobalt")])
    monkeypatch.setattr(run_mod, "_answer_bank", lambda sheet: {})
    assert run_mod.mark_prepared(Cfg(), rec("ardent"), run_mod.BY_HAND) == ""
    assert run_mod.mark_prepared(Cfg(), rec("cobalt"), run_mod.REFUSED_BLOCKING,
                                 bank={}) == ""
    # brisk was dealt with for a package since rebuilt: a new package is a new try
    assert run_mod.mark_prepared(Cfg(), rec("brisk", package_built_at="2026-10-01"),
                                 run_mod.BY_HAND) == ""
    rep = {}
    got = run_mod.wanted_for_approval(Cfg(), s, FakeCanon(), report=rep)
    assert [d["job_id"] for d in got] == ["brisk:vp"]
    assert rep["skips"] == {run_mod.PREPARED_SKIPS[run_mod.BY_HAND]: 1,
                            run_mod.PREPARED_SKIPS[run_mod.REFUSED_BLOCKING]: 1}
    marks = db.tables["hunter_alerts"]
    assert all(m["kind"] == "trouble" and m["fingerprint"].startswith("prepared:") for m in marks)
    assert not any("Ardent" in json.dumps(m) for m in marks), "no company in the mark"


def test_batches_get_past_roles_that_mint_no_token(monkeypatch, capsys):
    """Two forms hunter cannot fill sat at the head of every --max batch and
    were mailed again each time, and the roles behind them were never
    reached."""
    lines = [L(x, k="9") for x in ("ardent", "brisk", "cobalt", "dune")]
    s, _ = rebuild_world(monkeypatch, lines, [rec(x) for x in ("ardent", "brisk", "cobalt", "dune")])
    _commands(monkeypatch, s)
    calls = []

    def approvals(apply=False, job_id="", prefill=True, *, chosen=None, tally=None):
        row_ = chosen[0]
        calls.append(row_["job_id"])
        if row_["job_id"] in ("ardent:vp", "brisk:vp"):
            # what cmd_approvals does for a form it cannot fill
            run_mod.mark_prepared(Cfg(), row_, run_mod.BY_HAND)
            tally["by_hand"] = tally.get("by_hand", 0) + 1
        else:
            tally["sent"] = tally.get("sent", 0) + 1
        return 0
    monkeypatch.setattr(run_mod, "cmd_approvals", approvals)
    run_mod.cmd_approvals_wanted(apply=True, max_n=2)
    run_mod.cmd_approvals_wanted(apply=True, max_n=2)
    assert calls == ["ardent:vp", "brisk:vp", "cobalt:vp", "dune:vp"]


class Plan:
    """A fill plan as cmd_approvals reads one."""

    def __init__(self, *, readable=True, blocking=()):
        self.readable, self.fields = readable, (["a field"] if readable else [])
        self.blocking = [type("F", (), {"label": b})() for b in blocking]
        self.notes, self.essays, self.flagged = ["the form could not be read"], {}, []
        self.summary = self.hook = self.jd_url = ""
        self.ats = "linkedin"

    @property
    def ready(self):
        return not self.blocking

    def field_lines(self):
        return []

    def as_dict(self):
        return {"plan": 1}


def approvals_world(monkeypatch, lines, roles, plan):
    """cmd_approvals offline, as far as the send: the sheet, the record, the
    fill plan and every email are fakes, and what was mailed is recorded."""
    s, db = rebuild_world(monkeypatch, lines, roles)
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "GoogleOAuth",
                        lambda cfg: type("O", (), {"access_token": lambda self=None: "t"})())
    import hunter.docbuild as docbuild
    import hunter.notify as notify_mod
    from hunter.apply import approval, payload
    monkeypatch.setattr(docbuild, "DocBuild", lambda tok: type(
        "D", (), {"export_pdf": lambda self, doc_id: b"%PDF"})())
    monkeypatch.setattr(notify_mod, "mailbox", lambda cfg: "him@example.com")
    monkeypatch.setattr(run_mod, "_answer_bank", lambda sheet: {})
    monkeypatch.setattr(run_mod, "live_postings", lambda cfg: {})
    monkeypatch.setattr(run_mod, "posting_is_gone", lambda row: False)
    monkeypatch.setattr(payload, "build", lambda *a, **kw: None)
    monkeypatch.setattr(approval, "render", lambda **kw: type(
        "E", (), {"subject": "s", "text": "t", "html": "h"})())
    mailed = {"by_hand": [], "approval": []}
    monkeypatch.setattr(run_mod, "send_apply_by_hand",
                        lambda cfg, **kw: mailed["by_hand"].append(kw["company"]) or {"sent": True})
    monkeypatch.setattr(notify_mod, "send_email",
                        lambda *a, **kw: mailed["approval"].append(1) or {"id": "m", "sent": True})
    monkeypatch.setattr(approval, "record_sent", lambda cfg, **kw: None)
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)
    reached = []

    def build_fill_plan(cfg, sheet, row_, bank=None, view=None):
        reached.append(row_["job_id"])
        return plan, type("A", (), {"attachment_style": "", "autonomy_score": "1"})(), \
            type("R", (), {"company": row_["company"], "title": row_["title"],
                           "jd_url": row_["url"]})()
    monkeypatch.setattr(run_mod, "build_fill_plan", build_fill_plan)
    return db, mailed, reached


@pytest.mark.parametrize("plan,outcome", [(Plan(readable=False), "by_hand"),
                                          (Plan(blocking=("Work authorisation",)),
                                           "refused_blocking")])
def test_cmd_approvals_marks_what_it_dealt_with_and_minted_no_token_for(
        monkeypatch, plan, outcome):
    db, mailed, _ = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")], plan)
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {outcome: 1}
    marks = [m for m in db.tables.get("hunter_alerts", [])]
    assert [m["detail"]["outcome"] for m in marks] == [outcome]
    assert marks[0]["fingerprint"] == run_mod.prepared_fingerprint(
        rec("ardent"), outcome, bank=run_mod.bank_hash({}))
    assert mailed["by_hand"] == (["Ardent"] if outcome == "by_hand" else [])


def test_cmd_approvals_marks_nothing_it_sent(monkeypatch):
    db, mailed, _ = approvals_world(monkeypatch, [L("ardent", k="9")], [rec("ardent")], Plan())
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[rec("ardent")], tally=tally)
    assert tally == {"sent": 1} and mailed["approval"] == [1]
    assert not db.tables.get("hunter_alerts")


@pytest.mark.parametrize("now", [dict(k="4"), dict(k=""), dict(k="9", m="Duplicate"),
                                 dict(k="9", verdict="Declined - company"),
                                 dict(k="9", verdict="Applied")])
def test_prepare_all_never_sends_a_role_his_row_stopped_asking_for(monkeypatch, now):
    """Chosen at the start of the batch, sent minutes later: in between he
    lowered it, cleared it, called it a duplicate, declined it or applied."""
    db, mailed, reached = approvals_world(monkeypatch, [L("ardent", **now)], [rec("ardent")],
                                          Plan())
    chosen = dict(rec("ardent"), build_trigger="Krish Score 9", krish_score=9,
                  sheet_row=3, sheet_link=GH.format("ardent", 1))
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[chosen], tally=tally)
    assert reached == [] and mailed == {"by_hand": [], "approval": []}
    assert tally == {"no longer asked for": 1}


def test_prepare_all_finds_his_row_by_its_link_when_he_corrected_the_title(monkeypatch):
    corrected = line("New", "Ardent", "Head of Global Partnerships", GH.format("ardent", 1),
                     k="9")
    db, mailed, reached = approvals_world(monkeypatch, [corrected], [rec("ardent")], Plan())
    chosen = dict(rec("ardent"), build_trigger="Krish Score 9", krish_score=9,
                  sheet_row=3, sheet_link=GH.format("ardent", 1))
    tally = {}
    run_mod.cmd_approvals(apply=True, prefill=False, chosen=[chosen], tally=tally)
    assert reached == ["ardent:vp"] and tally == {"sent": 1}


# ---------- 8. counts only ----------

def _commands(monkeypatch, s):
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("T", (), {"access_token": "x"})())
    from hunter import spend
    import hunter.ats.discover as disc
    monkeypatch.setattr(spend, "over_budget", lambda cfg: None)
    monkeypatch.setattr(spend, "summary", lambda: [])
    monkeypatch.setattr(spend, "flush", lambda cfg=None: "")
    monkeypatch.setattr(run_mod.preflight, "run", lambda cfg, **kw: [])
    monkeypatch.setattr(run_mod.preflight, "gate", lambda checks: (True, []))
    monkeypatch.setattr(run_mod, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)


def test_rebuild_prints_counts_and_never_a_role_or_his_words(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent", m=REACTION), L("brisk", m=REACTION),
                                       L("cobalt", m=REACTION)],
                         [rec("ardent"), rec("brisk"), rec("cobalt")])
    _commands(monkeypatch, s)
    outcomes = iter([True, False, False])

    def build_one(cfg, canon, sheet, row, detail, **kw):
        ok = next(outcomes)
        jid = row["job_id"]
        detail.append(f"{'BUILT' if ok else 'HELD'} {jid} {row['company']} {REACTION}")
        return ok
    monkeypatch.setattr(run_mod, "build_one", build_one)
    assert run_mod.cmd_rebuild_packages(apply=True) == 0
    out = capsys.readouterr().out
    assert "rebuild: done: 2 held, the package before stays, 1 rebuilt" in out
    for leak in (MARK, "Ardent", "Brisk", "Cobalt", "ardent:vp", "cobalt:vp"):
        assert leak not in out, leak


def test_rebuild_dry_run_builds_nothing(monkeypatch, capsys):
    s, _ = rebuild_world(monkeypatch, [L("ardent")], [rec("ardent")])
    _commands(monkeypatch, s)
    monkeypatch.setattr(run_mod, "build_one", lambda *a, **kw: pytest.fail("built"))
    assert run_mod.cmd_rebuild_packages(apply=False) == 0
    out = capsys.readouterr().out
    assert "1 package(s) built without his reasons" in out and "dry run" in out


# ---------- 9. wiring ----------

def test_the_commands_are_wired_and_offered_by_hand():
    import yaml
    wf = yaml.safe_load((pathlib.Path(__file__).parent.parent
                         / ".github/workflows/hunter.yml").read_text())
    options = wf[True]["workflow_dispatch"]["inputs"]["command"]["options"]
    assert "rebuild-packages" in options and "approvals" in options
    src = pathlib.Path(run_mod.__file__).read_text()
    assert 'cmd == "rebuild-packages"' in src and '"--wanted" in argv' in src
    steps = " ".join(str(st.get("run", "")) for st in wf["jobs"]["hunter"]["steps"])
    assert "rebuild-packages" not in steps and "--wanted" not in steps, \
        "both are run by hand, never on the schedule"


def test_doctor_says_when_the_record_of_his_view_is_missing():
    from hunter import doctor
    ok = doctor.check_his_view_column(None, get=lambda cfg, t, p: [])
    missing = doctor.check_his_view_column(
        None, get=lambda cfg, t, p: (_ for _ in ()).throw(RuntimeError("42703")))
    assert ok.state == doctor.OK and missing.state == doctor.WARN
    assert "cc6" in missing.fix
