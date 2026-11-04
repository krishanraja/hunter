"""Employer replies, classified and matched to applications. Made-up companies only."""
import datetime as dt

from hunter.apply import outcomes as o

UTC = dt.timezone.utc


def msg(subject, snippet="", sender="jobs@zentrox.example", date="Mon, 03 Nov 2026 10:00:00 +0000"):
    return {"message_id": subject[:8], "subject": subject, "snippet": snippet,
            "sender": sender, "date": date}


def row(jid, company="Zentrox", title="VP Partnerships", applied="2026-10-20T10:00:00+00:00",
        state="Applied", **kw):
    return {"job_id": jid, "company": company, "title": title, "applied_at": applied,
            "application_state": state, **kw}


def test_a_rejection_is_a_rejection_even_when_it_mentions_an_interview():
    assert o.classify("Your application to Zentrox",
                      "Unfortunately we will not schedule an interview or a call at this time", "") == o.REJECTED


def test_an_invitation_is_a_next_step():
    assert o.classify("Zentrox: next steps", "Could you share your availability for a call?",
                      "") == o.NEXT_STEP


def test_a_receipt_is_a_receipt_and_vague_mail_is_nothing():
    assert o.classify("Thanks for applying to Zentrox", "", "") == o.RECEIPT
    assert o.classify("Zentrox newsletter", "Our product update this month", "") is None


def test_hunters_own_mail_is_never_an_employer_reply():
    assert o.classify("[hunter #12] Apply: Zentrox", "unfortunately", "") is None


def test_a_reply_is_matched_only_after_he_applied():
    early = msg("Zentrox update", "we regret to inform you", date="Mon, 13 Oct 2026 10:00:00 +0000")
    assert o.match([early], [row("a")]) == []
    late = msg("Zentrox update", "we regret to inform you")
    assert [(r["job_id"], k) for _, r, k in o.match([late], [row("a")])] == [("a", o.REJECTED)]


def test_two_roles_at_one_company_need_the_reply_to_name_the_role():
    rows = [row("a", title="VP Partnerships"), row("b", title="Head of Strategy")]
    vague = msg("Zentrox", "unfortunately we have decided to pursue other candidates")
    assert o.match([vague], rows) == []
    named = msg("Zentrox: Head of Strategy", "unfortunately we have decided not to proceed")
    assert [r["job_id"] for _, r, _ in o.match([named], rows)] == ["b"]


def test_another_companys_reply_matches_nothing():
    assert o.match([msg("Brightwell", "unfortunately", sender="no-reply@brightwell.example")],
                   [row("a")]) == []


def test_the_later_word_wins_and_a_receipt_never_beats_a_decision():
    r = row("a")
    pairs = [(msg("Thanks for applying to Zentrox"), r, o.RECEIPT),
             (msg("Zentrox", "your availability", date="Tue, 28 Oct 2026 10:00:00 +0000"), r, o.NEXT_STEP),
             (msg("Zentrox", "unfortunately", date="Mon, 03 Nov 2026 10:00:00 +0000"), r, o.REJECTED)]
    assert o.outcome_by_job(pairs) == {"a": o.REJECTED}
    assert o.outcome_by_job(pairs[:2]) == {"a": o.NEXT_STEP}


def test_an_offer_or_withdrawal_he_recorded_is_never_overwritten():
    assert o.may_move("Applied", o.REJECTED) and o.may_move("Applied", o.NEXT_STEP)
    assert o.may_move("Interviewing", o.REJECTED)
    assert not o.may_move("Interviewing", o.NEXT_STEP)
    assert not o.may_move("Offer", o.REJECTED) and not o.may_move("Withdrew", o.NEXT_STEP)
    assert not o.may_move("Applied", o.RECEIPT)


def test_silence_is_measured_from_the_day_he_applied():
    now = dt.datetime(2026, 11, 4, tzinfo=UTC)
    assert o.result_of(row("a", applied="2026-10-01T00:00:00+00:00"), None, now) == "silent"
    assert o.result_of(row("a", applied="2026-10-30T00:00:00+00:00"), None, now) == "too early"
    assert o.result_of(row("a"), o.NEXT_STEP, now) == "next step"


def test_the_report_carries_counts_and_never_a_company():
    now = dt.datetime(2026, 11, 4, tzinfo=UTC)
    rows = [row("a", presented_at="2026-10-19T00:00:00+00:00", package_style="v5"),
            row("b", company="Brightwell", presented_at="2026-10-01T00:00:00+00:00")]
    text = "\n".join(o.report(rows, {"a": o.REJECTED}, now))
    assert "Zentrox" not in text and "Brightwell" not in text
    assert "all: 2 applied, 0 next step, 1 rejected" in text
    assert "0-3 days" in text and "11+ days" in text
