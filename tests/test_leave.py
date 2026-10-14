"""Low rows leave Pipeline, and a score he raises brings one back.

His rulings 2026-10-13: "Move 1-5 and skipped blanks" off Pipeline to the
Applied tab with his score and reaction kept, and, where his score and an
older column A Yes disagree, "My score wins". These tests hold the rule
(leave.should_leave), the move's safety (one read, every target re-read, his
cells RAW and read back, never copied twice, column A never written), what
the record does after it (status set_aside, moved_at, a leaving Yes's
waiting application cancelled and no other), the way back, the canon gate,
and that nothing printed names a role.

Every company, title and word here is invented. The repository is public.
"""
import datetime
import fnmatch
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hunter.run as R  # noqa: E402
from hunter import config, leave, ratings  # noqa: E402
from hunter import sheet as sheet_mod  # noqa: E402
from hunter.apply import approval  # noqa: E402
from hunter.sheet import (ARCHIVE_WIDTH, COLS, HEADERS, KRISH_REACTION, KRISH_SCORE,  # noqa: E402
                          N_COLS, Sheet, SheetRow, make_row)
from test_his_columns import TwoTabs  # noqa: E402

K, M = COLS[KRISH_SCORE], COLS[KRISH_REACTION]
NOW = datetime.datetime(2026, 10, 14, 12, 0, tzinfo=datetime.timezone.utc)
EARLIER = "2026-10-13T09:00:00+00:00"


# ---------- rows and his record, invented ----------

def cells(company, role, verdict="New", k="", m="", loc="London", n=1, **over):
    c = make_row(company=company, role=role, jd_url=f"https://jobs.example/{company.lower()}/{n}",
                 score=7, location=loc, source="ats_sweep", jd_snippet="Builds the engine.")
    c[COLS["Verdict"]], c[K], c[M] = verdict, k, m
    for name, val in over.items():
        c[COLS[name]] = val
    return c


def as_row(c, n=3):
    return SheetRow(row_number=n, cells=list(c), verdict=c[0].strip(), company=c[1].strip(),
                    role=c[2].strip(), jd_url=sheet_mod.parse_hyperlink(c[COLS["Job Link"]])[0],
                    package_urls={})


def jid(c):
    return f"{c[1].lower()}:{c[2].lower().replace(' ', '-')}"


def record(c, kind, rid, *, job=None, twins=(), seen=EARLIER, moved_at=None):
    """A stored rating exactly as ratings.sync writes one for this row."""
    s = as_row(c)
    k = (c[K] or "").strip()
    m = ratings.norm(c[M])
    score = ratings.whole_score(k)
    return {"id": rid, "job_id": job or jid(c), "pairing": "matched", "company": c[1],
            "title": c[2], "location": c[COLS["Location"]] or None,
            "score": score if kind in (ratings.SCORED, ratings.DUPLICATE) else None,
            "score_text": k if kind == ratings.UNREADABLE else None, "reaction": m or None,
            "observation_key": leave.observation(s, kind), "kind": kind,
            "twins": list(twins), "first_seen_at": seen, "last_seen_at": seen,
            "moved_at": moved_at}


def role(c, status="staging", trigger=None, verdict=None):
    url = sheet_mod.parse_hyperlink(c[COLS["Job Link"]])[0]
    return {"job_id": jid(c), "company": c[1], "title": c[2], "url": url, "job_url": url,
            "status": status, "package_trigger": trigger, "krish_verdict": verdict,
            "location": c[COLS["Location"]], "presented_at": "2026-10-03T21:20:00+00:00"}


# ---------- the database, in memory ----------

def _values(v: str) -> list[str]:
    inner = v[v.index("(") + 1:v.rindex(")")]
    out, cur, quoted = [], "", False
    for ch in inner:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.replace('\\"', '"') for x in out if x != ""]


class DB:
    def __init__(self, **tables):
        self.t = {"hunter_krish_ratings": [], "hunter_seen_roles": [],
                  approval.TABLE: []}
        self.t.update({k: [dict(r) for r in v] for k, v in tables.items()})
        self.writes = []

    def ok(self, row, params):
        for k, v in params.items():
            if k in ("select", "order", "limit", "on_conflict"):
                continue
            v, got = str(v), row.get(k)
            if v.startswith("eq."):
                if str(got) != v[3:]:
                    return False
            elif v.startswith("neq."):
                if str(got) == v[4:]:
                    return False
            elif v.startswith("in."):
                if str(got) not in _values(v):
                    return False
            elif v.startswith("not.in."):
                if str(got) in _values(v):
                    return False
            elif v == "not.is.null":
                if got is None:
                    return False
            elif v.startswith("like."):
                if not fnmatch.fnmatch(str(got or ""), v[5:]):
                    return False
        return True

    def get(self, cfg, table, params):
        return [dict(r) for r in self.t.get(table, []) if self.ok(r, params)]

    def patch(self, cfg, table, match, values):
        self.writes.append((table, dict(match), dict(values)))
        for r in self.t.get(table, []):
            if all(str(r.get(k)) == str(v) for k, v in match.items()):
                r.update(values)

    def patch_where(self, cfg, table, filters, values):
        self.writes.append((table, dict(filters), dict(values)))
        n = 0
        for r in self.t.get(table, []):
            if self.ok(r, filters):
                r.update(values)
                n += 1
        return n

    def insert(self, cfg, table, rows, on_conflict=None, merge=False, ignore_duplicates=False,
               **kw):
        self.writes.append((table, "insert", len(rows)))
        keys = (on_conflict or "").split(",") if on_conflict else []
        have = self.t.setdefault(table, [])
        for row in rows:
            hit = next((r for r in have if keys and all(r.get(k) == row.get(k) for k in keys)),
                       None)
            if hit is not None and merge:
                hit.update(row)
            elif hit is None:
                new = dict(row)
                new.setdefault("id", len(have) + 1)
                new.setdefault("first_seen_at", row.get("last_seen_at"))
                have.append(new)

    def install(self, monkeypatch):
        monkeypatch.setattr(R, "db_get", self.get)
        monkeypatch.setattr(R, "db_patch", self.patch)
        monkeypatch.setattr(R, "db_patch_where", self.patch_where)
        monkeypatch.setattr(R, "db_insert", self.insert)
        monkeypatch.setattr(approval, "db_patch_where", self.patch_where)
        monkeypatch.setattr(ratings, "db_get", self.get)
        monkeypatch.setattr(ratings, "db_insert", self.insert)
        from hunter import amend
        monkeypatch.setattr(amend, "db_get", self.get)
        monkeypatch.setattr(amend, "db_insert", self.insert)
        return self

    def status(self, job):
        return next(r["status"] for r in self.t["hunter_seen_roles"] if r["job_id"] == job)


# ---------- the sheet, both tabs ----------

class Tabs(TwoTabs):
    """Pipeline and Applied behind the real Sheet methods, the real delete
    included, with the live sheet ids, and a count of archive_rows calls."""
    delete_rows = Sheet.delete_rows

    def __init__(self, pipe_rows, applied_rows=()):
        pipe = [list(HEADERS), [""] * N_COLS] + [list(r) for r in pipe_rows]
        applied = [list(HEADERS) + ["Archived On"]] + [list(r) + ["2026-10-01"]
                                                       for r in applied_rows]
        super().__init__(pipe, applied, applied_cols=ARCHIVE_WIDTH)
        self.ids = {1: "Pipeline", config.ARCHIVE_SHEET_ID: "Applied"}
        self.archive_calls = 0
        self.after_raw = None

    def archive_rows(self, rows, **kw):
        self.archive_calls += 1
        return super().archive_rows(rows, **kw)

    def _post(self, path, body):
        out = super()._post(path, body)
        if (self.after_raw and path == "/values:batchUpdate"
                and body["valueInputOption"] == "RAW"):
            hook, self.after_raw = self.after_raw, None
            hook(self)
        return out

    def pipe(self):
        return [r for r in self.grids["Pipeline"][2:] if any(str(x).strip() for x in r[:3])]

    def applied(self):
        return self.grids["Applied"][1:]


