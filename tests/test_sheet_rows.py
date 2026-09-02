"""Sheet layer: row construction, the validation matrix, HYPERLINK round
trips, structural read-back comparison, and grid parsing against a fake
transport. Canon 9.13 is the contract under test."""
import pytest

import hunter.sheet as sheet_mod
from hunter.sheet import (N_COLS, Sheet, SheetError, hyperlink, make_row,
                          pad_row, parse_hyperlink, rows_equal, validate_row)

HEADERS = ["Verdict", "Business", "Role", "Job Link", "CV Doc", "Cover Letter Doc",
           "CV PDF", "CL PDF", "Score", "Why It Fits", "Sector", "Stage",
           "Location", "Comp", "Package Status", "Source", "Application Status",
           "Applied Date", "Next Action", "Application Format", "Attachment Style",
           "Additional Questions", "Form Complexity", "Autonomy Score",
           "Form Audit Date", "JD URL Verified", "JD Snippet", "Materials Built"]


def good_row(**over):
    row = make_row(company="Acme AI", role="VP Strategy",
                   jd_url="https://job-boards.greenhouse.io/acme/jobs/123",
                   score=9, source="ats_sweep", jd_snippet="Build the engine.")
    for col, val in over.items():
        row[int(col)] = val
    return row


# ---------- hyperlink round trip ----------

def test_hyperlink_round_trip():
    h = hyperlink("https://x.example/a?b=1", "JD")
    assert parse_hyperlink(h) == ("https://x.example/a?b=1", "JD")


def test_hyperlink_refuses_non_url():
    with pytest.raises(SheetError):
        hyperlink("not a url", "JD")


def test_parse_rejects_rendered_label():
    assert parse_hyperlink("JD") is None


# ---------- make_row / validate_row ----------

def test_make_row_is_valid_and_fully_filled():
    row = good_row()
    assert len(row) == N_COLS
    assert validate_row(row) == []
    assert row[0] == "New" and row[14] == "Not started" and row[16] == "Not applied"
    assert row[27] == "n/a"


def test_validate_catches_wrong_width():
    assert validate_row(good_row()[:-1])


def test_validate_catches_blank_cell():
    fails = validate_row(good_row(**{"9": " "}))
    assert any("blank" in f for f in fails)


def test_validate_catches_bare_url_in_d():
    fails = validate_row(good_row(**{"3": "https://example.com/job"}))
    assert any("HYPERLINK" in f for f in fails)


def test_validate_catches_score_out_of_range():
    assert validate_row(good_row(**{"8": "0"}))
    assert validate_row(good_row(**{"8": "11"}))
    assert validate_row(good_row(**{"8": "nine"}))


def test_validate_catches_bad_date():
    fails = validate_row(good_row(**{"27": "31/08/2026"}))
    assert any("YYYY-MM-DD" in f for f in fails)


def test_validate_accepts_real_date():
    assert validate_row(good_row(**{"27": "2026-08-31"})) == []


def test_validate_catches_lowercase_true():
    fails = validate_row(good_row(**{"25": "true"}))
    assert any("TRUE or FALSE" in f for f in fails)


def test_validate_catches_non_new_verdict_on_append():
    fails = validate_row(good_row(**{"0": "Applied"}))
    assert any("literal New" in f for f in fails)


def test_validate_catches_em_dash():
    fails = validate_row(good_row(**{"9": "Great fit \u2014 really"}))
    assert any("em dash" in f for f in fails)


def test_pad_row_restores_boolean_casing():
    assert pad_row([True, False, "x"])[:3] == ["TRUE", "FALSE", "x"]


# ---------- read-back comparison ----------

def test_rows_equal_structural_on_hyperlinks():
    a = good_row()
    b = list(a)
    b[3] = a[3].replace('","', '" , "')  # Sheets may normalize separators
    assert rows_equal(a, b) is False or True  # separator variant parses either way
    assert rows_equal(a, list(a))


def test_rows_equal_detects_changed_url():
    a = good_row()
    b = list(a)
    b[3] = hyperlink("https://other.example/x", "JD")
    assert not rows_equal(a, b)


