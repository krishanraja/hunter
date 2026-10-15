"""A package in his house style, from hold to build (letters.py, run.house_build).

build_one end to end with the house builder: the holds that cost no fetch,
the letter asked of the writer and never the API, the writer's answer held
to its check, and the build read back and stamped. The emulator stands in
for Docs; every word, figure and name is invented."""
import copy
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.config as config_mod  # noqa: E402
import hunter.run as run_mod  # noqa: E402
from contact_bases import BASES  # noqa: E402
from fake_docs import FakeDocBuild  # noqa: E402
from hunter import drafts, facts, letters  # noqa: E402
from hunter.apply.merge import page_count  # noqa: E402
from hunter.package import compose, contact, house, masters, personal, style  # noqa: E402
from test_house import letter as specimen  # noqa: E402
from test_masters import cv_doc  # noqa: E402
from test_score_builds import Cfg, FakeCanon, _stub_build  # noqa: E402
from test_his_view import grid_of, line  # noqa: E402
from test_sheet_rows import FakeSheet  # noqa: E402

pytestmark = pytest.mark.house_builder

JOB = "kestrel:vp-partnerships"
URL = "https://job-boards.greenhouse.io/kestrel/jobs/7"
JD = ("Kestrel builds voice agents and audio tools for call centres. You will lead platform "
      "partnerships and licensing deals and report to the COO.")
LINE = "I have played in bands for twenty years and still record at home."
HAY = ("quorra: grew a partner engine from $2m to $9m. velmont: licensed the stack to three "
       "businesses. halcyon: closed a $1.5m partnership. i build ai native sales engines. "
       + JD.lower())


class FakeDb:
    def __init__(self, tables):
        self.t = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.next = 100

    @staticmethod
    def _ok(row, filters):
        for k, v in (filters or {}).items():
            if k in ("select", "limit", "order"):
                continue
            have = row.get(k)
            if v.startswith("eq."):
                if str(have) != v[3:]:
                    return False
            elif v.startswith("neq."):
                if str(have) == v[4:]:
                    return False
            elif v.startswith("in.("):
                if str(have) not in v[4:-1].split(","):
                    return False
        return True

    def get(self, cfg, table, params):
        rows = [dict(r) for r in self.t.get(table, []) if self._ok(r, params)]
        if (params or {}).get("order") == "id.desc":
            rows.sort(key=lambda r: -int(r.get("id") or 0))
        n = int((params or {}).get("limit") or 0)
        return rows[:n] if n else rows

    def insert(self, cfg, table, rows, **kw):
        for r in rows:
            r = dict(r)
            r.setdefault("id", self.next)
            self.next += 1
            if table == "hunter_drafts":
                r.setdefault("problems", [])
            self.t.setdefault(table, []).append(r)

    def patch(self, cfg, table, match, values):
        for r in self.t.get(table, []):
            if all(str(r.get(k)) == str(v) for k, v in match.items()):
                r.update(values)

    def patch_where(self, cfg, table, filters, values):
        n = 0
        for r in self.t.get(table, []):
            if self._ok(r, filters):
                r.update(values)
                n += 1
        return n

    def drafts(self):
        return self.t.get("hunter_drafts", [])

    def role(self):
        return self.t["hunter_seen_roles"][0]


class Canon914(FakeCanon):
    def __init__(self, entries=()):
        self.v914 = "## 9.14 PERSONAL CONNECTIONS\n" + "".join(
            personal.entry_block(i + 1, ["voice_audio_speech"], [], w, "2026-10-16")
            for i, w in enumerate(entries))

    def section_text(self, number):
        if number == "9.14":
            return self.v914
        return super().section_text(number)


def answer(**over):
    a = {"hiring_lead": "Hiring Team",
         "hook": "Kestrel is turning voice agents into a partner business. " + LINE,
         "identity": "I'm a commercial leader with technical roots, and I build AI native "
                     "sales engines for platform partnerships.",
         "bullets": [{"label": "Quorra:", "text": "grew a partner engine from $2M to $9M, the "
                                                   "platform partnerships work this seat leads."},
                     {"label": "Velmont:", "text": "licensed the stack to three businesses."},
                     {"label": "Halcyon:", "text": "closed a $1.5M partnership, the kind of "
                                                    "licensing deals Kestrel needs."}],
         "why_now": "I am looking for a platform business like Kestrel, where partnerships are "
                    "the growth line.",
         "close": "I'd rather show you the partner engines I have built than describe them, "
                  "starting with Kestrel's licensing deals.",
         "cv_summary": 'I build platform partnerships and licensing deals: at Quorra I grew a partner engine from $2M to $9M, at Velmont I licensed the stack to three businesses, and at Halcyon I closed a $1.5M partnership. I want to lead the platform partnerships of a voice agents business, reporting to the COO, with a small team built around licensing deals.',
         "asks": [{"ask": "partnerships", "quote": "lead platform partnerships and licensing deals",
                   "answered_by": "Velmont"}]}
    a.update(over)
    return a