class Canon:
    sheet_headers = HEADERS

    def __init__(self, rule=True):
        self.rule = rule

    def section_text(self, number):
        assert number == "9.13"
        return ("## 9.13 PIPELINE TAB SPEC. Decided rows do not stay. "
                + (leave.CANON_RULE + ", with K and M kept." if self.rule else ""))


# ---------- the predicate ----------

def r_(verdict="New", k="", m="", **over):
    return as_row(cells("Globex", "VP Operations", verdict=verdict, k=k, m=m, **over))


def rating(kind, **kw):
    return leave.RowRating(kind=kind, job_ids=("globex:vp-operations",), **kw)


S, U, RO, UN, YU, D = (ratings.SCORED, ratings.UNREADABLE, ratings.REACTION_ONLY,
                       ratings.UNSCORED, ratings.YES_UNSCORED, ratings.DUPLICATE)


@pytest.mark.parametrize("row, rr, want", [
    # column A decides, as it always has, whatever K says
    (r_("Applied"), None, leave.APPLIED),
    (r_("Declined - comp below bar", k="9"), None, leave.DECLINED),
    (r_("Applied", k="7.5"), rating(U), leave.APPLIED),
    # New or blank, scored 1 to 5: leaves
    (r_(k="1"), rating(S), leave.SCORED_LOW),
    (r_(k="3"), rating(S), leave.SCORED_LOW),
    (r_(k="5"), rating(S), leave.SCORED_LOW),
    (r_("", k="2"), rating(S), leave.SCORED_LOW),
    # 6 or more stays
    (r_(k="6"), rating(S), None),
    (r_(k="9"), rating(S), None),
    # a score never guessed: "7.5" and "4.5" stay
    (r_(k="7.5"), rating(U), None),
    (r_(k="4.5"), rating(U), None),
    # a reaction and no score
    (r_(m="Not the shape"), rating(RO), leave.REACTION),
    # a blank he has scored past, and a blank still pending (no rating)
    (r_(), rating(UN), leave.SCORED_PAST),
    (r_(), None, None),
    # Duplicate: blank K leaves, K 1 to 5 leaves as low, K 6 or more stays
    (r_(m="Duplicate"), rating(D), leave.DUPLICATE),
    (r_(m="Duplicate", k="4"), rating(D), leave.SCORED_LOW),
    (r_(m="Duplicate", k="8"), rating(S), None),
    # his score wins over an older Yes
    (r_("Yes", k="3"), rating(S), leave.YES_SCORED_LOW),
    (r_("Yes", m="Too junior"), rating(RO), leave.YES_REACTION),
    # a Yes he has not scored stays, and so do Yes rows scored 6 or more
    (r_("Yes"), rating(YU), None),
    (r_("Yes", k="6"), rating(S), None),
    (r_("Yes", k="10"), rating(S), None),
    (r_("Yes", m="Duplicate"), rating(D), None),
    # a package his score built stays, unless K falls to 5 or below
    (r_(k="8"), rating(S, score_built=True), None),
    (r_(m="Still keen"), rating(RO, score_built=True), None),
    (r_(), rating(UN, score_built=True), None),
    (r_(k="4"), rating(S, score_built=True), leave.SCORED_LOW),
    # no record of it, or the gate shut: only column A moves a row
    (r_(k="3"), None, None),
    (r_("Yes", k="3"), None, None),
    # a column A that is neither New, blank nor Yes is his, not undecided
    (r_("Awaiting reply", k="3"), rating(S), None),
    # his ruling 2026-10-14, "Move the gone ones": a role he scored whose
    # posting hunter found gone leaves; a Yes marked dead stays, it is his
    (r_(k="9", **{"Package Status": sheet_mod.PKG_DEAD}), rating(S), leave.GONE),
    (r_(k="6", **{"Package Status": sheet_mod.PKG_DEAD}), rating(S), leave.GONE),
    (r_(k="9", **{"Package Status": "Not started"}), rating(S), None),
    (r_("Yes", **{"Package Status": sheet_mod.PKG_DEAD}), rating(YU), None),
    (r_("Yes", k="9", **{"Package Status": sheet_mod.PKG_DEAD}), rating(S), None),
    (r_(k="7.5", **{"Package Status": sheet_mod.PKG_DEAD}), rating(U), None),
])
def test_the_one_predicate(row, rr, want):
    assert leave.should_leave(row, rr) == want


@pytest.mark.parametrize("row, rr, want", [
    (r_(k="7"), rating(S), True),
    (r_("", k="10"), rating(S), True),
    (r_("Yes", k="8"), rating(S), True),
    (r_(k="6"), rating(S), False),
    (r_(k="7.5"), rating(U), False),
    (r_(k="8"), None, False),
    (r_("Declined - stage wrong", k="9"), rating(S), False),
    (r_("Applied", k="9"), rating(S), False),
    # a gone posting stays on Applied: back on Pipeline it could only leave again
    (r_(k="9", **{"Package Status": sheet_mod.PKG_DEAD}), rating(S), False),
])
def test_what_comes_back(row, rr, want):
    assert leave.comes_back(row, rr) is want


def test_the_cancel_reason_names_his_score_or_his_reaction_and_nothing_else():
    assert leave.cancel_reason(r_("Yes", k="3")) == "his Krish Score 3"
    assert leave.cancel_reason(r_("Yes", m="Too junior")) == "his reaction, no score"


# ---------- his record ----------

def test_a_row_leaves_only_on_the_latest_rating_of_its_role():
    """He scored it 3 on Monday and 8 on Tuesday; the sheet now reads 3
    again and no sync has seen it. The 3 on record is not his latest word."""
    c = cells("Initech", "Chief of Staff", k="3")
    old = record(c, S, 1, seen="2026-10-12T09:00:00+00:00")
    eight = record(cells("Initech", "Chief of Staff", k="8"), S, 2,
                   seen="2026-10-13T09:00:00+00:00")
    assert leave.Records.of([old]).rate([as_row(c)])
    assert not leave.Records.of([old, eight]).rate([as_row(c)])
    cleared = dict(record(c, S, 3, seen="2026-10-14T09:00:00+00:00"), kind=ratings.CLEARED)
    assert not leave.Records.of([old, cleared]).rate([as_row(c)])


def test_a_duplicate_twin_is_found_on_the_rating_of_the_row_he_scored():
    scored = cells("Hooli", "Chief of Staff, Sales", k="9", n=1, loc="New York")
    dup = cells("Hooli", "Chief of Staff, Sales", m="Duplicate", n=2, loc="New York")
    twin = {"tab": "Pipeline", "row": 4, "kind": D, "score": None, "score_text": None,
            "reaction": "Duplicate", "location": "New York"}
    recs = leave.Records.of([record(scored, S, 1, twins=[twin])])
    rated = recs.rate([as_row(scored, 3), as_row(dup, 4)])
    assert rated[3].kind == S and rated[4].kind == D
    assert rated[3].job_ids == rated[4].job_ids
    # his 9 built a package on its own row; the Duplicate beside it is not
    # that row, and leaves
    built_row = as_row(scored, 3)
    built_row.package_urls = {"cv": "https://docs.example/cv"}
    roles = {rated[3].job_ids[0]: {"package_trigger": "Krish Score 9"}}
    marked = leave.mark_score_built([built_row, as_row(dup, 4)], rated, roles)
    assert marked[3].score_built and not marked[4].score_built
    assert leave.should_leave(built_row, marked[3]) is None
    assert leave.should_leave(as_row(dup, 4), marked[4]) == leave.DUPLICATE


