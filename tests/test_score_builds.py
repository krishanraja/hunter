"""PR3: a Krish Score of 7 or more builds the package.

His ruling 2026-10-13, asked whether a high Krish Score should build the
package by itself: "I'd take 7 or more". Column A Yes still builds too. What
has to hold, each with a test here:

- the trigger (router.build_trigger): A undecided, K a whole number 7 to 10,
  M not Duplicate; Applied or Declined in A always wins; "7.5" never builds;
- the order under a cap: Yes first, then K from highest, then oldest;
- the cap: score builds run in their own pass, a few at a time, never inside
  process or run;
- the record: package_trigger says why, and column A and krish_verdict are
  never written for a score;
- build_one re-reads A and K before it fetches or pays;
- settle cancels what is waiting on him when K drops below 7, and only then;
- a refused score build is not paid for again until K, the link or the
  masters change;
- the review email asks for a score in K and counts what is left to score;
- a gh_jid link (a careers page carrying a Greenhouse posting id and no
  board) is never read as a dead posting.

Every company here is invented. The repository is public: no real company
sits beside a score of his in this file (CLAUDE.md 3f).
"""
import json
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as run_mod  # noqa: E402
import hunter.sheet as sheet_mod  # noqa: E402
from hunter import alerts, invariants, router  # noqa: E402
from hunter.sheet import COLS, HEADERS, KRISH_REACTION, KRISH_SCORE, N_COLS, SheetRow  # noqa: E402
from test_sheet_rows import FakeSheet, data_row  # noqa: E402

C = COLS
EM_DASH = chr(0x2014)


class Cfg:
    def __init__(self, raw=None):
        self.raw = raw or {}

    def optional(self, key, default=""):
        return self.raw.get(key) or default

    def require(self, key):
        return self.raw[key]

    def require_json(self, key):
        return json.loads(self.raw[key])


class FakeCanon:
    """Canon v10 as far as the score pass reads it: 9.13 carries the rule."""
    sheet_headers = HEADERS
    bar = 8
    universe = []
    v913 = ("9.13 SHEET.\nYes, or a Krish Score of 7 or more in K on a row column A "
            "leaves undecided, builds a package.")

    def section_text(self, number):
        return self.v913 if number == "9.13" else ""


def srow(verdict="New", k="", m="", n=3, company="Quillfield", role="VP Strategy",
         url="https://job-boards.greenhouse.io/quillfield/jobs/11", **over):
    cells = data_row(verdict, company, role, url, **over)
    cells[C[KRISH_SCORE]], cells[C[KRISH_REACTION]] = k, m
    return SheetRow(row_number=n, cells=cells, verdict=verdict, company=company,
                    role=role, jd_url=url)


def grid_of(*rows):
    return [list(HEADERS), [""] * N_COLS] + [list(r) for r in rows]


def line(verdict, company, role, url, k="", m="", **over):
    cells = data_row(verdict, company, role, url, **over)
    cells[C[KRISH_SCORE]], cells[C[KRISH_REACTION]] = k, m
    return cells


# ---------- 1. what builds ----------

@pytest.mark.parametrize("verdict,k,m,want", [
    ("New", "7", "", "Krish Score 7"),
    ("", "10", "", "Krish Score 10"),
    ("New", " 8 ", "great fit", "Krish Score 8"),
    ("New", "6", "", None),
    ("New", "7.5", "", None),
    ("New", "8/10", "", None),
    ("New", "", "", None),
    ("Declined - stage wrong", "9", "", None),
    ("Applied", "9", "", None),
    ("Yes", "", "", "column A Yes"),
    ("Yes", "3", "", "column A Yes"),
    ("New", "8", "Duplicate", None),
    ("New", "9", "duplicate.", None),
])
def test_the_truth_table_of_what_builds(verdict, k, m, want):
    """A score is a whole number from 7 to 10 on a row column A leaves
    undecided, and M not calling it a duplicate. Column A Applied or
    Declined always wins; the trigger reads a Yes as a Yes whatever K says
    (leave.py moves a Yes he scored 1 to 5 off Pipeline, and under canon v11
    select_for_build(score_wins=True) refuses to build it while it waits)."""
    assert router.build_trigger(srow(verdict, k, m)) == want


def test_process_builds_yes_only_and_the_score_pass_builds_scores_only(monkeypatch):
    grid = grid_of(
        line("Yes", "Quillfield", "VP Strategy", "https://a.example/1"),
        line("New", "Ostravia", "Chief of Staff", "https://a.example/2", k="8"),
        line("New", "Marrowby", "Head of BD", "https://a.example/3", k="6"),
        line("Declined - stage wrong", "Tessaly", "GM", "https://a.example/4", k="9"))
    s = FakeSheet(grid)
    known = [{"job_id": f"j{i}", "company": c, "title": t, "url": f"https://a.example/{i}",
              "package_status": "none"}
             for i, (c, t) in enumerate([("Quillfield", "VP Strategy"),
                                         ("Ostravia", "Chief of Staff"),
                                         ("Marrowby", "Head of BD"),
                                         ("Tessaly", "GM")], start=1)]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    yes = router.select_for_build(Cfg(), s, HEADERS, cap=0)
    assert [d["job_id"] for d in yes] == ["j1"]
    assert yes[0]["build_trigger"] == "column A Yes"
    score = router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                    masters=lambda: "m1")
    assert [(d["job_id"], d["build_trigger"], d["krish_score"]) for d in score] == [
        ("j2", "Krish Score 8", 8)]
    assert score[0]["attempt"]


def test_neither_process_nor_run_can_build_on_a_score():
    """The backlog he scored lands in the hourly pass, capped, and never in
    the batch run he is waiting on."""
    import inspect
    for fn in (run_mod.process_step, run_mod.cmd_run, run_mod.cmd_process):
        src = inspect.getsource(fn)
        assert "router.SCORE" not in src and "score_build_step" not in src, fn.__name__
    assert router.select_for_build.__kwdefaults__["triggers"] == (router.YES,)


# ---------- 2. the order ----------

def test_yes_first_then_his_score_from_highest_then_oldest(monkeypatch):
    grid = grid_of(
        line("New", "Ostravia", "Chief of Staff", "https://a.example/1", k="7"),
        line("New", "Marrowby", "Head of BD", "https://a.example/2", k="9"),
        line("Yes", "Quillfield", "VP Strategy", "https://a.example/3"),
        line("New", "Tessaly", "GM", "https://a.example/4", k="9"),
        line("New", "Brannock", "COO", "https://a.example/5", k="10"))
    s = FakeSheet(grid)
    shown = {1: "2026-10-04", 2: "2026-10-11", 3: "2026-10-08", 4: "2026-10-03",
             5: "2026-10-11"}
    known = [{"job_id": f"j{i}", "company": c, "title": t, "url": f"https://a.example/{i}",
              "package_status": "none", "presented_at": shown[i],
              # the rule score runs against his: building by it would put the
              # role he scored lowest first
              "score": {1: 10, 2: 1, 3: 2, 4: 3, 5: 4}[i]}
             for i, (c, t) in enumerate([("Ostravia", "Chief of Staff"),
                                         ("Marrowby", "Head of BD"),
                                         ("Quillfield", "VP Strategy"),
                                         ("Tessaly", "GM"), ("Brannock", "COO")], start=1)]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    got = router.select_for_build(Cfg(), s, HEADERS, cap=0,
                                  triggers=(router.YES, router.SCORE), masters=lambda: "m")
    # Yes; then 10; then the two 9s, the one shown earlier first; then 7
    assert [d["job_id"] for d in got] == ["j3", "j5", "j4", "j2", "j1"]


# ---------- 3. the cap ----------

def _many_scored(monkeypatch, n=5):
    grid = grid_of(*[line("New", f"Co{i}", "Chief of Staff", f"https://a.example/{i}", k="8")
                     for i in range(n)])
    known = [{"job_id": f"co{i}:cos", "company": f"Co{i}", "title": "Chief of Staff",
              "url": f"https://a.example/{i}", "package_status": "none",
              "presented_at": f"2026-10-0{i + 1}"} for i in range(n)]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    return FakeSheet(grid)


