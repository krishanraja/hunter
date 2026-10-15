"""His own line, asked by email and written to canon 9.14 as he wrote it
(facts.py, canonwrite.py).

Everything here is invented: the canon is a miniature with the real section
headings, and every line "he" writes is made up. His real lines live in
canon, never in this repository.
"""
import pytest

from hunter import canonwrite, facts
from hunter.apply import approval, inbox
from hunter.canon import _parse_sections
from hunter.package import personal

HIM = "hello@krishraja.com"

CANON = """# KRISH CANON
## 9.12 TEMPLATE CONTRACT
The letter master has slots.
## 9.13 SHEET CONTRACT
Header, exact, in order:
A Verdict, B Company.

Rows are his.
"""


class FakeDb:
    def __init__(self, body=CANON, version=11):
        self.t = {"canon_documents": [{"slug": "krish-canon", "body": body, "version": version}],
                  "hunter_fact_requests": [],
                  "hunter_seen_roles": [{"job_id": "kestrel:vp", "company": "Kestrel",
                                         "title": "VP Partnerships", "judge_answers": {
                                             "business": {"quote": "voice agents for call centres",
                                                          "what_it_sells": "Voice AI agents and audio tools"}}}]}
        self.next_id = 1
        self.mangle = None          # a function applied to a canon body on write
        self.writes = 0

    @staticmethod
    def _match(row, filters):
        for k, v in filters.items():
            if k in ("select", "limit", "order"):
                continue
            have = row.get(k)
            if v.startswith("eq."):
                if str(have) != v[3:]:
                    return False
            elif v.startswith("in.("):
                if str(have) not in v[4:-1].split(","):
                    return False
        return True

    def get(self, cfg, table, params):
        return [dict(r) for r in self.t[table] if self._match(r, params)]

    def insert(self, cfg, table, rows, **kw):
        for r in rows:
            if table == "hunter_fact_requests":
                if any(x["category"] == r["category"] and x["status"] in ("queued", "asked")
                       for x in self.t[table]):
                    raise RuntimeError("409 duplicate key")
                r = {"id": self.next_id, "processed_message_ids": [], "result": None,
                     "answer": None, **r}
                self.next_id += 1
            self.t[table].append(r)

    def patch_where(self, cfg, table, filters, values):
        n = 0
        for r in self.t[table]:
            if self._match(r, filters):
                v = dict(values)
                if table == "canon_documents":
                    self.writes += 1
                    if self.mangle and "body" in v:
                        v["body"] = self.mangle(v["body"])
                r.update(v)
                n += 1
        return n

    @property
    def body(self):
        return self.t["canon_documents"][0]["body"]

    @property
    def version(self):
        return self.t["canon_documents"][0]["version"]


@pytest.fixture
def db(monkeypatch):
    d = FakeDb()
    for mod in (canonwrite, facts):
        monkeypatch.setattr(mod, "db_get", d.get)
        monkeypatch.setattr(mod, "db_patch_where", d.patch_where)
    monkeypatch.setattr(facts, "db_insert", d.insert)
    return d


@pytest.fixture
def with_section(db):
    canonwrite.add_section(None, expect_version=11)
    return db


def bank(db):
    return personal.parse_bank(_parse_sections(db.body)[canonwrite.SECTION][1])


def others(body):
    s = _parse_sections(body)
    s.pop(canonwrite.SECTION, None)
    return s


# ---------- canonwrite ----------

def test_the_section_is_made_once_at_the_version_he_was_shown(db):
    with pytest.raises(canonwrite.CanonWriteError):
        canonwrite.add_section(None, expect_version=10)
    assert db.body == CANON and db.writes == 0
    assert canonwrite.add_section(None, expect_version=11) == 12
    assert others(db.body) == others(CANON) and bank(db) == []
    with pytest.raises(canonwrite.CanonWriteError):
        canonwrite.add_section(None, expect_version=12)


def test_an_entry_is_appended_his_words_exact_and_nothing_else_moves(with_section):
    db = with_section
    words = "I’ve read every issue of a music magazine since I was twelve."
    w = canonwrite.add_entry(None, categories=["voice_audio_speech"], terms=["music"],
                             words=words, added="2026-10-16")
    assert (w.entry, w.version_before, w.version_after) == (1, 12, 13)
    assert bank(db) == [personal.Entry(1, ("voice_audio_speech",), ("music",), words)]
    assert others(db.body) == others(CANON)
    assert words in db.body


def test_the_same_words_are_not_written_twice(with_section):
    db = with_section
    canonwrite.add_entry(None, categories=["mindset"], terms=[], words="Words one.")
    before = db.writes
    w = canonwrite.add_entry(None, categories=["mindset"], terms=[], words="Words one.")
    assert w.entry == 1 and db.writes == before and len(bank(db)) == 1


def test_an_entry_lands_inside_914_when_another_section_follows_it(db):
    db.t["canon_documents"][0]["body"] = CANON + "\n## 9.14 PERSONAL CONNECTIONS\nIntro.\n\n## 9.15 LATER THING\nKept.\n"
    canonwrite.add_entry(None, categories=["mindset"], terms=["calm"], words="A line.")
    s = _parse_sections(db.body)
    assert personal.parse_bank(s["9.14"][1])[0].words == "A line."
    assert s["9.15"][1] == "## 9.15 LATER THING\nKept."


