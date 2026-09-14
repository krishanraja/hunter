"""The approval gate. A bug here sends an application Krish never agreed to, so
these tests are deliberately paranoid about the ambiguous cases.

The asymmetry under test: misreading approval as feedback costs a round trip;
misreading feedback as approval is unrecoverable.
"""
from __future__ import annotations

import pytest

from hunter.apply import approval, inbox
from hunter.apply.approval import (APPROVE_WORD, AWAITING, FieldLine, plan_hash,
                                   render, subject_for, token_from_subject)

LINES = [
    FieldLine("Legal Name", "Krish Raja", True, "Info Bank: Full legal name"),
    FieldLine("Email", "hello@krishraja.com", True, "Info Bank: Email"),
    FieldLine("Are you authorized to work in the United States?", "Yes", True,
              "Info Bank: Authorized to work in US?"),
    FieldLine("What are your salary expectations?", "250000", True,
              "the recorded comp floor", flagged=True),
    FieldLine("Race / ethnicity", "Two or more races", False,
              "Info Bank: Race / ethnicity", flagged=True),
    FieldLine("Do you live within 45 miles of a talent hub?", "", True,
              "no stored answer matches", unresolved=True),
]


def build(**kw):
    base = dict(company="Harvey", role="Head of GTM Strategy",
                jd_url="https://jobs.ashbyhq.com/harvey/abc",
                autonomy="Full", token="harvey-cos-1a2b3c4d",
                to="hello@krishraja.com", lines=LINES)
    base.update(kw)
    return render(**base)


# ---------------- the email carries the whole application ----------------

def test_every_field_and_value_appears_in_the_body():
    email = build()
    for line in LINES:
        assert line.label in email.html
        if line.value:
            assert line.value in email.html
    assert "Krish Raja" in email.text and "250000" in email.text


def test_provenance_travels_with_every_value():
    """Krish has to be able to see WHY an answer says what it says."""
    email = build()
    assert "Info Bank: Authorized to work in US?" in email.html
    assert "the recorded comp floor" in email.html


def test_the_flagged_and_unresolved_items_lead_the_email():
    email = build()
    head = email.html[:email.html.index("Approve and submit")]
    assert "Read before approving" in head
    assert "250000" in head, "the salary number must be seen before approving"
    assert "Two or more races" in head
    assert "45 miles" in head


def test_an_unanswered_required_field_is_called_out_not_silently_blank():
    email = build()
    assert "no answer" in email.html
    assert "no answer" in email.text


def test_generated_prose_is_reviewable_in_the_body():
    email = build(summary="Sixteen years commercializing data and tech.",
                  hook="Harvey is scaling legal AI into the enterprise.")
    assert "Written for this role" in email.html
    assert "Sixteen years commercializing" in email.html
    assert "Harvey is scaling legal AI" in email.text


def test_essays_appear_in_full():
    email = build(essays={"Why Harvey?": "Because the bottleneck is distribution."})
    assert "Why Harvey?" in email.html
    assert "Because the bottleneck is distribution." in email.text


# ---------------- documents: two links, or one attachment ----------------

def test_two_links_when_the_form_takes_two_uploads():
    email = build(cv_url="https://docs.google.com/cv",
                  letter_url="https://docs.google.com/letter")
    assert "https://docs.google.com/cv" in email.html
    assert "https://docs.google.com/letter" in email.html
    assert "one upload" not in email.html


def test_one_attachment_named_when_the_form_takes_one_upload():
    email = build(merged_attachment="KrishRaja_Application_Harvey.pdf")
    assert "KrishRaja_Application_Harvey.pdf" in email.html
    assert "one upload" in email.html


# ---------------- the buttons ----------------

def test_both_buttons_are_mailto_links_carrying_the_token():
    email = build()
    assert email.html.count("mailto:hello@krishraja.com") == 2
    assert "harvey-cos-1a2b3c4d" in email.html


def test_the_approve_button_prefills_exactly_the_word_that_works():
    email = build()
    approve_href = email.html.split('href="mailto:')[1].split('"')[0]
    assert f"body={APPROVE_WORD}" in approve_href


def test_the_email_states_the_exact_match_rule():
    """He should not have to guess that "sure, go ahead" will not work."""
    email = build()
    assert APPROVE_WORD in email.html
    assert "own line" in email.html


# ---------------- the subject token ----------------

def test_the_token_survives_a_reply_prefix():
    subject = subject_for("Harvey", "Head of GTM", "harvey-1a2b")
    for prefix in ("", "Re: ", "RE: ", "Fwd: Re: ", "Re:Re: "):
        assert token_from_subject(prefix + subject) == "harvey-1a2b"


def test_a_subject_with_no_token_yields_nothing():
    assert token_from_subject("Re: lunch tomorrow") == ""
    assert token_from_subject("") == ""


def test_tokens_are_not_guessable_from_the_job_id():
    a = approval.new_token("harvey:head-of-gtm")
    b = approval.new_token("harvey:head-of-gtm")
    assert a != b


# ---------------- the plan hash ----------------