def test_the_score_pass_builds_at_most_its_cap(monkeypatch):
    s = _many_scored(monkeypatch)
    built = []

    def fake_build(cfg, canon, sheet, row, summary, **kw):
        built.append(row["job_id"])
        return True
    monkeypatch.setattr(run_mod, "build_one", fake_build)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    out = run_mod.score_build_step(Cfg(), FakeCanon(), s, apply=True, cap=3, detail=[],
                                   masters=lambda: "m")
    assert out["eligible"] == 5 and out["chosen"] == 3 and out["built"] == 3
    assert built == ["co0:cos", "co1:cos", "co2:cos"]
    lines = run_mod.score_build_lines(out, apply=True, cap=3)
    assert "2 wait for the next" in lines[0]
    # A posting found gone costs no tailor call and uses no place: the pass
    # goes on down the list until it has used its three.
    built.clear()

    def some_dead(cfg, canon, sheet, row, summary, **kw):
        built.append(row["job_id"])
        if row["job_id"] in ("co0:cos", "co2:cos"):
            summary.append(f"DEAD {row['job_id']}: posting verifiably gone")
            return False
        return True
    monkeypatch.setattr(run_mod, "build_one", some_dead)
    out = run_mod.score_build_step(Cfg(), FakeCanon(), s, apply=True, cap=2, detail=[],
                                   masters=lambda: "m")
    assert built == ["co0:cos", "co1:cos", "co2:cos", "co3:cos"]
    assert out["built"] == 2 and out["dead"] == 2 and out["chosen"] == 4
    # a refused build does use its place: it may have paid for a tailor call
    built.clear()
    monkeypatch.setattr(run_mod, "build_one",
                        lambda cfg, canon, sheet, row, summary, **kw:
                        built.append(row["job_id"]) or False)
    out = run_mod.score_build_step(Cfg(), FakeCanon(), s, apply=True, cap=2, detail=[],
                                   masters=lambda: "m")
    assert built == ["co0:cos", "co1:cos"] and out["blocked"] == 2
    # 0 switches the pass off
    built.clear()
    off = run_mod.score_build_step(Cfg(), FakeCanon(), s, apply=True, cap=0, detail=[],
                                   masters=lambda: "m")
    assert off["chosen"] == 0 and built == []


def test_the_cap_is_read_from_system_config_and_defaults_to_three(monkeypatch, capsys):
    s = _many_scored(monkeypatch)
    seen = {}

    def fake_step(cfg, canon, sheet, *, apply, cap, detail, **kw):
        seen["cap"] = cap
        return {"eligible": 0, "chosen": 0, "built": 0, "unverified": 0, "dead": 0,
                "blocked": 0, "skipped": 0, "failed": 0, "waiting": 0,
                "unpaired": 0, "masters_unread": False,
                "preflight_failed": False}
    monkeypatch.setattr(run_mod, "score_build_step", fake_step)
    monkeypatch.setattr(run_mod, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    for raw, want in (({}, 3), ({"hunter_max_score_builds_per_run": "1"}, 1)):
        monkeypatch.setattr(run_mod, "build_context", lambda raw=raw: (Cfg(raw), FakeCanon()))
        assert run_mod.cmd_score_builds(apply=False) == 0
        assert seen["cap"] == want
    assert run_mod.cmd_score_builds(apply=False, max_builds=2) == 0 and seen["cap"] == 2


# ---------- 4. the record ----------

class _Report:
    passed = True

    def failures(self):
        return []


def _stub_build(monkeypatch, *, gate_fail=None):
    """Everything build_one touches past the sheet, offline. Returns what was
    written to hunter_seen_roles and to hunter_krish_ratings."""
    from hunter.sources import ResolvedRole
    import hunter.package.build as build_mod
    import hunter.package.tailor as tailor_mod
    import hunter.apply.infobank as infobank_mod
    import hunter.config as config_mod
    rec = {"patches": [], "ratings": [], "resolved": 0, "tailored": 0}

    def resolve(cfg, row, cache, **kw):
        rec["resolved"] += 1
        return (ResolvedRole(company=row["company"], title=row["title"], url=row["url"],
                             jd_url=row["url"], jd_text="x" * 300, live=True,
                             source="t"), None, [])
    monkeypatch.setattr(run_mod, "resolve_for_build", resolve)
    monkeypatch.setattr(run_mod.employer, "build_index", lambda cfg, declines=None: None)

    class Fail:
        def __init__(self, gate):
            self.gate, self.reason = gate, "refused"

    class FailReport:
        passed = False

        def failures(self):
            return [Fail(gate_fail)]
    monkeypatch.setattr(run_mod, "run_gates",
                        lambda role, **kw: FailReport() if gate_fail else _Report())
    monkeypatch.setattr(tailor_mod, "load_blocks", lambda cfg: ({}, {}))

    def tailor(*a, **kw):
        rec["tailored"] += 1
        return type("TR", (), {"block_key": "b", "flags": []})()
    monkeypatch.setattr(tailor_mod, "tailor", tailor)

    class Ok:
        ok = True
        body_word_count = 300
        failures = []

    class Bad:
        ok = False
        body_word_count = 900
        failures = ["letter runs to two pages"]
    rec["verify_fails"] = 0

    def build_package(*a, **kw):
        # A fresh pair of documents each call, so a test can tell whose
        # package landed on which row.
        n = rec["tailored"]
        bad = rec["verify_fails"] > 0
        if bad:
            rec["verify_fails"] -= 1
        return type("R", (), {"letter_report": Bad() if bad else Ok(), "cv_report": Ok(),
                              "letter_doc_id": f"L{n}", "cv_doc_id": f"V{n}",
                              "cv_url": f"https://docs.example/cv{n}",
                              "letter_url": f"https://docs.example/cl{n}",
                              "cv_pdf_url": f"https://docs.example/cvp{n}",
                              "letter_pdf_url": f"https://docs.example/clp{n}",
                              "notes": []})()
    monkeypatch.setattr(build_mod, "build_package", build_package)
    facts = type("F", (), {"cv_master_text": "", "cv_competencies": [], "cv_highlights": [],
                           "cv_summary_bold": []})()
    monkeypatch.setattr(build_mod, "read_master_facts", lambda db: facts)
    monkeypatch.setattr(infobank_mod, "load_bank", lambda read: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(run_mod, "GoogleOAuth", lambda cfg: type("O", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "DocBuild", lambda token: object())
    monkeypatch.setattr(run_mod, "_doc_text", lambda db, doc_id: "text")
    monkeypatch.setattr(run_mod.amend, "save_docs", lambda *a, **kw: None)
    monkeypatch.setattr(run_mod, "doc_plain_text", lambda *a, **kw: "")
    stored = {}

    def patch(cfg, table, match, values):
        rec["patches"].append((table, dict(match), dict(values)))
        stored.update(values)
    monkeypatch.setattr(run_mod, "db_patch", patch)
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [
        dict(stored, package_trigger=stored.get("package_trigger"))])

    def patch_where(cfg, table, filters, values):
        rec["ratings"].append((table, dict(filters), dict(values)))
        return 1
    monkeypatch.setattr(config_mod, "db_patch_where", patch_where)
    return rec


def _role(job="ostravia:chief-of-staff", url="https://job-boards.greenhouse.io/ostravia/jobs/7",
          **extra):
    return dict({"job_id": job, "company": "Ostravia", "title": "Chief of Staff",
                 "url": url, "status": "staging"}, **extra)


def test_a_score_build_records_its_trigger_and_never_writes_column_a(monkeypatch):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="8", m="sharp")))
    rec = _stub_build(monkeypatch)
    summary = []
    row = _role(url=url, build_trigger="Krish Score 8", krish_score=8, attempt="a" * 16)
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             summary, cache={}) is True
    built = [v for t, _, v in rec["patches"] if v.get("package_status") == "built"]
    assert built and built[0]["package_trigger"] == "Krish Score 8"
    # never a verdict on his behalf, in either place
    assert not any("krish_verdict" in v or "verdict_source" in v for _, _, v in rec["patches"])
    assert s.grid[2][C["Verdict"]] == "New"
    assert (s.grid[2][C[KRISH_SCORE]], s.grid[2][C[KRISH_REACTION]]) == ("8", "sharp")
    assert s.grid[2][C["Package Status"]] in sheet_mod.BUILT_STATUSES
    # the rating that built it carries the same trigger
    assert rec["ratings"] == [("hunter_krish_ratings",
                               {"job_id": "eq.ostravia:chief-of-staff", "kind": "eq.scored",
                                "score": "eq.8"},
                               {"package_trigger": "Krish Score 8"})]
    assert not any("did not read back" in x for x in summary)


def test_a_yes_build_records_column_a_yes(monkeypatch):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("Yes", "Ostravia", "Chief of Staff", url)))
    rec = _stub_build(monkeypatch)
    row = _role(url=url, build_trigger="column A Yes")
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             [], cache={}) is True
    built = [v for t, _, v in rec["patches"] if v.get("package_status") == "built"]
    assert built[0]["package_trigger"] == "column A Yes"
    assert rec["ratings"] == [], "a Yes is not a score; no rating is marked"