def test_a_canon_that_moved_while_writing_is_not_overwritten(with_section, monkeypatch):
    db = with_section
    real = db.patch_where

    def moved(cfg, table, filters, values):
        db.t["canon_documents"][0]["version"] += 1          # someone else wrote
        return real(cfg, table, filters, values)
    monkeypatch.setattr(canonwrite, "db_patch_where", moved)
    body = db.body
    with pytest.raises(canonwrite.CanonWriteError, match="moved"):
        canonwrite.add_entry(None, categories=["mindset"], terms=[], words="A line.")
    assert db.body == body


def test_a_write_that_reads_back_wrong_is_put_back(with_section):
    db = with_section
    body = db.body
    db.mangle = lambda b: b.replace("A line", "A LINE")
    with pytest.raises(canonwrite.CanonWriteError, match="restored"):
        canonwrite.add_entry(None, categories=["mindset"], terms=[], words="A line.")
    assert db.body == body


def test_an_entry_is_taken_back_only_while_it_holds_what_was_written(with_section):
    db = with_section
    canonwrite.add_entry(None, categories=["mindset"], terms=[], words="First.")
    canonwrite.add_entry(None, categories=["mindset"], terms=[], words="Second.")
    with pytest.raises(canonwrite.CanonWriteError, match="changed"):
        canonwrite.remove_entry(None, number=1, words="Something else.")
    canonwrite.remove_entry(None, number=1, words="First.")
    assert [e.words for e in bank(db)] == ["Second."]
    assert others(db.body) == others(CANON)


def test_no_section_no_entry(db):
    with pytest.raises(canonwrite.CanonWriteError, match="no 9.14"):
        canonwrite.add_entry(None, categories=["mindset"], terms=[], words="A line.")
    assert db.writes == 0


# ---------- the ask ----------

class Mail:
    def __init__(self, give_id=True):
        self.sent, self.give_id, self.n = [], give_id, 0

    def __call__(self, cfg, subject, html, **kw):
        self.n += 1
        self.sent.append((subject, html))
        return {"sent": True, "id": f"out{self.n}", "thread_id": "th1"} if self.give_id \
            else {"sent": False}


def reply(token, body, *, sender=HIM, mid="r1", at=1):
    return {"message_id": mid, "thread_id": "th1", "token": str(token), "from": sender,
            "subject": f"Re: One line {facts.SUBJECT_MARK}{token}]", "body": body,
            "internal_date": at}


def test_one_open_ask_per_category(db):
    a = facts.request(None, "voice_audio_speech", "kestrel:vp")
    b = facts.request(None, "voice_audio_speech", "other:role")
    assert a["id"] == b["id"] and len(db.t["hunter_fact_requests"]) == 1
    with pytest.raises(ValueError):
        facts.request(None, "astrology", "kestrel:vp")


def test_an_ask_is_marked_asked_only_when_gmail_gave_an_id(db):
    facts.request(None, "voice_audio_speech", "kestrel:vp")
    assert facts.ask(None, Mail(give_id=False)) == {"asked": 0, "not_sent": 1}
    assert db.t["hunter_fact_requests"][0]["status"] == "queued"
    mail = Mail()
    assert facts.ask(None, mail) == {"asked": 1, "not_sent": 0}
    row = db.t["hunter_fact_requests"][0]
    assert (row["status"], row["gmail_message_id"]) == ("asked", "out1")
    subject, html = mail.sent[0]
    assert facts.fact_id_from_subject(subject) == "1"
    assert "Kestrel" in html and inbox.OUTBOUND_MARKER in html


def test_the_approval_reader_never_sees_a_fact_subject():
    assert approval.token_from_subject(f"Re: One line {facts.SUBJECT_MARK}4]") == ""
    assert facts.fact_id_from_subject("Re: Apply: X Y [hunter #abc]") == ""
    assert facts.fact_id_from_subject(f"Fwd: Re: x {facts.SUBJECT_MARK}12]") == "12"


@pytest.fixture
def asked(with_section):
    facts.request(None, "voice_audio_speech", "kestrel:vp")
    facts.ask(None, Mail())
    return with_section


# ---------- his reply ----------

def test_his_reply_is_written_exactly_and_quoted_back_to_him(asked, capsys):
    db = asked
    words = "I recorded voiceovers for audio dramas through university."
    body = f"{words}\n\nOn Thu, 15 Oct 2026, hunter wrote:\n> Reply with the line"
    mail = Mail()
    out = facts.read(None, fetch=lambda cfg: [reply(1, body)], send=mail)
    assert out.counts["written"] == 1
    (e,) = bank(db)
    assert e.words == words and e.categories == ("voice_audio_speech",)
    assert e.terms == ("audio",)
    row = db.t["hunter_fact_requests"][0]
    assert (row["status"], row["answer"], row["canon_version_after"]) == ("written", words, db.version)
    subject, html = mail.sent[-1]
    assert words in html and "entry 1" in html
    assert words not in capsys.readouterr().out