def test_key_order_does_not_change_the_hash():
    assert plan_hash({"a": 1, "b": 2}) == plan_hash({"b": 2, "a": 1})


def test_changing_any_answer_changes_the_hash():
    """This is what stops an approved application being silently altered."""
    base = {"first_name": "Krish", "salary": "250000"}
    assert plan_hash(base) != plan_hash({**base, "salary": "200000"})
    assert plan_hash(base) != plan_hash({**base, "extra": "x"})


# ---------------- reading the reply: the paranoid half ----------------

@pytest.mark.parametrize("body", [
    "APPROVE",
    "approve",
    "Approve",
    "  APPROVE  ",
    "APPROVE.",
    "APPROVE\n\nOn Sun, hunter wrote:\n> the whole previous email",
    "APPROVE\nlooks good",
])
def test_these_approve(body):
    decision, _ = inbox.read_instruction(body)
    assert decision == "approve", body


@pytest.mark.parametrize("body", [
    "Approved!",
    "approve please",
    "yes go ahead",
    "sure, APPROVE it",
    "I approve",
    "Please APPROVE this one",
    "looks good to me",
    "ok",
])
def test_these_do_not_approve(body):
    """Every one of these is a human plausibly meaning yes. None of them may
    submit an application, because the cost of being wrong is asymmetric."""
    decision, _ = inbox.read_instruction(body)
    assert decision != "approve", f"{body!r} must not approve"


def test_the_quoted_original_cannot_approve_on_its_own():
    """The email we send contains the word APPROVE in its instructions. A naive
    substring search over a reply would approve every single time."""
    quoted = ("Change the hook please.\n\n"
              "On Sun, 14 Sep 2026, hunter wrote:\n"
              "> Reply APPROVE on its own line to submit.\n"
              "> APPROVE\n")
    decision, feedback = inbox.read_instruction(quoted)
    assert decision == "amend"
    assert "Change the hook please." in feedback
    assert "APPROVE" not in feedback


def test_a_phone_signature_does_not_become_feedback():
    decision, feedback = inbox.read_instruction(
        "APPROVE\n\nSent from my iPhone")
    assert decision == "approve"
    assert "iPhone" not in feedback


def test_feedback_comes_through_verbatim():
    body = ("Make the hook about their pricing problem, not distribution.\n"
            "And cut the Microsoft bullet.\n\n"
            "On Sun, hunter wrote:\n> quoted stuff\n")
    decision, feedback = inbox.read_instruction(body)
    assert decision == "amend"
    assert feedback == ("Make the hook about their pricing problem, not "
                        "distribution.\nAnd cut the Microsoft bullet.")


def test_an_empty_reply_is_unclear_not_approval_and_not_feedback():
    for body in ("", "   ", "\n\n\n", "> only quoted text\n"):
        decision, feedback = inbox.read_instruction(body)
        assert decision == "unclear", body


def test_the_unfilled_amend_template_is_unclear():
    """He pressed Amend, then sent it without writing anything."""
    body = ("Change the following, then resend for approval:\n\n-\n\n"
            "(Write freely. Your words are passed through verbatim.)")
    decision, _ = inbox.read_instruction(body)
    assert decision == "unclear"


# ---------------- classify: state and sender ----------------

def row(**kw):
    base = {"token": "t", "state": AWAITING, "processed_message_ids": []}
    base.update(kw)
    return base


def reply(**kw):
    base = {"message_id": "m1", "token": "t", "body": "APPROVE",
            "from": "Krish Raja <hello@krishraja.com>"}
    base.update(kw)
    return base


def test_an_approval_from_krish_is_accepted():
    action, _ = inbox.classify(reply(), row())
    assert action == "approve"


def test_an_approval_from_anyone_else_is_rejected():
    """A token in a forwarded email must not let a third party submit."""
    action, detail = inbox.classify(
        reply(**{"from": "recruiter@openai.com"}), row())
    assert action == "reject" and "not from Krish" in detail


def test_a_duplicate_message_is_a_no_op():
    action, _ = inbox.classify(reply(), row(processed_message_ids=["m1"]))
    assert action == "skip"


def test_a_reply_on_an_already_submitted_token_is_skipped():
    action, detail = inbox.classify(reply(), row(state=approval.SUBMITTED))
    assert action == "skip" and "submitted" in detail


def test_a_reply_on_a_superseded_token_is_skipped():
    """An amend kills the old token, so a late APPROVE on it must not land."""
    action, _ = inbox.classify(reply(), row(state=approval.CANCELLED))
    assert action == "skip"


def test_a_reply_too_short_to_act_on_is_reported_not_rebuilt():
    """"ok" is not an instruction. Treating it as feedback would burn a model call
    and mail him a near-identical application. It still does not approve."""
    action, detail = inbox.classify(reply(body="ok"), row())
    assert action == "reject"
    assert "APPROVE" in detail


def test_a_short_but_real_instruction_does_amend():
    action, _ = inbox.classify(
        reply(body="Cut the Microsoft bullet from the letter."), row())
    assert action == "amend"