# ---------- 5. re-read before paying ----------

@pytest.mark.parametrize("now_k,now_a", [("5", "New"), ("", "New"), ("7.5", "New"),
                                         ("9", "Declined - stage wrong"), ("9", "New")])
def test_build_one_stops_before_fetching_when_the_row_changed(monkeypatch, now_k, now_a):
    """Chosen on an 8. By its turn he had lowered it, cleared it, typed 7.5,
    declined it, or raised it to 9: nothing is fetched or paid for. A raised
    score is built by the next pass, on what it reads then."""
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line(now_a, "Ostravia", "Chief of Staff", url, k=now_k)))
    rec = _stub_build(monkeypatch)
    summary = []
    row = _role(url=url, build_trigger="Krish Score 8", krish_score=8, attempt="a" * 16)
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             summary, cache={}) is False
    assert rec["resolved"] == 0 and rec["tailored"] == 0 and rec["patches"] == []
    assert summary[0].startswith("SKIPPED ostravia:chief-of-staff")


def test_an_amend_of_a_score_built_package_is_held_to_the_score(monkeypatch):
    """approvals-drain rebuilds from the stored record, which carries the
    trigger and no selection. A score he has since lowered stops it."""
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="4")))
    rec = _stub_build(monkeypatch)
    row = _role(url=url, package_trigger="Krish Score 8", package_status="built")
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             [], cache={}, feedback="shorter") is False
    assert rec["tailored"] == 0


# ---------- 6. a score taken back takes the application back ----------

class FakeDb:
    """hunter_seen_roles and hunter_application_approvals, with the PostgREST
    filters the code under test uses: eq., in.(...), not.is.null."""

    def __init__(self, roles, approvals):
        self.t = {"hunter_seen_roles": [dict(r) for r in roles],
                  "hunter_application_approvals": [dict(a) for a in approvals]}

    @staticmethod
    def _match(row, filters):
        for k, v in filters.items():
            if k in ("select", "limit", "order"):
                continue
            have = row.get(k)
            if v == "not.is.null":
                if have is None:
                    return False
            elif v.startswith("eq."):
                if str(have) != v[3:]:
                    return False
            elif v.startswith("in.("):
                if str(have) not in v[4:-1].split(","):
                    return False
            elif v.startswith("neq."):
                if str(have) == v[4:]:
                    return False
        return True

    def get(self, cfg, table, params):
        return [dict(r) for r in self.t.get(table, []) if self._match(r, params)]

    def patch(self, cfg, table, match, values):
        for r in self.t[table]:
            if all(str(r.get(k)) == str(v) for k, v in match.items()):
                r.update(values)

    def patch_where(self, cfg, table, filters, values):
        n = 0
        for r in self.t[table]:
            if self._match(r, filters):
                r.update(values)
                n += 1
        return n


def _withdrawal_world(monkeypatch, *, k, a="New", m="", trigger="Krish Score 8",
                      state="awaiting"):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line(a, "Ostravia", "Chief of Staff", url, k=k, m=m)))
    db = FakeDb(
        roles=[{"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
                "title": "Chief of Staff", "url": url, "job_url": url,
                "package_status": "built", "package_trigger": trigger,
                "package_cv_url": "https://docs.example/cv",
                "package_built_at": "2026-10-14T10:00:00Z"},
               # one posting filed under a second id, with its own token
               {"job_id": "ostravia:chief-of-staff-2", "company": "Ostravia",
                "title": "Chief of Staff", "url": url + "?utm_source=x", "job_url": url,
                "package_status": "none", "package_trigger": None}],
        approvals=[{"token": "t1", "job_id": "ostravia:chief-of-staff", "state": state},
                   {"token": "t2", "job_id": "ostravia:chief-of-staff-2", "state": "approved"},
                   {"token": "t3", "job_id": "other:role", "state": "awaiting"}])
    monkeypatch.setattr(run_mod, "db_get", db.get)
    monkeypatch.setattr(run_mod, "db_patch", db.patch)
    import hunter.apply.approval as approval_mod
    monkeypatch.setattr(approval_mod, "db_patch_where", db.patch_where)
    return s, db


@pytest.mark.parametrize("k,a,m,why", [
    ("5", "New", "", "Krish Score now 5"),
    ("6", "New", "", "Krish Score now 6"),
    ("", "New", "", "Krish Score cleared"),
    ("8", "New", "Duplicate", "Krish Reaction says Duplicate"),
    ("8", "Declined - stage wrong", "", "column A Declined"),
])
def test_settle_cancels_what_waits_on_him_when_his_score_stops_asking(monkeypatch, k, a, m, why):
    s, db = _withdrawal_world(monkeypatch, k=k, a=a, m=m)
    summary = []
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, summary, apply=True)
    states = {x["token"]: x["state"] for x in db.t["hunter_application_approvals"]}
    # both tokens for the posting, under either id; nothing else
    assert states == {"t1": "cancelled", "t2": "cancelled", "t3": "awaiting"}
    record = db.t["hunter_seen_roles"][0]
    assert record["package_trigger"] == f"withdrawn: {why} (built on Krish Score 8)"
    assert record["package_status"] == "built", "the documents stay"
    assert out["withdrawn"] == 1 and out["approvals_cancelled"] == 2 and not out["unconfirmed"]
    assert s.grid[2][C["Verdict"]] == a, "column A is his"
    # counts only: the log is public
    assert "Ostravia" not in " ".join(summary) and why not in " ".join(summary)


@pytest.mark.parametrize("k,a", [("8", "New"), ("7", "New"), ("3", "Yes"), ("8", "Applied")])
def test_settle_leaves_them_while_the_row_still_asks_or_he_applied(monkeypatch, k, a):
    s, db = _withdrawal_world(monkeypatch, k=k, a=a)
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, [], apply=True)
    states = {x["token"]: x["state"] for x in db.t["hunter_application_approvals"]}
    assert states == {"t1": "awaiting", "t2": "approved", "t3": "awaiting"}
    assert db.t["hunter_seen_roles"][0]["package_trigger"] == "Krish Score 8"
    assert out["withdrawn"] == 0


def test_a_score_raised_again_puts_the_role_back(monkeypatch):
    s, db = _withdrawal_world(monkeypatch, k="9",
                              trigger="withdrawn: Krish Score now 5 (built on Krish Score 8)",
                              state="cancelled")
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, [], apply=True)
    assert db.t["hunter_seen_roles"][0]["package_trigger"] == "Krish Score 9"
    assert out["reinstated"] == 1


def test_a_dry_run_changes_nothing(monkeypatch):
    s, db = _withdrawal_world(monkeypatch, k="4")
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, [], apply=False)
    assert out["withdrawn"] == 1 and out["approvals_cancelled"] == 2
    assert db.t["hunter_application_approvals"][0]["state"] == "awaiting"
    assert db.t["hunter_seen_roles"][0]["package_trigger"] == "Krish Score 8"


def test_settle_runs_the_withdrawals_before_it_moves_anything():
    import inspect
    src = inspect.getsource(run_mod.cmd_settle)
    assert src.index("score_withdrawals_step") < src.index("settle_step(cfg")


# ---------- 7. a refused score build waits for something to change ----------

def _guard_world(monkeypatch, *, k="8", url="https://a.example/1", recorded=None,
                 package_status="blocked", **over):
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k=k, **over)))
    known = [{"job_id": "j1", "company": "Ostravia", "title": "Chief of Staff",
              "url": "https://a.example/1", "package_status": package_status,
              "package_trigger": recorded}]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    return s


def test_a_refused_score_build_is_not_tried_again_unchanged(monkeypatch):
    fp = router.attempt_fingerprint(8, "https://a.example/1", "https://a.example/1", "m1")
    marker = router.not_built("Krish Score 8", fp)
    pick = lambda s, m="m1": router.select_for_build(  # noqa: E731
        Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,), masters=lambda: m, report=rep)
    rep = {}
    assert pick(_guard_world(monkeypatch, recorded=marker)) == []
    assert rep["waiting"] == ["j1"]
    # his score changed
    assert [d["job_id"] for d in pick(_guard_world(monkeypatch, k="9", recorded=marker))] == ["j1"]
    # the masters changed
    assert [d["job_id"] for d in pick(_guard_world(monkeypatch, recorded=marker), "m2")] == ["j1"]
    # the link on his row changed (the record still pairs by company and title)
    got = pick(_guard_world(monkeypatch, url="https://a.example/1b", recorded=marker))
    assert [d["job_id"] for d in got] == ["j1"] and got[0]["attempt"] != fp
    # a tracking parameter is not a new link
    s = _guard_world(monkeypatch, recorded=marker)
    s.grid[2][C["Job Link"]] = sheet_mod.hyperlink("https://a.example/1?utm_source=x", "Job")
    assert pick(s) == []


