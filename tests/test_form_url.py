"""The form is read from the link a board can serve.

2026-10-20: Actively, iFIT and Nebius reached him as apply by hand. Their
record held the Greenhouse board link; the build had replaced the link it read
the form from with the company's careers page (absolute_url, ?gh_jid=), which
no adapter reads. His words: "they were just one click away".
"""
import hunter.run as R

BOARD = "https://job-boards.greenhouse.io/ifit/jobs/4335626009"
CAREERS = "http://www.ifit.com/careers?gh_jid=4335626009"


def test_the_board_link_wins_over_the_careers_page():
    assert R.form_url(CAREERS, None, BOARD, BOARD) == BOARD
    assert R.form_url(CAREERS, BOARD, CAREERS, "") == BOARD


def test_with_no_board_link_the_page_shown_is_read():
    li = "https://www.linkedin.com/jobs/view/4446699868"
    assert R.form_url(li, None, li, "") == li
    assert R.form_url("", None, "", "") == ""


def test_build_fill_plan_reads_the_board_link(monkeypatch):
    from hunter.apply import fetch
    seen = []
    role = R.ResolvedRole(company="iFIT", title="Director", url=BOARD, jd_url=CAREERS,
                          jd_text="x", live=True, source="", location="Remote", comp="")
    monkeypatch.setattr(R, "resolve_for_build", lambda cfg, row, cache: (role, None, []))
    monkeypatch.setattr(R, "_answer_bank", lambda sheet: None)

    class Stop(Exception):
        pass

    def form_for(url, key):
        seen.append(url)
        raise Stop
    monkeypatch.setattr(fetch, "form_for", form_for)
    from hunter.ats import discover as disc
    monkeypatch.setattr(disc, "load_cache", lambda cfg: {})
    try:
        R.build_fill_plan(None, None, {"url": BOARD, "job_url": BOARD}, bank=object())
    except Stop:
        pass
    assert seen == [BOARD]


def test_an_offsite_linkedin_posting_is_never_called_easy_apply(monkeypatch):
    from hunter.ats import linkedin
    from hunter.apply import fetch
    offsite = '<a data-tracking-control-name="public_jobs_apply-link-offsite_contextual-sign-in-modal">'
    easy = '<button class="apply-link-onsite">Easy Apply</button>'
    assert linkedin.apply_mode_of(offsite) == linkedin.OFFSITE
    assert linkedin.apply_mode_of(easy) == linkedin.EASY
    assert linkedin.apply_mode_of("<html></html>") is None
    url = "https://www.linkedin.com/jobs/view/head-of-sales-at-x-4472562283?position=39"
    for mode, words in ((linkedin.OFFSITE, "company's own site"), (linkedin.EASY, "Easy Apply"),
                        (None, "could not tell")):
        monkeypatch.setattr(linkedin, "apply_mode", lambda u, m=mode: m)
        spec = fetch.form_for(url, R.board_key)
        assert not spec.readable and words in spec.note, (mode, spec.note)