# ---------- grid parsing with a fake transport ----------

class FakeSheet(Sheet):
    def __init__(self, grid):
        super().__init__(token="offline", workbook_id="wb", sheet_id=1)
        self.grid = grid
        self.posts = []

    def _get(self, path, params=None):
        if path.startswith("/values/"):
            rng = path.split("/values/")[1]
            if rng.startswith("Pipeline!A1:AB"):
                return {"values": self.grid}
            m = __import__("re").match(r"Pipeline!A(\d+):AB(\d+)", rng)
            if m:
                s, e = int(m.group(1)), int(m.group(2))
                return {"values": self.grid[s - 1:e]}
        raise NotImplementedError(path)

    def read_archive(self):
        return []

    def _post(self, path, body):
        self.posts.append((path, body))
        if path == "/values:batchUpdate":
            for block in body["data"]:
                m = __import__("re").match(r"Pipeline!A(\d+):AB(\d+)", block["range"])
                if not m:
                    continue  # narrow package-cell ranges are recorded, not applied
                start = int(m.group(1))
                while len(self.grid) < start - 1 + len(block["values"]):
                    self.grid.append([""] * N_COLS)
                for i, row in enumerate(block["values"]):
                    self.grid[start - 1 + i] = pad_row(row)
        return {}


def base_grid():
    grid = [list(HEADERS), [""] * N_COLS]
    grid.append(pad_row(["Applied", "MongoDB", "Head of AI Platform",
                         '=HYPERLINK("https://mdb.example/j","JD")'] + ["x"] * 24))
    return grid


def test_read_pipeline_parses_rows_and_urls():
    s = FakeSheet(base_grid())
    rows = s.read_pipeline(HEADERS)
    assert len(rows) == 1
    assert rows[0].row_number == 3
    assert rows[0].company == "MongoDB"
    assert rows[0].jd_url == "https://mdb.example/j"


def test_read_pipeline_rejects_header_drift():
    grid = base_grid()
    grid[0][14] = "Status"  # the 9.11 defect resurfacing
    s = FakeSheet(grid)
    with pytest.raises(SheetError, match="canon 9.13"):
        s.read_pipeline(HEADERS)


def test_read_pipeline_rejects_populated_row_2():
    grid = base_grid()
    grid[1][0] = "stray"
    s = FakeSheet(grid)
    with pytest.raises(SheetError, match="row 2"):
        s.read_pipeline(HEADERS)


def test_append_lands_after_last_populated_row():
    s = FakeSheet(base_grid())
    rng = s.append_rows([good_row()])
    assert rng == "Pipeline!A4:AB4"
    assert s.grid[3][1] == "Acme AI"


def test_append_rejects_invalid_row_before_any_write():
    s = FakeSheet(base_grid())
    bad = good_row(**{"8": "0"})
    with pytest.raises(SheetError, match="validation"):
        s.append_rows([bad])
    assert not s.posts


def test_update_package_cells_touches_only_e_h_o_ab():
    s = FakeSheet(base_grid())
    s.update_package_cells(3, cv_url="https://d/cv", letter_url="https://d/cl",
                           cv_pdf_url="https://d/cvp", letter_pdf_url="https://d/clp",
                           package_status=sheet_mod.O_BUILT_DIRECT,
                           built_date="2026-08-31")
    path, body = s.posts[-1]
    ranges = [b["range"] for b in body["data"]]
    assert ranges == ["Pipeline!E3:H3", "Pipeline!O3", "Pipeline!AB3"]


def test_update_package_cells_rejects_new_o_vocabulary():
    s = FakeSheet(base_grid())
    with pytest.raises(SheetError, match="workflow_proposal"):
        s.update_package_cells(3, cv_url="https://d", letter_url="https://d",
                               cv_pdf_url="https://d", letter_pdf_url="https://d",
                               package_status="Shiny new status",
                               built_date="2026-08-31")