def test_once_only_refused_rows_are_left_the_hourly_pass_skips_the_full_read(monkeypatch):
    """After the backlog clears, the rows left are postings found gone and
    builds refused. Reading every recorded role each hour to learn that again
    is what the narrow read of the marked records avoids."""
    fp = router.attempt_fingerprint(8, "https://a.example/1", "https://a.example/1", "m1")
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", "https://a.example/1", k="8")))
    record = {"job_id": "j1", "company": "Ostravia", "title": "Chief of Staff",
              "url": "https://a.example/1", "package_status": "blocked",
              "package_trigger": router.not_built("Krish Score 8", fp)}
    asked = []

    def get(cfg, table, params):
        asked.append(dict(params))
        return [record]
    monkeypatch.setattr(router, "db_get", get)
    reads = []
    rep = {}
    assert router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                   masters=lambda: reads.append(1) or "m1", report=rep) == []
    assert rep["waiting"] == ["j1"] and len(reads) == 1
    assert all(p.get("package_trigger") == "like.Krish Score*" for p in asked)
    # his score moved: the full read decides, and the masters are read once
    asked.clear(), reads.clear()
    s.grid[2][C[KRISH_SCORE]] = "9"
    got = router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                  masters=lambda: reads.append(1) or "m1")
    assert [d["job_id"] for d in got] == ["j1"] and len(reads) == 1
    assert any("package_trigger" not in p for p in asked)


def test_masters_that_cannot_be_read_build_nothing_and_say_so(monkeypatch):
    rep = {}
    s = _guard_world(monkeypatch, package_status="none")
    assert router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                   masters=lambda: None, report=rep) == []
    assert rep["masters_unread"] is True


def test_a_refusal_is_recorded_with_what_it_saw(monkeypatch):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="8")))
    rec = _stub_build(monkeypatch, gate_fail="G8")
    row = _role(url=url, build_trigger="Krish Score 8", krish_score=8, attempt="b" * 16)
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             [], cache={}) is False
    blocked = [v for _, _, v in rec["patches"] if v.get("package_status") == "blocked"]
    assert blocked[0]["package_trigger"] == "Krish Score 8, not built [" + "b" * 16 + "]"
    assert router.attempt_of(blocked[0]["package_trigger"]) == "b" * 16
    assert rec["tailored"] == 0


def test_a_score_row_marked_dead_before_this_rule_is_checked_once_then_waits(monkeypatch):
    """A row marked dead with no attempt on record (before this rule, or by
    the gh_jid misreading) is checked once: a board read, no tailor call.
    Found gone again, it is recorded like any refusal and then waits for its
    score, its link or the masters to change, which is what the hourly line
    says. It used to be skipped for good whatever changed."""
    s = _guard_world(monkeypatch, package_status="blocked",
                     **{"Package Status": sheet_mod.PKG_DEAD})
    got = router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                  masters=lambda: "m")
    assert [d["job_id"] for d in got] == ["j1"]
    fp = got[0]["attempt"]
    s = _guard_world(monkeypatch, package_status="blocked",
                     recorded=router.not_built("Krish Score 8", fp),
                     **{"Package Status": sheet_mod.PKG_DEAD})
    rep = {}
    assert router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                   masters=lambda: "m", report=rep) == []
    assert rep["waiting"] == ["j1"]
    s = _guard_world(monkeypatch, k="9", package_status="blocked",
                     recorded=router.not_built("Krish Score 8", fp),
                     **{"Package Status": sheet_mod.PKG_DEAD})
    assert [d["job_id"] for d in router.select_for_build(
        Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,), masters=lambda: "m")] == ["j1"]
    assert not any("dead_skipped" in line or "marked dead before this rule" in line
                   for line in run_mod.score_build_lines(
                       {"eligible": 0, "chosen": 0, "waiting": 1, "unpaired": 0,
                        "masters_unread": False}, apply=True, cap=3))


# ---------- 8. approvals ----------

def _approvals_world(monkeypatch, *, k, trigger="Krish Score 8", job_id=""):
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k=k)))
    rows = [{"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
             "title": "Chief of Staff", "url": url, "package_status": "built",
             "package_trigger": trigger, "status": "staging"}]
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("T", (), {"access_token": "x"})())
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


def test_bulk_approvals_never_mail_a_package_his_score_built(monkeypatch, capsys):
    reached = _approvals_world(monkeypatch, k="8")
    assert run_mod.cmd_approvals(apply=True) == 0
    assert reached == []
    out = capsys.readouterr().out
    assert "Prepare" in out and "Ostravia" not in out


def test_prepare_sends_one_only_while_its_row_still_asks(monkeypatch, capsys):
    reached = _approvals_world(monkeypatch, k="5")
    assert run_mod.cmd_approvals(apply=True, job_id="ostravia:chief-of-staff") == 1
    assert reached == []
    assert "Ostravia" not in capsys.readouterr().out
    reached = _approvals_world(monkeypatch, k="8")
    with pytest.raises(RuntimeError, match="stop here"):
        run_mod.cmd_approvals(apply=True, job_id="ostravia:chief-of-staff")
    assert reached == [True]


# ---------- 9. the review email ----------

def test_the_review_email_asks_for_a_score_in_k_and_links_the_pipeline():
    rows = [srow(n=3, company="Quillfield"), srow(n=4, company="Ostravia")]
    subject, html, text = alerts.review_email(rows, staged=2,
                                              act_url="https://cc.example/#/people")
    assert "Score each role 1 to 10 in K; 7 or more builds the pack." in text
    assert "Score each role 1 to 10 in K" in html and "7 or more builds the pack" in html
    assert alerts.pipeline_url() in html and alerts.pipeline_url() in text.split("\n")[1]
    assert "Yes or No" not in html + text and "Hunt lane" not in html + text
    assert EM_DASH not in subject + html + text


def test_to_review_counts_only_what_he_has_still_to_score():
    rows = [srow(n=3, company="Quillfield"),                     # blank: to score
            srow(n=4, company="Ostravia", k="8"),                 # scored
            srow(n=5, company="Marrowby", m="not for me"),        # reaction only
            srow(n=6, company="Tessaly"),                         # blank, scored past
            srow("Yes", n=7, company="Brannock"),                 # a Yes
            srow("Declined - stage wrong", n=8, company="Corvane")]
    from hunter import ratings
    # as the record stores the row: "Not stated" is kept as no location
    assert rows[3].cell("Location") == "Not stated"
    past = {ratings.ident_of_record({"company": "Tessaly", "title": "VP Strategy",
                                     "location": None})}
    st = alerts.batch_state(rows, past)
    assert st["to_review"] == 1 and st["approved"] == 2 and st["skipped_read"]
    _, html, _ = alerts.review_email(rows, staged=1, state=dict(st, skipped=past))
    assert "Quillfield" in html
    for gone in ("Ostravia", "Marrowby", "Tessaly", "Brannock", "Corvane"):
        assert gone not in html, gone
    # unread record: counted, and said to be uncertain rather than exact
    st2 = alerts.batch_state(rows, None)
    assert st2["to_review"] == 2 and not st2["skipped_read"]
    _, html2, _ = alerts.review_email(rows, staged=1, state=st2)
    assert "could not read which" in html2


def test_passed_over_reads_the_record_and_says_when_it_cannot(monkeypatch):
    from hunter import ratings
    monkeypatch.setattr(ratings, "latest", lambda cfg: {
        "a": {"kind": "unscored_after_review", "company": "Tessaly", "title": "GM",
              "location": None},
        "b": {"kind": "scored", "company": "Ostravia", "title": "COO", "location": None}})
    assert alerts.passed_over(Cfg()) == {("tessaly", "gm", "")}
    monkeypatch.setattr(ratings, "latest", lambda cfg: (_ for _ in ()).throw(RuntimeError()))
    assert alerts.passed_over(Cfg()) is None


# ---------- 10. gh_jid links ----------

CAREERS_PAGE = "http://www.quillfield.com/careers?gh_jid=4335626009"
CAREERS_HOST = "https://careers.ostravia.com/?gh_jid=4999092101"