# ---------- the move itself ----------

def test_a_cell_he_changed_after_the_decision_is_refused_and_stays():
    s = Tabs([cells("Globex", "VP Operations", k="3"), cells("Initech", "Head", k="2", n=2)])
    rows = s.read_pipeline(HEADERS)
    s.grids["Pipeline"][2][K] = "8"            # he raised it before the move
    rep = {}
    moved = s.archive_rows(rows, archive_tab="Applied", archive_sheet_id=config.ARCHIVE_SHEET_ID,
                           headers=HEADERS, report=rep)
    assert moved == 1 and rep["refused"] == [3]
    assert [r[1] for r in s.pipe()] == ["Globex"], "the changed row is still his to finish"
    assert [r[1] for r in s.applied()] == ["Initech"], "and was never copied"


def test_a_cell_he_changed_between_copy_and_delete_takes_its_copy_back():
    s = Tabs([cells("Globex", "VP Operations", k="3")])
    rows = s.read_pipeline(HEADERS)

    def he_edits(tabs):
        tabs.grids["Pipeline"][2][M] = "Actually, maybe"
    s.after_raw = he_edits
    rep = {}
    moved = s.archive_rows(rows, archive_tab="Applied", archive_sheet_id=config.ARCHIVE_SHEET_ID,
                           headers=HEADERS, report=rep)
    assert moved == 0 and rep["changed"] == [3]
    assert [r[1] for r in s.pipe()] == ["Globex"]
    assert s.applied() == [], "Applied never holds a ruling he has since replaced"


def test_his_cells_and_the_stamp_go_raw_and_read_back_and_column_a_is_untouched():
    c = cells("Globex", "VP Operations", verdict="Yes", k="4", m="-pay is thin, 10/12 call")
    s = Tabs([c])
    moved = s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                           archive_sheet_id=config.ARCHIVE_SHEET_ID, headers=HEADERS)
    assert moved == 1
    landed = s.applied()[0]
    assert landed[K] == "4" and landed[M] == "-pay is thin, 10/12 call"
    assert landed[COLS["Verdict"]] == "Yes", "column A goes across as he left it"
    assert landed[ARCHIVE_WIDTH - 1] == datetime.date.today().isoformat()
    for col in (K, M, ARCHIVE_WIDTH - 1):
        at = sheet_mod.col_letter(col)
        assert f"Applied!{at}2:{at}2" in s.raw_ranges
    writes = [b for p, b in s.posts if p == "/values:batchUpdate"]
    assert not any(blk["range"].startswith("Pipeline!") for b in writes for blk in b["data"]), \
        "nothing is written to Pipeline, column A least of all"


def test_a_stamp_that_does_not_land_as_text_deletes_nothing():
    class Serial(Tabs):
        def _post(self, path, body):
            out = super()._post(path, body)
            if path == "/values:batchUpdate" and body["valueInputOption"] == "RAW":
                for blk in body["data"]:
                    if blk["range"].startswith(f"Applied!{sheet_mod.ARCHIVE_LAST_COL}"):
                        self.grids["Applied"][1][ARCHIVE_WIDTH - 1] = "46309"
            return out
    s = Serial([cells("Globex", "VP Operations", k="3")])
    with pytest.raises(sheet_mod.SheetError, match="Archived On.*nothing deleted"):
        s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                       archive_sheet_id=config.ARCHIVE_SHEET_ID, headers=HEADERS)
    assert len(s.pipe()) == 1


def test_a_row_already_on_applied_is_not_copied_twice():
    """A move copied the row and its delete failed. The next move finds the
    copy and deletes the Pipeline row without writing a second one."""
    c = cells("Globex", "VP Operations", k="3")
    s = Tabs([c, cells("Initech", "Head", k="2", n=2)], applied_rows=[c])
    rep = {}
    moved = s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                           archive_sheet_id=config.ARCHIVE_SHEET_ID, headers=HEADERS,
                           report=rep)
    assert moved == 2 and rep["already"] == [3]
    assert [r[1] for r in s.applied()] == ["Globex", "Initech"], "one Globex, not two"
    assert s.pipe() == []


def test_an_older_ruling_on_applied_with_another_link_is_not_the_same_row():
    old = cells("Globex", "VP Operations", verdict="Declined - stage wrong", n=1)
    again = cells("Globex", "VP Operations", verdict="Declined - stage wrong", n=2)
    s = Tabs([again], applied_rows=[old])
    rep = {}
    s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                   archive_sheet_id=config.ARCHIVE_SHEET_ID, headers=HEADERS, report=rep)
    assert rep["already"] == [] and len(s.applied()) == 2


# ---------- the pass: settle and process ----------

ROWS = {
    "low": cells("Globex", "VP Operations", k="3"),
    "yes_low": cells("Initech", "Chief of Staff", verdict="Yes", k="4"),
    "yes_blank": cells("Hooli", "GM", verdict="Yes"),
    "six": cells("Umbrella", "Head of Partnerships", k="6"),
    "declined": cells("Vandelay", "Director", verdict="Declined - comp below bar"),
    "words": cells("Soylent", "VP Strategy", m="Not the shape I want"),
    "unreadable": cells("Wonka", "Chief of Staff", k="7.5"),
    "unrecorded": cells("Tyrell", "VP Sales", k="2"),
}


def world(monkeypatch, *, rule=True, applied_rows=(), extra_records=(), extra_roles=()):
    rows = list(ROWS.values())
    recs = [record(ROWS["low"], S, 1), record(ROWS["yes_low"], S, 2),
            record(ROWS["yes_blank"], YU, 3), record(ROWS["six"], S, 4),
            record(ROWS["words"], RO, 5), record(ROWS["unreadable"], U, 6)]
    roles = [role(ROWS["low"]), role(ROWS["yes_low"], verdict="Yes", trigger="column A Yes"),
             role(ROWS["yes_blank"], verdict="Yes"), role(ROWS["six"]),
             role(ROWS["declined"], status="dropped",
                  verdict="Declined - comp below bar"),
             role(ROWS["words"], status="unresolved"), role(ROWS["unreadable"]),
             role(ROWS["unrecorded"])]
    approvals = [{"token": "tok-initech", "job_id": jid(ROWS["yes_low"]), "state": "awaiting"},
                 {"token": "tok-hooli", "job_id": jid(ROWS["yes_blank"]), "state": "awaiting"}]
    db = DB(hunter_krish_ratings=recs + list(extra_records),
            hunter_seen_roles=roles + list(extra_roles),
            **{approval.TABLE: approvals}).install(monkeypatch)
    return Tabs(rows, applied_rows=applied_rows), db, Canon(rule=rule)


NAMES = [c[1] for c in ROWS.values()] + [c[2] for c in ROWS.values()] + [
    "Not the shape I want"]


