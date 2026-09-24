"""The Lever driver and the apply-by-hand delivery.

Reading a Lever form is only half of it: without a driver, payload.build raises
"no driver for this ats: 'lever'" and the extension cannot be armed, which is
exactly what the 2026-09-24 run printed for Versapay and Rembrand.
"""
from __future__ import annotations

import pathlib

from hunter.apply.fill import FillPlan
from hunter.apply.payload import css_only
from hunter.apply.submit import DRIVERS, LeverDriver, driver_for

FIX = pathlib.Path(__file__).parent / "fixtures"


class F:
    def __init__(self, key):
        self.key = key


def plan():
    return FillPlan(ats="lever", slug="rembrand", posting_id="pid",
                    company="Rembrand", role="Head")


def drv():
    return LeverDriver(None, plan())


def test_lever_has_a_driver_at_all():
    assert "lever" in DRIVERS
    assert driver_for(plan()) is LeverDriver


def test_the_apply_url_is_the_form_not_the_posting():
    """The posting page has no inputs on it."""
    assert drv().apply_url() == ("https://jobs.lever.co/rembrand/pid/apply")


def test_bracketed_names_are_addressed_by_attribute_not_by_id():
    """Lever gives these inputs no id matching their name, and a bracketed id
    selector is invalid CSS anyway."""
    for key in ("urls[LinkedIn]", "cards[24bae3c0-543c][field0]",
                "surveysResponses[95081cfc][responses][field2]"):
        for sel in (drv().text_selectors(F(key))
                    + drv().choice_selectors(F(key))):
            assert f'[name="{key}"]' in sel
            assert not sel.startswith("#")


def test_every_selector_survives_the_extension_filter():
    """payload.css_only strips Playwright-only pseudo-classes. A selector
    dropped there reaches his browser as a field that silently stays empty."""
    sels = (drv().text_selectors(F("urls[LinkedIn]"))
            + drv().choice_selectors(F("cards[abc][field0]"))
            + drv().file_selectors(F("resume")))
    assert css_only(sels) == sels


def test_the_selectors_match_the_real_saved_form():
    """Every selector the driver would use is checked against the actual page,
    so a rename on Lever's side fails here rather than in his browser."""
    import re
    from hunter.apply import lever_form as L
    raw = (FIX / "lever_versapay_apply.html").read_text()
    spec = L.parse(raw, slug="versapay", posting_id="pid")
    d = drv()
    names = set(re.findall(r'name="([^"]*)"', raw))
    for f in spec.fields:
        assert f.key in names, f"{f.key} is not on the page"
        sels = (d.choice_selectors(f) if f.options else d.text_selectors(f))
        assert any(f'[name="{f.key}"]' in s for s in sels), f.key


def test_the_file_slot_is_named_before_it_is_guessed():
    """Lever gives the application one file input, called resume. Naming it
    first keeps a future second slot from taking the CV, the same defect
    GreenhouseDriver.file_selectors exists to prevent."""
    sels = drv().file_selectors(F("resume"))
    assert sels[0] == 'input[type="file"][name="resume"]'
    assert sels[-1] == 'input[type="file"]'


def test_the_lever_driver_is_never_pressed_by_the_preview_path():
    """Canon 9: the last click is his.

    Read off the bytecode, not the source text, the way test_submit.py already
    does it. A first version of this grepped inspect.getsource and failed on
    the word appearing in preview's own DOCSTRING, which is a test measuring
    prose rather than behaviour.
    """
    from hunter.apply import submit as S
    assert "press_submit" not in set(S.preview.__code__.co_names)
    assert "press_submit" not in set(S._open_and_fill.__code__.co_names)
    # and the driver does carry one, for the path Krish drives himself
    assert hasattr(LeverDriver, "press_submit")


# ---------- the apply-by-hand delivery ----------

def test_the_documents_are_sent_when_the_form_cannot_be_filled(monkeypatch):
    """Krish, about BOI: "BOI I can apply myself (still give me the application
    via email)". Before this the run refused and sent nothing, so a package
    that had been written, gated and built never reached him."""
    import hunter.run as R
    sent = {}

    import hunter.notify as N
    monkeypatch.setattr(N, "send_email",
                        lambda cfg, subj, html, **kw: sent.update(
                            subject=subj, html=html, kw=kw))
    monkeypatch.setattr(N, "mailbox", lambda cfg: "krish@example.com")
    R.send_apply_by_hand(
        None, company="BOI (Board of Innovation)",
        role="AI Transformation Director",
        jd_url="https://www.linkedin.com/jobs/view/4470969603",
        cv_url="http://cv", letter_url="http://letter",
        essays={"Why BOI?": "Because."},
        why="LinkedIn Easy Apply: the application is completed inside LinkedIn")
    assert "Apply by hand" in sent["subject"]
    assert "BOI (Board of Innovation)" in sent["subject"]
    body = sent["html"]
    assert "http://cv" in body and "http://letter" in body
    assert "Why BOI?" in body and "Because." in body
    assert "cannot fill this form" in body


def test_the_delivery_never_claims_the_form_was_filled(monkeypatch):
    """The exact shape of the "Filled all 17 fields" banner over an untouched
    section."""
    import hunter.run as R
    import hunter.notify as N
    sent = {}
    monkeypatch.setattr(N, "send_email",
                        lambda cfg, subj, html, **kw: sent.update(html=html))
    monkeypatch.setattr(N, "mailbox", lambda cfg: "k@e.com")
    R.send_apply_by_hand(None, company="C", role="R", jd_url="u", cv_url="",
                         letter_url="", essays={}, why="no adapter")
    low = sent["html"].lower()
    assert "filled in" not in low
    assert "open the filled form" not in low
    assert "nothing was submitted" in low


def test_the_delivery_mints_no_token(monkeypatch):
    """Replying APPROVE to it would move a token into a submit path with no
    form to drive, and it would wait there for a browser that never opens."""
    import inspect
    import hunter.run as R
    src = inspect.getsource(R.send_apply_by_hand)
    assert "new_token" not in src and "record_sent" not in src
