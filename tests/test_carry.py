"""Pending roles brought back to the judge (carry.py), and stage-picks.

On 2026-11-05, 395 roles sat in judge_pending, the oldest from 27 August,
because a pending role was judged again only when a sweep found it again."""
from hunter import carry
from hunter import run


def _row(**kw):
    base = {"job_id": "loop:director-m-a", "company": "Loop", "title": "Director, M&A",
            "url": "https://www.linkedin.com/jobs/view/director-m-a-at-loop-4476771283?position=2",
            "job_url": None, "employer_url": None, "jd_text": "x" * 400,
            "source": "apify_linkedin", "location": "New York, NY", "comp": "$250,000"}
    base.update(kw)
    return base


def test_linkedin_page_states():
    assert carry.linkedin_state(200, "<h1>Director</h1>") == carry.OPEN
    assert carry.linkedin_state(200, "No longer accepting applications") == carry.CLOSED
    assert carry.linkedin_state(404, "") == carry.CLOSED
    # Rate limited is not closed.
    assert carry.linkedin_state(429, "") == carry.UNKNOWN


def test_posting_state_reads_the_linkedin_number():
    asked = []

    def get(url):
        asked.append(url)
        return 200, "open"
    assert carry.posting_state(_row(), board_key=run.board_key, fetchers={}, get=get) == carry.OPEN
    assert asked == [carry.LI_GUEST.format("4476771283")]


def test_posting_state_asks_the_board_first():
    row = _row(url="https://jobs.ashbyhq.com/higgsfieldai/030945f9-5e3e-49bc-9167-7388362a6f6e")
    fetchers = {"ashby": lambda slug, pid: (False, "", "")}
    assert carry.posting_state(row, board_key=run.board_key, fetchers=fetchers,
                               get=lambda u: (200, "")) == carry.CLOSED


def test_an_unreadable_board_is_unknown_not_closed():
    row = _row(url="https://jobs.lever.co/acme/2b0f1e45-e7db-450f-a6fb-4c5365e120db")

    def boom(slug, pid):
        raise OSError("reset")
    assert carry.posting_state(row, board_key=run.board_key, fetchers={"lever": boom},
                               get=lambda u: (200, "")) == carry.UNKNOWN
    assert carry.posting_state(_row(url="https://example.com/careers/1"),
                               board_key=run.board_key, fetchers={},
                               get=lambda u: (200, "")) == carry.UNKNOWN


def test_carry_marks_closed_dead_and_returns_open(monkeypatch):
    rows = [_row(),
            _row(job_id="acme:vp", company="Acme", title="VP",
                 url="https://www.linkedin.com/jobs/view/vp-at-acme-4400000001"),
            _row(job_id="thin:vp", company="Thin", title="VP", jd_text="short")]
    asked, patched = {}, []

    def fake_get(cfg, table, params):
        asked.update(params)
        return rows
    monkeypatch.setattr(carry, "db_get", fake_get)
    monkeypatch.setattr(carry, "db_patch", lambda cfg, t, m, p: patched.append((m, p)))

    def page(url):
        return (200, "No longer accepting applications") if "4400000001" in url else (200, "ok")
    out, counts = carry.carry(None, limit=10, exclude=set(), board_key=run.board_key,
                              fetchers={}, get=page, today="2026-11-05")
    assert [r["job_id"] for r in out] == ["loop:director-m-a"]
    assert counts == {carry.OPEN: 1, carry.CLOSED: 1, carry.UNKNOWN: 1}
    assert patched == [({"job_id": "acme:vp"},
                        {"status": "dead",
                         "rejection_reason": "posting closed, read 2026-11-05, never judged"})]
    # Never shown, pending only, the least recently read first.
    assert asked["status"] == "eq.judge_pending" and asked["presented_at"] == "is.null"
    assert asked["order"].startswith("last_verified_at.asc.nullsfirst")


def test_carry_skips_this_runs_own_roles_and_can_be_off(monkeypatch):
    monkeypatch.setattr(carry, "db_get", lambda cfg, t, p: [_row()])
    out, _ = carry.carry(None, limit=5, exclude={"loop:director-m-a"}, board_key=run.board_key,
                         fetchers={}, get=lambda u: (200, "ok"), today="2026-11-05")
    assert out == []
    assert carry.pending_rows(None, limit=0, exclude=set()) == []


class _Cfg:
    def __init__(self, n):
        self.n = n

    def optional(self, key, default):
        return self.n if key == "hunter_judge_carry_per_run" else default


class _Canon:
    universe = None


def test_carry_into_adds_the_row_under_its_own_id(monkeypatch):
    suffixed = _row(job_id="loop:director-m-a-uk")
    other = _row(job_id="something-else:x")
    monkeypatch.setattr(carry, "carry", lambda cfg, **kw: (
        [suffixed, other], {carry.OPEN: 2, carry.CLOSED: 0, carry.UNKNOWN: 0}))

    class R:
        score, merit, why_it_fits = 8, 8, ""
    monkeypatch.setattr(run, "score_role", lambda role, **kw: R())
    inserts, cands, labels = [], [], {}
    lines = run.carry_into(_Cfg("40"), _Canon(), inserts, cands, labels, fetchers={},
                           employer_index=None, rank_of=lambda r, res: -res.score)
    assert [c.role.job_id for c in cands] == ["loop:director-m-a-uk"]
    assert inserts[0]["job_id"] == "loop:director-m-a-uk"
    assert inserts[0]["status"] == "staging" and inserts[0]["score"] == 8
    # Every column the merge writes is present, so none is nulled.
    assert "employer_url" in inserts[0] and "jd_text" in inserts[0]
    assert "1 open" in lines[0] and "1 unknown" in lines[0]


def test_carry_into_off_reads_nothing(monkeypatch):
    monkeypatch.setattr(carry, "carry", lambda cfg, **kw: (_ for _ in ()).throw(AssertionError))
    assert run.carry_into(_Cfg("0"), _Canon(), [], [], {}, fetchers={},
                          employer_index=None, rank_of=None) == []


def test_pick_items_keep_their_own_source_and_refuse_a_closed_posting():
    recs = [_row(),
            _row(job_id="higgsfieldai:sales-gtm-director", company="higgsfieldai",
                 title="Sales/GTM Director", jd_text="", source="ashby:higgsfieldai",
                 url="https://jobs.ashbyhq.com/higgsfieldai/030945f9-5e3e-49bc-9167-7388362a6f6e"),
            _row(job_id="gone:x", company="Gone", title="X")]
    items, refused = run.pick_items(recs, lambda r: "closed" if r["job_id"] == "gone:x" else "open")
    assert refused == [{"job_id": "gone:x", "outcome": "not staged: posting closed"}]
    assert items[0]["source"] == "apify_linkedin" and items[0]["open_read"]
    assert "ats" not in items[0]
    assert items[1]["ats"] == "ashby" and items[1]["slug"] == "higgsfieldai"
    # Never labelled as a posting he sent, which would build without his score.
    assert all(i["source"] != run.SENT_SOURCE for i in items)