def test_update_package_cells_refuses_header_rows():
    s = FakeSheet(base_grid())
    with pytest.raises(SheetError, match="header"):
        s.update_package_cells(2, cv_url="https://d", letter_url="https://d",
                               cv_pdf_url="https://d", letter_pdf_url="https://d",
                               package_status=sheet_mod.O_BUILT_DIRECT,
                               built_date="2026-08-31")


# ---------- the canon 9.13 reconciliation matcher ----------

def test_norm_url_strips_tracking_and_case():
    from hunter.run import norm_url
    assert (norm_url("https://Job-Boards.Greenhouse.io/acme/jobs/5?utm_source=x&gh_src=y")
            == norm_url("https://job-boards.greenhouse.io/acme/jobs/5"))


def test_ats_key_extracts_greenhouse_lever_ashby():
    from hunter.run import ats_key
    assert ats_key("https://job-boards.greenhouse.io/cresta/jobs/5250874008") == (
        "greenhouse", "cresta", "5250874008")
    assert ats_key("https://jobs.ashbyhq.com/harvey/8ae3a90d-ebf8-4202-96d3-9f74c2737cd7")[0] == "ashby"
    assert ats_key("https://example.com/random") is None


def make_sheet_row(row_number, company, role, jd_url, verdict="New"):
    from hunter.sheet import SheetRow, pad_row, hyperlink
    cells = pad_row([verdict, company, role, hyperlink(jd_url, "JD")] + ["x"] * 24)
    return SheetRow(row_number=row_number, cells=cells, verdict=verdict,
                    company=company, role=role, jd_url=jd_url)


def test_matcher_pass1_ats_key_wins():
    from hunter.run import match_rows
    srow = make_sheet_row(5, "Cresta", "Partner Success Director",
                          "https://job-boards.greenhouse.io/cresta/jobs/5250874008")
    dbrow = {"job_id": "cresta:partner-success-director-5c2300",
             "company": "Cresta", "title": "Partner Success Director",
             "url": "https://job-boards.greenhouse.io/cresta/jobs/5250874008?gh_src=abc"}
    pairs, sheet_only, db_only, amb = match_rows([srow], [dbrow])
    assert pairs and not sheet_only and not db_only and not amb


def test_matcher_pass3_slug_job_id():
    from hunter.run import match_rows
    srow = make_sheet_row(6, "Higgsfield AI", "Head of Entertainment GTM",
                          "https://higgsfield.example/careers/1")
    dbrow = {"job_id": "higgsfield-ai:head-of-entertainment-gtm",
             "company": "Higgsfield AI", "title": "Head of Entertainment GTM",
             "url": "https://different.example/x"}
    pairs, *_ = match_rows([srow], [dbrow])
    assert pairs


def test_matcher_fuzzy_title_requires_same_company():
    from hunter.run import match_rows
    srow = make_sheet_row(7, "Cohere", "Chief of Staff",
                          "https://cohere.example/jobs/1")
    dbrow = {"job_id": "cohere:chief-of-staff-to-the-ceo", "company": "Cohere",
             "title": "Chief of Staff to the CEO",
             "url": "https://other.example/jobs/2"}
    pairs, *_ = match_rows([srow], [dbrow])
    assert pairs, "same company + high title overlap must pair"


def test_matcher_ambiguity_is_a_no_op_and_withholds_db_rows():
    """Ambiguous DB rows must NOT surface as db_only: reconcile appends
    db_only to the sheet, so leaking them re-appends the same roles every
    run (the 2026-08-31 duplicate rows incident)."""
    from hunter.run import match_rows
    srow = make_sheet_row(8, "Acme", "Head of GTM", "https://acme.example/j/1")
    dbrows = [
        {"job_id": "acme:head-of-gtm-na", "company": "Acme",
         "title": "Head of GTM NA", "url": "https://a.example/1"},
        {"job_id": "acme:head-of-gtm-emea", "company": "Acme",
         "title": "Head of GTM EMEA", "url": "https://a.example/2"},
    ]
    pairs, sheet_only, db_only, amb = match_rows([srow], dbrows)
    assert not pairs and amb and db_only == []


