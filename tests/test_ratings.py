"""His Krish Score and Krish Reaction become the record (PR2), offline.

Every company, title and reaction here is invented. The repository is public,
and his own words and his scores against real companies never go in it.

What these hold: each row gets exactly one kind, by a fixed precedence; "7.5"
is never guessed into a 7; a Duplicate loses to a score of 6 or more; a blank
row is "not wanted" only behind a later batch he scored a day ago, and never
when hunter cannot date it; two countries are never one role; his reaction
never reaches a verdict path or the event log; the summary is counts only; a
second sync of an unchanged sheet writes nothing.
"""
import datetime
import pathlib
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from hunter import (alerts, batchstats, invariants, judgedata, learn, ratings,  # noqa: E402
                    report, universe, verdicts)
from hunter import run as R  # noqa: E402
from hunter import sheet as sheet_mod  # noqa: E402
from hunter.sheet import COLS, HEADERS, KRISH_REACTION, KRISH_SCORE, N_COLS, make_row  # noqa: E402
from hunter.sources import job_id  # noqa: E402
from test_his_columns import TwoTabs, pipeline  # noqa: E402

K, M = COLS[KRISH_SCORE], COLS[KRISH_REACTION]
NOW = datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.timezone.utc)
HOURS = datetime.timedelta(hours=1)

# Invented words, distinctive enough that a leak into a summary line is
# unmistakable.
WORDS_DOMAIN = "This is not my domain at all, quokka freight"
WORDS_LEGAL = "Not interested in wombat tooling as a business"
WORDS_LIKE = "Zephyrine has the platform team I want to build"


def cells(company, role, *, verdict="New", k="", m="", loc="New York, NY", ai=7, url=None):
    row = make_row(company=company, role=role,
                   jd_url=url or f"https://jobs.example.test/{job_id(company, role)}/{loc[:3]}",
                   score=ai, location=loc, source="ats_sweep", jd_snippet="x")
    row[COLS["Verdict"]] = verdict
    row[K], row[M] = k, m
    return row


def applied(*rows):
    return [list(HEADERS) + ["Archived On"]] + [list(r) + ["2026-10-01"] for r in rows]


def db_row(company, role, *, loc="New York, NY", presented="2026-10-03T12:00:00.123+00:00",
           suffix="", url=None, fit=None):
    return {"job_id": job_id(company, role) + suffix, "company": company, "title": role,
            "url": url or f"https://jobs.example.test/{job_id(company, role)}/{loc[:3]}",
            "job_url": "", "location": loc, "presented_at": presented,
            "judge_fit": fit, "judge_prompt_version": "v-test" if fit else None}


class DB:
    """hunter_seen_roles, hunter_krish_ratings and the event log, in memory,
    with PostgREST's merge-duplicates upsert on the ratings key."""

    def __init__(self, seen=(), stored=()):
        self.t = {"hunter_seen_roles": list(seen), "hunter_krish_ratings": [],
                  "hunter_verdict_events": []}
        self.gets, self.inserts, self.patches = [], [], []
        self.next_id = 1
        for s in stored:
            self.add_stored(s)

    def add_stored(self, row):
        row = dict(row)
        row.setdefault("id", self.next_id)
        self.next_id = max(self.next_id, row["id"]) + 1
        self.t["hunter_krish_ratings"].append(row)

    def get(self, cfg, table, params):
        self.gets.append(table)
        if table not in self.t:
            raise requests.HTTPError("404 relation does not exist")
        rows = list(self.t[table])
        for k, v in params.items():
            if k in ("select", "limit", "order"):
                continue
            if str(v).startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return [dict(r) for r in rows]

    def insert(self, cfg, table, rows, *, on_conflict=None, merge=False,
               ignore_duplicates=False):
        self.inserts.append((table, [dict(r) for r in rows]))
        if table != "hunter_krish_ratings":
            self.t.setdefault(table, []).extend(dict(r) for r in rows)
            return
        cols = on_conflict.split(",")
        for r in rows:
            key = tuple(r.get(c) for c in cols)
            have = next((x for x in self.t[table]
                         if tuple(x.get(c) for c in cols) == key), None)
            if have is not None:
                have.update(r)
            else:
                new = dict(r, id=self.next_id)
                self.next_id += 1
                new.setdefault("first_seen_at", r["last_seen_at"])
                self.t[table].append(new)

    def patch(self, cfg, table, match, values):
        self.patches.append((table, match, values))


@pytest.fixture
def wire(monkeypatch):
    def install(db):
        monkeypatch.setattr(ratings, "db_get", db.get)
        monkeypatch.setattr(ratings, "db_insert", db.insert)
        monkeypatch.setattr(R, "db_patch", db.patch)
        monkeypatch.setattr(R, "db_insert", db.insert)
        # ratings_step reads and forgets its failure marks in hunter_alerts
        db.ledger = wire_ledger(monkeypatch)
        return db
    return install


def tabs(pipe_rows, applied_rows=()):
    return TwoTabs(pipeline(*pipe_rows), applied(*applied_rows), applied_cols=N_COLS + 1)


def sync(sheet, *, apply=False, now=NOW, **kw):
    return ratings.sync(None, sheet, apply=apply, canon_headers=HEADERS, now=now, **kw)


CANON = type("C", (), {"sheet_headers": HEADERS})()


class Ledger:
    """hunter_alerts, workflow_runs and silent_failures in memory, behind
    the real alerts and report functions, so a test reads back what the
    watchdog would read."""

    def __init__(self, runs=()):
        self.t = {"hunter_alerts": [], "workflow_runs": list(runs), "silent_failures": [],
                  "agents": [], "hunter_application_approvals": [], "hunter_commands": []}

    @staticmethod
    def _match(row, k, v):
        v = str(v)
        if v.startswith("eq."):
            return str(row.get(k)) == v[3:]
        if v.startswith("like.") and v.endswith("*"):
            return str(row.get(k) or "").startswith(v[5:-1])
        if v == "is.null":
            return row.get(k) is None
        return True

    def get(self, cfg, table, params):
        rows = [r for r in self.t.get(table, [])
                if all(self._match(r, k, v) for k, v in params.items()
                       if k not in ("select", "limit", "order"))]
        if params.get("order") == "run_at.desc":
            rows = sorted(rows, key=lambda r: r["run_at"], reverse=True)
        if params.get("limit", "").isdigit():
            rows = rows[:int(params["limit"])]
        return [dict(r) for r in rows]

    def insert(self, cfg, table, rows, **kw):
        for r in rows:
            if table == "hunter_alerts" and any(
                    x["kind"] == r["kind"] and x["fingerprint"] == r["fingerprint"]
                    for x in self.t[table]):
                continue
            self.t.setdefault(table, []).append(dict(r))

    def delete(self, cfg, table, params):
        self.t[table] = [r for r in self.t[table]
                         if not all(self._match(r, k, v) for k, v in params.items())]

    def patch(self, cfg, table, match, values):
        for r in self.t.get(table, []):
            if all(str(r.get(k)) == str(v) for k, v in match.items()):
                r.update(values)

    def runs(self):
        return [r for r in self.t["workflow_runs"] if r.get("outcome") == R.RATINGS_FAILED]


def wire_ledger(monkeypatch, runs=()):
    led = Ledger(runs)
    for mod in (alerts, report):
        monkeypatch.setattr(mod, "db_get", led.get)
        monkeypatch.setattr(mod, "db_insert", led.insert)
    monkeypatch.setattr(alerts, "db_delete", led.delete)
    monkeypatch.setattr(report, "db_patch", led.patch)
    return led


def stored(company, role, score, *, presented, first_seen, suffix=""):
    return {"job_id": job_id(company, role) + suffix, "kind": "scored", "score": score,
            "score_text": None, "reaction": None, "presented_at": presented,
            "observation_key": ratings.observation_key("scored", score, None, None),
            "first_seen_at": first_seen.isoformat(), "last_seen_at": first_seen.isoformat(),
            "company": company, "title": role, "pairing": "matched", "sheet_hash": "old"}


# ---------- one kind per row ----------

