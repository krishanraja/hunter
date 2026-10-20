"""A posting he found himself and sent reaches his sheet past the gates
(run.stage_sent), his words 2026-10-20: "Hunter should have found these"."""
import types

from hunter import run
from hunter.sheet import parse_hyperlink

JD = ("Acme builds revenue agents. You will report to the co-founder and run strategic "
      "initiatives across GTM, finance and operations, from New York. " * 4)


class FakeSheet:
    def __init__(self):
        self.rows, self.appended = [], []

    def read_pipeline(self, headers):
        return self.rows

    def append_rows(self, rows):
        self.appended.extend(rows)
        for r in rows:
            self.rows.append(types.SimpleNamespace(
                jd_url=parse_hyperlink(r[4])[0], company=r[1], role=r[2]))


def world(monkeypatch):
    db = []
    monkeypatch.setattr(run, "db_insert", lambda cfg, t, rows, **k: db.extend(rows))

    class Marker:
        def __init__(self, *a): pass
        def mark(self, *a): pass
        def report(self): pass
    monkeypatch.setattr(run, "PresentedMarker", Marker)
    monkeypatch.setattr(run, "fetch_with_retry", lambda f, slug, pid: (True, JD, "https://x/jobs/1"))
    canon = types.SimpleNamespace(sheet_headers=[], universe=[])
    return db, canon


def rationale(cfg, canon, **k):
    return "why it fits", "snippet", []


ITEM = {"url": "https://boards.greenhouse.io/acme/jobs/1", "company": "Acme",
        "title": "Chief of Staff to Co-Founder", "location": "New York", "comp": "",
        "ats": "greenhouse", "slug": "acme", "posting_id": "1"}


def test_a_sent_posting_is_staged_presented_and_read_back(monkeypatch):
    db, canon = world(monkeypatch)
    sheet = FakeSheet()
    got = run.stage_sent(None, canon, sheet, [ITEM], [], apply=True, rationale=rationale)
    assert got[0]["outcome"] == "on Pipeline, read back"
    assert db[0]["status"] == "staging" and db[0]["source"] == run.SENT_SOURCE
    assert len(sheet.appended) == 1


def test_a_title_no_archetype_matches_still_reaches_him(monkeypatch):
    """Horizon Media's VP AI seat was blocked at G11; sending it is his ruling."""
    db, canon = world(monkeypatch)
    item = dict(ITEM, title="VP, AI (Agentic Platforms & Transformation)")
    got = run.stage_sent(None, canon, FakeSheet(), [item], [], apply=True, rationale=rationale)
    assert got[0]["outcome"] == "on Pipeline, read back"


def test_a_posting_already_on_his_sheet_is_not_added_twice(monkeypatch):
    db, canon = world(monkeypatch)
    sheet = FakeSheet()
    sheet.rows.append(types.SimpleNamespace(jd_url=ITEM["url"], company="Acme",
                                            role=ITEM["title"]))
    got = run.stage_sent(None, canon, sheet, [ITEM], [], apply=True, rationale=rationale)
    assert got[0]["outcome"] == "already on Pipeline" and not db


def test_a_dry_run_writes_nothing(monkeypatch):
    db, canon = world(monkeypatch)
    sheet = FakeSheet()
    got = run.stage_sent(None, canon, sheet, [ITEM], [], apply=False, rationale=rationale)
    assert got[0]["outcome"].startswith("would stage") and not db and not sheet.appended