def test_a_gh_jid_key_names_no_board():
    for url, jid in ((CAREERS_PAGE, "4335626009"), (CAREERS_HOST, "4999092101")):
        assert run_mod.ats_key(url) == ("greenhouse", jid, "")
        assert run_mod.board_key(url) is None
        assert run_mod.gh_jid(url) == jid
    board = "https://job-boards.greenhouse.io/ostravia/jobs/4999092101"
    assert run_mod.board_key(board) == ("greenhouse", "ostravia", "4999092101")
    assert run_mod.gh_jid(board) == ""


def _never_fetch_the_posting_id_as_a_board(monkeypatch, live_on=None):
    import hunter.ats.greenhouse as gh
    calls = []

    def fetch(slug, pid):
        calls.append((slug, pid))
        if slug.isdigit() or not pid:
            # what the old key asked for: boards/<posting id>/jobs/ is a 404
            return False, "", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{pid}"
        return ((slug, pid) == live_on), "Posting text. " * 40, f"https://x/{slug}/{pid}"
    monkeypatch.setattr(gh, "fetch_posting", fetch)
    return calls


def test_a_careers_page_with_gh_jid_and_no_slug_finds_its_board(monkeypatch):
    """The first real shape: a company's careers page with ?gh_jid=. Its board
    lists the posting under the careers page link, gh_jid and all."""
    import hunter.ats.discover as disc
    import hunter.ats.greenhouse as gh
    from hunter.sources import RolePosting
    calls = _never_fetch_the_posting_id_as_a_board(monkeypatch,
                                                   live_on=("quillfield", "4335626009"))
    asked = {}

    def discover(cfg, company, cache, careers_url=""):
        asked["careers_url"] = careers_url
        return ("greenhouse", "quillfield")
    monkeypatch.setattr(disc, "discover", discover)
    monkeypatch.setattr(gh, "board", lambda slug: [
        RolePosting(company=slug, title="Head of Retail Partnerships", url=CAREERS_PAGE,
                    source="greenhouse:quillfield", ats="greenhouse", ats_slug="quillfield",
                    ats_posting_id="4335626009")])
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Director, Retail Partnerships", "company": "Quillfield",
               "url": CAREERS_PAGE}, {})
    assert role.live and role.liveness == "checked"
    assert relink == "https://job-boards.greenhouse.io/quillfield/jobs/4335626009"
    assert asked["careers_url"] == CAREERS_PAGE
    assert all(not slug.isdigit() for slug, _ in calls), calls


def test_a_careers_subdomain_with_gh_jid_and_no_board_found_is_unverified_not_dead(monkeypatch):
    """The second real shape (a careers subdomain carrying ?gh_jid=). Where no
    board answers, the worst it can be is unverified: the posting id was
    never a board to ask, and a 404 from asking it is not a dead posting."""
    import hunter.ats.discover as disc
    calls = _never_fetch_the_posting_id_as_a_board(monkeypatch)
    monkeypatch.setattr(disc, "discover", lambda cfg, company, cache, careers_url="": None)
    monkeypatch.setattr(run_mod, "fetch_jd_plain",
                        lambda url: (None, "Ostravia is hiring a Head of Partnerships. " * 10))
    role, relink, flags = run_mod.resolve_for_build(
        None, {"title": "Head of Strategic Partnerships", "company": "Ostravia",
               "url": CAREERS_HOST}, {})
    assert role.liveness == "unverified" and relink is None
    assert calls == []
    assert run_mod.package_status_for(role, {}) == sheet_mod.PKG_BUILT_UNVERIFIED


def test_build_one_never_marks_a_gh_jid_posting_dead(monkeypatch):
    import hunter.ats.discover as disc
    _never_fetch_the_posting_id_as_a_board(monkeypatch)
    monkeypatch.setattr(disc, "discover", lambda cfg, company, cache, careers_url="": None)
    monkeypatch.setattr(run_mod, "fetch_jd_plain", lambda url: (None, ""))
    s = FakeSheet(grid_of(line("New", "Ostravia", "Head of Strategic Partnerships",
                               CAREERS_HOST, k="8")))
    patched = []
    monkeypatch.setattr(run_mod, "db_patch", lambda cfg, t, m, v: patched.append(v))
    monkeypatch.setattr(run_mod, "run_gates", lambda role, **kw: (_ for _ in ()).throw(
        RuntimeError("reached the gates: the posting was not called dead")))
    monkeypatch.setattr(run_mod.employer, "build_index", lambda cfg, declines=None: None)
    row = {"job_id": "ostravia:hosp", "company": "Ostravia",
           "title": "Head of Strategic Partnerships", "url": CAREERS_HOST,
           "status": "staging", "build_trigger": "Krish Score 8", "attempt": "c" * 16}
    with pytest.raises(RuntimeError, match="reached the gates"):
        run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row, [],
                          cache={})
    assert s.grid[2][C["Package Status"]] != sheet_mod.PKG_DEAD
    assert not any(v.get("status") == "dead" for v in patched)


def test_a_gh_jid_link_has_no_liveness_answer_rather_than_a_dead_one(monkeypatch):
    calls = _never_fetch_the_posting_id_as_a_board(monkeypatch)
    assert run_mod.posting_is_gone({"url": CAREERS_HOST}) is None
    assert calls == []


# ---------- 11. downstream readers ----------

def test_every_role_he_scored_seven_or_more_is_accounted_for():
    rows = [srow(n=3, k="8", **{"Package Status": "Something odd"}),
            srow(n=4, k="6", **{"Package Status": "Something odd"})]
    f = invariants.check_yes_rows_accounted_for(rows)
    assert f.state == invariants.BROKEN and f.rows == [3]
    dead = invariants.check_dead_yes_rows([srow(n=3, k="9",
                                                **{"Package Status": sheet_mod.PKG_DEAD})])
    assert dead.rows == [3]


def test_a_twin_he_scored_seven_or_more_outranks_one_he_scored_lower():
    hi, lo = srow(n=5, k="8"), srow(n=3, k="4")
    assert max([lo, hi], key=invariants._standing).row_number == 5


def test_the_database_twin_his_score_built_is_the_posting():
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    known = [{"job_id": "a", "url": url, "package_status": "blocked", "package_trigger": None},
             {"job_id": "b", "url": url, "package_status": "built",
              "package_trigger": "Krish Score 8"},
             {"job_id": "c", "url": url, "package_status": "built",
              "package_trigger": "column A Yes"}]
    assert router.same_posting(srow(url=url), known)["job_id"] == "b"


# ---------- 12. the schedule ----------