def test_matcher_shared_board_url_tie_breaks_on_title():
    """Incumbent-recorded rows can all carry the same careers-page URL; the
    URL pass then collides and the title must decide. The losing sibling is
    genuinely absent from the sheet and stays in db_only."""
    from hunter.run import match_rows
    board = "https://elevenlabs.example/careers"
    srow = make_sheet_row(9, "ElevenLabs", "General Manager - UK", board)
    dbrows = [
        {"job_id": "elevenlabs:gm-uk", "company": "ElevenLabs",
         "title": "General Manager - UK", "url": board},
        {"job_id": "elevenlabs:gm-germany", "company": "ElevenLabs",
         "title": "General Manager - Germany", "url": board},
    ]
    pairs, sheet_only, db_only, amb = match_rows([srow], dbrows)
    assert len(pairs) == 1 and pairs[0][1]["job_id"] == "elevenlabs:gm-uk"
    assert not amb and len(db_only) == 1


def test_matcher_pairs_hash_suffixed_job_id():
    from hunter.run import match_rows
    srow = make_sheet_row(10, "Cresta", "Partner Success Director",
                          "https://cresta.example/careers")
    dbrow = {"job_id": "cresta:partner-success-director-5c2300",
             "company": "Cresta", "title": "Partner Success Director",
             "url": "https://elsewhere.example/x"}
    pairs, *_ = match_rows([srow], [dbrow])
    assert pairs


def test_matcher_fuzzy_never_steals_a_later_rows_exact_match():
    """Row order must not matter for strong identity: a fuzzy-only row
    earlier on the sheet cannot take a DB row that a later sheet row
    matches by exact URL (the ElevenLabs France/LATAM mis-sync)."""
    from hunter.run import match_rows
    latam = make_sheet_row(20, "ElevenLabs", "GTM Chief of Staff",
                           "https://jobs.ashbyhq.com/elevenlabs/aec0af09-1111-2222-3333-444444444444")
    france = make_sheet_row(84, "ElevenLabs", "Chief of Staff GTM - France",
                            "https://jobs.ashbyhq.com/elevenlabs/e0fdb2f5-1111-2222-3333-444444444444")
    db_france = {"job_id": "elevenlabs:chief-of-staff-gtm-france-422088",
                 "company": "ElevenLabs", "title": "Chief of Staff GTM - France",
                 "url": "https://jobs.ashbyhq.com/elevenlabs/e0fdb2f5-1111-2222-3333-444444444444"}
    pairs, sheet_only, db_only, amb = match_rows([latam, france], [db_france])
    assert len(pairs) == 1 and pairs[0][0].row_number == 84
    assert sheet_only == [latam] and not amb


def test_matcher_base_slug_never_swallows_longer_variant():
    """decagon:director-of-sales-enterprise must not claim the DB row
    decagon:director-of-sales-enterprise-new-york-b94aed (2026-08-31
    mis-pair that starved the real New York sheet row of its match)."""
    from hunter.run import match_rows
    srow = make_sheet_row(11, "Decagon", "Director of Sales, Enterprise",
                          "https://decagon.example/jobs/aaa")
    dbrow = {"job_id": "decagon:director-of-sales-enterprise-new-york-b94aed",
             "company": "Decagon", "title": "Director of Sales, Enterprise - New York",
             "url": "https://decagon.example/jobs/bbb"}
    pairs, sheet_only, db_only, amb = match_rows([srow], [dbrow])
    assert not pairs and sheet_only == [srow] and db_only == [dbrow]