@pytest.mark.parametrize("k,m,a,past,kind", [
    ("8", "", "New", False, "scored"),
    ("10", "Strong", "New", False, "scored"),
    ("1", "", "Declined - comp below bar", False, "scored"),
    ("7.5", "", "New", False, "score_unreadable"),
    ("8/10", "", "New", False, "score_unreadable"),
    ("seven", "", "Yes", False, "score_unreadable"),
    ("11", "", "New", False, "score_unreadable"),
    ("", "Not for me", "New", False, "reaction_only"),
    ("", "Not for me", "Yes", True, "reaction_only"),
    ("", "Duplicate", "New", False, "duplicate"),
    ("", " duplicate. ", "New", False, "duplicate"),
    ("3", "DUPLICATE", "New", False, "duplicate"),
    ("5", "Duplicate", "New", False, "duplicate"),
    ("6", "Duplicate", "New", False, "scored"),
    ("9", "Duplicate", "New", False, "scored"),
    ("7.5", "Duplicate", "New", False, "score_unreadable"),
    ("", "Not a duplicate of the other one", "New", False, "reaction_only"),
    ("", "Same role as above", "New", False, "reaction_only"),
    ("", "", "New", True, "unscored_after_review"),
    ("", "", "", True, "unscored_after_review"),
    ("", "", "New", False, "pending"),
    ("", "", "Yes", True, "yes_unscored"),
    ("", "", "Applied", True, "column_a_only"),
    ("", "", "Declined - stage wrong", True, "column_a_only"),
])
def test_every_row_gets_exactly_one_kind(k, m, a, past, kind):
    assert ratings.kind_of(k, m, a, reviewed_past=past) == kind


def test_a_half_point_is_never_rounded_into_a_score(wire):
    wire(DB(seen=[db_row("Quillfeather", "VP Strategy")]))
    out = sync(tabs([cells("Quillfeather", "VP Strategy", k="7.5")]))
    assert out["roles"]["score_unreadable"] == 1 and out["roles"]["scored"] == 0
    assert ratings.whole_score("7.5") is None and ratings.whole_score("7") == 7


def test_the_unreadable_score_is_kept_as_typed_and_scores_nothing(wire):
    db = wire(DB(seen=[db_row("Quillfeather", "VP Strategy")]))
    sync(tabs([cells("Quillfeather", "VP Strategy", k="7.5")]), apply=True)
    (row,) = db.t["hunter_krish_ratings"]
    assert row["kind"] == "score_unreadable"
    assert row["score"] is None and row["score_text"] == "7.5"


def test_precedence_is_his_number_first():
    assert ratings.PRECEDENCE == ("scored", "score_unreadable", "reaction_only",
                                  "unscored_after_review", "yes_unscored", "duplicate")


def test_one_rating_per_role_and_the_rest_are_its_twins(wire):
    """Two rows of one role: a Duplicate above a scored 8. The 8 speaks."""
    db = wire(DB(seen=[db_row("Orrery Labs", "Head of Operations")]))
    sheet = tabs([cells("Orrery Labs", "Head of Operations", m="Duplicate"),
                  cells("Orrery Labs", "Head of Operations", k="8",
                        url="https://jobs.example.test/other-link")])
    out = sync(sheet, apply=True)
    assert out["rows"]["duplicate"] == 1 and out["rows"]["scored"] == 1
    assert out["roles"] == {"scored": 1}
    (row,) = db.t["hunter_krish_ratings"]
    assert row["score"] == 8 and row["kind"] == "scored"
    assert [t["kind"] for t in row["twins"]] == ["duplicate"]
    # A twin on the sheet: reported, and the duplicate check never sees it.
    assert out["sheet_twins"] == [("Pipeline", 3)]
    assert out["duplicate_events"] == []


def test_a_reaction_beats_a_blank_yes_on_the_same_role(wire):
    wire(DB(seen=[db_row("Tidewell", "Head of Partnerships")]))
    sheet = tabs([cells("Tidewell", "Head of Partnerships", verdict="Yes"),
                  cells("Tidewell", "Head of Partnerships", m="Too narrow",
                        url="https://jobs.example.test/tidewell-2")])
    out = sync(sheet)
    assert out["roles"] == {"reaction_only": 1}


# ---------- the batch rule ----------

def batch_case(wire, *, later_presented, first_seen_ago, blank_db=True):
    seen = [db_row("Larchmont", "Director, Strategy",
                   presented="2026-10-03T12:00:00.1+00:00"),
            db_row("Fernhollow", "VP Partnerships", presented=later_presented)]
    if not blank_db:
        seen = seen[1:]
    db = DB(seen=seen, stored=[stored("Fernhollow", "VP Partnerships", 8,
                                      presented=later_presented,
                                      first_seen=NOW - first_seen_ago)])
    wire(db)
    sheet = tabs([cells("Larchmont", "Director, Strategy"),
                  cells("Fernhollow", "VP Partnerships", k="8")])
    return sync(sheet)


def test_a_blank_behind_a_later_batch_he_scored_a_day_ago_is_not_wanted(wire):
    out = batch_case(wire, later_presented="2026-10-08T09:00:00+00:00",
                     first_seen_ago=25 * HOURS)
    assert out["roles"]["unscored_after_review"] == 1 and out["pending_roles"] == 0


def test_the_wait_is_a_full_day(wire):
    out = batch_case(wire, later_presented="2026-10-08T09:00:00+00:00",
                     first_seen_ago=23 * HOURS)
    assert out["roles"]["unscored_after_review"] == 0 and out["pending_roles"] == 1


def test_a_score_in_the_same_batch_says_nothing_about_the_rows_below_it(wire):
    """Rows staged in one run differ by milliseconds; the batch is the date."""
    out = batch_case(wire, later_presented="2026-10-03T12:00:00.9+00:00",
                     first_seen_ago=72 * HOURS)
    assert out["roles"]["unscored_after_review"] == 0 and out["pending_roles"] == 1


def test_a_row_hunter_cannot_date_is_never_unscored(wire):
    out = batch_case(wire, later_presented="2026-10-08T09:00:00+00:00",
                     first_seen_ago=72 * HOURS, blank_db=False)
    assert out["roles"]["unscored_after_review"] == 0 and out["pending_roles"] == 1


def test_with_no_first_score_on_record_nothing_is_past_its_wait(wire):
    """The first sync, or a dry run before the table exists."""
    db = DB(seen=[db_row("Larchmont", "Director, Strategy"),
                  db_row("Fernhollow", "VP Partnerships",
                         presented="2026-10-08T09:00:00+00:00")])
    del db.t["hunter_krish_ratings"]
    wire(db)
    out = sync(tabs([cells("Larchmont", "Director, Strategy"),
                     cells("Fernhollow", "VP Partnerships", k="8")]))
    assert out["stored_readable"] is False
    assert out["roles"] == {"scored": 1} and out["pending_roles"] == 1
    assert any("treated as empty" in line for line in ratings.lines(out))


def test_a_yes_he_has_not_scored_is_a_yes_not_a_no(wire):
    out = batch_case(wire, later_presented="2026-10-08T09:00:00+00:00",
                     first_seen_ago=72 * HOURS)
    db = DB(seen=[db_row("Larchmont", "Director, Strategy")])
    wire(db)
    out = sync(tabs([cells("Larchmont", "Director, Strategy", verdict="Yes")]))
    assert out["roles"] == {"yes_unscored": 1}


# ---------- pairing ----------

def test_two_countries_are_two_roles_even_when_london_comes_first(wire):
    """match_rows ignores location, so a London row above its New York twin
    took the New York record on job id alone. The pair is not trusted."""
    ny = db_row("Brightwater", "Director, Alliances", loc="New York, NY",
                url="https://jobs.example.test/bw-ny")
    uk = db_row("Brightwater", "Director, Alliances", loc="London, UK",
                suffix="-uk", url="https://jobs.example.test/bw-uk")
    db = wire(DB(seen=[ny, uk]))
    sheet = tabs([cells("Brightwater", "Director, Alliances", k="6",
                        loc="London, UK", url="https://jobs.example.test/bw-lnd-other"),
                  cells("Brightwater", "Director, Alliances", k="4",
                        loc="New York, NY", url="https://jobs.example.test/bw-nyc-other")])
    out = sync(sheet, apply=True)
    assert out["roles"] == {"scored": 2}
    got = {r["job_id"]: r["score"] for r in db.t["hunter_krish_ratings"]}
    assert got == {ny["job_id"]: 4, uk["job_id"]: 6}


