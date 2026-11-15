"""A posting he sends as a link becomes a stage-sent item (sentlink.py), so
the scheduled job can stage it. His message 2026-11-15 was a LinkedIn link."""
from hunter import run, sentlink
from hunter.sources import RolePosting

PAGE = """
<h2 class="top-card-layout__title topcard__title">GTM Narrative Lead</h2>
<a class="topcard__org-name-link topcard__flavor--black-link" href="x">
  Acme AI </a>
<span class="topcard__flavor topcard__flavor--bullet">New York, NY</span>
<div class="show-more-less-html__markup">About the role<br>Own the story.</div>
"""

GH = RolePosting(company="acme", title="GTM Narrative Lead",
                 url="https://job-boards.greenhouse.io/acme/jobs/55", source="greenhouse:acme",
                 location="New York City, NY", ats="greenhouse", ats_slug="acme",
                 ats_posting_id="55", raw={"first_published": "2026-11-13T16:52:16-05:00",
                                           "updated_at": "2026-11-14T00:00:00-05:00"})


def readers(**over):
    r = {"board_key": run.board_key, "boards": {"greenhouse": lambda slug: [GH]},
         "board_name": lambda ats, slug: "Acme AI", "get": lambda url: PAGE,
         "find_board": lambda name: ("greenhouse", "acme"),
         "linkedin_open": lambda url: True}
    r.update(over)
    return r


def test_a_board_link_is_read_off_its_board():
    item, why = sentlink.item_for("https://job-boards.greenhouse.io/acme/jobs/55", **readers())
    assert why == ""
    assert item["company"] == "Acme AI" and item["title"] == "GTM Narrative Lead"
    assert (item["ats"], item["slug"], item["posting_id"]) == ("greenhouse", "acme", "55")
    assert item["posted_at"] == "2026-11-13"


def test_an_updated_date_is_never_a_posting_date():
    bare = RolePosting(**{**GH.__dict__, "raw": {"updated_at": "2026-11-14"}})
    item, _ = sentlink.item_for("https://job-boards.greenhouse.io/acme/jobs/55",
                                **readers(boards={"greenhouse": lambda s: [bare]}))
    assert item["posted_at"] == ""


def test_a_board_link_not_on_its_board_is_refused():
    item, why = sentlink.item_for("https://job-boards.greenhouse.io/acme/jobs/99", **readers())
    assert item is None and "not on its board" in why


def test_a_linkedin_link_goes_to_the_employers_own_posting():
    item, why = sentlink.item_for("https://www.linkedin.com/jobs/view/4489825651/", **readers())
    assert why == ""
    assert item["url"] == GH.url and item["ats"] == "greenhouse" and item["posting_id"] == "55"
    assert item["company"] == "Acme AI"


def test_a_linkedin_link_with_no_board_uses_linkedin_checked_only_when_open():
    item, _ = sentlink.item_for("https://www.linkedin.com/jobs/view/4489825651/",
                                **readers(find_board=lambda name: None))
    assert "ats" not in item and item["open_read"] is True
    assert "Own the story" in item["jd_text"]
    item, _ = sentlink.item_for("https://www.linkedin.com/jobs/view/4489825651/",
                                **readers(find_board=lambda name: None,
                                          linkedin_open=lambda url: None))
    assert "open_read" not in item


def test_a_title_the_board_does_not_hold_is_not_matched_to_another_role():
    other = RolePosting(**{**GH.__dict__, "title": "Narrative Designer"})
    item, _ = sentlink.item_for("https://www.linkedin.com/jobs/view/4489825651/",
                                **readers(boards={"greenhouse": lambda s: [other]}))
    assert "ats" not in item and item["url"].startswith("https://www.linkedin.com/jobs/view/")


def test_search_page_links_and_unreadable_links():
    assert sentlink.linkedin_number(
        "https://www.linkedin.com/jobs/search/?currentJobId=4489825651&q=x") == "4489825651"
    assert sentlink.linkedin_number(
        "https://www.linkedin.com/jobs/view/gtm-narrative-lead-at-acme-4489825651") == "4489825651"
    item, why = sentlink.item_for("https://example.com/careers/1", **readers())
    assert item is None and "not a link hunter can read" in why
    item, why = sentlink.item_for("https://www.linkedin.com/jobs/view/4489825651/",
                                  **readers(get=lambda url: ""))
    assert item is None and "did not answer" in why


def test_the_company_he_names_wins():
    item, _ = sentlink.item_for("https://job-boards.greenhouse.io/acme/jobs/55",
                                company="Acme", **readers())
    assert item["company"] == "Acme"