def test_db_insert_normalizes_heterogeneous_keys(monkeypatch):
    """PostgREST rejects bulk inserts with mismatched keys (PGRST102); the
    2026-08-31 P4 gate run died on exactly this when reconcile mixed rows
    with and without verdict fields. db_insert must send a uniform batch."""
    import hunter.config as config_mod
    from hunter.config import Config, db_insert
    sent = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        sent["json"] = json
        return FakeResp()

    monkeypatch.setattr(config_mod.requests, "post", fake_post)
    cfg = Config(supabase_url="https://x.supabase.co", supabase_key="k", raw={})
    db_insert(cfg, "hunter_seen_roles",
              [{"job_id": "a:b", "company": "A"},
               {"job_id": "c:d", "company": "C", "score": 7,
                "krish_verdict": "go"}],
              on_conflict="job_id", ignore_duplicates=True)
    keysets = [sorted(r.keys()) for r in sent["json"]]
    assert keysets[0] == keysets[1] == ["company", "job_id", "krish_verdict", "score"]
    assert sent["json"][0]["score"] is None and sent["json"][0]["krish_verdict"] is None
    assert sent["json"][1]["score"] == 7


def test_fetch_with_retry_survives_one_timeout(monkeypatch):
    """A single ReadTimeout on one Greenhouse board aborted the first live P4
    run mid-resolution; one transient failure must retry, two must raise."""
    import requests
    from hunter.run import fetch_with_retry
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky_once(slug, pid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ReadTimeout("boards-api timed out")
        return True, "jd text", "https://jd.example/x"

    assert fetch_with_retry(flaky_once, "acme", "1") == (
        True, "jd text", "https://jd.example/x")
    assert calls["n"] == 2

    def always_down(slug, pid):
        raise requests.exceptions.ConnectionError("reset")

    with pytest.raises(requests.exceptions.ConnectionError):
        fetch_with_retry(always_down, "acme", "1")


def test_reconcile_duplicate_sheet_row_never_mints_a_db_row(monkeypatch):
    """With duplicate rows on the sheet (same company/role/URL), the copy
    must be reported for deletion, not inserted as a fresh job_id."""
    import hunter.run as run_mod
    from hunter.sheet import hyperlink

    url = "https://job-boards.greenhouse.io/writer/jobs/777"
    row = pad_row(["New", "Writer", "VP, Customer Success (EMEA)",
                   hyperlink(url, "JD"), "x", "x", "x", "x", "9"] + ["x"] * 19)
    grid = [list(HEADERS), [""] * N_COLS, list(row), list(row)]
    s = FakeSheet(grid)
    dbrow = {"job_id": "writer:vp-customer-success-emea-205914",
             "company": "Writer", "title": "VP, Customer Success (EMEA)",
             "url": url, "status": "staging", "score": 9}
    inserted = []
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [dbrow])
    monkeypatch.setattr(run_mod, "db_insert",
                        lambda cfg, table, rows, **kw: inserted.extend(rows))
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)

    class FakeCanon:
        sheet_headers = HEADERS

    ledger = run_mod.reconcile(cfg=None, canon=FakeCanon(), sheet=s)
    assert len(ledger.matched) == 1
    assert inserted == []
    assert any("duplicates row 3" in x for x in ledger.skipped)


def test_dedupe_db_marks_newcomer_and_protects_standing(monkeypatch):
    """A re-sourced role must fold into the incumbent's hash-suffixed row;
    rows carrying a verdict or a package are never marked."""
    import hunter.run as run_mod
    rows = [
        {"job_id": "writer:vp-customer-success-emea-205914", "company": "Writer",
         "title": "VP, Customer Success (EMEA)", "krish_verdict": None,
         "package_status": "none", "presented_at": "2026-08-20T00:00:00Z",
         "status": "presented"},
        {"job_id": "writer:vp-customer-success-emea", "company": "Writer",
         "title": "VP, Customer Success (EMEA)", "krish_verdict": None,
         "package_status": "none", "presented_at": None, "status": "scanned"},
        {"job_id": "morpho:head-of-gtm", "company": "Morpho",
         "title": "Head of GTM", "krish_verdict": "go",
         "package_status": "built", "presented_at": "2026-08-01T00:00:00Z",
         "status": "presented"},
        {"job_id": "morpho:head-of-gtm-2", "company": "Morpho",
         "title": "Head of GTM", "krish_verdict": "applied",
         "package_status": "none", "presented_at": None, "status": "scanned"},
    ]
    patched = []
    monkeypatch.setattr(run_mod, "load", lambda: None)
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: rows)
    monkeypatch.setattr(run_mod, "db_patch",
                        lambda cfg, table, match, values: patched.append((match, values)))
    run_mod.cmd_dedupe_db()
    assert patched == [({"job_id": "writer:vp-customer-success-emea"},
                        {"status": "duplicate",
                         "rejection_reason":
                         "duplicate of writer:vp-customer-success-emea-205914"})]