def test_two_countries_hunter_never_recorded_mint_two_ids(wire):
    db = wire(DB(seen=[]))
    sheet = tabs([cells("Saltmarsh", "VP Revenue", k="6", loc="New York, NY"),
                  cells("Saltmarsh", "VP Revenue", k="8", loc="London, UK")])
    sync(sheet, apply=True)
    ids = sorted(r["job_id"] for r in db.t["hunter_krish_ratings"])
    assert ids == [job_id("Saltmarsh", "VP Revenue"), job_id("Saltmarsh", "VP Revenue") + "-uk"]
    assert {r["pairing"] for r in db.t["hunter_krish_ratings"]} == {"minted"}


def test_a_minted_id_never_lands_on_a_role_in_another_country(wire):
    """The bare id is held by a US record hunter has; a London row with no
    record of its own gets its own id, not the US role's."""
    us = db_row("Saltmarsh", "VP Revenue", loc="Austin, TX")
    db = wire(DB(seen=[us]))
    sync(tabs([cells("Saltmarsh", "VP Revenue", k="8", loc="London, UK",
                     url="https://jobs.example.test/elsewhere")]), apply=True)
    (row,) = db.t["hunter_krish_ratings"]
    assert row["job_id"] != us["job_id"] and row["pairing"] == "minted"


def test_a_record_of_unknown_place_never_joins_two_countries(wire):
    """An unknown place matches anything, so both rows would find the one
    record; the second country is minted its own id."""
    rec = db_row("Saltmarsh", "VP Revenue", loc="")
    db = wire(DB(seen=[rec]))
    sync(tabs([cells("Saltmarsh", "VP Revenue", k="6", loc="New York, NY",
                     url="https://jobs.example.test/s-ny"),
               cells("Saltmarsh", "VP Revenue", k="8", loc="London, UK",
                     url="https://jobs.example.test/s-ldn")]), apply=True)
    got = {r["job_id"]: (r["score"], r["pairing"]) for r in db.t["hunter_krish_ratings"]}
    assert got == {rec["job_id"]: (6, "matched"), rec["job_id"] + "-uk": (8, "minted")}


def test_an_unmatched_twin_is_paired_by_identity_over_every_record(wire):
    """match_rows gives the one record to the first row; the second row of
    the same role is unmatched, and is still that role."""
    rec = db_row("Kestrel AI", "Head of Events")
    pairs = ratings.pair_sheet(
        tabs([cells("Kestrel AI", "Head of Events"),
              cells("Kestrel AI", "Head of Events",
                    url="https://jobs.example.test/second")]).read_pipeline(HEADERS),
        [rec])
    assert [(p.job_id, p.pairing) for p in pairs] == [
        (rec["job_id"], "matched"), (rec["job_id"], "identity")]


def test_a_row_with_no_record_still_gets_its_rating(wire):
    db = wire(DB(seen=[db_row("Otterbrook", "VP Sales")]))
    out = sync(tabs([cells("Otterbrook", "VP Sales", k="7"),
                     cells("Nimbleford", "Chief of Staff", k="9", loc="Remote")]),
               apply=True)
    assert out["roles"] == {"scored": 2}
    assert out["pairing"] == {"matched": 1, "minted": 1}
    minted = [r for r in db.t["hunter_krish_ratings"] if r["pairing"] == "minted"]
    assert minted[0]["presented_at"] is None


def test_judgedata_keeps_its_ids_for_rows_match_rows_cannot_pair(monkeypatch):
    """collect shares the pairing but not the identity fallback: taking it
    re-dated 47 ruled roles and would have moved them across judge-eval's
    cutoff. The second row of a twin is unmatched; ratings files it under
    the identity hit, collect keeps the bare id and no record, as before."""
    rec = db_row("Kestrel AI", "Head of Events", suffix="-a1b2c3",
                 presented="2026-09-01T00:00:00+00:00")
    rows = [cells("Kestrel AI", "Head of Events", verdict="Declined - travel",
                  url="https://jobs.example.test/no-such-link"),
            cells("Kestrel AI", "Head of Events", verdict="Declined - travel",
                  url="https://jobs.example.test/another-link")]
    sheet = tabs([], rows)
    pairs = ratings.pair_sheet(sheet.read_archive(), [rec])
    assert [(p.job_id, p.pairing) for p in pairs] == [(rec["job_id"], "matched"),
                                                     (rec["job_id"], "identity")]
    monkeypatch.setattr(judgedata, "db_get",
                        lambda cfg, t, p: [rec] if t == "hunter_seen_roles" else [])
    monkeypatch.setattr(judgedata, "blind_rulings", lambda *a: [])

    class Canon:
        sheet_headers = HEADERS
    ruled, _ = judgedata.collect(None, sheet, Canon())
    assert [(r.job_id, r.presented_at) for r in ruled] == [
        (rec["job_id"], "2026-09-01T00:00:00+00:00"),
        (job_id("Kestrel AI", "Head of Events"), None)]


def test_applied_on_the_previous_layout_is_paired_but_never_rated(wire):
    prev = [list(sheet_mod.PREVIOUS_HEADERS) + ["Archived On"]]
    row = [""] * len(sheet_mod.PREVIOUS_HEADERS)
    row[0], row[1], row[2] = "Declined - travel", "Dunmore", "VP Growth"
    # Under the new positions these cells would read as a K and an M.
    row[10], row[12] = "Prose about the fit", "Series B"
    prev.append(row + ["2026-09-30"])
    wire(DB(seen=[db_row("Dunmore", "VP Growth")]))
    sheet = TwoTabs(pipeline(cells("Halyard", "VP Strategy", k="8")), prev)
    out = sync(sheet)
    assert out["roles"] == {"scored": 1}
    assert out["applied_tab"].startswith("skipped: previous layout")
    assert any("Applied skipped: previous layout" in line for line in ratings.lines(out))


# ---------- his words never become a verdict ----------

def test_his_reaction_never_reaches_a_verdict_path(wire, monkeypatch):
    calls = []
    for mod, name in ((verdicts, "parse"), (learn, "classify"), (learn, "infer"),
                      (learn, "record"), (learn, "from_sheet_rows"),
                      (judgedata, "label_of")):
        real = getattr(mod, name)

        def spy(*a, _real=real, _name=name, **k):
            calls.append((_name, a))
            return _real(*a, **k)
        monkeypatch.setattr(mod, name, spy)
    seen = [db_row("Quokkaline", "VP Partnerships"), db_row("Wombatique", "Head of BD"),
            db_row("Zephyrine", "Chief of Staff")]
    db = wire(DB(seen=seen))
    sheet = tabs([cells("Quokkaline", "VP Partnerships", m=WORDS_DOMAIN),
                  cells("Wombatique", "Head of BD", k="3", m=WORDS_LEGAL),
                  cells("Zephyrine", "Chief of Staff", k="9", m=WORDS_LIKE)])
    before = learn.company_declines(db.t["hunter_verdict_events"])
    summary = []
    R.ratings_step(None, type("C", (), {"sheet_headers": HEADERS})(), sheet, summary,
                   apply=True, roles=seen)
    for name, args in calls:
        assert not any(w in str(args) for w in (WORDS_DOMAIN, WORDS_LEGAL, WORDS_LIKE)), name
    assert {t for t, _ in db.inserts} == {"hunter_krish_ratings"}
    assert db.patches == [], "no krish_verdict, no status, nothing on hunter_seen_roles"
    assert db.t["hunter_verdict_events"] == []
    assert learn.company_declines(db.t["hunter_verdict_events"]) == before == {}
    stored_words = {r["reaction"] for r in db.t["hunter_krish_ratings"]}
    assert ratings.norm(WORDS_DOMAIN) in stored_words, "kept, normalised, in its own table"