def test_decided_and_scored_rows_leave_in_one_move_and_the_record_follows(monkeypatch):
    s, db, canon = world(monkeypatch)
    summary = []
    out = R.leave_step(None, canon, s, summary, apply=True)

    assert s.archive_calls == 1, "decided rows and his score's rows move together"
    stayed = [r[1] for r in s.pipe()]
    assert stayed == ["Hooli", "Umbrella", "Wonka", "Tyrell"]
    landed = {r[1]: r for r in s.applied()}
    assert set(landed) == {"Globex", "Initech", "Vandelay", "Soylent"}
    for key in ("low", "yes_low", "declined", "words"):
        c = ROWS[key]
        got = landed[c[1]]
        assert (got[1], got[2], got[K], got[M]) == (c[1], c[2], c[K], c[M])
        assert got[COLS["Verdict"]] == c[COLS["Verdict"]], "column A as he left it"
        assert got[ARCHIVE_WIDTH - 1], "dated"
    assert out["moved"] == 4 and out["decided"] == 1 and out["unrecorded"] == 1

    # set aside: the rows his score moved, and no other
    assert db.status(jid(ROWS["low"])) == leave.SET_ASIDE
    assert db.status(jid(ROWS["yes_low"])) == leave.SET_ASIDE
    assert db.status(jid(ROWS["words"])) == leave.SET_ASIDE, "unresolved too: it was shown"
    assert db.status(jid(ROWS["declined"])) == "dropped"
    assert db.status(jid(ROWS["six"])) == "staging"
    assert out["set_aside"] == 3

    # moved_at on the ratings that moved them
    moved_at = {r["id"]: r["moved_at"] for r in db.t["hunter_krish_ratings"]}
    assert moved_at[1] and moved_at[2] and moved_at[5]
    assert not moved_at[3] and not moved_at[4] and not moved_at[6]

    # the leaving Yes: its waiting application cancelled, his score the
    # reason; the Yes he has not scored keeps its own
    toks = {a["token"]: a for a in db.t[approval.TABLE]}
    assert toks["tok-initech"]["state"] == approval.CANCELLED
    assert toks["tok-initech"]["failure_reason"] == "his Krish Score 4"
    assert toks["tok-hooli"]["state"] == "awaiting"
    assert out["approvals_cancelled"] == 1

    # never a verdict: krish_verdict is as it was, and nothing wrote column A
    kv = {r["job_id"]: r["krish_verdict"] for r in db.t["hunter_seen_roles"]}
    assert kv[jid(ROWS["yes_low"])] == "Yes" and kv[jid(ROWS["low"])] is None
    assert not any("krish_verdict" in w[2] for w in db.writes if isinstance(w[2], dict))

    # counts only: nothing he could be identified by
    text = "\n".join(summary)
    for name in NAMES:
        assert name not in text, name


def test_the_pass_is_idempotent(monkeypatch):
    s, db, canon = world(monkeypatch)
    R.leave_step(None, canon, s, [], apply=True)
    before = (len(s.applied()), [dict(r) for r in db.t[approval.TABLE]], len(db.writes))
    out = R.leave_step(None, canon, s, [], apply=True)
    assert len(s.applied()) == before[0], "nothing copied twice"
    assert db.t[approval.TABLE] == before[1]
    assert out["moved"] == 0 and out["set_aside"] == 0 and out["moved_at"] == 0
    assert out["approvals_cancelled"] == 0


def test_a_write_that_failed_once_is_made_on_the_next_pass(monkeypatch):
    """The move happened and the status patch did not land. The next pass
    reads Applied, sees the role still live, and sets it aside then."""
    s, db, canon = world(monkeypatch)
    real = db.patch_where
    monkeypatch.setattr(R, "db_patch_where", lambda *a, **k: 0)
    out = R.leave_step(None, canon, s, [], apply=True)
    assert out["unconfirmed"] >= 1 and db.status(jid(ROWS["low"])) == "staging"
    monkeypatch.setattr(R, "db_patch_where", real)
    out = R.leave_step(None, canon, s, [], apply=True)
    assert db.status(jid(ROWS["low"])) == leave.SET_ASIDE and out["set_aside"] == 3


def test_nothing_moves_for_his_score_until_canon_carries_the_rule(monkeypatch):
    s, db, canon = world(monkeypatch, rule=False)
    summary = []
    out = R.leave_step(None, canon, s, summary, apply=True)
    assert [r[1] for r in s.applied()] == ["Vandelay"], "column A still moves its rows"
    assert len(s.pipe()) == len(ROWS) - 1
    assert not [w for w in db.writes if w[0] in ("hunter_seen_roles", "hunter_krish_ratings",
                                                  approval.TABLE)]
    assert out["by_score"] == {w: 0 for w in leave.SCORE_REASONS}
    assert "does not carry the rule" in summary[-1]


def test_the_canon_sentence_is_what_opens_the_gate():
    assert leave.in_canon(Canon(rule=True)) and not leave.in_canon(Canon(rule=False))

    class Broken:
        def section_text(self, n):
            raise KeyError(n)
    assert not leave.in_canon(Broken())


def test_a_dry_run_writes_nothing_prints_counts_and_lists_the_rows_locally(monkeypatch,
                                                                            tmp_path):
    s, db, canon = world(monkeypatch, rule=False)
    out_file = tmp_path / "list.json"
    summary = []
    out = R.leave_step(None, canon, s, summary, apply=False, out_path=str(out_file))
    assert not [p for p, _ in s.posts], "nothing written to the sheet"
    assert db.writes == [], "nothing written to the database"
    assert out["by_score"]["scored_low"] == 1 and out["by_score"]["yes_scored_low"] == 1
    assert out["would_cancel"] == 1
    text = "\n".join(summary)
    for name in NAMES:
        assert name not in text, name
    listed = json.loads(out_file.read_text())
    assert listed["rule_in_canon"] is False
    by = {x["company"]: x for x in listed["leave"]}
    assert set(by) == {"Globex", "Initech", "Vandelay", "Soylent"}
    assert by["Initech"]["reason"] == leave.YES_SCORED_LOW
    assert by["Initech"]["approvals_cancelled"] == [
        {"job_id": jid(ROWS["yes_low"]), "state": "awaiting"}]
    assert by["Globex"]["krish_score"] == "3" and by["Globex"]["column_a"] == "New"


def test_a_ratings_table_that_cannot_be_read_moves_only_column_a(monkeypatch):
    s, db, canon = world(monkeypatch)
    real = db.get

    def broken(cfg, table, params):
        if table == ratings.TABLE:
            raise RuntimeError("503")
        return real(cfg, table, params)
    monkeypatch.setattr(R, "db_get", broken)
    summary = []
    R.leave_step(None, canon, s, summary, apply=True)
    assert [r[1] for r in s.applied()] == ["Vandelay"]
    assert any("could not be read" in line for line in summary)


# ---------- the way back ----------

def test_a_row_he_scores_7_or_more_on_applied_comes_back(monkeypatch):
    raised = cells("Acme", "Head of Strategy", k="8", m="Second look: yes")
    yes_raised = cells("Stark", "Chief of Staff", verdict="Yes", k="9", n=2)
    declined = cells("Cyberdyne", "VP", verdict="Declined - stage wrong", k="9", n=3)
    s, db, canon = world(
        monkeypatch, applied_rows=[raised, yes_raised, declined],
        extra_records=[record(raised, S, 11, seen="2026-10-14T08:00:00+00:00"),
                       record(yes_raised, S, 12, seen="2026-10-14T08:00:00+00:00"),
                       record(declined, S, 13)],
        extra_roles=[role(raised, status=leave.SET_ASIDE),
                     role(yes_raised, status=leave.SET_ASIDE, verdict="Yes"),
                     role(declined, status="dropped")])
    summary = []
    out = R.leave_step(None, canon, s, summary, apply=True)
    back = {r[1]: r for r in s.pipe()}
    assert "Acme" in back and "Stark" in back and "Cyberdyne" not in back
    assert (back["Acme"][K], back["Acme"][M]) == ("8", "Second look: yes")
    assert back["Acme"][COLS["Verdict"]] == "New" and back["Stark"][COLS["Verdict"]] == "Yes"
    on_applied = [r[1] for r in s.applied()]
    assert "Acme" not in on_applied and "Stark" not in on_applied and "Cyberdyne" in on_applied
    assert db.status(jid(raised)) == leave.BACK_STATUS
    assert db.status(jid(yes_raised)) == leave.BACK_STATUS
    assert db.status(jid(declined)) == "dropped"
    assert out["back"] == 2 and out["restored"] == 2
    assert "Acme" not in "\n".join(summary)


