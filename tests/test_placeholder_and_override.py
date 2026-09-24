"""Two ways a company that is not a company reached his sheet, 2026-09-24.

Run 125 staged four roles. Two of them should never have been there.

"Confidential" is not a company. It scored 6.7 and cleared the 6.0 floor on
evidence reading "in ai infrastructure (inference and confidential GPU)": the
advert path matched the WORD confidential in somebody else's posting and
attached it to a company that does not exist. Same failure as resolving
Perplexity to a domain registrar.

Strativ Group IS refusable. It scored 0.0 with "staffing (Recruitment Agency)
(-5)", which is the evidence fix working, and staged anyway because its role
cleared EXCEPTIONAL_MERIT. That override is Krish's own ruling on Citi and it
needs the company to be the employer.
"""
from __future__ import annotations

import hunter.run as R
from hunter import company as comp
from hunter import companyintel as intel


def test_a_placeholder_employer_names_no_company():
    for name in ("Confidential", "  stealth startup ", "Undisclosed", "N/A",
                 "Our Client", "", "  ", "TBC"):
        assert intel.is_placeholder(name), name
    for name in ("Sierra", "Selby Jennings", "OpenAI", "Confidential Computing Inc"):
        assert not intel.is_placeholder(name), name


def test_nothing_is_read_out_of_an_advert_for_a_placeholder():
    """Whatever the advert says, it is not about this employer, because no
    employer is named. Anything stored would be a fact about somebody else."""
    jd = ("Confidential is a leader in ai infrastructure for inference and "
          "confidential GPU workloads at scale across every major cloud.")
    assert intel.from_posting(jd, "https://x/1", "Confidential") == {}
    # The same sentence about a real company is still evidence.
    jd2 = jd.replace("Confidential is", "Together AI is")
    assert intel.from_posting(jd2, "https://x/1", "Together AI")


def _score(points, evidenced=True):
    class C:
        name, points_, evidenced_ = "disqualifier", points, evidenced
    c = comp.Component("disqualifier", points, evidenced, "staffing", "https://x")
    class S:
        components = [c]
    return S()


def test_an_exceptional_role_does_not_open_a_disqualified_company():
    """A recruitment agency is not an employer whose role can be ideal."""
    assert R.disqualified(_score(comp.DISQUALIFIER_PENALTY)) is True


def test_an_ordinary_low_score_still_opens_on_merit():
    """Citi. His ruling, and it is not being changed: G14 opens for a role
    scoring 9 or more on merit at a company that is merely poor."""
    class S:
        components = [comp.Component("category", 0.0, True, "banking", "https://x")]
    assert R.disqualified(S()) is False
    assert R.disqualified(None if False else type("E", (), {"components": []})()) is False


def test_an_unevidenced_penalty_is_not_a_disqualification():
    """Unknown and zero are different answers, here as everywhere."""
    assert R.disqualified(_score(comp.DISQUALIFIER_PENALTY, evidenced=False)) is False


def test_a_board_miss_is_retried_rather_than_remembered_for_ever(monkeypatch):
    """The boards fix, 2026-09-24.

    A failed probe was written to the cache as null and never retried: no TTL,
    no staleness check, no invalidation anywhere. companyintel has exactly this
    rule and says why ("a company hunter could not read a fortnight ago may
    simply have had a page down, and without a retry that becomes a policy").
    The board cache had nothing, so a company probed in a quiet week with no
    open jobs, a slug the 2 to 5 candidate spellings missed, or a careers page
    that happened to be down was excluded from the sweep permanently. Fourteen
    of his own target companies were in that state, Anysphere (Cursor),
    Beehiiv, Every and Lindy among them.
    """
    from datetime import datetime, timezone
    from hunter.ats import discover as disc

    probed: list[str] = []

    def fake_probe(slug):
        probed.append(slug)
        return ("ashby", 4)

    monkeypatch.setattr(disc, "probe", fake_probe)

    # A miss recorded moments ago is still settled: the budget goes elsewhere.
    fresh = {"missed_at": datetime.now(timezone.utc).isoformat()}
    cache = {"nope": dict(fresh)}
    assert disc.discover(None, "Nope", cache) is None
    assert probed == [], "a fresh miss must not be probed again"

    # One recorded long ago is not. It gets another look.
    cache = {"nope": {"missed_at": "2020-01-01T00:00:00+00:00"}}
    assert disc.discover(None, "Nope", cache) == ("ashby", "nope")
    assert probed, "a stale miss must be retried"

    # And a hit is never re-probed, whatever its age.
    probed.clear()
    cache = {"yes": {"ats": "ashby", "slug": "yes"}}
    assert disc.discover(None, "Yes", cache) == ("ashby", "yes")
    assert probed == []