def test_a_duplicate_role_goes_to_the_duplicate_check_in_memory_only(wire):
    a = db_row("Gannetry", "Director, Sales Development")
    b = db_row("Gannetry", "Director, Sales Development, USA",
               url="https://jobs.example.test/inkwell-usa")
    db = wire(DB(seen=[a, b]))
    sheet = tabs([cells("Gannetry", "Director, Sales Development", k="7"),
                  cells("Gannetry", "Director, Sales Development, USA", m="Duplicate",
                        url="https://jobs.example.test/inkwell-usa")])
    summary = []
    R.ratings_step(None, type("C", (), {"sheet_headers": HEADERS})(), sheet, summary,
                   apply=True, roles=[dict(a, krish_verdict=""), dict(b, krish_verdict="")])
    assert db.t["hunter_verdict_events"] == []
    assert [(m["job_id"], v["status"]) for t, m, v in db.patches] == [(b["job_id"], "duplicate")]
    assert any("1 checked, 1 marked duplicate" in line for line in summary)


# ---------- counts only ----------

def test_the_summary_is_counts_only(wire, capsys):
    seen = [db_row("Quokkaline", "VP Partnerships"), db_row("Zephyrine", "Chief of Staff")]
    wire(DB(seen=seen))
    sheet = tabs([cells("Quokkaline", "VP Partnerships", m=WORDS_DOMAIN),
                  cells("Zephyrine", "Chief of Staff", k="9", m=WORDS_LIKE),
                  cells("Zephyrine", "Chief of Staff", m="Duplicate",
                        url="https://jobs.example.test/z2")])
    summary = R.Summary()
    R.ratings_step(None, type("C", (), {"sheet_headers": HEADERS})(), sheet, summary,
                   apply=True, roles=seen)
    printed = capsys.readouterr().out + "\n".join(summary)
    assert "Krish Score rows:" in printed
    for leak in (WORDS_DOMAIN, WORDS_LIKE, "Quokkaline", "Zephyrine", "Chief of Staff"):
        assert leak not in printed


def test_a_failed_sync_is_recorded_without_quoting_the_row(monkeypatch):
    def boom(*a, **k):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = f'Failing row contains (x, {WORDS_DOMAIN})'.encode()
        raise requests.HTTPError(f"400: {resp.text}", response=resp)
    monkeypatch.setattr(ratings, "sync", boom)
    led = wire_ledger(monkeypatch)
    summary = []
    for _ in range(2):
        out = R.ratings_step(None, CANON, object(), summary, apply=True)
    assert out == {}
    assert summary[0] == f"{R.RATINGS_FAILED}: HTTPError 400"
    (run,) = led.t["workflow_runs"]
    assert run["status"] == "error" and run["error_message"] == "HTTPError 400"
    everything = str(led.t) + "\n".join(summary)
    assert WORDS_DOMAIN not in everything and "Failing row" not in everything
    before = str(led.t)
    R.ratings_step(None, CANON, object(), [], apply=False)
    R.ratings_step(None, CANON, object(), [], apply=False)
    assert str(led.t) == before, "a dry run records nothing"


# ---------- writes, idempotence, latest ----------

def test_a_second_sync_of_the_same_sheet_writes_nothing(wire):
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy"), db_row("Mossgate", "Head of GTM")]))
    sheet = tabs([cells("Halyard", "VP Strategy", k="8"),
                  cells("Mossgate", "Head of GTM", m="Too early")])
    first = sync(sheet, apply=True)
    assert first["new"] == 2 and first["written"] == 2
    writes, rows = len(db.inserts), [dict(r) for r in db.t["hunter_krish_ratings"]]
    db.gets.clear()
    again = sync(sheet, apply=True, now=NOW + HOURS)
    assert again["unchanged"] and len(db.inserts) == writes
    assert "hunter_seen_roles" not in db.gets, "nothing re-paired"
    assert db.t["hunter_krish_ratings"] == rows
    forced = sync(sheet, apply=True, now=NOW + 2 * HOURS, force=True)
    assert forced["new"] == 0 and forced["seen_again"] == 2
    assert len(db.t["hunter_krish_ratings"]) == 2
    assert {r["first_seen_at"] for r in db.t["hunter_krish_ratings"]} == {NOW.isoformat()}


def test_a_re_sort_alone_is_not_a_change(wire):
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy"), db_row("Mossgate", "Head of GTM")]))
    a, b = cells("Halyard", "VP Strategy", k="8"), cells("Mossgate", "Head of GTM", k="4")
    sync(tabs([a, b]), apply=True)
    assert sync(tabs([b, a]), apply=True, now=NOW + HOURS)["unchanged"]


def test_time_alone_re_reads_when_a_batch_becomes_reviewed(wire):
    db = wire(DB(seen=[db_row("Larchmont", "Director, Strategy"),
                       db_row("Fernhollow", "VP Partnerships",
                              presented="2026-10-08T09:00:00+00:00")]))
    sheet = tabs([cells("Larchmont", "Director, Strategy"),
                  cells("Fernhollow", "VP Partnerships", k="8")])
    first = sync(sheet, apply=True)
    assert first["pending_roles"] == 1
    assert sync(sheet, apply=True, now=NOW + 23 * HOURS)["unchanged"]
    later = sync(sheet, apply=True, now=NOW + 25 * HOURS)
    assert not later["unchanged"] and later["roles"]["unscored_after_review"] == 1


def test_the_then_columns_are_frozen_at_the_first_sighting(wire):
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy", fit=6)]))
    sync(tabs([cells("Halyard", "VP Strategy", k="8", ai=4)]), apply=True)
    sync(tabs([cells("Halyard", "VP Strategy", k="8", ai=9)]), apply=True,
         now=NOW + HOURS, force=True)
    (row,) = db.t["hunter_krish_ratings"]
    assert (row["ai_score_then"], row["judge_fit_then"]) == (4, 6)
    assert row["judge_prompt_version_then"] == "v-test"


def test_latest_is_the_rating_seen_most_recently(wire):
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy")]))
    sync(tabs([cells("Halyard", "VP Strategy", k="8")]), apply=True)
    sync(tabs([cells("Halyard", "VP Strategy", k="9")]), apply=True, now=NOW + HOURS)
    assert ratings.latest(None)[job_id("Halyard", "VP Strategy")]["score"] == 9
    # and back again: the first observation is bumped, not duplicated
    sync(tabs([cells("Halyard", "VP Strategy", k="8")]), apply=True, now=NOW + 2 * HOURS)
    assert ratings.latest(None)[job_id("Halyard", "VP Strategy")]["score"] == 8
    assert len(db.t["hunter_krish_ratings"]) == 2


def test_a_write_that_does_not_read_back_is_an_error(wire, monkeypatch):
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy")]))
    monkeypatch.setattr(ratings, "db_insert", lambda *a, **k: None)
    with pytest.raises(ratings.RatingsError, match="read back 0 of 1"):
        sync(tabs([cells("Halyard", "VP Strategy", k="8")]), apply=True)


def test_a_dry_run_writes_nothing(wire):
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy")]))
    out = sync(tabs([cells("Halyard", "VP Strategy", k="8")]))
    assert out["new"] == 1 and db.inserts == []


# ---------- the wiring ----------

def test_settle_reads_his_scores_even_when_nothing_is_decided(monkeypatch):
    calls = []

    class Spy:
        def ensure_archive_columns(self, **kw):
            return {"noop": True, "state": "current", "rows": 0}

    canon = type("C", (), {"sheet_headers": HEADERS})()
    monkeypatch.setattr(R, "build_context", lambda: (None, canon))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: Spy())
    monkeypatch.setattr(R, "settle_step", lambda *a, **k: calls.append("settle") or {"decided": 0})
    monkeypatch.setattr(R, "ratings_step",
                        lambda cfg, c, s, summary, apply, roles=None:
                        calls.append(("ratings", apply, roles)) or {})
    # The one move comes after the record has read his scores (PR4).
    monkeypatch.setattr(R, "leave_step", lambda cfg, c, s, summary, apply, **kw:
                        calls.append(("leave", apply)) or {})
    assert R.cmd_settle(apply=True) == 0
    assert calls == ["settle", ("ratings", True, None), ("leave", True)]