def test_a_score_of_6_on_applied_stays_there(monkeypatch):
    six = cells("Acme", "Head of Strategy", k="6")
    s, db, canon = world(monkeypatch, applied_rows=[six],
                         extra_records=[record(six, S, 11)],
                         extra_roles=[role(six, status=leave.SET_ASIDE)])
    R.leave_step(None, canon, s, [], apply=True)
    assert "Acme" in [r[1] for r in s.applied()] and db.status(jid(six)) == leave.SET_ASIDE


# ---------- set aside counts as seen everywhere ----------

def test_staging_dedupe_counts_a_role_set_aside_as_seen(monkeypatch):
    rows = [{"job_id": "globex:vp-operations", "company": "Globex", "title": "VP Operations",
             "location": "London", "url": "https://jobs.example/globex/1",
             "status": leave.SET_ASIDE, "presented_at": "2026-10-03T21:20:00+00:00"},
            {"job_id": "initech:head", "company": "Initech", "title": "Head",
             "location": "", "url": "", "status": "unresolved", "presented_at": None}]
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: [dict(r) for r in rows])
    keys = R.seen_identity_keys(None)
    assert "globex:vp-operations" in keys and "initech:head" not in keys


def test_reconcile_never_appends_a_set_aside_role_back_nor_calls_it_his_delete(monkeypatch):
    d = role(cells("Globex", "VP Operations", k="3"), status=leave.SET_ASIDE)
    d.update({"score": 8, "sweep_date": "2026-10-03", "why_it_fits": "x"})
    db = DB(hunter_seen_roles=[d]).install(monkeypatch)
    s = Tabs([])
    ledger = R.reconcile(None, Canon(), s)
    assert not ledger.db_to_sheet and s.pipe() == []
    assert db.status(d["job_id"]) == leave.SET_ASIDE
    assert not [w for w in db.writes if w[0] == "hunter_seen_roles"]
    assert any("set aside" in line for line in ledger.skipped)


def test_a_company_set_aside_is_still_one_he_has_been_shown(monkeypatch):
    seen = []
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: seen.append(params) or [
        {"company": "Globex"}])
    assert R.known_company_keys(None)
    assert leave.SET_ASIDE in seen[0]["status"] and "staging" in seen[0]["status"]


def test_warm_paths_are_not_built_into_a_yes_his_score_set_aside(monkeypatch):
    from hunter.people import bridges
    params = []
    monkeypatch.setattr(bridges, "db_get", lambda cfg, table, p: params.append(p) or [])
    bridges.target_roles(None)
    assert "set_aside" in params[0]["status"] and params[0]["status"].startswith("not.in.")


def test_the_status_has_one_spelling():
    assert R.SET_ASIDE_STATUS == leave.SET_ASIDE == "set_aside"
    assert leave.SET_ASIDE not in leave.LIVE_STATUSES


# ---------- order, and the record read before the row leaves ----------

def test_settle_moves_after_his_scores_are_read_and_process_before_builds(monkeypatch):
    calls = []

    class Spy:
        def ensure_archive_columns(self, **kw):
            return {"noop": True, "state": "current", "rows": 0}

    canon = Canon()
    monkeypatch.setattr(R, "build_context", lambda: (None, canon))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: Spy())
    monkeypatch.setattr(R, "score_withdrawals_step", lambda *a, **k: calls.append("withdraw"))
    monkeypatch.setattr(R, "settle_step", lambda *a, **k: calls.append(
        ("settle", k.get("archive"))) or {"decided": 0})
    monkeypatch.setattr(R, "ratings_step", lambda *a, **k: calls.append("ratings") or {})
    monkeypatch.setattr(R, "leave_step", lambda *a, **k: calls.append("leave") or {})
    assert R.cmd_settle(apply=True) == 0
    assert calls == ["withdraw", ("settle", False), "ratings", "leave"], \
        "settle does not move decided rows itself: the one move comes after the record"

    calls.clear()

    class Stop(BaseException):
        """Past every except Exception in process_step."""
    monkeypatch.setattr(R, "archive_columns_step", lambda sheet, apply: "ok")
    monkeypatch.setattr(R, "amendment_step", lambda *a: 0)
    monkeypatch.setattr(R, "stored_company_declines", lambda cfg: ({}, []))
    monkeypatch.setattr(R, "reconcile", lambda *a, **k: R.ReconcileLedger())
    monkeypatch.setattr(R, "sync_applied_state", lambda *a: {"written": 0, "from_sheet": 0,
                                                              "from_ledger": 0})
    monkeypatch.setattr(R, "learning_step", lambda cfg, apply: {
        "roles": [], "company_declines": {}, "recorded": 0, "findings": [], "fixed": [],
        "unexplained": []})
    monkeypatch.setattr(R, "leave_step", lambda *a, **k: calls.append("leave") or {"moved": 2})

    def select(*a, **k):
        calls.append("select")
        assert k.get("score_wins") is True, "process asks with canon's ruling on a low Yes"
        raise Stop
    import hunter.ats.discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    monkeypatch.setattr(R, "select_for_build", select)
    monkeypatch.setattr(R.invariants, "enforce", lambda *a, **k: calls.append("invariants"))
    with pytest.raises(Stop):
        R.process_step(None, canon, object(), [])
    assert calls == ["ratings", "leave", "select"]


def test_what_sync_records_is_what_the_move_reads(monkeypatch):
    """The move finds a row in the record by the observation key ratings.sync
    computed for it. Run the real sync over the same sheet, then the pass."""
    low = cells("Globex", "VP Operations", k="3")
    words = cells("Soylent", "VP Strategy", m="Not the shape I want")
    keep = cells("Umbrella", "Head of Partnerships", k="8")
    db = DB(hunter_seen_roles=[role(low), role(words), role(keep)]).install(monkeypatch)
    s = Tabs([low, words, keep])
    out = ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    assert out.get("written") == 3
    res = R.leave_step(None, Canon(), s, [], apply=True)
    assert res["moved"] == 2 and [r[1] for r in s.pipe()] == ["Umbrella"]
    assert db.status(jid(low)) == db.status(jid(words)) == leave.SET_ASIDE


def test_a_proposed_bridge_into_a_role_set_aside_is_purged(monkeypatch):
    from hunter.people import bridges
    deleted = []
    monkeypatch.setattr(bridges, "db_get", lambda cfg, table, p: (
        [{"bridge_id": "b1", "job_id": "globex:vp-operations"},
         {"bridge_id": "b2", "job_id": "hooli:gm"}] if table == "bridge_candidates" else
        [{"job_id": "globex:vp-operations", "krish_verdict": "Yes", "status": leave.SET_ASIDE},
         {"job_id": "hooli:gm", "krish_verdict": "Yes", "status": "staging"}]))
    monkeypatch.setattr(bridges, "db_delete", lambda cfg, table, p: deleted.append(p))
    out = bridges.purge_orphan_bridges(None)
    assert out["decided"] == 1 and out["deleted"] == 1
    assert deleted and "b1" in deleted[0]["bridge_id"] and "b2" not in deleted[0]["bridge_id"]