def test_the_hourly_drain_runs_score_builds_after_settle_like_its_neighbours():
    import yaml
    wf = yaml.safe_load((pathlib.Path(__file__).parent.parent
                         / ".github/workflows/hunter.yml").read_text())
    steps = wf["jobs"]["hunter"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    settle = next(i for i, r in enumerate(runs) if "hunter.run settle --apply" in r)
    score = next(i for i, r in enumerate(runs) if "hunter.run score-builds --apply" in r)
    assert score == settle + 1
    assert steps[score]["if"] == steps[settle]["if"]
    assert "run" not in runs[score].split("score-builds")[1]


def test_doctor_finds_the_score_pass_in_this_cli(monkeypatch):
    import inspect
    from hunter import doctor
    yml = (pathlib.Path(__file__).parent.parent / ".github/workflows/hunter.yml").read_text()
    monkeypatch.setattr(doctor, "_git", lambda *a: yml)
    got = doctor.check_scheduled_commands(inspect.getsource(run_mod.main))
    assert got.state == doctor.OK and "score-builds" in got.detail


def test_the_score_pass_prints_counts_and_never_a_role(monkeypatch, capsys):
    s = _many_scored(monkeypatch, n=2)

    def fake_build(cfg, canon, sheet, row, summary, **kw):
        summary.append(f"BUILT {row['job_id']} flags=built on your score over G6")
        return True
    monkeypatch.setattr(run_mod, "build_one", fake_build)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(run_mod, "masters_fingerprint", lambda cfg: "m")
    monkeypatch.setattr(run_mod, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod.preflight, "run", lambda cfg, **kw: [])
    monkeypatch.setattr(run_mod.preflight, "gate", lambda checks: (True, []))
    import hunter.spend as spend
    monkeypatch.setattr(spend, "over_budget", lambda cfg: None)
    monkeypatch.setattr(spend, "flush", lambda cfg=None: "")
    assert run_mod.cmd_score_builds(apply=True) == 0
    out = capsys.readouterr().out
    assert "2 built" in out
    assert "co0" not in out.lower() and "Krish Score 8" not in out


# ---------- 13. two postings of one title in two countries ----------
#
# CLAUDE.md 10: a role is company, title and country. His live list on
# 2026-10-14 held one company's same title in two countries, scored 10 and 9,
# both in the first capped pass.

TWIN_UK = "https://job-boards.greenhouse.io/ostravia/jobs/8249"
TWIN_US = "https://job-boards.greenhouse.io/ostravia/jobs/8187"


def _twins(monkeypatch, *, k_uk, k_us, uk_first):
    uk = line("New", "Ostravia", "Chief of Staff", TWIN_UK, k=k_uk, Location="London, UK")
    us = line("New", "Ostravia", "Chief of Staff", TWIN_US, k=k_us, Location="New York, NY")
    s = FakeSheet(grid_of(*((uk, us) if uk_first else (us, uk))))
    known = [{"job_id": "ostravia:chief-of-staff-uk", "company": "Ostravia",
              "title": "Chief of Staff", "url": TWIN_UK, "package_status": "none",
              "status": "staging", "presented_at": "2026-10-04"},
             {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
              "title": "Chief of Staff", "url": TWIN_US, "package_status": "none",
              "status": "staging", "presented_at": "2026-10-04"}]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    rec = _stub_build(monkeypatch)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    return s, rec


@pytest.mark.parametrize("k_uk,k_us", [("10", "9"), ("10", "10")])
@pytest.mark.parametrize("uk_first", [True, False])
def test_two_postings_of_one_title_each_build_onto_their_own_row(monkeypatch, k_uk, k_us,
                                                                  uk_first):
    """build_one re-found its row with match_rows against the one record,
    which walks Pipeline from the top, so the upper twin took both records.
    With 10 and 9 one of them was skipped as "changed" every hour, for good;
    with equal scores both packages were written onto the upper row and the
    lower one stayed Not started while its record said built."""
    s, rec = _twins(monkeypatch, k_uk=k_uk, k_us=k_us, uk_first=uk_first)
    detail = []
    out = run_mod.score_build_step(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s,
                                   apply=True, cap=3, detail=detail, masters=lambda: "m")
    assert out["built"] == 2 and out["skipped"] == 0, detail
    cv_of = {m["job_id"]: v["package_cv_url"] for _, m, v in rec["patches"]
             if v.get("package_status") == "built"}
    on_sheet = {r.jd_url: r.package_urls.get("cv") for r in s.read_pipeline(HEADERS)}
    assert on_sheet[TWIN_UK] == cv_of["ostravia:chief-of-staff-uk"]
    assert on_sheet[TWIN_US] == cv_of["ostravia:chief-of-staff"]
    assert on_sheet[TWIN_UK] != on_sheet[TWIN_US]


def test_own_sheet_row_never_hands_a_record_the_twin_posting():
    """With no chosen row (build --job-id, an amend), the record's own link
    decides, and a row whose link is another ATS posting is never its row."""
    uk = srow(k="10", url=TWIN_UK, n=3, company="Ostravia", role="Chief of Staff")
    us = srow(k="9", url=TWIN_US, n=4, company="Ostravia", role="Chief of Staff")
    rec = {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
           "title": "Chief of Staff", "url": TWIN_US}
    assert run_mod.own_sheet_row([uk, us], rec).row_number == 4
    # its own row has left Pipeline: the twin is not it
    assert run_mod.own_sheet_row([uk], rec) is None
    # chosen by the selection: that row, by its link, then its number
    assert run_mod.own_sheet_row([uk, us], dict(rec, sheet_link=TWIN_UK)).row_number == 3


# ---------- 14. an error is not a refusal ----------

def _error_world(monkeypatch):
    s = _many_scored(monkeypatch, n=1)
    known = router.db_get(None, "hunter_seen_roles", {})

    def patch(cfg, table, match, values):
        for d in known:
            if d["job_id"] == match["job_id"]:
                d.update(values)
    monkeypatch.setattr(run_mod, "db_patch", patch)
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [
        dict(d) for d in known if params.get("job_id") == f"eq.{d['job_id']}"])
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    return s, known


def test_a_build_that_raised_is_tried_again_next_hour(monkeypatch):
    """One ConnectionError used to record the attempt fingerprint, and the
    next hour filed the role as refused and unchanged: a role he scored
    never built, with the sheet saying Not started and the run green."""
    import requests
    s, known = _error_world(monkeypatch)
    calls = []

    def flaky(cfg, canon, sheet, row, summary, **kw):
        calls.append(row["job_id"])
        if len(calls) == 1:
            raise requests.ConnectionError("Sheets 503")
        return True
    monkeypatch.setattr(run_mod, "build_one", flaky)
    hour1 = run_mod.score_build_step(Cfg(), FakeCanon(), s, apply=True, cap=3, detail=[],
                                     masters=lambda: "m")
    assert hour1["failed"] == 1
    assert known[0]["package_status"] == "blocked"
    assert known[0]["rejection_reason"] == "build error: ConnectionError"
    assert router.attempt_of(known[0].get("package_trigger")) is None
    hour2 = run_mod.score_build_step(Cfg(), FakeCanon(), s, apply=True, cap=3, detail=[],
                                     masters=lambda: "m")
    assert hour2["waiting"] == 0 and hour2["built"] == 1
    assert calls == ["co0:cos", "co0:cos"]


def test_an_error_after_the_package_was_built_leaves_it_built(monkeypatch):
    """build_one records the package as built before it writes the links to
    the sheet. An error in that write used to overwrite the record to
    blocked and not built, hiding a finished, paid-for package for good."""
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="8")))
    known = [{"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
              "title": "Chief of Staff", "url": url, "package_status": "none",
              "status": "staging"}]
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: known)
    rec = _stub_build(monkeypatch)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)

    def boom(*a, **kw):
        raise sheet_mod.SheetError("read-back disagreed")
    monkeypatch.setattr(s, "update_package_cells", boom)
    detail = []
    out = run_mod.score_build_step(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s,
                                   apply=True, cap=3, detail=detail, masters=lambda: "m")
    assert out["failed"] == 1
    last = rec["patches"][-1][2]
    assert last.get("package_status") == "built" and last["package_trigger"] == "Krish Score 8"
    assert any("left as built" in x for x in detail)


def test_a_verification_failure_gets_one_more_try_then_waits(monkeypatch):
    """A letter is a model's output: a second call can come in at one page
    where the first did not. One more try on the same inputs, then it waits
    for something to change, because every try is paid for."""
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="8")))
    rec = _stub_build(monkeypatch)
    rec["verify_fails"] = 2
    fp = "d" * 16
    row = _role(url=url, build_trigger="Krish Score 8", krish_score=8, attempt=fp)
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             [], cache={}) is False
    first = rec["patches"][-1][2]["package_trigger"]
    assert router.retry_of(first) == fp and router.attempt_of(first) is None
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s,
                             dict(row, package_trigger=first), [], cache={}) is False
    second = rec["patches"][-1][2]["package_trigger"]
    assert router.attempt_of(second) == fp
    assert rec["tailored"] == 2
    # what the selection makes of each
    fpg = router.attempt_fingerprint(8, "https://a.example/1", "https://a.example/1", "m1")
    for marker, waits in ((router.not_built("Krish Score 8", fpg, final=False), False),
                          (router.not_built("Krish Score 8", fpg), True)):
        rep = {}
        got = router.select_for_build(Cfg(), _guard_world(monkeypatch, recorded=marker),
                                      HEADERS, cap=0, triggers=(router.SCORE,),
                                      masters=lambda: "m1", report=rep)
        assert (got == []) is waits and (rep["waiting"] == ["j1"]) is waits


def test_a_refusal_after_a_relink_is_fingerprinted_on_the_new_link(monkeypatch):
    """build_one relinks his row and the record before the gates. A refusal
    fingerprinted on the old links did not match the next hour's, and the
    same refusal was paid for twice."""
    old = "https://www.ostravia.example/careers/chief-of-staff"
    new = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", old, k="8")))
    rec = _stub_build(monkeypatch, gate_fail="G8")
    from hunter.sources import ResolvedRole
    monkeypatch.setattr(run_mod, "resolve_for_build", lambda cfg, row, cache, **kw: (
        ResolvedRole(company="Ostravia", title="Chief of Staff", url=new, jd_url=new,
                     jd_text="x" * 300, live=True, source="t"), new, []))
    row = _role(url=old, build_trigger="Krish Score 8", krish_score=8,
                attempt=router.attempt_fingerprint(8, old, old, "m"), attempt_masters="m",
                sheet_link=old, sheet_row=3)
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             [], cache={}) is False
    marker = rec["patches"][-1][2]["package_trigger"]
    assert s.read_pipeline(HEADERS)[0].jd_url == new
    rep = {}
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: [
        {"job_id": "j1", "company": "Ostravia", "title": "Chief of Staff", "url": new,
         "package_status": "blocked", "package_trigger": marker}])
    assert router.select_for_build(Cfg(), s, HEADERS, cap=0, triggers=(router.SCORE,),
                                   masters=lambda: "m", report=rep) == []
    assert rep["waiting"] == ["j1"]