@pytest.fixture
def world(monkeypatch):
    rec = _stub_build(monkeypatch)
    from hunter.sources import ResolvedRole

    def resolve(cfg, row, cache, **kw):
        rec["resolved"] += 1
        return (ResolvedRole(company="Kestrel", title="VP Partnerships", url=URL, jd_url=URL,
                             jd_text=JD, live=True, source="t"), None, [])
    monkeypatch.setattr(run_mod, "resolve_for_build", resolve)
    db = FakeDb({"hunter_seen_roles": [{
        "job_id": JOB, "company": "Kestrel", "title": "VP Partnerships", "url": URL,
        "job_url": URL, "location": "New York, NY", "jd_text": JD, "status": "staging",
        "package_status": "none", "header_base": None,
        "judge_answers": {"business": {"quote": "voice agents and audio tools for call centres",
                                       "what_it_sells": "Voice AI agents and audio tools"}}}],
        "hunter_drafts": [], "hunter_fact_requests": [], "hunter_row_state": [
            {"job_id": JOB, "fields": {}}]})
    for mod in (run_mod, letters, drafts, facts):
        for name, fn in (("db_get", db.get), ("db_insert", db.insert), ("db_patch", db.patch),
                         ("db_patch_where", db.patch_where)):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fn)
    docs = FakeDocBuild({"spec": specimen(), "cv": cv_doc()})
    docs.export_pages = [1]
    v5 = masters.build_v5(docs, "spec", folder_id="f", page_count=page_count, apply=True)["made"]
    docs.export_pages = [2]
    cvm = masters.build_cv_master(docs, "cv", "spec", folder_id="f", page_count=page_count,
                                  apply=True)["made"]
    docs.export_pages = [1]
    monkeypatch.setattr(config_mod, "LETTER_MASTER_ID", v5)
    monkeypatch.setattr(config_mod, "CV_MASTER_ID", cvm)
    monkeypatch.setattr(run_mod, "DocBuild", lambda token: docs)
    monkeypatch.setattr(contact, "load_bases", lambda cfg: (BASES, "2 contact base(s) read"))
    monkeypatch.setattr(run_mod, "house_evidence", lambda cfg, sheet, db, jd: {
        "text": "Quorra, Velmont, Halcyon", "haystack": HAY, "banned": ("world-class",),
        "model": "Zentrox is busy. " + LINE + " I'm a commercial leader."})
    sheet = FakeSheet(grid_of(line("New", "Kestrel", "VP Partnerships", URL, k="8")))
    return {"db": db, "docs": docs, "rec": rec, "sheet": sheet}


def build(world, canon):
    summary = []
    row = {"job_id": JOB, "company": "Kestrel", "title": "VP Partnerships", "url": URL,
           "status": "staging", "build_trigger": "Krish Score 8", "krish_score": 8,
           "attempt": "a" * 16, "sheet_link": URL, "sheet_row": 3}
    ok = run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), canon, world["sheet"], row,
                           summary, cache={})
    return ok, summary


def routine_writes(world, a):
    d = world["db"].drafts()[-1]
    d.update(status="drafted", output=a, drafted_by="routine")


def test_a_business_of_his_kind_with_no_line_holds_asks_once_and_fetches_nothing(world):
    ok, summary = build(world, Canon914())
    assert not ok and world["rec"]["resolved"] == 0
    assert summary == [f"SKIPPED {JOB}: held: needs one line from you about voice, audio or "
                       f"speech companies; nothing fetched or built"]
    asks = world["db"].t["hunter_fact_requests"]
    assert [(a["category"], a["status"]) for a in asks] == [("voice_audio_speech", "queued")]
    build(world, Canon914())
    assert len(world["db"].t["hunter_fact_requests"]) == 1
    assert world["db"].role()["package_status"] == "blocked"


def test_a_role_with_no_header_holds_for_his_pick(world):
    world["db"].role()["location"] = "Singapore"
    world["db"].role()["jd_text"] = "A role."
    ok, summary = build(world, Canon914([LINE]))
    assert not ok and world["rec"]["resolved"] == 0
    assert world["db"].role()["rejection_reason"] == letters.HELD_BASE
    assert contact.HEADER_ASK in summary[0]


def test_the_letter_is_asked_of_the_routine_then_waits_without_a_fetch(world):
    ok, summary = build(world, Canon914([LINE]))
    assert not ok and world["rec"]["resolved"] == 1
    (d,) = world["db"].drafts()
    assert (d["kind"], d["status"], d["ref"]) == ("letter", "queued", JOB)
    assert d["meta"]["base"] == "us" and d["meta"]["entries"] == [LINE]
    assert "PERSONAL LINE" in d["prompt"] and "POSTING\n" + JD in d["prompt"]
    assert world["db"].role()["rejection_reason"] == letters.WAITING
    ok, summary = build(world, Canon914([LINE]))
    assert world["rec"]["resolved"] == 1 and len(world["db"].drafts()) == 1
    assert summary == [f"SKIPPED {JOB}: {letters.WAITING}; nothing fetched or built"]