def test_the_archive_command_asks_the_same_predicate_through_the_same_pass(monkeypatch):
    calls = []
    s = Tabs([cells("Vandelay", "Director", verdict="Declined - comp below bar")])
    monkeypatch.setattr(R, "build_context", lambda: (None, Canon()))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: s)
    monkeypatch.setattr(R, "leave_step", lambda cfg, canon, sheet, summary, apply, **kw:
                        calls.append(("leave", apply, kw.get("out_path"))) or {})
    assert R.cmd_archive(apply=True) == 0
    assert R.cmd_leave(apply=False, out="/tmp/x.json") == 0
    assert calls == [("leave", True, None), ("leave", False, "/tmp/x.json")]
    assert R.main(["leave", "--out", "/tmp/y.json"]) == 0
    assert calls[-1] == ("leave", False, "/tmp/y.json")


def test_an_application_another_row_of_the_posting_still_asks_for_is_kept(monkeypatch):
    """One posting, two rows: he scored one 3 and the other, a Yes, 8. The
    approval is the posting's, so the 3 leaving does not cancel it."""
    low = cells("Initech", "Chief of Staff", verdict="Yes", k="3", n=7)
    keep = cells("Initech", "Chief of Staff (EMEA)", verdict="Yes", k="8", n=7)
    db = DB(hunter_krish_ratings=[record(low, S, 1), record(keep, S, 2)],
            hunter_seen_roles=[role(low, verdict="Yes"), role(keep, verdict="Yes")],
            **{approval.TABLE: [{"token": "tok-1", "job_id": jid(low),
                                 "state": "awaiting"}]}).install(monkeypatch)
    s = Tabs([low, keep])
    summary = []
    out = R.leave_step(None, Canon(), s, summary, apply=True)
    assert [r[2] for r in s.pipe()] == ["Chief of Staff (EMEA)"]
    assert db.t[approval.TABLE][0]["state"] == "awaiting"
    assert out["kept_for_twin"] == 1 and out["approvals_cancelled"] == 0


def test_a_twin_in_another_office_is_found_on_its_role_s_rating():
    """One seat listed in two offices is one role: the rating is the London
    row's, and the New York row he answered in words is kept beside it with
    its own location. Looked up under London alone, it was never found."""
    scored = cells("Hooli", "Head of Partnerships", k="4", n=1, loc="London")
    words = cells("Hooli", "Head of Partnerships", m="Not this one", n=2, loc="New York")
    twin = {"tab": "Pipeline", "row": 4, "kind": RO, "score": None, "score_text": None,
            "reaction": "Not this one", "location": "New York"}
    rated = leave.Records.of([record(scored, S, 1, twins=[twin])]).rate(
        [as_row(scored, 3), as_row(words, 4)])
    assert rated[3].kind == S and rated[4].kind == RO
    assert leave.should_leave(as_row(words, 4), rated[4]) == leave.REACTION
    # a row in a third office the rating does not carry is not found
    other = cells("Hooli", "Head of Partnerships", m="Not this one", n=3, loc="Berlin")
    assert not leave.Records.of([record(scored, S, 1, twins=[twin])]).rate([as_row(other, 5)])


def test_a_duplicate_that_leaves_does_not_set_aside_the_twin_he_scored(monkeypatch):
    """One role, two rows: a Duplicate he typed and the twin he scored 9.
    The Duplicate goes; the role is still on Pipeline through the 9, so its
    status, its rating and anything waiting on him for it stay as they are."""
    scored = cells("Hooli", "Chief of Staff, Sales", verdict="Yes", k="9", n=1, loc="New York")
    dup = cells("Hooli", "Chief of Staff, Sales", m="Duplicate", n=2, loc="New York")
    twin = {"tab": "Pipeline", "row": 4, "kind": D, "score": None, "score_text": None,
            "reaction": "Duplicate", "location": "New York"}
    db = DB(hunter_krish_ratings=[record(scored, S, 1, twins=[twin])],
            hunter_seen_roles=[role(scored, verdict="Yes")],
            **{approval.TABLE: [{"token": "t", "job_id": jid(scored),
                                 "state": "awaiting"}]}).install(monkeypatch)
    s = Tabs([scored, dup])
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert out["moved"] == 1 and [r[K] for r in s.pipe()] == ["9"]
    assert db.status(jid(scored)) == "staging"
    assert not db.t["hunter_krish_ratings"][0]["moved_at"]
    assert db.t[approval.TABLE][0]["state"] == "awaiting"


def test_his_edits_on_a_row_his_score_moves_are_read_before_it_goes(monkeypatch):
    s, db, canon = world(monkeypatch)
    seen = []
    monkeypatch.setattr(R.amend, "load_state", lambda cfg: {"x": {}})
    monkeypatch.setattr(R.amend, "detect", lambda rows, paired, state: seen.append(
        (sorted(r.company for r in rows), len(s.applied()))) or [])
    monkeypatch.setattr(R.amend, "record", lambda cfg, found: len(found))
    R.leave_step(None, canon, s, [], apply=True)
    assert seen == [(["Globex", "Initech", "Soylent"], 0)], \
        "read for the rows his score moves, while Applied does not yet hold them"


# ---------- review 2026-10-14: twins typed two ways, twins left on Pipeline ----------

def test_a_twin_whose_title_is_typed_another_way_is_found_and_leaves(monkeypatch):
    """pair_sheet files "VP, Sales" and "VP Sales" at one company as one
    role, and the record keeps the second row as the first one's twin. Kept
    under the winner's text, the 3 was never found by its own row: it stayed
    on Pipeline every pass, counted as one a sync had yet to record."""
    nine = cells("Hooli", "VP, Sales", k="9", n=1)
    three = cells("Hooli", "VP Sales", k="3", n=2)
    db = DB(hunter_seen_roles=[role(nine)]).install(monkeypatch)
    s = Tabs([nine, three])
    assert ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)["written"] == 1
    stored = db.t["hunter_krish_ratings"]
    assert len(stored) == 1 and stored[0]["twins"][0]["title"] == "VP Sales"
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert out["unrecorded"] == 0 and out["moved"] == 1
    assert [r[K] for r in s.pipe()] == ["9"] and [r[K] for r in s.applied()] == ["3"]
    assert db.status(jid(nine)) == "staging", "the 9 still holds the role on Pipeline"


def _one_role_two_offices(top_k="3", twin_k="", top_verdict="New", twin_verdict="New"):
    """One role on two rows: two board postings of one title in one country,
    so the invariants' twin check passes them and pair_sheet files both under
    one job id."""
    london = cells("Hooli", "Head of Partnerships", verdict=top_verdict, k=top_k, n=111,
                   loc="London")
    manchester = cells("Hooli", "Head of Partnerships", verdict=twin_verdict, k=twin_k, n=222,
                       loc="Manchester")
    return london, manchester


def test_two_rows_of_one_role_that_both_leave_set_it_aside_once(monkeypatch):
    """Live, 2026-10-14: two rows of one role, a 3 and a reaction, both left,
    and the summary said one more role was set aside than the table held,
    because the role was patched and counted once per row."""
    london, manchester = _one_role_two_offices(top_k="3", twin_k="4")
    db = DB(hunter_seen_roles=[role(london)]).install(monkeypatch)
    patched = []
    real = db.patch_where
    monkeypatch.setattr(R, "db_patch_where", lambda cfg, table, params, body: (
        patched.append((table, params.get("job_id"), body.get("status"))),
        real(cfg, table, params, body))[1])
    s = Tabs([london, manchester])
    ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert out["moved"] == 2 and s.pipe() == []
    assert db.status(jid(london)) == leave.SET_ASIDE
    assert out["set_aside"] == 1, "one role, counted once"
    assert [p for p in patched if p[2] == leave.SET_ASIDE] == [
        ("hunter_seen_roles", f"eq.{jid(london)}", leave.SET_ASIDE)]


