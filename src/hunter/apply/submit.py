"""The only module that can put an application in front of an employer.

Everything else in this layer prepares. This sends, and that asymmetry decides
its design: a submission cannot be recalled, edited or apologised for, so every
question it asks is asked before the browser opens and every answer is checked
rather than assumed.

The gate, in order, and all of it before a page loads:

  1. An approval row exists for the token and its state is APPROVED. Not
     awaiting, not amending, not a token Krish never saw.
  2. The plan hash still equals the hash of what was emailed. He approved a
     specific set of answers; if the package was rebuilt since, that approval is
     for a document that no longer exists and the run refuses.
  3. Every required field has an answer. A form half filled is worse than one
     not started, because the employer sees the half.

Then the browser fills, attaches, screenshots, and STOPS. Pressing submit needs
confirm=True passed explicitly by a caller that has already cleared the gate, so
the default of every path, CLI included, is a filled form nobody sent.

Outcomes are recorded and never silent. A captcha, a login wall or a vendor the
drivers do not know records QUEUED with the reason and leaves the application for
Krish's own browser. Nothing is retried blindly: a blind retry on a submit is how
one application becomes two.
"""
from __future__ import annotations

import datetime

from ..config import Config
from . import approval
from .audit import ATT_CV
from .fill import FillPlan

# What a driver is allowed to leave behind when it cannot finish.
QUEUE_REASONS = (
    "captcha", "login required", "no driver for this ats", "field not found",
    "upload rejected", "navigation failed", "playwright missing",
)

SCREENSHOT_DIR = "hunter-submissions"


class SubmitError(RuntimeError):
    """Raised for a refusal, never for a vendor's rejection of the form.

    A refusal is hunter declining to act. Anything the site does to us is an
    outcome, recorded on the row, not an exception.
    """


class SubmitBlocked(SubmitError):
    """The gate said no. The caller must not work around this."""


def check_gate(cfg: Config, token: str, plan: FillPlan) -> dict:
    """The approval row, or a refusal explaining exactly which rule stopped it.

    Separate from submit() so it can be tested without a browser and called by a
    dry run, and so the reason a submission was refused is one string rather than
    a control-flow path.
    """
    row = approval.get_row(cfg, token)
    if not row:
        raise SubmitBlocked(f"no approval row for token {token!r}; nothing was ever sent")
    state = row.get("state")
    if state != approval.APPROVED:
        raise SubmitBlocked(
            f"token is {state!r}, not {approval.APPROVED!r}. Only an approval reply "
            f"moves it, and only APPROVE on its own line is an approval.")
    want = row.get("plan_hash") or ""
    have = approval.plan_hash(plan.as_dict())
    if want != have:
        raise SubmitBlocked(
            f"the package changed since you approved it (approved {want[:12]}, "
            f"now {have[:12]}). Re-send for approval rather than submitting a "
            f"different application than the one you read.")
    blocking = plan.blocking
    if blocking:
        raise SubmitBlocked(
            f"{len(blocking)} required field(s) have no answer: "
            + ", ".join(f.label for f in blocking))
    return row