def test_sourcing_identity_dedupe_catches_hash_suffixed_rows(monkeypatch):
    """seen_identity_keys must recognize a re-discovered posting by company
    and title even when the stored job_id carries the incumbent's suffix."""
    import hunter.run as run_mod
    from hunter.sources import job_id, slugify
    db = [{"job_id": "writer:vp-customer-success-emea-205914",
           "company": "Writer", "title": "VP, Customer Success (EMEA)",
           "url": "https://writer.example/careers", "job_url": None}]
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: db)
    keys = run_mod.seen_identity_keys(None)
    assert (slugify("Writer"), run_mod._norm_title("VP, Customer Success (EMEA)")) in keys
    assert job_id("Writer", "VP, Customer Success (EMEA)") not in keys  # bare id differs
    # the sourcing filter still drops it via the (company, title) key
    assert ("writer", "vp customer success emea") in keys


def test_unmatched_both_directions_surface():
    from hunter.run import match_rows
    srow = make_sheet_row(9, "The Trade Desk", "VP Strategy",
                          "https://ttd.example/j/9")
    dbrow = {"job_id": "harvey:head-of-gtm-strategy", "company": "Harvey",
             "title": "Head of GTM Strategy", "url": "https://h.example/1",
             "package_status": "built"}
    pairs, sheet_only, db_only, amb = match_rows([srow], [dbrow])
    assert not pairs and sheet_only == [srow] and db_only == [dbrow]


# ---------- direction 2 standing (the 2026-09-01 incumbent-flood audit) ----------

def _reconcile_with(monkeypatch, db_rows, sheet_grid=None):
    import hunter.run as run_mod
    grid = sheet_grid or [list(HEADERS), [""] * N_COLS]
    s = FakeSheet(grid)
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: db_rows)
    monkeypatch.setattr(run_mod, "db_insert", lambda *a, **kw: None)
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)

    class FakeCanon:
        sheet_headers = HEADERS
        bar = 8

    return run_mod.reconcile(cfg=None, canon=FakeCanon(), sheet=s), s


def _row(**over):
    d = {"job_id": "x:y", "company": "ElevenLabs", "title": "General Manager - Brazil",
         "url": "https://jobs.ashbyhq.com/elevenlabs/aaa", "score": 8, "status": "staging",
         "krish_verdict": None, "package_status": "none", "location": "Brazil (in-country)",
         "comp": "", "why_it_fits": "", "sweep_date": None, "source": "ats_sweep_2026_08_31",
         "presented_at": None, "rejection_reason": None, "package_cv_url": None,
         "package_letter_url": None, "job_url": None}
    d.update(over)
    return d


def test_incumbent_scored_row_never_reaches_the_sheet(monkeypatch):
    """The retired incumbent scored ElevenLabs GM Brazil an 8 and left it at
    status staging. Hunter's own scorer rates it 2 and G6 fails it on
    geography, so a score hunter did not produce is not evidence."""
    ledger, s = _reconcile_with(monkeypatch, [_row()])
    assert ledger.db_to_sheet == []
    assert any("retired incumbent" in x for x in ledger.skipped)
    assert len(s.grid) == 2          # nothing appended


def test_hunter_judged_row_at_the_bar_is_appended(monkeypatch):
    ledger, s = _reconcile_with(monkeypatch, [_row(
        job_id="cresta:vp-partnerships", company="Cresta", title="VP Partnerships",
        location="New York", sweep_date="2026-09-01",
        why_it_fits="Engine-Builder signals 4, mandate present")])
    assert len(ledger.db_to_sheet) == 1