def test_terms_are_what_his_line_and_the_business_both_say():
    sentences = ["Voice AI agents and audio tools", "voice agents for call centres"]
    assert facts.terms_for("I have run call centres and built voice tools.", sentences) == \
        ["call", "centres", "voice", "tools"]
    assert facts.terms_for("I love what you are doing.", sentences) == []


def test_one_pair_of_quotes_around_his_line_is_taken_off():
    assert facts.his_line('"I sing."\n') == "I sing."
    assert facts.his_line('“I sing.”') == "I sing."
    assert facts.his_line('I said "sing" twice.') == 'I said "sing" twice.'
    assert facts.his_line("Line one\nline two\n-- \nKrish") == "Line one line two"


def test_a_reply_not_from_him_writes_nothing(asked):
    db = asked
    out = facts.read(None, fetch=lambda cfg: [reply(1, "A line.", sender="x@example.com")],
                     send=Mail())
    assert out.counts["not_his"] == 1 and bank(db) == []


def test_hunters_own_mail_is_never_read_as_his(asked):
    db = asked
    body = f"Reply with the line\n{inbox.OUTBOUND_MARKER}"
    out = facts.read(None, fetch=lambda cfg: [reply(1, body, mid="own")], send=Mail())
    assert out.counts["written"] == 0 and bank(db) == []


def test_a_reply_is_acted_on_once(asked):
    db = asked
    fetch = lambda cfg: [reply(1, "A line about audio.")]
    facts.read(None, fetch=fetch, send=Mail())
    writes = db.writes
    out = facts.read(None, fetch=fetch, send=Mail())
    assert out.counts["written"] == 0 and db.writes == writes and len(bank(db)) == 1


def test_undo_takes_it_back_and_a_later_line_replaces(asked):
    db = asked
    facts.read(None, fetch=lambda cfg: [reply(1, "First line about audio.", mid="a", at=1)],
               send=Mail())
    out = facts.read(None, fetch=lambda cfg: [reply(1, "Second line about audio.", mid="b", at=2)],
                     send=Mail())
    assert out.counts["replaced"] == 1 and [e.words for e in bank(db)] == ["Second line about audio."]
    mail = Mail()
    out = facts.read(None, fetch=lambda cfg: [reply(1, "undo", mid="c", at=3)], send=mail)
    assert out.counts["undone"] == 1 and bank(db) == []
    assert db.t["hunter_fact_requests"][0]["status"] == "undone"
    assert "Taken back" in mail.sent[-1][0]


def test_an_entry_he_changed_is_not_taken_back(asked):
    db = asked
    facts.read(None, fetch=lambda cfg: [reply(1, "A line about audio.", mid="a")], send=Mail())
    db.t["canon_documents"][0]["body"] = db.body.replace("A line about audio.", "His own edit.")
    mail = Mail()
    out = facts.read(None, fetch=lambda cfg: [reply(1, "UNDO", mid="b", at=2)], send=mail)
    assert out.counts["not_undone"] == 1 and [e.words for e in bank(db)] == ["His own edit."]
    assert "Not taken back" in mail.sent[-1][1]


def test_a_reply_too_long_for_a_letter_is_asked_again_not_written(asked):
    db = asked
    mail = Mail()
    out = facts.read(None, fetch=lambda cfg: [reply(1, "word " * 200)], send=mail)
    assert out.counts["too_long"] == 1 and bank(db) == []
    assert "Nothing was written" in mail.sent[-1][1]


def test_a_failed_canon_write_is_recorded_and_not_confirmed(asked):
    db = asked
    db.mangle = lambda b: b + "x"
    mail = Mail()
    out = facts.read(None, fetch=lambda cfg: [reply(1, "A line about audio.")], send=mail)
    assert out.counts["failed"] == 1 and bank(db) == []
    row = db.t["hunter_fact_requests"][0]
    assert row["status"] == "failed" and "A line" not in (row["result"] or "")
    assert not any(s.startswith("Written") for s, _ in mail.sent)


def test_the_hold_names_the_category_in_his_words():
    assert facts.hold_reason("voice_audio_speech") == \
        "held: needs one line from you about voice, audio or speech companies"


def test_an_insert_that_would_not_read_back_is_refused_before_it_is_sent(db):
    """Two 9.14 headings: the insert lands in the first, the parser reads the
    second, so the entry would be written and never read."""
    db.t["canon_documents"][0]["body"] = (CANON + "\n## 9.14 PERSONAL CONNECTIONS\nOne.\n"
                                          "\n## 9.14 PERSONAL CONNECTIONS\nTwo.\n")
    body = db.body
    with pytest.raises(canonwrite.CanonWriteError, match="did not read back"):
        canonwrite.add_entry(None, categories=["mindset"], terms=[], words="A line.")
    assert db.body == body and db.writes == 0


def test_with_nothing_asked_the_mailbox_is_not_read(db):
    def fetch(cfg):
        raise AssertionError("read the mailbox with nothing asked")
    assert facts.read(None, fetch=fetch, send=Mail()).counts["written"] == 0