def test_process_reads_his_scores_after_the_learning_step(monkeypatch):
    calls = []

    class Stop(Exception):
        pass
    monkeypatch.setattr(R, "archive_columns_step", lambda sheet, apply: "ok")
    monkeypatch.setattr(R, "amendment_step", lambda *a: 0)
    monkeypatch.setattr(R, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(R, "reconcile", lambda *a, **k: R.ReconcileLedger())
    monkeypatch.setattr(R, "sync_applied_state", lambda *a: {"written": 0, "from_sheet": 0,
                                                              "from_ledger": 0})
    monkeypatch.setattr(R, "learning_step", lambda cfg, apply: calls.append("learn") or {
        "roles": ["r"], "company_declines": {}, "recorded": 0, "findings": [], "fixed": [],
        "unexplained": []})

    def rate(cfg, canon, sheet, summary, *, apply, roles=None):
        calls.append(("ratings", apply, roles))
        raise Stop
    monkeypatch.setattr(R, "ratings_step", rate)
    with pytest.raises(Stop):
        R.process_step(None, None, object(), [])
    assert calls == ["learn", ("ratings", True, ["r"])]


def test_staging_records_the_score_he_is_shown_with_presented_at(monkeypatch):
    patches = []
    monkeypatch.setattr(R, "db_patch", lambda cfg, t, m, v: patches.append(v))
    summary = []
    marker = R.PresentedMarker(None, summary)
    marker.mark("acme:vp", {"presented_at": "now"}, 8)
    marker.report()
    assert patches == [{"presented_at": "now", "ai_score_shown": 8}] and summary == []


def test_staging_still_writes_presented_at_before_the_column_exists(monkeypatch):
    patches = []

    def patch(cfg, t, m, v):
        if "ai_score_shown" in v:
            resp = requests.Response()
            resp.status_code = 400
            resp._content = b"Could not find the 'ai_score_shown' column of 'hunter_seen_roles'"
            raise requests.HTTPError("400", response=resp)
        patches.append(v)
    monkeypatch.setattr(R, "db_patch", patch)
    summary = []
    marker = R.PresentedMarker(None, summary)
    for jid in ("a", "b"):
        marker.mark(jid, {"presented_at": "now"}, 8)
    marker.report()
    assert patches == [{"presented_at": "now"}, {"presented_at": "now"}]
    assert summary and "not recorded for 2 staged row(s)" in summary[0]


def test_any_other_staging_failure_still_raises(monkeypatch):
    def patch(cfg, t, m, v):
        resp = requests.Response()
        resp.status_code = 500
        resp._content = b"server fell over"
        raise requests.HTTPError("500", response=resp)
    monkeypatch.setattr(R, "db_patch", patch)
    with pytest.raises(requests.HTTPError):
        R.PresentedMarker(None, []).mark("a", {"presented_at": "now"}, 8)


# ---------- companies he said Yes to ----------

def test_a_company_he_scored_8_becomes_top_only_with_lookalike_5(monkeypatch):
    plover = job_id("Plover Systems", "VP Partnerships")
    tarn = job_id("Tarnwick", "Head of Strategy")
    old_yes = job_id("Corbel Bank", "Director, AI")
    monkeypatch.setattr(universe, "db_get", lambda cfg, t, p: {
        "hunter_verdict_events": [{"job_id": old_yes, "verdict": "go"}],
        "hunter_seen_roles": [{"job_id": plover, "company": "Plover Systems"},
                              {"job_id": tarn, "company": "Tarnwick"},
                              {"job_id": old_yes, "company": "Corbel Bank"}]}[t])
    monkeypatch.setattr(ratings, "latest", lambda cfg: {
        plover: {"kind": "scored", "score": 8, "company": "Plover Systems"},
        tarn: {"kind": "scored", "score": 8, "company": "Tarnwick"},
        old_yes: {"kind": "scored", "score": 4, "company": "Corbel Bank"}})
    got = {c.name: c for c in universe.from_yes(None)}
    assert set(got) == {"Plover Systems", "Tarnwick"}, "his 4 wins over a column A Yes"
    got["Plover Systems"].lookalike = 5.0
    got["Tarnwick"].lookalike = 3.0
    assert got["Plover Systems"].top
    assert not got["Tarnwick"].top
    assert universe.SAID_YES_MIN == 5


@pytest.mark.parametrize("rating,kept", [
    ({"kind": "scored", "score": 7}, False),
    ({"kind": "scored", "score": 8}, True),
    ({"kind": "reaction_only", "score": None}, False),
    ({"kind": "unscored_after_review", "score": None}, False),
    ({"kind": "yes_unscored", "score": None}, True),
    ({"kind": "duplicate", "score": None}, True),
    ({"kind": "score_unreadable", "score": None}, True),
])
def test_his_score_overrides_a_column_a_yes_both_ways(rating, kept):
    yes, _ = universe.yes_job_ids([{"job_id": "j", "verdict": "go"}], {"j": rating})
    assert ("j" in yes) is kept


def test_a_ratings_table_that_cannot_be_read_leaves_column_a_alone(monkeypatch, capsys):
    monkeypatch.setattr(universe, "db_get", lambda cfg, t, p: {
        "hunter_verdict_events": [{"job_id": "a:b", "verdict": "go"}],
        "hunter_seen_roles": [{"job_id": "a:b", "company": "Arbor"}]}[t])

    def missing(cfg):
        raise requests.HTTPError("404")
    monkeypatch.setattr(ratings, "latest", missing)
    assert [c.name for c in universe.from_yes(None)] == ["Arbor"]
    assert "from column A only" in capsys.readouterr().out


# ---------- accept means accept, everywhere ----------

@pytest.mark.parametrize("text,ok", [
    ("go", True), ("applied", True), ("Yes", True), ("approved", True),
    ("Applied", True), ("Applied 2026-10-03", True), ("y", True),
    ("rejection", False), ("Declined - travel", False), ("New", False), ("", False),
    ("Already applied to the other one", False), ("I would apply", False),
])
def test_is_accept(text, ok):
    assert verdicts.is_accept(text) is ok


def events_and_roles():
    roles = [{"job_id": f"c{i}:r", "company": f"Co{i}", "presented_at": "2026-09-24T10:00:00+00:00",
              "source": "ats_sweep", "krish_verdict": ""} for i in range(6)]
    events = [{"job_id": "c0:r", "company": "Co0", "verdict": "applied", "source": "sheet column A",
               "recorded_at": "1"},
              {"job_id": "c1:r", "company": "Co1", "verdict": "applied", "source": "sheet column A",
               "recorded_at": "2"},
              {"job_id": "c2:r", "company": "Co2", "verdict": "go", "source": "sheet column A",
               "recorded_at": "3"}] + [
              {"job_id": f"c{i}:r", "company": f"Co{i}", "verdict": "rejection",
               "source": "sheet column A", "recorded_at": str(i)} for i in (3, 4, 5)]
    return roles, events


def test_applied_counts_as_accepted_in_the_accept_rate(monkeypatch):
    roles, events = events_and_roles()
    monkeypatch.setattr(batchstats, "db_get",
                        lambda cfg, t, p: roles if t == "hunter_seen_roles" else events)
    monkeypatch.setattr(ratings, "latest", lambda cfg: {})
    (b,) = batchstats.measure(None)
    assert (b.accepted, b.verdicted, b.rate) == (3, 6, 0.5)


def test_applied_counts_as_yes_on_the_target_companies_tab(monkeypatch):
    roles, events = events_and_roles()
    monkeypatch.setattr(R, "db_get",
                        lambda cfg, t, p: roles if t == "hunter_seen_roles" else events)
    out = R.company_outcomes(None)
    assert sum(v["yes"] for v in out.values()) == 3
    assert sum(v["no"] for v in out.values()) == 3


def test_applied_counts_as_yes_in_the_staged_company_table(monkeypatch):
    roles, events = events_and_roles()
    monkeypatch.setattr(R, "db_get",
                        lambda cfg, t, p: roles if t == "hunter_seen_roles" else events)
    lines = R.staged_company_lines(None)
    yes = sum(int(line.split()[2]) for line in lines[2:])
    assert yes == 3


# ---------- the score by batch ----------

def test_spearman_with_ties_matches_the_hand_computed_value():
    # x ranks: 1, 2.5, 2.5, 4, 5, 6; y ranks: 2, 1, 3.5, 3.5, 6, 5
    x = [1, 2, 2, 3, 4, 5]
    y = [5, 4, 6, 6, 9, 8]
    rx = [1, 2.5, 2.5, 4, 5, 6]
    ry = [2, 1, 3.5, 3.5, 6, 5]
    mx, my = sum(rx) / 6, sum(ry) / 6
    want = (sum((a - mx) * (b - my) for a, b in zip(rx, ry))
            / (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5)
    assert batchstats.spearman(x, y) == pytest.approx(want)
    # by hand: rank deviations (-2.5, -1, -1, .5, 1.5, 2.5) and
    # (-1.5, -2.5, 0, 0, 2.5, 1.5); covariance 13.75, each variance 17
    assert want == pytest.approx(13.75 / 17)


def test_spearman_edges():
    assert batchstats.spearman([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]) == pytest.approx(1)
    assert batchstats.spearman([1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]) == pytest.approx(-1)
    assert batchstats.spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) is None, "under 6 pairs"
    assert batchstats.spearman([7] * 6, [1, 2, 3, 4, 5, 6]) is None, "constant AI score"
    assert batchstats.spearman([1, 2, 3, 4, 5, 6], [8] * 6) is None, "constant score"
    with pytest.raises(ValueError):
        batchstats.spearman([1, 2], [1])


def batch(key, scores, ai=None):
    b = batchstats.Batch(key=key, staged=len(scores))
    b.scores = list(scores)
    b.ai_pairs = list(zip(ai, scores)) if ai else []
    return b


def test_the_score_alarm_fires_on_two_settled_batches_under_the_floor():
    low = [8, 3, 4, 2, 5, 6, 3]           # 1 of 7 at 7 or more
    batches = [batch("2026-10-20", low), batch("2026-10-23", low)]
    warn = batchstats.score_falling(batches)
    assert warn and "Fewer than 40%" in warn
    assert batchstats.falling(batches) == warn
    f = invariants.check_accept_rate(batches)
    assert f.state == invariants.BROKEN


def test_the_score_alarm_stays_quiet_when_it_should():
    low = [8, 3, 4, 2, 5, 6, 3]
    his_worst = [7, 8, 9, 7, 7, 8, 3, 4, 2, 5]   # 6 of 10, his 10-04 batch
    assert batchstats.score_falling([batch("a", low), batch("b", his_worst)]) == ""
    assert batchstats.score_falling([batch("a", low)]) == "", "one batch is not a trend"
    assert batchstats.score_falling([batch("a", low), batch("b", [2, 3, 4])]) == "", \
        "a batch he has barely scored has no share"
    f = invariants.check_accept_rate([batch("a", his_worst), batch("b", his_worst)])
    assert f.state == invariants.OK and "Krish Score by batch" in f.detail


def test_the_floor_is_clearly_below_his_four_batches():
    his = [17 / 25, 6 / 10, 5 / 7, 11 / 14]
    assert batchstats.SCORE_FLOOR <= min(his) - 0.15


def test_measure_counts_his_scores_per_batch(monkeypatch):
    roles = [{"job_id": f"j{i}", "company": "Co", "presented_at": "2026-10-11T12:00:00+00:00",
              "source": "x", "krish_verdict": "", "ai_score_shown": ai}
             for i, ai in enumerate([3, 4, 5, 6, 7, 8, 9])]
    monkeypatch.setattr(batchstats, "db_get",
                        lambda cfg, t, p: roles if t == "hunter_seen_roles" else [])
    scores = [2, 4, 5, 7, 8, 8, 9]
    rated = {f"j{i}": {"kind": "scored", "score": s, "ai_score_then": roles[i]["ai_score_shown"]}
             for i, s in enumerate(scores)}
    rated["j6"]["ai_score_then"] = 2          # J moved after he was shown it
    rated["j0"] = {"kind": "reaction_only", "score": None}
    monkeypatch.setattr(ratings, "latest", lambda cfg: rated)
    (b,) = batchstats.measure(None)
    assert b.scored == 6 and b.share_7_plus == pytest.approx(4 / 6)
    assert b.mean_score == pytest.approx(41 / 6)
    assert b.spearman_n == 6 and b.prospective == 5
    assert b.spearman_ai is not None


def test_column_a_rulings_per_batch_are_in_the_summary():
    a = batchstats.Batch(key="2026-10-03", staged=25, verdicted=4)
    b = batchstats.Batch(key="2026-10-11", staged=14, verdicted=0)
    line = batchstats.column_a_line([a, b])
    assert "10-03 4/25" in line and "10-11 0/14" in line
    assert line in batchstats.lines([a, b])


def http_error(status, body):
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode()
    return requests.HTTPError(f"{status} writing rows: {body[:300]}", response=resp)


def test_a_failed_stats_write_is_reported_not_swallowed(monkeypatch):
    writes = []

    def insert(cfg, table, rows, **kw):
        writes.append(set(rows[0]))
        if "scored" in rows[0]:
            raise http_error(400, '{"code":"PGRST204","message":"Could not find the '
                                  "'scored' column of 'hunter_batch_stats' in the "
                                  'schema cache"}')
    monkeypatch.setattr(batchstats, "db_insert", insert)
    note = batchstats.save(None, [batch("2026-10-11", [8, 7, 9, 3, 4, 8])])
    assert "written without the Krish Score columns" in note
    assert len(writes) == 2 and "scored" not in writes[1]

    def refuse(cfg, table, rows, **kw):
        raise requests.HTTPError("503")
    monkeypatch.setattr(batchstats, "db_insert", refuse)
    with pytest.raises(batchstats.NotSaved, match="NOT written"):
        batchstats.save(None, [batch("2026-10-11", [8])])


def test_funnel_batches_says_the_write_failed_and_still_prints_the_rate(monkeypatch):
    monkeypatch.setattr(batchstats, "measure", lambda cfg, tiers, notes=None: [
        batch("2026-10-11", [8, 7, 9, 3, 4, 8])])

    def refuse(cfg, batches):
        raise batchstats.NotSaved("hunter_batch_stats NOT written (HTTPError: 503)")
    monkeypatch.setattr(batchstats, "save", refuse)
    from hunter import companyintel
    monkeypatch.setattr(companyintel, "load", lambda cfg: {})
    summary = []
    R.funnel_batches(None, None, summary)
    assert any("NOT written" in line for line in summary)
    assert any(line.startswith("Krish Score by batch") for line in summary)
    assert not any("not measured" in line for line in summary)


# ---------- a rating he takes back ----------

def two_syncs(wire, first, second, *, seen):
    db = wire(DB(seen=seen))
    sync(tabs(first), apply=True)
    out = sync(tabs(second), apply=True, now=NOW + HOURS)
    return db, out


def test_a_score_he_clears_is_no_longer_his_rating(wire, monkeypatch):
    """A 9 typed on the wrong row and cleared, with the 9 typed on the right
    one. The wrong row's company must not stay a Yes company, and the 9 must
    not stay in the batch's scores."""
    quokka, wombat = job_id("Quokkaco", "VP Strategy"), job_id("Wombatco", "VP Operations")
    seen = [db_row("Quokkaco", "VP Strategy"), db_row("Wombatco", "VP Operations")]
    db, out = two_syncs(wire, [cells("Quokkaco", "VP Strategy", k="9"),
                               cells("Wombatco", "VP Operations")],
                        [cells("Quokkaco", "VP Strategy"),
                         cells("Wombatco", "VP Operations", k="9")], seen=seen)
    assert out["cleared"] == 1 and out["pending_roles"] == 1
    kinds = {(r["job_id"], r["kind"]) for r in db.t["hunter_krish_ratings"]}
    assert (quokka, "cleared") in kinds, "the clear is written, not left out"
    now = ratings.latest(None)
    assert quokka not in now and now[wombat]["score"] == 9
    yes, _ = universe.yes_job_ids([], now)
    assert yes == {wombat}
    monkeypatch.setattr(batchstats, "db_get", lambda cfg, t, p: [
        dict(r, presented_at="2026-10-03T12:00:00+00:00", source="x", krish_verdict="")
        for r in seen] if t == "hunter_seen_roles" else [])
    (b,) = batchstats.measure(None)
    assert b.scores == [9]
    assert any("1 cleared" in line for line in ratings.lines(out))


def test_a_score_cleared_beside_a_column_a_decline_is_not_a_yes(wire):
    jid = job_id("Quokkaco", "VP Strategy")
    seen = [db_row("Quokkaco", "VP Strategy")]
    _, out = two_syncs(wire, [cells("Quokkaco", "VP Strategy", k="9")],
                       [cells("Quokkaco", "VP Strategy", verdict="Declined - not for me")],
                       seen=seen)
    assert out["rows"]["column_a_only"] == 1 and out["cleared"] == 1
    yes, _ = universe.yes_job_ids([{"job_id": jid, "verdict": "rejection"}],
                                  ratings.latest(None))
    assert jid not in yes


def test_a_score_he_types_again_after_clearing_it_is_his_rating(wire):
    jid = job_id("Quokkaco", "VP Strategy")
    db = wire(DB(seen=[db_row("Quokkaco", "VP Strategy")]))
    sync(tabs([cells("Quokkaco", "VP Strategy", k="9")]), apply=True)
    sync(tabs([cells("Quokkaco", "VP Strategy")]), apply=True, now=NOW + HOURS)
    again = sync(tabs([cells("Quokkaco", "VP Strategy")]), apply=True,
                 now=NOW + 2 * HOURS)
    assert again["unchanged"] and again["cleared"] == 1
    sync(tabs([cells("Quokkaco", "VP Strategy", k="6")]), apply=True, now=NOW + 3 * HOURS)
    assert ratings.latest(None)[jid]["score"] == 6


def test_a_row_he_deleted_is_not_a_score_taken_back(wire):
    """Gone from the sheet is not cleared: prune and the archive delete rows,
    and his score on them is still what he said."""
    keep = job_id("Halyard", "VP Strategy")
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy"), db_row("Mossgate", "Head of GTM")]))
    sync(tabs([cells("Halyard", "VP Strategy", k="8"),
               cells("Mossgate", "Head of GTM", k="5")]), apply=True)
    out = sync(tabs([cells("Mossgate", "Head of GTM", k="5")]), apply=True, now=NOW + HOURS)
    assert out["cleared"] == 0
    assert ratings.latest(None)[keep]["score"] == 8


def test_a_cleared_score_does_not_make_its_batch_reviewed(wire):
    """His only score on the 10-08 batch, cleared: the 10-03 blank above it
    is pending, not 'not wanted', however long ago the score was typed."""
    seen = [db_row("Larchmont", "Director, Strategy"),
            db_row("Fernhollow", "VP Partnerships", presented="2026-10-08T09:00:00+00:00")]
    db = wire(DB(seen=seen))
    sync(tabs([cells("Larchmont", "Director, Strategy"),
               cells("Fernhollow", "VP Partnerships", k="8")]), apply=True)
    sync(tabs([cells("Larchmont", "Director, Strategy"),
               cells("Fernhollow", "VP Partnerships")]), apply=True, now=NOW + HOURS)
    out = sync(tabs([cells("Larchmont", "Director, Strategy"),
                     cells("Fernhollow", "VP Partnerships")]), apply=True,
               now=NOW + 30 * HOURS, force=True)
    assert out["reviewed_batches"] == 0
    assert out["roles"].get("unscored_after_review", 0) == 0 and out["pending_roles"] == 2


def test_a_blank_twin_beside_his_score_is_not_pending(wire):
    wire(DB(seen=[db_row("Orrery Labs", "Head of Operations")]))
    out = sync(tabs([cells("Orrery Labs", "Head of Operations", k="8"),
                     cells("Orrery Labs", "Head of Operations",
                           url="https://jobs.example.test/orrery-2")]))
    assert out["rows"]["pending"] == 1 and out["roles"] == {"scored": 1}
    assert out["pending_roles"] == 0


# ---------- what he was shown, frozen per role ----------

@pytest.mark.parametrize("second", [dict(k="7"), dict(k="8", m="Wombat tooling again")])
def test_the_then_columns_are_frozen_per_role_not_per_observation(wire, second):
    """J is rewritten by regate and the drafts pass, and the judge refits.
    A changed score, or a reaction added, is a new observation of the same
    role, and still pairs with what he was shown first."""
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy", fit=6)]))
    sync(tabs([cells("Halyard", "VP Strategy", k="8", ai=4)]), apply=True)
    db.t["hunter_seen_roles"][0].update(judge_fit=8, judge_prompt_version="v-later")
    sync(tabs([cells("Halyard", "VP Strategy", ai=9, **second)]), apply=True,
         now=NOW + HOURS)
    assert len(db.t["hunter_krish_ratings"]) == 2
    now = ratings.latest(None)[job_id("Halyard", "VP Strategy")]
    assert (now["ai_score_then"], now["judge_fit_then"],
            now["judge_prompt_version_then"]) == (4, 6, "v-test")


# ---------- the duplicate check after an hourly settle ----------

def gannetry(wire):
    a = db_row("Gannetry", "Director, Sales Development")
    b = db_row("Gannetry", "Director, Sales Development, USA",
               url="https://jobs.example.test/inkwell-usa")
    db = wire(DB(seen=[a, b]))
    sheet = tabs([cells("Gannetry", "Director, Sales Development", k="7"),
                  cells("Gannetry", "Director, Sales Development, USA", m="Duplicate",
                        url="https://jobs.example.test/inkwell-usa")])
    return db, sheet, a, b


def test_process_checks_a_duplicate_the_hourly_settle_saw_first(wire):
    """Settle syncs every hour and stamps the sheet's hash; the process run
    after it saw the same sheet and, before, returned at the hash with no
    duplicate events, so his Duplicate never reached hunter_seen_roles."""
    db, sheet, a, b = gannetry(wire)
    settled = []
    R.ratings_step(None, CANON, sheet, settled, apply=True)
    assert db.patches == []
    assert any("not checked here" in line for line in settled)
    assert not any("go to the duplicate check" in line for line in settled)
    processed = []
    R.ratings_step(None, CANON, sheet, processed, apply=True,
                   roles=[dict(a, krish_verdict=""), dict(b, krish_verdict="")])
    assert [(m["job_id"], v["status"]) for t, m, v in db.patches] == [(b["job_id"], "duplicate")]
    assert any("1 checked, 1 marked duplicate" in line for line in processed)


def test_settle_never_forces_a_re_pair_and_process_always_does(wire, monkeypatch):
    forced = []
    monkeypatch.setattr(ratings, "sync", lambda cfg, sheet, **kw: forced.append(kw["force"])
                        or {"duplicate_events": []})
    monkeypatch.setattr(ratings, "lines", lambda out: [])
    wire_ledger(monkeypatch)
    R.ratings_step(None, CANON, object(), [], apply=True)
    R.ratings_step(None, CANON, object(), [], apply=True, roles=[])
    assert forced == [False, True]


# ---------- the unique key never holds his words ----------

def test_the_unique_key_is_a_fixed_length_hash_of_the_observation(wire):
    """A btree entry is at most 2,704 bytes; a reaction of a few thousand
    characters in the key refused every write. The key is 64 hex characters
    whatever he writes, and a second sync of it is the same observation."""
    assert ratings.ON_CONFLICT == "job_id,observation_key"
    assert "reaction" not in ratings.ON_CONFLICT and "score_text" not in ratings.ON_CONFLICT
    long_words = " ".join([WORDS_LEGAL] * 200)
    db = wire(DB(seen=[db_row("Halyard", "VP Strategy")]))
    sheet = tabs([cells("Halyard", "VP Strategy", k="7.5 " + long_words, m=long_words)])
    sync(sheet, apply=True)
    (row,) = db.t["hunter_krish_ratings"]
    assert len(row["observation_key"]) == 64
    assert set(row["observation_key"]) <= set("0123456789abcdef")
    assert row["reaction"] == ratings.norm(long_words), "his words are kept, not keyed"
    again = sync(sheet, apply=True, now=NOW + HOURS, force=True)
    assert again["new"] == 0 and len(db.t["hunter_krish_ratings"]) == 1


def test_every_part_of_an_observation_changes_its_key():
    base = ("scored", 8, None, "Too narrow")
    k = ratings.observation_key(*base)
    for other in (("scored", 7, None, "Too narrow"), ("duplicate", 8, None, "Too narrow"),
                  ("scored", 8, "8", "Too narrow"), ("scored", 8, None, "Too narrow!"),
                  ("scored", 8, None, None)):
        assert ratings.observation_key(*other) != k


# ---------- a failure recorded once, not hourly ----------

def failing(monkeypatch, why="hunter_krish_ratings could not be read (HTTPError 404)"):
    def boom(*a, **k):
        raise ratings.RatingsError(why)
    monkeypatch.setattr(ratings, "sync", boom)


def ago(hours):
    """On the real clock: report_run stamps the real time, and the watchdog
    orders and ages runs by it."""
    return (datetime.datetime.now(datetime.timezone.utc) - hours * HOURS).isoformat()


def a_good_run(hours_ago):
    return {"run_at": ago(hours_ago), "status": "success",
            "agent_id": "hunter",
            "outcome": {"staged": 4, "discovered": 40}, "workflow_name": "run",
            "error_message": None}


def test_three_failing_settles_write_one_run_row_and_no_runs_are_failing(monkeypatch):
    led = wire_ledger(monkeypatch, runs=[a_good_run(3)])
    failing(monkeypatch)
    for _ in range(3):
        R.ratings_step(None, CANON, object(), [], apply=True)
    assert len(led.runs()) == 1
    assert led.runs()[0]["status"] == "error"
    kinds = {p["kind"] for p in alerts.trouble_checks(None)}
    assert "runs are failing" not in kinds and "no runs on record" not in kinds


def test_one_failure_is_a_summary_line_and_nothing_more(monkeypatch):
    led = wire_ledger(monkeypatch)
    failing(monkeypatch)
    summary = []
    R.ratings_step(None, CANON, object(), summary, apply=True)
    assert led.runs() == [] and led.t["silent_failures"] == []
    assert any("not recorded as a failure yet" in line for line in summary)


def test_a_healthy_sync_forgets_the_failure_so_the_next_one_counts_afresh(monkeypatch):
    led = wire_ledger(monkeypatch)
    failing(monkeypatch)
    R.ratings_step(None, CANON, object(), [], apply=True)
    R.ratings_step(None, CANON, object(), [], apply=True)
    assert len(led.runs()) == 1
    monkeypatch.setattr(ratings, "sync", lambda *a, **k: {"duplicate_events": []})
    monkeypatch.setattr(ratings, "lines", lambda out: [])
    R.ratings_step(None, CANON, object(), [], apply=True)
    assert not [a for a in led.t["hunter_alerts"] if a["fingerprint"].startswith("ratings:")]
    failing(monkeypatch)
    R.ratings_step(None, CANON, object(), [], apply=True)
    assert len(led.runs()) == 1, "noted, not yet recorded"
    R.ratings_step(None, CANON, object(), [], apply=True)
    assert len(led.runs()) == 2, "the same failure twice in a row again"


def test_a_ratings_failure_row_is_never_counted_as_a_failed_run(monkeypatch):
    def bad(h):
        return {"run_at": ago(h), "status": "error",
                "agent_id": "hunter",
                "outcome": R.RATINGS_FAILED, "workflow_name": "run",
                "error_message": "RatingsError"}
    wire_ledger(monkeypatch, runs=[bad(1), bad(2), a_good_run(3)])
    assert "runs are failing" not in {p["kind"] for p in alerts.trouble_checks(None)}


# ---------- one word, one meaning ----------

@pytest.mark.parametrize("text,dup", [
    ("Duplicate", True), (" duplicate. ", True), ("DUPLICATE!", True),
    ("Duplicate?", False), ("duplicate:", False), ("(duplicate)", False),
    ("Duplicate - same as row 5", False), ("Not a duplicate", False), ("", False),
])
def test_the_sheet_and_the_record_read_duplicate_the_same_way(text, dup):
    (row,) = tabs([cells("Orrery Labs", "COO", m=text)]).read_pipeline(HEADERS)
    assert ratings.is_duplicate(ratings.norm(text)) is dup
    assert invariants._called_duplicate(row) is dup
    assert (ratings.kind_of("", text, "New", reviewed_past=False) == "duplicate") is dup


# ---------- judgedata exactly as before ----------

def test_judgedata_keeps_the_pair_match_rows_made_across_two_countries(monkeypatch):
    """A London row whose only record is the New York posting of the same
    title. match_rows pairs them; collect reads that pair as it always had,
    so judge_eval.json still reproduces, while ratings files the London row
    under an id of its own."""
    ny = db_row("Harrowgate", "VP Partnerships", loc="New York, NY",
                presented="2026-09-08T10:00:00+00:00")
    sheet = tabs([], [cells("Harrowgate", "VP Partnerships", verdict="Declined - travel",
                            loc="London, UK", url="https://jobs.example.test/hg-ldn")])
    (p,) = ratings.pair_sheet(sheet.read_archive(), [ny])
    assert p.pairing == "minted" and p.job_id == ny["job_id"] + "-uk"
    assert p.raw is not None and p.raw["job_id"] == ny["job_id"]
    monkeypatch.setattr(judgedata, "db_get",
                        lambda cfg, t, q: [ny] if t == "hunter_seen_roles" else [])
    monkeypatch.setattr(judgedata, "blind_rulings", lambda *a: [])
    (ruled,), _ = judgedata.collect(None, sheet, CANON)
    assert (ruled.job_id, ruled.presented_at) == (ny["job_id"], "2026-09-08T10:00:00+00:00")


# ---------- the score by batch, only what was observed ----------

def test_an_unread_ratings_table_is_not_saved_as_no_scores(monkeypatch):
    roles = [{"job_id": f"j{i}", "company": "Co", "presented_at": "2026-10-11T12:00:00+00:00",
              "source": "x", "krish_verdict": "", "ai_score_shown": 5} for i in range(6)]
    monkeypatch.setattr(batchstats, "db_get",
                        lambda cfg, t, p: roles if t == "hunter_seen_roles" else [])

    def unread(cfg):
        raise requests.HTTPError("503")
    monkeypatch.setattr(ratings, "latest", unread)
    notes = []
    (b,) = batchstats.measure(None, notes=notes)
    assert b.ratings_read is False and notes
    written = []
    monkeypatch.setattr(batchstats, "db_insert", lambda cfg, t, rows, **kw: written.extend(rows))
    assert batchstats.save(None, [b]) == ""
    assert not set(batchstats.RATING_COLUMNS) & set(written[0]), \
        "the stored scores stand; nothing observed none"
    assert "not measured this run" in batchstats.score_trend([b])
    assert "no batch has enough Krish Scores" not in " ".join(batchstats.lines([b]))
    assert "not measured this run" in invariants.check_accept_rate([b]).detail


def test_an_unread_ai_score_shown_is_not_saved_as_no_prospective_pairs(monkeypatch):
    calls = []

    def get(cfg, t, p):
        if t != "hunter_seen_roles":
            return []
        calls.append(p["select"])
        if "ai_score_shown" in p["select"]:
            raise requests.HTTPError("503")
        return [{"job_id": "j0", "company": "Co", "presented_at": "2026-10-11T12:00:00+00:00",
                 "source": "x", "krish_verdict": ""}]
    monkeypatch.setattr(batchstats, "db_get", get)
    monkeypatch.setattr(ratings, "latest", lambda cfg: {})
    (b,) = batchstats.measure(None)
    row = batchstats._row(b, "now")
    assert "prospective_n" not in row and "scored" in row


def test_a_stats_write_that_fails_for_another_reason_is_not_blamed_on_the_migration(
        monkeypatch):
    writes = []

    def insert(cfg, table, rows, **kw):
        writes.append(rows)
        raise http_error(503, "upstream connect error")
    monkeypatch.setattr(batchstats, "db_insert", insert)
    with pytest.raises(batchstats.NotSaved) as got:
        batchstats.save(None, [batch("2026-10-11", [8, 7, 9, 3, 4, 8])])
    assert len(writes) == 1, "no second write without the Krish Score columns"
    assert "migration" not in str(got.value)
