"""Every edit he makes to a letter or CV hunter built (docedits.py).

Invented documents throughout: a paragraph is {"text", "bullet", "runs",
"images"} as letters.style_record keeps it, each run [text, font, size,
bold, colour, link]."""
import copy
import datetime as dt

from hunter import docedits
from hunter.apply import approval


def para(text, size=9.0, bold=False, font="Arial", bullet=False):
    return {"text": text, "bullet": bullet,
            "runs": [[text, font, size, bold, "#1a1a1a", ""]], "images": []}


BASE = [para("KRISH RAJA", 23.0, True), para("Dear Hiring Team,"),
        para("Zentrox is busy and the gap is signed partners."),
        para("Quorra: grew a partner engine.", bullet=True), para("Krish Raja")]


def test_words_and_formatting_are_told_apart():
    same = docedits.diff(BASE, copy.deepcopy(BASE))
    assert not same["text_changed"] and not same["style_changed"] and same["edit_chars"] == 0
    worded = copy.deepcopy(BASE)
    worded[2] = para("Zentrox is clearly very busy and the gap is signed partners.")
    d = docedits.diff(BASE, worded)
    assert d["text_changed"] and not d["style_changed"] and d["edit_chars"] == len("clearly very ")
    styled = copy.deepcopy(BASE)
    styled[2] = para(BASE[2]["text"], size=10.0)
    d = docedits.diff(BASE, styled)
    assert not d["text_changed"] and d["style_changed"]
    assert d["style_diff"][0]["paragraph"] == 2


class Db:
    def __init__(self, approvals=()):
        self.t = {"hunter_row_state": [], "hunter_doc_edits": [], approval.TABLE: list(approvals)}

    def get(self, cfg, table, params):
        rows = [dict(r) for r in self.t[table]]
        if table == approval.TABLE and params.get("state", "").startswith("in.("):
            want = params["state"][4:-1].split(",")
            rows = [r for r in rows if r["state"] in want]
        if table == approval.TABLE and params.get("token", "").startswith("in.("):
            want = params["token"][4:-1].split(",")
            rows = [r for r in rows if r["token"] in want]
        return rows

    def insert(self, cfg, table, rows, **kw):
        for r in rows:
            if not any(x["doc_id"] == r["doc_id"] and x["modified_at"] == r["modified_at"]
                       for x in self.t[table]):
                self.t[table].append(dict(r))

    def patch_where(self, cfg, table, filters, values):
        n = 0
        for r in self.t[table]:
            key = "job_id" if "job_id" in filters else "token"
            want = filters[key][3:]
            if r.get(key) == want and (table != approval.TABLE or r["state"] != "submitted"):
                r.update(values)
                n += 1
        return n


class Docs:
    def __init__(self, modified, docs):
        self.modified, self.docs, self.read = modified, docs, []

    def modified_time(self, doc_id):
        return self.modified[doc_id]

    def get(self, doc_id):
        self.read.append(doc_id)
        return self.docs[doc_id]


def world(monkeypatch, *, job="zentrox:vp", last=None, approvals=()):
    db = Db(approvals)
    db.t["hunter_row_state"].append({
        "job_id": job, "letter_doc_id": "L1", "cv_doc_id": "C1", "letter_style": BASE,
        "cv_style": BASE, "docs_modified_at": last, "docs_read_at": "2026-10-15T09:00:00+00:00"})
    for name, fn in (("db_get", db.get), ("db_insert", db.insert), ("db_patch_where", db.patch_where)):
        monkeypatch.setattr(docedits, name, fn)
    import hunter.apply.approval as approval_mod
    monkeypatch.setattr(approval_mod, "db_patch_where", db.patch_where)
    return db


NOW = dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)


def run(db, docs, apply=True):
    return docedits.run_pass(None, db=docs, apply=apply, now=NOW, snapshot=lambda doc: doc)


def test_a_document_drive_has_not_seen_change_is_not_read(monkeypatch):
    db = world(monkeypatch, last="2026-10-15T10:00:00+00:00")
    docs = Docs({"L1": "2026-10-15T10:00:00Z", "C1": "2026-10-15T09:00:00Z"}, {})
    out = run(db, docs)
    assert docs.read == [] and out["unchanged"] == 2 and db.t["hunter_doc_edits"] == []


def test_his_edit_is_stored_with_both_versions_and_read_once(monkeypatch):
    db = world(monkeypatch)
    edited = copy.deepcopy(BASE)
    edited[2] = para("Zentrox is clearly very busy and the gap is signed partners.", size=10.0)
    docs = Docs({"L1": "2026-10-15T11:00:00Z", "C1": "2026-10-15T11:00:00Z"},
                {"L1": edited, "C1": copy.deepcopy(BASE)})
    out = run(db, docs)
    assert (out["edited"], out["words"], out["formatting"], out["after_send"]) == (1, 1, 0, 0)
    (e,) = db.t["hunter_doc_edits"]
    assert (e["doc_kind"], e["text_changed"], e["after_send"], e["scratch"]) == ("letter", True, False, False)
    assert e["before_paragraphs"] == BASE and e["after_paragraphs"] == edited
    assert db.t["hunter_row_state"][0]["docs_modified_at"].startswith("2026-10-15T11:00")
    again = run(db, docs)
    assert again["read"] == 0 and len(db.t["hunter_doc_edits"]) == 1


