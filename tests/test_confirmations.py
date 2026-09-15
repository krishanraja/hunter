"""Reading the employer's own receipt, and refusing to read anything else.

Krish presses Submit himself, so hunter cannot see the click, and asking him to
run one more command afterwards is a step he will forget. The receipt closes the
loop, and it is better evidence than anything hunter could observe from its own
side. The risk is the opposite one: recording an application on the strength of
a rejection letter or a newsletter.
"""
from __future__ import annotations

import pytest

from hunter.apply import confirmations as C


def msg(subject, snippet="", sender="careers@harvey.ai"):
    return {"message_id": "m1", "subject": subject, "snippet": snippet,
            "sender": sender, "date": ""}


def row(company="Harvey", token="t1", state="approved"):
    return {"token": token, "company": company, "role": "Head of GTM",
            "state": state, "job_id": "harvey:x", "submitted_at": None}


# ---------- what counts as a receipt ----------

@pytest.mark.parametrize("subject", [
    "Thank you for applying to Harvey",
    "Thanks for applying!",
    "We received your application",
    "Your application has been received",
    "Application received: Head of GTM",
])
def test_an_acknowledgement_is_recognised(subject):
    assert C.looks_like_ack(subject, "", "careers@harvey.ai")


@pytest.mark.parametrize("subject,snippet", [
    ("Your application to Harvey",
     "Unfortunately we have decided not to move forward"),
    ("An update on your application",
     "we will not be progressing your application"),
    ("Application update", "you were unsuccessful on this occasion"),
])
def test_a_rejection_is_not_a_receipt(subject, snippet):
    """However friendly the wording. A rejection is evidence the application
    arrived, but recording it as a fresh receipt would close a loop that a later
    genuine receipt should have closed, and reads as good news in his inbox."""
    assert not C.looks_like_ack(subject, snippet, "careers@harvey.ai")


def test_hunters_own_mail_is_never_a_receipt():
    """hunter sends from Krish's account to the same mailbox, so its own
    "Submitted:" receipt comes back looking like employer mail."""
    assert not C.looks_like_ack(
        "Apply: Harvey Head of GTM [hunter #harvey:x-1234]", "", "krish@x.com")
    assert not C.looks_like_ack(
        "Submitted: Harvey Head of GTM", "[hunter-outbound] Submitted", "krish@x.com")


# ---------- which application it answers ----------

def test_the_receipt_is_matched_to_the_company():
    pairs = C.match([msg("Thanks for applying to Harvey")], [row()])
    assert len(pairs) == 1 and pairs[0][1]["company"] == "Harvey"


def test_a_receipt_from_someone_else_matches_nothing():
    pairs = C.match([msg("Thanks for applying", sender="jobs@elevenlabs.io")],
                    [row("Harvey")])
    assert pairs == []


def test_one_receipt_closes_one_application():
    """Two roles at the same company must not both be closed by a single
    receipt for one of them."""
    rows = [row("Harvey", "t1"), row("Harvey", "t2")]
    pairs = C.match([msg("Thanks for applying to Harvey")], rows)
    assert len(pairs) == 1


def test_a_generic_word_never_identifies_a_company():
    """"Inc" and "AI" are in half these names; matching on them would close every
    open application on the first receipt that arrived."""
    assert C.company_tokens("Acme Technologies Inc") == {"acme"}
    assert C.company_tokens("AI Labs Ltd") == set()
    pairs = C.match([msg("Thanks for applying", sender="jobs@somewhere.com")],
                    [row("AI Labs Ltd")])
    assert pairs == []


def test_the_sender_domain_can_carry_the_match():
    """Ashby sends on the employer's behalf and the subject may not name them."""
    pairs = C.match([msg("Thanks for applying", sender="no-reply@harvey.ashbyhq.com")],
                    [row("Harvey")])
    assert len(pairs) == 1