# ---------- 15. a gh_jid posting is its id, never its title ----------

def test_a_gh_jid_posting_is_matched_by_its_id_and_never_by_a_title(monkeypatch):
    """Posting 111 has closed and the board lists the same title as 222,
    another country. Title matching relinked the row to 222 and built a
    package for a role he never scored, calling a closed posting live."""
    import hunter.ats.discover as disc
    import hunter.ats.greenhouse as gh
    from hunter.sources import RolePosting
    calls = _never_fetch_the_posting_id_as_a_board(monkeypatch, live_on=("quillfield", "222"))
    link = "http://www.quillfield.com/careers?gh_jid=111"
    monkeypatch.setattr(disc, "discover",
                        lambda cfg, company, cache, careers_url="": ("greenhouse", "quillfield"))
    monkeypatch.setattr(gh, "board", lambda slug: [
        RolePosting(company=slug, title="Regional Vice President, Sales",
                    url="https://job-boards.greenhouse.io/quillfield/jobs/222",
                    source="greenhouse:quillfield", ats="greenhouse", ats_slug="quillfield",
                    ats_posting_id="222")])
    monkeypatch.setattr(run_mod, "fetch_jd_plain",
                        lambda url: (None, "Quillfield careers. " * 20))
    row = {"title": "Regional Vice President, Sales", "company": "Quillfield", "url": link}
    # a board its own page does not name: unknown, built as unverified on its own link
    monkeypatch.setattr(disc, "from_careers_page", lambda url, **kw: None)
    role, relink, _ = run_mod.resolve_for_build(None, row, {})
    assert relink is None and role.liveness == "unverified" and role.url == link
    assert ("quillfield", "222") not in calls
    # the board its own page names: the id is gone from it, so the posting is
    monkeypatch.setattr(disc, "from_careers_page",
                        lambda url, **kw: ("greenhouse", "quillfield"))
    role, relink, _ = run_mod.resolve_for_build(None, row, {})
    assert relink is None and not role.live and role.liveness == "checked"
    # a board on another ATS has no posting 111 to look for
    monkeypatch.setattr(disc, "discover",
                        lambda cfg, company, cache, careers_url="": ("ashby", "quillfield"))
    import hunter.ats.ashby as ashby
    monkeypatch.setattr(ashby, "board", lambda slug: [
        RolePosting(company=slug, title="Regional Vice President, Sales",
                    url="https://jobs.ashbyhq.com/quillfield/abc", source="ashby:quillfield",
                    ats="ashby", ats_slug="quillfield", ats_posting_id="abc")])
    state, hit, _ = run_mod.discover_posting(None, "Quillfield", row["title"], {},
                                             careers_url=link, posting_id="111")
    assert state == "unknown" and hit is None


# ---------- 16. what settle withdraws, and what it leaves ----------

def test_a_declined_role_that_reconcile_reached_first_is_still_withdrawn(monkeypatch):
    """Reconcile writes package_status blocked on a Declined row, and on an
    hour a batch is owed it runs before settle. Watching only "built" left
    the approval for a role he had declined waiting for good."""
    s, db = _withdrawal_world(monkeypatch, k="8", a="Declined - stage wrong")
    db.t["hunter_seen_roles"][0].update(krish_verdict="Declined - stage wrong",
                                        status="dropped", package_status="blocked")
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, [], apply=True)
    states = {x["token"]: x["state"] for x in db.t["hunter_application_approvals"]}
    assert states == {"t1": "cancelled", "t2": "cancelled", "t3": "awaiting"}
    assert out["watched"] == 1 and out["withdrawn"] == 1


def test_a_declined_role_already_moved_to_applied_is_withdrawn(monkeypatch):
    """The hour the withdrawals step failed, settle still archived the
    Declined row and wrote blocked. The next hour reads it off Applied."""
    s, db = _withdrawal_world(monkeypatch, k="8", a="Declined - stage wrong")
    db.t["hunter_seen_roles"][0].update(package_status="blocked")
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    empty = FakeSheet(grid_of())
    monkeypatch.setattr(empty, "read_archive_grid", lambda: [["header"]])
    monkeypatch.setattr(sheet_mod, "archive_layout", lambda grid: "current")
    monkeypatch.setattr(empty, "read_archive", lambda: [
        srow("Declined - stage wrong", k="8", url=url, company="Ostravia",
             role="Chief of Staff")])
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), empty, [], apply=True)
    assert db.t["hunter_application_approvals"][0]["state"] == "cancelled"
    assert out["withdrawn"] == 1


@pytest.mark.parametrize("k", ["8.5", "8/10", "9?"])
def test_a_score_that_is_not_a_whole_number_cancels_nothing(monkeypatch, k):
    """Unreadable is an absent observation, not a lower score. Cancelling on
    it destroyed an APPROVE he had given, and setting K back to 8 brought
    the trigger back but not the approval."""
    s, db = _withdrawal_world(monkeypatch, k=k, state="approved")
    summary = []
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, summary, apply=True)
    states = {x["token"]: x["state"] for x in db.t["hunter_application_approvals"]}
    assert states == {"t1": "approved", "t2": "approved", "t3": "awaiting"}
    assert db.t["hunter_seen_roles"][0]["package_trigger"] == "Krish Score 8"
    assert out["unreadable"] == 1 and out["withdrawn"] == 0
    assert "1 with a Krish Score that is not a whole number and left alone" in summary[0]
    # Declined still says no whatever K holds
    s, db = _withdrawal_world(monkeypatch, k=k, a="Declined - stage wrong", state="approved")
    run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, [], apply=True)
    assert db.t["hunter_application_approvals"][0]["state"] == "cancelled"


def test_an_application_prepared_after_the_withdrawal_is_cancelled_too(monkeypatch):
    """Prepare pressed on a Hunt lane that had not caught up makes a token
    for a role already withdrawn. Skipping withdrawn records left it awaiting,
    and the watchdog nagged him about a role he had marked down."""
    s, db = _withdrawal_world(monkeypatch, k="5",
                              trigger="withdrawn: Krish Score now 5 (built on Krish Score 8)")
    summary = []
    out = run_mod.score_withdrawals_step(Cfg(), FakeCanon(), s, summary, apply=True)
    states = {x["token"]: x["state"] for x in db.t["hunter_application_approvals"]}
    assert states == {"t1": "cancelled", "t2": "cancelled", "t3": "awaiting"}
    assert out["late_cancelled"] == 2 and out["withdrawn"] == 0
    assert (db.t["hunter_seen_roles"][0]["package_trigger"]
            == "withdrawn: Krish Score now 5 (built on Krish Score 8)")
    assert any("prepared after the role was withdrawn" in x for x in summary)


def test_prepare_reads_the_row_for_this_posting_not_its_twin(monkeypatch, capsys):
    """He lowered one posting to 4 while its twin in another country still
    reads 9. Matching by company and title let the application for the one
    he took back go out."""
    lowered, twin = TWIN_UK, TWIN_US
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", lowered, k="4"),
                          line("New", "Ostravia", "Chief of Staff", twin, k="9")))
    rows = [{"job_id": "ostravia:chief-of-staff-uk", "company": "Ostravia",
             "title": "Chief of Staff", "url": lowered, "package_status": "built",
             "package_trigger": "Krish Score 8", "status": "staging"}]
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [
        r for r in rows if not params.get("job_id") or params["job_id"] == f"eq.{r['job_id']}"])
    import hunter.notify as notify_mod
    reached = []
    monkeypatch.setattr(notify_mod, "mailbox", lambda cfg: reached.append(1) or (
        _ for _ in ()).throw(RuntimeError("the guard let it through")))
    assert run_mod.cmd_approvals(apply=True, job_id="ostravia:chief-of-staff-uk") == 1
    assert reached == []
    assert "REFUSING" in capsys.readouterr().out


# ---------- 17. his score stays out of the public log ----------