def test_an_edit_after_the_apply_email_cancels_it_so_prepare_sends_his_version(monkeypatch):
    db = world(monkeypatch, approvals=[{"token": "t1", "job_id": "zentrox:vp", "state": "awaiting"},
                                       {"token": "t2", "job_id": "other:vp", "state": "awaiting"}])
    edited = copy.deepcopy(BASE)
    edited[2] = para("A line of his own.")
    docs = Docs({"L1": "2026-10-15T11:00:00Z", "C1": "2026-10-15T09:00:00Z"},
                {"L1": edited, "C1": BASE})
    out = run(db, docs)
    assert out["after_send"] == 1 and out["cancelled"] == 1
    states = {a["token"]: (a["state"], a.get("failure_reason")) for a in db.t[approval.TABLE]}
    assert states == {"t1": ("cancelled", docedits.EDITED_AFTER_SEND), "t2": ("awaiting", None)}


def test_a_preview_edit_is_learned_from_and_never_cancels_anything(monkeypatch):
    db = world(monkeypatch, job="preview:zentrox:vp",
               approvals=[{"token": "t1", "job_id": "preview:zentrox:vp", "state": "awaiting"}])
    edited = copy.deepcopy(BASE)
    edited[2] = para("A line of his own.")
    docs = Docs({"L1": "2026-10-15T11:00:00Z", "C1": "2026-10-15T09:00:00Z"},
                {"L1": edited, "C1": BASE})
    out = run(db, docs)
    assert out["scratch"] == 1 and out["cancelled"] == 0
    assert db.t["hunter_doc_edits"][0]["scratch"] is True


def test_a_dry_run_stores_nothing(monkeypatch):
    db = world(monkeypatch)
    edited = copy.deepcopy(BASE)
    edited[2] = para("A line of his own.")
    out = run(db, Docs({"L1": "2026-10-15T11:00:00Z", "C1": "2026-10-15T09:00:00Z"},
                       {"L1": edited, "C1": BASE}), apply=False)
    assert out["edited"] == 1 and db.t["hunter_doc_edits"] == []
    assert db.t["hunter_row_state"][0]["docs_modified_at"] is None
    assert "dry run" in docedits.lines(out, apply=False)[0]


def test_his_edits_reach_the_writer_without_his_personal_lines(monkeypatch):
    line = "I have played in bands for twenty years and still record at home."
    after = copy.deepcopy(BASE)
    after[2] = para("Zentrox is clearly very busy. " + line)
    rows = [{"before_paragraphs": BASE, "after_paragraphs": after, "noticed_at": "x"}]
    monkeypatch.setattr(docedits, "db_get", lambda cfg, t, p: rows)
    got = docedits.edit_examples(None, [line])
    assert "HUNTER WROTE: Zentrox is busy and the gap is signed partners." in got
    assert "HE MADE IT: Zentrox is clearly very busy." in got
    assert "bands" not in got


def test_the_batch_record_says_how_much_he_edits_the_letters(monkeypatch):
    from hunter import batchstats, ratings
    from hunter.package import style
    roles = [{"job_id": f"r{i}", "company": f"Co{i}", "presented_at": "2026-10-16T10:00:00+00:00",
              "source": "ats_sweep", "krish_verdict": "",
              style.FIELD: style.HOUSE_STYLE if i < 4 else None} for i in range(6)]
    edits = [{"job_id": "r0", "edit_chars": 40, "style_changed": False, "scratch": False},
             {"job_id": "r0", "edit_chars": 10, "style_changed": True, "scratch": False},
             {"job_id": "r1", "edit_chars": 100, "style_changed": False, "scratch": False},
             {"job_id": "r2", "edit_chars": 999, "style_changed": True, "scratch": True}]
    tables = {"hunter_seen_roles": roles, "hunter_doc_edits": edits, "hunter_verdict_events": []}
    monkeypatch.setattr(batchstats, "db_get", lambda cfg, t, p: tables[t])
    monkeypatch.setattr(ratings, "latest", lambda cfg: {})
    monkeypatch.setattr(style, "stamped", lambda row: (row or {}).get(style.FIELD) == style.HOUSE_STYLE)
    (b,) = batchstats.measure(None)
    assert (b.letters_built, b.letters_edited, b.formatting_edited) == (4, 2, 1)
    assert b.edit_chars_median == 70, "the largest edit per role, a preview never counted"
    row = batchstats._row(b, "now")
    assert (row["letters_built"], row["letters_edited"], row["edit_chars_median"],
            row["formatting_edited"]) == (4, 2, 70, 1)