def test_the_move_line_counts_the_gone_postings(monkeypatch):
    gone = cells("Hooli", "VP Sales", k="9", **{"Package Status": sheet_mod.PKG_DEAD})
    db = DB(hunter_seen_roles=[role(gone, status="dead")]).install(monkeypatch)
    s = Tabs([gone])
    ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    summary = []
    out = R.leave_step(None, Canon(), s, summary, apply=True)
    assert out["moved"] == 1
    line = next(x for x in summary if "by his score (" in x)
    assert "1 by his score" in line and "1 scored whose posting is gone" in line, line


def test_a_pending_blank_twin_still_on_pipeline_keeps_the_role_live(monkeypatch):
    """He scored the London row 3 and has not reached the Manchester one. The
    3 leaves; the role is still on his list through the blank, so it is not
    set aside, and when he later scores the blank 8 it is a live role that
    builds, not one hunter has filed as gone."""
    london, manchester = _one_role_two_offices()
    db = DB(hunter_seen_roles=[role(london)]).install(monkeypatch)
    s = Tabs([london, manchester])
    ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert out["moved"] == 1 and [r[COLS["Location"]] for r in s.pipe()] == ["Manchester"]
    assert db.status(jid(london)) == "staging", "a row of it is still on Pipeline"
    assert out["set_aside"] == 0 and out["held_by_pipeline"] == 1
    assert not any(r.get("moved_at") for r in db.t["hunter_krish_ratings"])

    s.grids["Pipeline"][2][K] = "8"
    ratings.sync(None, s, apply=True, canon_headers=HEADERS,
                 now=NOW + datetime.timedelta(hours=1))
    R.leave_step(None, Canon(), s, [], apply=True)
    assert db.status(jid(london)) == "staging"
    assert [r[K] for r in s.pipe()] == ["8"]


def test_a_role_set_aside_whose_twin_he_scores_later_is_put_back(monkeypatch):
    """Set aside on an earlier pass, with its other row still on Pipeline.
    He scores that row 8: the role goes back to staging, read back, so Roles
    to rule on, the warm path and its bridges see a live role again."""
    london, manchester = _one_role_two_offices(twin_k="8")
    db = DB(hunter_seen_roles=[role(london, status=leave.SET_ASIDE)]).install(monkeypatch)
    s = Tabs([manchester], applied_rows=[london])
    ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    summary = []
    out = R.leave_step(None, Canon(), s, summary, apply=True)
    assert db.status(jid(london)) == "staging"
    assert out["held_restored"] == 1 and out["unconfirmed"] == 0
    assert any("put back to staging" in line for line in summary)
    for name in ("Hooli", "Head of Partnerships"):
        assert name not in "\n".join(summary)
    again = R.leave_step(None, Canon(), s, [], apply=True)
    assert db.status(jid(london)) == "staging" and not again.get("held_restored")


def test_an_unrated_twin_holds_its_role_by_identity_alone():
    london, manchester = _one_role_two_offices()
    roles = {jid(london): role(london)}
    holds = R._pipeline_holds([as_row(manchester, 3)], {}, roles)
    assert holds[3] == {jid(london)}
    elsewhere = cells("Hooli", "Head of Partnerships", n=333, loc="New York")
    assert R._pipeline_holds([as_row(elsewhere, 3)], {}, roles)[3] == set(), \
        "another country is another role"


def test_a_twin_that_does_not_ask_for_the_application_does_not_keep_it(monkeypatch):
    """A Yes he scored 3 and its twin he left New at 6. The twin holds the
    role, so the role stays live; but a 6 does not build, so nothing asks for
    the Yes's application any more, and his score takes it back."""
    london, manchester = _one_role_two_offices(top_verdict="Yes", twin_k="6")
    db = DB(hunter_seen_roles=[role(london, verdict="Yes", trigger="column A Yes")],
            **{approval.TABLE: [{"token": "tok-1", "job_id": jid(london),
                                 "state": "awaiting"}]}).install(monkeypatch)
    s = Tabs([london, manchester])
    ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert [r[K] for r in s.pipe()] == ["6"]
    assert db.status(jid(london)) == "staging"
    assert db.t[approval.TABLE][0]["state"] == approval.CANCELLED
    assert out["approvals_cancelled"] == 1 and not out.get("kept_for_twin")


def test_a_yes_he_scored_9_on_a_twin_typed_another_way_keeps_its_application(monkeypatch):
    """Both rows Yes. The 3 is on top, so it wins the role's rating, and the
    9 is kept beside it as a twin typed "VP, Sales". The 3 leaves; the 9 is
    the role on Pipeline and still asks for the application."""
    three = cells("Hooli", "VP Sales", verdict="Yes", k="3", n=1)
    nine = cells("Hooli", "VP, Sales", verdict="Yes", k="9", n=2)
    db = DB(hunter_seen_roles=[role(three, verdict="Yes", trigger="column A Yes")],
            **{approval.TABLE: [{"token": "tok-1", "job_id": jid(three),
                                 "state": "awaiting"}]}).install(monkeypatch)
    s = Tabs([three, nine])
    ratings.sync(None, s, apply=True, canon_headers=HEADERS, now=NOW)
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert [r[K] for r in s.pipe()] == ["9"] and [r[K] for r in s.applied()] == ["3"]
    assert db.status(jid(three)) == "staging"
    assert db.t[approval.TABLE][0]["state"] == "awaiting"
    assert out["kept_for_twin"] == 1 and out["approvals_cancelled"] == 0


class SheetsLike(Tabs):
    """USER_ENTERED as Sheets applies it, and a formula read of the result:
    a leading apostrophe keeps the text and is not stored, an ISO date is a
    date and reads back as its serial, money and plain figures are numbers.
    RAW is stored as it was sent. The other fakes store every value as sent,
    which is why a dated row coming back passed here and failed on the sheet."""
    EPOCH = datetime.date(1899, 12, 30)

    def _post(self, path, body):
        if path == "/values:batchUpdate" and body["valueInputOption"] == "USER_ENTERED":
            import re

            def parse(v):
                v = str(v)
                if v.startswith("'"):
                    return v[1:]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    return str((datetime.date.fromisoformat(v) - self.EPOCH).days)
                if re.fullmatch(r"\$?\d[\d,]*", v):
                    return v.replace("$", "").replace(",", "")
                return v
            body = dict(body, data=[dict(b, values=[[parse(v) for v in row]
                                                     for row in b["values"]])
                                    for b in body["data"]])
        return super()._post(path, body)