def record_outcome(cfg: Config, token: str, state: str, *, reason: str = "",
                   screenshot_url: str = "") -> None:
    extra: dict = {}
    if reason:
        extra["failure_reason"] = reason[:500]
    if screenshot_url:
        extra["screenshot_url"] = screenshot_url
    if state == approval.SUBMITTED:
        extra["submitted_at"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
    approval.set_state(cfg, token, state, **extra)


# ---------- drivers ----------

class Driver:
    """One ATS. Fill, attach, screenshot, and press only when told.

    Deliberately thin: the answers are decided by fill.build_payload long before
    a driver runs, so a driver's only job is to put a known value into a known
    field and to say clearly when it cannot find one.
    """

    ats = ""

    def __init__(self, page, plan: FillPlan):
        self.page = page
        self.plan = plan
        self.filled: list[str] = []
        self.missed: list[str] = []

    def apply_url(self) -> str:
        raise NotImplementedError

    def fill(self) -> None:
        raise NotImplementedError

    def press_submit(self) -> None:
        raise NotImplementedError


def _fill_one(page, selectors: list[str], value: str) -> bool:
    """First selector that resolves to exactly one visible control wins.

    Several selectors per field because a vendor's markup is not a contract: an
    id, a label association and a name attribute are three guesses at the same
    input, and guessing once then failing the whole submission is the wrong
    trade.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() != 1:
                continue
            loc.first.fill(value, timeout=5000)
            return True
        except Exception:
            continue
    return False


class AshbyDriver(Driver):
    ats = "ashby"

    def apply_url(self) -> str:
        return (f"https://jobs.ashbyhq.com/{self.plan.slug}/"
                f"{self.plan.posting_id}/application")

    def fill(self) -> None:
        for f in self.plan.fields:
            if f.kind in ("file_resume", "file_cover") or not f.value:
                continue
            label = f.label.replace('"', '\\"')
            ok = _fill_one(self.page, [
                f'input[name="{f.key}"]',
                f'textarea[name="{f.key}"]',
                f'input[aria-label="{label}"]',
                f'textarea[aria-label="{label}"]',
            ], f.value)
            (self.filled if ok else self.missed).append(f.label)

    def press_submit(self) -> None:
        self.page.get_by_role("button", name="Submit Application").click(timeout=15000)


class GreenhouseDriver(Driver):
    ats = "greenhouse"

    def apply_url(self) -> str:
        return (f"https://boards.greenhouse.io/{self.plan.slug}/jobs/"
                f"{self.plan.posting_id}#app")

    def fill(self) -> None:
        for f in self.plan.fields:
            if f.kind in ("file_resume", "file_cover") or not f.value:
                continue
            ok = _fill_one(self.page, [
                f'#{f.key}', f'input[name="{f.key}"]', f'textarea[name="{f.key}"]',
            ], f.value)
            (self.filled if ok else self.missed).append(f.label)

    def press_submit(self) -> None:
        self.page.get_by_role("button", name="Submit Application").click(timeout=15000)


DRIVERS = {d.ats: d for d in (AshbyDriver, GreenhouseDriver)}


def driver_for(plan: FillPlan):
    cls = DRIVERS.get(plan.ats)
    if cls is None:
        raise SubmitBlocked(f"no driver for this ats: {plan.ats!r}")
    return cls


# ---------- blockers the page puts in our way ----------

CAPTCHA_MARKS = ("recaptcha", "hcaptcha", "cf-turnstile", "are you human",
                 "verify you are human")
LOGIN_MARKS = ("sign in to apply", "log in to apply", "create an account to apply")


def page_blocker(page) -> str:
    """A captcha or a login wall, named, or empty when the page is workable.

    Checked before filling and again before submitting, because a captcha that
    appears after the form is filled is the common case and the one that would
    otherwise be discovered by clicking submit into it.
    """
    try:
        html = (page.content() or "").lower()
    except Exception:
        return ""
    for mark in CAPTCHA_MARKS:
        if mark in html:
            return "captcha"
    for mark in LOGIN_MARKS:
        if mark in html:
            return "login required"
    return ""


# ---------- the run ----------

def submit(cfg: Config, token: str, plan: FillPlan, *,
           attachments: dict[str, bytes] | None = None,
           confirm: bool = False,
           browser_factory=None,
           shot_sink=None) -> dict:
    """Fill one application. Press submit only when confirm is True.

    attachments maps a field kind ("file_resume", "file_cover") to PDF bytes.
    browser_factory and shot_sink are injected so the whole path is testable
    without a browser or a network: the gate, the outcome recording and the
    refusal to press are the parts that matter and none of them need Chromium.

    Returns a result dict; raises only for a refusal by the gate.
    """
    result: dict = {"token": token, "state": "", "reason": "", "pressed": False,
                    "filled": [], "missed": [], "screenshot": ""}
    check_gate(cfg, token, plan)
    cls = driver_for(plan)

    if browser_factory is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            record_outcome(cfg, token, approval.QUEUED, reason="playwright missing")
            result.update(state=approval.QUEUED, reason="playwright missing")
            return result
        browser_factory = sync_playwright

    try:
        with browser_factory() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            driver = cls(page, plan)
            page.goto(driver.apply_url(), timeout=60000)

            blocker = page_blocker(page)
            if blocker:
                shot = _shoot(page, token, shot_sink)
                record_outcome(cfg, token, approval.QUEUED, reason=blocker,
                               screenshot_url=shot)
                result.update(state=approval.QUEUED, reason=blocker, screenshot=shot)
                browser.close()
                return result

            driver.fill()
            _attach(page, plan, attachments or {}, driver)
            result["filled"], result["missed"] = driver.filled, driver.missed

            # A field we could not find is a field the employer will see empty.
            # Required and missing is a refusal to press, not a warning.
            required_missed = [f.label for f in plan.fields
                               if f.required and f.label in driver.missed]
            if required_missed:
                shot = _shoot(page, token, shot_sink)
                reason = "field not found: " + ", ".join(required_missed[:3])
                record_outcome(cfg, token, approval.QUEUED, reason=reason,
                               screenshot_url=shot)
                result.update(state=approval.QUEUED, reason=reason, screenshot=shot)
                browser.close()
                return result

            shot = _shoot(page, token, shot_sink)
            result["screenshot"] = shot

            if not confirm:
                # The default. A filled form, a picture of it, and no click.
                result.update(state="filled", reason="dry run, submit not pressed")
                browser.close()
                return result

            # Re-check: a captcha commonly appears only once the form is complete.
            blocker = page_blocker(page)
            if blocker:
                record_outcome(cfg, token, approval.QUEUED, reason=blocker,
                               screenshot_url=shot)
                result.update(state=approval.QUEUED, reason=blocker)
                browser.close()
                return result

            driver.press_submit()
            result["pressed"] = True
            record_outcome(cfg, token, approval.SUBMITTED, screenshot_url=shot)
            result.update(state=approval.SUBMITTED)
            browser.close()
            return result
    except SubmitError:
        raise
    except Exception as e:
        # Anything the site or the driver does to us is an outcome, recorded, so
        # the row never sits in approved with nothing behind it.
        reason = f"{e.__class__.__name__}: {str(e)[:200]}"
        record_outcome(cfg, token, approval.QUEUED, reason=reason)
        result.update(state=approval.QUEUED, reason=reason)
        return result


def _attach(page, plan: FillPlan, attachments: dict[str, bytes], driver) -> None:
    """Put the built PDFs on the form.

    One slot takes the merged file, per the audit's attachment_style: the same
    rule the approval email already told Krish it would follow, so what he
    approved and what the employer receives are the same document.
    """
    import tempfile, os
    wanted = [f for f in plan.fields if f.kind in ("file_resume", "file_cover")]
    for f in wanted:
        key = "file_resume" if plan.attachment_style == ATT_CV else f.kind
        blob = attachments.get(key) or attachments.get(f.kind)
        if not blob:
            driver.missed.append(f.label)
            continue
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="hunter_")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            page.locator(f'input[type="file"]').first.set_input_files(path, timeout=15000)
            driver.filled.append(f.label)
        except Exception:
            driver.missed.append(f.label)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def _shoot(page, token: str, sink) -> str:
    """A full-page picture of what hunter is about to send, or just sent.

    The one artifact that lets Krish audit a submission after the fact, so it is
    taken on every path including the refusals.
    """
    try:
        png = page.screenshot(full_page=True)
    except Exception:
        return ""
    if sink is None:
        return ""
    try:
        return sink(token, png) or ""
    except Exception:
        return ""