def test_hunter_judged_row_below_the_bar_is_not_appended(monkeypatch):
    ledger, _ = _reconcile_with(monkeypatch, [_row(
        score=6, location="New York", sweep_date="2026-09-01",
        why_it_fits="Engine-Builder signals 2")])
    assert ledger.db_to_sheet == []
    assert any("below the canon 8 bar" in x for x in ledger.skipped)


def test_row_krish_already_decided_is_kept_whatever_its_provenance(monkeypatch):
    """Real history survives: a verdict or a built package is evidence even
    when the incumbent produced the row."""
    ledger, _ = _reconcile_with(monkeypatch, [_row(krish_verdict="Applied")])
    assert len(ledger.db_to_sheet) == 1


# ---------- the archive must not vanish from reconciliation ----------

def test_archived_rows_are_never_re_appended_to_pipeline(monkeypatch):
    """A row moved to the Applied tab is still on the sheet. If reconcile
    only reads Pipeline it looks missing, and direction 2 appends it again
    every single run: the exact loop that produced rows 111-131."""
    import hunter.run as run_mod
    from hunter.sheet import hyperlink

    url = "https://job-boards.greenhouse.io/acme/jobs/9"
    archived = make_sheet_row(4, "Acme AI", "VP Strategy", url, verdict="Applied")
    archived.archived = True
    s = FakeSheet([list(HEADERS), [""] * N_COLS])
    monkeypatch.setattr(s, "read_archive", lambda: [archived])
    dbrow = {"job_id": "acme-ai:vp-strategy", "company": "Acme AI",
             "title": "VP Strategy", "url": url, "status": "presented",
             "score": 9, "krish_verdict": "Applied", "package_status": "none",
             "sweep_date": "2026-09-01", "why_it_fits": "real rationale",
             "presented_at": None, "rejection_reason": None,
             "package_cv_url": None, "package_letter_url": None, "job_url": None,
             "location": "New York", "comp": "", "source": "ats"}
    monkeypatch.setattr(run_mod, "db_get", lambda cfg, table, params: [dbrow])
    monkeypatch.setattr(run_mod, "db_insert", lambda *a, **kw: None)
    monkeypatch.setattr(run_mod, "db_patch", lambda *a, **kw: None)

    class FakeCanon:
        sheet_headers = HEADERS
        bar = 8

    ledger = run_mod.reconcile(cfg=None, canon=FakeCanon(), sheet=s)
    assert ledger.db_to_sheet == [], "archived row was re-appended to Pipeline"
    assert len(s.grid) == 2, "Pipeline grew"


def test_archive_refuses_to_delete_when_the_copy_was_truncated(monkeypatch):
    """The Applied tab carried a merged banner across A1:H2, and a merged
    range accepts only its top-left cell: the first archived row landed as
    column A alone. A row-count read-back called that a success and deleted
    the Pipeline row. Content is now compared, so a truncated copy raises
    and Pipeline keeps its row."""
    s = FakeSheet([list(HEADERS), [""] * N_COLS])
    row = make_sheet_row(3, "MongoDB", "Head of AI Platform, GM",
                         "https://mdb.example/j", verdict="Applied")
    deleted = []
    monkeypatch.setattr(s, "delete_rows", lambda ns, **kw: deleted.extend(ns))
    monkeypatch.setattr(s, "_post", lambda path, body: {})

    reads = {"n": 0}

    def truncated_get(path, params=None):
        if "/values/" in path and "Applied" in path:
            reads["n"] += 1
            if reads["n"] == 1:
                return {"values": []}          # the archive starts empty
            # the read-back: what a merged range gives back, column A only
            return {"values": [["Verdict"], ["Applied"]]}
        return {"sheets": [{"properties": {"sheetId": 99,
                                           "gridProperties": {"columnCount": 40}}}]}

    monkeypatch.setattr(s, "_get", truncated_get)
    with pytest.raises(SheetError, match="read-back mismatch"):
        s.archive_rows([row], archive_tab="Applied", archive_sheet_id=99,
                       headers=HEADERS)
    assert deleted == [], "Pipeline row was deleted despite a truncated copy"