def test_an_amend_of_a_score_built_package_never_prints_his_score(monkeypatch):
    """approvals-drain rebuilds an amended package hourly, in Actions, and
    prints every summary line. "built on your Krish Score 9 over G6" beside
    the role was his score beside a company in a public log (CLAUDE.md 3f)."""
    import re as re_mod
    url = "https://job-boards.greenhouse.io/ostravia/jobs/7"
    s = FakeSheet(grid_of(line("New", "Ostravia", "Chief of Staff", url, k="9")))
    _stub_build(monkeypatch, gate_fail="G6")
    summary = run_mod.Summary()
    row = _role(url=url, package_trigger="Krish Score 9", package_status="built")
    assert run_mod.build_one(Cfg({"hunter_never_apply": "[]"}), FakeCanon(), s, row,
                             summary, cache={}, feedback="shorter") is True
    text = "\n".join(summary)
    assert "built on your score over G6" in text
    assert not re_mod.search(r"Krish Score \d", text), text


# ---------- 18. a role he scored 7 or more is his to archive ----------

def _verify_world(monkeypatch, rows):
    s = FakeSheet(grid_of(*rows))
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    known = [{"job_id": f"co{i}:gm", "company": f"Co{i}", "title": "GM",
              "url": f"https://job-boards.greenhouse.io/co{i}/jobs/{i}", "krish_verdict": None}
             for i in range(len(rows))]
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: known)
    import hunter.ats.discover as disc
    import hunter.ats.greenhouse as gh
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(disc, "save_cache", lambda cfg, cache: None)
    monkeypatch.setattr(gh, "fetch_posting", lambda slug, pid: (False, "", ""))
    stamped, archived = [], []
    monkeypatch.setattr(run_mod, "stamp_auto_verdicts",
                        lambda cfg, sheet, movers: stamped.extend(m[1] for m in movers))
    monkeypatch.setattr(s, "archive_rows",
                        lambda movers, **kw: archived.extend(r.row_number for r in movers))
    return s, stamped, archived


def test_verify_never_stamps_or_archives_a_dead_row_he_scored_seven_or_more(monkeypatch):
    s, stamped, archived = _verify_world(monkeypatch, [
        line("New", f"Co{i}", "GM", f"https://job-boards.greenhouse.io/co{i}/jobs/{i}", k=k)
        for i, k in enumerate(["8", "", "5"])])
    assert run_mod.cmd_verify(apply=True) == 0
    assert stamped == ["co1:gm", "co2:gm"]
    assert archived == [4, 5]
    assert s.grid[2][C["Verdict"]] == "New"


def test_regate_holds_a_row_he_scored_seven_or_more(monkeypatch, capsys):
    s = FakeSheet(grid_of(line("New", "Co0", "Gardener", "https://a.example/0", k="8"),
                          line("New", "Co1", "Gardener", "https://a.example/1")))
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg({"hunter_never_apply": "[]"}),
                                                           FakeCanon()))
    monkeypatch.setattr(run_mod, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": "x"})())
    monkeypatch.setattr(run_mod, "Sheet", lambda token: s)
    known = [{"job_id": f"co{i}:gardener", "company": f"Co{i}", "title": "Gardener",
              "url": f"https://a.example/{i}", "krish_verdict": None} for i in range(2)]
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: known)
    monkeypatch.setattr(run_mod, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(run_mod.employer, "build_index",
                        lambda cfg, declines=None: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(run_mod, "archetype", lambda title: None)
    monkeypatch.setattr(run_mod, "resolve_db_row",
                        lambda r, known, paired: (paired.get(r.row_number), ""))
    stamped, archived = [], []
    monkeypatch.setattr(run_mod, "stamp_auto_verdicts",
                        lambda cfg, sheet, movers: stamped.extend(m[1] for m in movers))
    monkeypatch.setattr(s, "archive_rows",
                        lambda movers, **kw: archived.extend(r.row_number for r in movers))
    assert run_mod.cmd_regate(from_row=3, apply=True) == 0
    assert stamped == ["co1:gardener"] and archived == [4]
    out = capsys.readouterr().out
    assert run_mod.ASKS_FOR_PACKAGE in out and "Krish Score 8" not in out


# ---------- 19. the switch and canon ----------

def test_cap_zero_switches_the_pass_off_without_reading_anything(monkeypatch, capsys):
    monkeypatch.setattr(run_mod, "build_context", lambda: (
        Cfg({"hunter_max_score_builds_per_run": "0"}), FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: (_ for _ in ()).throw(
        AssertionError("the sheet was read")))
    monkeypatch.setattr(run_mod, "masters_fingerprint", lambda cfg: (_ for _ in ()).throw(
        AssertionError("the masters were read")))
    assert run_mod.cmd_score_builds(apply=True) == 0
    assert "switched off" in capsys.readouterr().out


def test_nothing_builds_on_a_score_until_canon_says_so(monkeypatch, capsys):
    """Canon supersedes code. v9's 9.13 says Yes is the only value that
    builds a package; merged before v10 is written, the hourly pass would
    have paid for builds canon still forbade."""
    class V9(FakeCanon):
        v913 = "9.13 SHEET.\nYes is the only value that builds a package."
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), V9()))
    monkeypatch.setattr(run_mod, "Sheet", lambda token: (_ for _ in ()).throw(
        AssertionError("the sheet was read")))
    assert run_mod.cmd_score_builds(apply=True) == 0
    assert "canon 9.13 does not yet say" in capsys.readouterr().out
    assert run_mod.scores_build_in_canon(FakeCanon())


def test_rows_for_pairs_by_company_and_title_only_when_one_row_carries_them():
    """The fallback for a record whose link no row carries. Two rows with one
    company and title are two postings, and picking either is a guess."""
    rec = {"job_id": "ostravia:chief-of-staff", "company": "Ostravia",
           "title": "Chief of Staff", "url": "https://ostravia.example/old-link"}
    uk = srow(k="4", url=TWIN_UK, n=3, company="Ostravia", role="Chief of Staff")
    us = srow(k="9", url=TWIN_US, n=4, company="Ostravia", role="Chief of Staff")
    assert run_mod._rows_for(rec, [uk, us]) == []
    assert [r.row_number for r in run_mod._rows_for(rec, [us])] == [4]
    assert [r.row_number for r in run_mod._rows_for(dict(rec, url=TWIN_UK), [uk, us])] == [3]


def test_no_batch_build_can_be_handed_a_trigger_list():
    """The guard above reads the source for the constant. Spelt as the
    literals ("yes", "score") the same regression passed every test, so this
    reads the calls themselves: a batch run (process, run, the packages
    button) calls select_for_build with no triggers at all, and therefore can
    only ever build what column A says Yes to."""
    import ast
    import inspect
    import textwrap
    for fn in (run_mod.process_step, run_mod.cmd_run, run_mod.cmd_process,
               run_mod.run_command):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", "")) == "select_for_build"]
        for call in calls:
            assert "triggers" not in {k.arg for k in call.keywords}, fn.__name__


def test_verify_reads_a_gh_jid_link_through_its_own_page_never_as_a_board(monkeypatch, capsys):
    """verify --apply archives what it calls dead. A careers page carrying a
    Greenhouse posting id and no board slug once read as boards/<posting
    id>/jobs/, a 404, and so as gone, while the posting was live."""
    url = "https://www.quillfield.example/careers?gh_jid=4335626009"
    rows = [srow("New", k="8", company="Quillfield", role="VP Strategy", url=url)]

    class Sheet:
        def __init__(self, *a, **k):
            pass

        def read_pipeline(self, headers):
            return rows

    fetched, asked = [], []
    monkeypatch.setattr(run_mod, "build_context", lambda: (Cfg(), FakeCanon()))
    monkeypatch.setattr(run_mod, "Sheet", Sheet)
    monkeypatch.setattr(run_mod, "GoogleServiceAccount", lambda cfg: type(
        "T", (), {"access_token": staticmethod(lambda: "t")})())
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [])
    monkeypatch.setattr(run_mod, "fetch_with_retry",
                        lambda fetch, slug, pid: fetched.append((slug, pid)) or (False, "", ""))

    def discover(cfg, company, title, cache, careers_url="", posting_id=""):
        asked.append(posting_id)
        return "live", {"ats": "greenhouse", "slug": "quillfield", "id": posting_id}, "found by id"

    monkeypatch.setattr(run_mod, "discover_posting", discover)
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    run_mod.cmd_verify(apply=False)
    out = capsys.readouterr().out
    assert fetched == [], "a gh_jid link was fetched as if its posting id were a board"
    assert asked == ["4335626009"]
    assert "LIVE 1, DEAD 0" in out
