"""Fresh postings build first within one score, and the Apply email says how old a posting is."""
import datetime as dt

from hunter import router
from hunter.apply import approval
from hunter.sources import posted_words

TODAY = dt.date(2026, 11, 4)


def role(jid, k, posted="", source="posted", shown="2026-10-01T00:00:00+00:00"):
    return {"job_id": jid, "krish_score": k, "build_trigger": "Krish Score",
            "posted_at": posted, "posted_source": source if posted else None,
            "presented_at": shown}


def order(rows):
    return [r["job_id"] for r in sorted(rows, key=lambda d: router.build_order(d, TODAY))]


def test_within_one_score_the_freshest_posting_builds_first():
    rows = [role("old_on_sheet", 8, shown="2026-09-01T00:00:00+00:00"),
            role("fresh_3d", 8, posted="2026-11-01"),
            role("fresh_10d", 8, posted="2026-10-25")]
    assert order(rows) == ["fresh_3d", "fresh_10d", "old_on_sheet"]


def test_his_score_still_comes_before_freshness():
    rows = [role("fresh_7", 7, posted="2026-11-03"), role("unknown_9", 9)]
    assert order(rows) == ["unknown_9", "fresh_7"]


def test_a_last_changed_date_or_an_old_posting_is_not_fresh():
    rows = [role("updated", 8, posted="2026-11-03", source="updated",
                 shown="2026-09-01T00:00:00+00:00"),
            role("posted_40d", 8, posted="2026-09-25", shown="2026-09-02T00:00:00+00:00"),
            role("fresh", 8, posted="2026-11-02", shown="2026-10-30T00:00:00+00:00")]
    assert order(rows) == ["fresh", "updated", "posted_40d"]


def test_posted_words_say_how_old_and_never_call_an_update_a_posting():
    assert posted_words("2026-11-01", "posted", "2026-11-04") == "Posted 3 days ago (2026-11-01)."
    assert "shortlist" in posted_words("2026-10-01", "posted", "2026-11-04")
    assert posted_words("2026-11-01", "updated", "2026-11-04").startswith("Posting last updated")
    assert posted_words("", "posted", "2026-11-04") == ""


def test_the_email_carries_the_age():
    em = approval.render(company="Zentrox", role="VP", jd_url="", autonomy="", token="t",
                         to="me@example.com", lines=[], posted="Posted 3 days ago (2026-11-01).")
    assert "Posted 3 days ago" in em.text and "Posted 3 days ago" in em.html