def test_a_built_row_goes_out_and_comes_back_as_the_text_it_was(monkeypatch):
    """A Yes with a package he scored 3 leaves; he scores it 8 on Applied and
    it comes back. Its Materials Built is a date on Pipeline (a formula read
    gives the serial) and its Comp is the text "$225,000". Both must come
    back as they were, and the way back must pass its own read-back: through
    USER_ENTERED the date read back as a serial and the pass raised with the
    row on both tabs."""
    built = cells("Initech", "Chief of Staff", verdict="Yes", k="3", **{
        "Materials Built": "46305", "Comp": "$225,000",
        "CV Doc": sheet_mod.hyperlink("https://docs.example/cv", "CV")})
    db = DB(hunter_krish_ratings=[record(built, S, 1)],
            hunter_seen_roles=[role(built, verdict="Yes", trigger="column A Yes")]
            ).install(monkeypatch)
    s = SheetsLike([built])
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert out["moved"] == 1 and s.pipe() == []
    landed = s.applied()[0]
    assert landed[COLS["Comp"]] == "$225,000", "Applied holds the text Pipeline did"
    assert landed[COLS["Materials Built"]] == "46305"
    assert db.status(jid(built)) == leave.SET_ASIDE

    s.grids["Applied"][1][K] = "8"
    raised = list(built)
    raised[K] = "8"
    db.t["hunter_krish_ratings"].append(record(raised, S, 2, seen="2026-10-14T08:00:00+00:00"))
    out = R.leave_step(None, Canon(), s, [], apply=True)
    assert out["back"] == 1 and out["restored"] == 1 and s.applied() == []
    back = s.pipe()[0]
    assert back[COLS["Comp"]] == "$225,000"
    assert back[COLS["Materials Built"]] == "2026-10-10"
    assert (back[COLS["Verdict"]], back[K]) == ("Yes", "8")
    assert db.status(jid(built)) == leave.BACK_STATUS


def test_a_raw_write_that_fails_leaves_no_half_copy_on_applied():
    """The row landed on Applied and the RAW write of his cells and the stamp
    then failed (a 503). Left there, the half copy read as a row he never
    scored and matched nothing the next move looks for, so the row was
    copied a second time beside it."""
    class Flaky(Tabs):
        fail = True

        def _post(self, path, body):
            if (self.fail and path == "/values:batchUpdate"
                    and body["valueInputOption"] == "RAW"):
                self.fail = False
                raise RuntimeError("503")
            return super()._post(path, body)

    s = Flaky([cells("Globex", "VP Operations", k="3")])
    with pytest.raises(RuntimeError):
        s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                       archive_sheet_id=config.ARCHIVE_SHEET_ID, headers=HEADERS)
    assert s.applied() == [] and len(s.pipe()) == 1
    assert s.archive_rows(s.read_pipeline(HEADERS), archive_tab="Applied",
                          archive_sheet_id=config.ARCHIVE_SHEET_ID, headers=HEADERS) == 1
    assert [(r[1], r[K]) for r in s.applied()] == [("Globex", "3")] and s.pipe() == []


@pytest.mark.parametrize("rule, want", [
    (True, ["globex:vp-operations", "initech:gm"]),
    (False, ["globex:vp-operations", "hooli:chief-of-staff", "initech:gm",
             "umbrella:head-of-bd"]),
])
def test_the_packages_button_never_builds_a_yes_his_score_took_back(monkeypatch, rule, want):
    """He types 3 in K on a Yes row at 10:05 and presses the packages button
    at 10:20, before the hourly pass moves it. Under v11 his score wins over
    the Yes, so nothing is paid for and no application opens; under v10 a
    Yes builds whatever K says, as before."""
    from hunter import router
    rows = [cells("Globex", "VP Operations", verdict="Yes", n=1),
            cells("Hooli", "Chief of Staff", verdict="Yes", k="3", n=2),
            cells("Initech", "GM", verdict="Yes", k="4.5", n=3),
            cells("Umbrella", "Head of BD", verdict="Yes", m="Too junior", n=4)]
    known = [dict(role(c), package_status="none") for c in rows]
    s = Tabs(rows)
    built = []
    monkeypatch.setattr(R, "load_canon", lambda cfg: Canon(rule=rule))
    monkeypatch.setattr(R, "assert_canon_alignment", lambda canon: None)
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: s)
    monkeypatch.setattr(R, "send_summary", lambda cfg, text: None)
    monkeypatch.setattr(router, "db_get", lambda cfg, table, params: [dict(d) for d in known])
    monkeypatch.setattr(R, "build_one", lambda cfg, canon, sheet, row, summary, **kw:
                        built.append(row["job_id"]) or True)
    cfg = type("Cfg", (), {"optional": lambda self, key, default="": "10"})()
    line = R.run_command(cfg, "packages")
    assert sorted(built) == want and line.startswith(f"{len(want)} of {len(want)}")


def test_a_yes_his_score_set_aside_is_never_prepared_again(monkeypatch, capsys):
    """London Yes scored 3 left and was set aside. A New York Yes of the same
    title is still on Pipeline, and the sheet check goes by company and title,
    so Prepare on the stale London card mailed a fresh application for it."""
    london = cells("Stark", "Chief of Staff", verdict="Yes", k="3", n=1, loc="London")
    new_york = cells("Stark", "Chief of Staff", verdict="Yes", n=2, loc="New York")
    db = DB(hunter_seen_roles=[dict(role(london, status=leave.SET_ASIDE, verdict="Yes",
                                         trigger="column A Yes"),
                                    package_status="built",
                                    package_built_at="2026-10-12T09:00:00+00:00")]
            ).install(monkeypatch)
    s = Tabs([new_york], applied_rows=[london])
    monkeypatch.setattr(R, "build_context", lambda: (None, Canon()))
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("G", (), {"access_token": None})())
    monkeypatch.setattr(R, "Sheet", lambda token: s)
    from hunter import notify
    monkeypatch.setattr(notify, "mailbox", lambda cfg: pytest.fail("an email was prepared"))
    assert R.cmd_approvals(apply=True, job_id=jid(london)) == 1
    assert "no built packages to send" in capsys.readouterr().out
    assert db.t[approval.TABLE] == []


def test_a_twin_holds_its_role_by_the_row_on_applied_when_the_database_titles_it_otherwise():
    """The database titled the role "Head of Partnerships, EMEA"; the rows
    read "Head of Partnerships". The row that left is filed under the role,
    and the row still on Pipeline is that row's company, title and country."""
    london, manchester = _one_role_two_offices()
    roles = {jid(london): dict(role(london), title="Head of Partnerships, EMEA",
                               url="https://jobs.example/elsewhere")}
    assert R._pipeline_holds([as_row(manchester, 3)], {}, roles)[3] == set()
    filed = (((jid(london),), london[1], london[2], london[COLS["Location"]]),)
    assert R._pipeline_holds([as_row(manchester, 3)], {}, roles, filed=filed)[3] == {jid(london)}



def test_a_finished_batch_counts_as_reviewed_from_the_date_he_set():
    """hunter_reviewed_through: every score he typed from 11 to 13 October was
    first seen on the 14th, when the table was made, so the 24 hour wait ran
    from the wrong moment. A batch staged on or before the date counts as
    reviewed at once; a later one still waits; one he never scored in never
    counts; a value that is not a date counts for nothing."""
    first = NOW - datetime.timedelta(hours=1)
    stored = [
        {"job_id": "a", "kind": ratings.SCORED, "presented_at": "2026-10-11T09:00:00+00:00",
         "first_seen_at": first.isoformat(), "last_seen_at": first.isoformat()},
        {"job_id": "b", "kind": ratings.SCORED, "presented_at": "2026-10-15T09:00:00+00:00",
         "first_seen_at": first.isoformat(), "last_seen_at": first.isoformat()},
    ]
    assert ratings.reviewed_batches(stored, NOW) == {}
    got = ratings.reviewed_batches(stored, NOW, "2026-10-13")
    assert list(got) == ["2026-10-11"]

    class C:
        def __init__(self, v):
            self.v = v

        def optional(self, key, default=""):
            return self.v if key == ratings.REVIEWED_THROUGH_KEY else default

    assert ratings.reviewed_through(C("2026-10-13")) == "2026-10-13"
    assert ratings.reviewed_through(C("13 October")) == ""
    assert ratings.reviewed_through(C("")) == ""