def test_a_new_miss_is_written_with_its_date(monkeypatch):
    """Undated misses cannot expire, which is how the old ones became
    permanent."""
    from hunter.ats import discover as disc
    monkeypatch.setattr(disc, "probe", lambda slug: None)
    monkeypatch.setattr(disc, "from_careers_page", lambda url: None)
    cache: dict = {}
    assert disc.discover(None, "Nowhere Ltd", cache) is None
    entry = cache[disc.slugify("Nowhere Ltd")]
    assert disc.is_miss(entry) and entry.get("missed_at"), entry
    assert not disc._miss_is_stale(entry), "a miss just written is not stale"


def test_a_row_he_names_is_removed_whatever_column_a_says(monkeypatch, capsys):
    """2026-09-24: he asked for two rows off the sheet by name, and had already
    marked both of them Yes.

    prune-sheet refuses any row that is not still "New", on purpose, so that
    nothing HUNTER decides can delete a row he has written on. That guard is
    not weakened here. Naming the job id is the only way past it, because then
    the judgement is his and not the machine's.
    """
    import hunter.run as R
    from hunter.sheet import SheetRow, N_COLS

    rows = [SheetRow(row_number=3, cells=[""] * N_COLS, verdict="Yes",
                     company="Confidential", role="SVP Revenue", jd_url=None),
            SheetRow(row_number=4, cells=[""] * N_COLS, verdict="Yes",
                     company="Keepme", role="Head of GTM", jd_url=None)]
    db = [{"job_id": "confidential:svp", "company": "Confidential",
           "title": "SVP Revenue", "krish_verdict": "Yes", "location": "London"},
          {"job_id": "keepme:head", "company": "Keepme", "title": "Head of GTM",
           "krish_verdict": "Yes", "location": "London"}]
    deleted: list = []

    class FakeSheet:
        def read_pipeline(self, headers):
            return rows

        def delete_rows(self, ns):
            deleted.extend(ns)
            return len(ns)

    monkeypatch.setattr(R, "build_context", lambda: (None, type("C", (), {
        "sheet_headers": []})()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: FakeSheet())
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: db)
    monkeypatch.setattr(R, "match_rows",
                        lambda s, d: (list(zip(rows, db)), [], [], []))

    assert R.cmd_prune_sheet(apply=True, job_ids="confidential:svp") == 0
    assert deleted == [3], "only the row he named"
    out = capsys.readouterr().out
    assert "removed by name" in out

    # And an id that matches nothing stops, rather than reporting success while
    # the row he wanted gone is still there.
    deleted.clear()
    assert R.cmd_prune_sheet(apply=True, job_ids="typo:nothing") == 1
    assert deleted == []


def test_prune_still_refuses_a_judged_row_it_was_not_given(monkeypatch):
    """The guard itself. Without a named id, a row carrying his verdict is
    untouchable however bad hunter thinks it is."""
    import hunter.run as R
    from hunter.sheet import SheetRow, N_COLS
    rows = [SheetRow(row_number=3, cells=[""] * N_COLS, verdict="Yes",
                     company="Confidential", role="SVP Revenue", jd_url=None)]
    db = [{"job_id": "confidential:svp", "company": "Confidential",
           "title": "SVP Revenue", "krish_verdict": "Yes",
           "location": "Brazil"}]
    deleted: list = []

    class FakeSheet:
        def read_pipeline(self, headers):
            return rows

        def delete_rows(self, ns):
            deleted.extend(ns)
            return len(ns)

    monkeypatch.setattr(R, "build_context", lambda: (None, type("C", (), {
        "sheet_headers": []})()))
    monkeypatch.setattr(R, "Sheet", lambda *a, **k: FakeSheet())
    monkeypatch.setattr(R, "GoogleServiceAccount",
                        lambda cfg: type("T", (), {"access_token": ""})())
    monkeypatch.setattr(R, "db_get", lambda cfg, table, params: db)
    monkeypatch.setattr(R, "match_rows",
                        lambda s, d: (list(zip(rows, db)), [], [], []))
    # Brazil would normally be pruned as outside canon geography.
    assert R.cmd_prune_sheet(apply=True) == 0
    assert deleted == [], "a row he has judged is never deleted by machine"