def test_the_routines_letter_is_built_read_back_and_stamped(world, capsys):
    build(world, Canon914([LINE]))
    routine_writes(world, answer())
    ok, summary = build(world, Canon914([LINE]))
    assert ok, summary
    r = world["db"].role()
    assert (r["package_status"], r[style.FIELD], r["header_base"]) == \
        ("built", style.HOUSE_STYLE, "us")
    assert r["package_personal"] and r["package_asks"] == {"asks": 1, "answered": 1, "gaps": 0}
    assert r["package_trigger"] == "Krish Score 8" and r["rejection_reason"] is None
    assert world["db"].drafts()[-1]["status"] == "used"
    state = world["db"].t["hunter_row_state"][0]
    assert state["letter_doc_id"] and state["letter_style"][0]["runs"][0][1] == "Arial"
    doc = world["docs"].get(state["letter_doc_id"])
    assert house.check(house.snapshot(doc), kind="letter", base=BASES["us"], bases=BASES) == []
    assert LINE in " ".join(p.text for p in house.snapshot(doc))
    built = [s for s in summary if s.startswith("BUILT")]
    assert built and "personal line" in built[0]
    out = capsys.readouterr().out + "\n".join(summary)
    assert LINE not in out and "Quorra" not in out, "never a word of the letter in the log"


def test_a_failing_answer_is_failed_and_asked_again(world):
    build(world, Canon914([LINE]))
    routine_writes(world, answer(close="Would rather show you the engines than describe them."))
    ok, summary = build(world, Canon914([LINE]))
    assert not ok and summary[-1].startswith(f"BLOCKED {JOB}: the writer's letter failed")
    assert world["db"].drafts()[-1]["status"] == "failed"
    ok, summary = build(world, Canon914([LINE]))
    assert [d["status"] for d in world["db"].drafts()] == ["failed", "queued"]


def test_his_newer_score_asks_the_writer_again(world):
    build(world, Canon914([LINE]))
    routine_writes(world, answer())
    world["sheet"] = FakeSheet(grid_of(line("New", "Kestrel", "VP Partnerships", URL, k="9")))
    summary = []
    row = {"job_id": JOB, "company": "Kestrel", "title": "VP Partnerships", "url": URL,
           "status": "staging", "build_trigger": "Krish Score 9", "krish_score": 9,
           "attempt": "b" * 16, "sheet_link": URL, "sheet_row": 3}
    run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), Canon914([LINE]), world["sheet"], row,
                      summary, cache={})
    assert [d["status"] for d in world["db"].drafts()] == ["expired", "queued"]


def test_a_letter_over_one_page_is_blocked_and_left_for_review(world):
    build(world, Canon914([LINE]))
    routine_writes(world, answer())
    world["docs"].export_pages = [2]
    ok, summary = build(world, Canon914([LINE]))
    assert not ok and world["db"].role()["package_status"] == "blocked"
    assert "house style read-back failed" in world["db"].role()["rejection_reason"]
    assert any("A9 the letter renders 2 pages" in s for s in summary)
    assert world["db"].role().get(style.FIELD) is None


def test_the_preview_folder_is_made_once_and_reused(monkeypatch):
    import hunter.docbuild as docbuild_mod
    made = []

    class R:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data
    found = {"files": []}
    monkeypatch.setattr(docbuild_mod.requests, "get", lambda *a, **kw: R(found))

    def post(url, **kw):
        made.append(kw["json"])
        return R({"id": "F1"})
    monkeypatch.setattr(docbuild_mod.requests, "post", post)
    db = docbuild_mod.DocBuild("token")
    assert db.folder(run_mod.PREVIEW_FOLDER) == "F1"
    assert made == [{"name": "hunter preview", "mimeType": "application/vnd.google-apps.folder",
                     "parents": ["root"]}]
    found["files"] = [{"id": "F1", "name": "hunter preview"}]
    assert db.folder(run_mod.PREVIEW_FOLDER) == "F1" and len(made) == 1


def test_the_preview_builds_the_written_letter_and_touches_nothing_else(world, monkeypatch, capsys):
    before = dict(world["db"].role())
    monkeypatch.setattr(config_mod, "HOUSE_LETTER_MASTER_ID", config_mod.LETTER_MASTER_ID)
    monkeypatch.setattr(config_mod, "HOUSE_CV_MASTER_ID", config_mod.CV_MASTER_ID)
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg({}), Canon914([LINE])))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: world["sheet"])
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("S", (), {"access_token": "x"})())
    assert run_mod.cmd_build_preview([JOB], queue=True, folder="", apply=False,
                                     db=world["docs"], page_count=page_count) == 0
    assert f"{JOB}: letter asked of the writer" in capsys.readouterr().out
    routine_writes(world, answer())
    assert run_mod.cmd_build_preview([JOB], queue=False, folder="f", apply=True,
                                     db=world["docs"], page_count=page_count) == 0
    out = capsys.readouterr().out
    assert f"{JOB}: letter 1 page(s), CV 1 page(s), US header, asks 1 of 1, problems 0" in out
    assert "  CL https://docs.google.com/document/d/" in out
    assert LINE not in out
    assert world["db"].role() == before, "a preview records nothing on the role"
    assert world["db"].drafts()[-1]["status"] == "drafted", "nor marks the letter used"
