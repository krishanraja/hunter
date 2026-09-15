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
import re
import time

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
        # What a control offered when nothing matched the stored answer. A field
        # reported missed with no explanation sends Krish back to the browser to
        # find out why; the offered options let him correct the answer bank once.
        self.notes: list[str] = []

    def apply_url(self) -> str:
        raise NotImplementedError

    def fill(self) -> None:
        raise NotImplementedError

    def press_submit(self) -> None:
        raise NotImplementedError


# A text box takes typing. A dropdown, a radio group and a checkbox do not, and
# treating them all as text is how a driver silently fills 12 of 15 fields. Harvey's
# form alone carries a boolean and two single_selects, all three required, so a
# text-only driver could never have completed a single real application.
CHOICE_KINDS = frozenset({"single_select", "multi_select", "boolean", "consent",
                          "demographic"})

# A place is not a text box either. Ashby's Location is a combobox backed by a
# geocoder with no name attribute at all, so the text path could not even find it
# and a required field went blank on every run.
TYPEAHEAD_KINDS = frozenset({"location"})


def _norm(text: str | None) -> str:
    """Whitespace-collapsed, lowercased. Every comparison here goes through it."""
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _resolve(page, selectors: list[str]):
    """The first selector that resolves to exactly one control, or None.

    Exactly one, not at least one, because a selector matching three elements is
    a selector that does not know which field it means.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() == 1:
                return loc.first
        except Exception:
            continue
    return None


def _press_group(page, el, want: str) -> bool:
    """A segmented Yes/No: real buttons, a hidden checkbox mirroring them.

    Harvey's Ashby form renders the work-authorisation question as
      <div class="...yesno"><button aria-pressed data-option="yes">Yes</button>
                            <button aria-pressed data-option="no">No</button>
                            <input type="checkbox" style="display:none"></div>
    so check() on that input fails actionability, and the unverified last-resort
    label lookup underneath then reported success. The live form went to the
    approval picture with "are you legally authorized to work" blank while the
    run called it filled. The buttons are the control; the checkbox is a mirror.
    """
    try:
        buttons = el.locator("xpath=../button[@aria-pressed or @data-option]")
        n = buttons.count()
    except Exception:
        return False
    for i in range(n):
        b = buttons.nth(i)
        try:
            text, opt = _norm(b.inner_text()), _norm(b.get_attribute("data-option"))
        except Exception:
            continue
        if want == text or (opt and want == opt):
            try:
                b.click(timeout=5000)
            except Exception:
                return False
            return _norm(b.get_attribute("aria-pressed")) == "true"
    return False


def _option_texts(page, limit: int = 12) -> list[str]:
    try:
        opts = page.get_by_role("option")
        n = min(opts.count(), limit)
    except Exception:
        return []
    out = []
    for i in range(n):
        try:
            out.append(opts.nth(i).inner_text().strip())
        except Exception:
            continue
    return out


def _typeahead(page, el, value: str, notes: list[str], label: str) -> bool:
    """A combobox that fetches its options as you type, like Ashby's Location.

    Typing a value is not choosing one: the form keeps a structured place and an
    input left unconfirmed submits empty. So it types, waits for the listbox, and
    takes only an option that actually matches.

    The matching is strict on purpose. Ashby's geocoder has no "Brooklyn, New
    York": typing it offers New York City first, and typing "Brooklyn" alone
    offers Brooklyn Park, MINNESOTA first. Clicking the first option would have
    put the wrong city on his application and reported the field filled. So an
    option is taken only when it equals the stored answer or contains it whole,
    and otherwise the field is missed and the offered options are recorded, which
    is the one fix that compounds: he corrects the answer bank once.
    """
    want = _norm(value)
    offered: list[str] = []
    for term in _typeahead_terms(value):
        try:
            el.click(timeout=5000)
            el.fill("", timeout=5000)
            el.type(term, delay=20, timeout=10000)
        except Exception:
            continue
        try:
            page.get_by_role("option").first.wait_for(timeout=8000)
        except Exception:
            continue
        texts = _option_texts(page)
        offered = offered or texts
        for i, text in enumerate(texts):
            if _norm(text) == want or want in _norm(text):
                try:
                    page.get_by_role("option").nth(i).click(timeout=5000)
                except Exception:
                    return False
                return bool(_norm(el.input_value()))
    if offered:
        notes.append(f"{label}: no option matched {value!r}. The form offers: "
                     + "; ".join(offered[:6]))
    return False


def _typeahead_terms(value: str) -> list[str]:
    """The whole answer first, then its leading segment, both as he wrote them.

    A geocoder that does not hold the full string often holds the town. Typed
    verbatim: an earlier version typed the lowercased comparison string, which is
    not the answer he stored and is not what a case-sensitive search expects.
    """
    value = (value or "").strip()
    terms = [value]
    lead = value.split(",")[0].strip()
    if lead and lead != value:
        terms.append(lead)
    return terms


def _choose_one(page, selectors: list[str], value: str, *,
                notes: list[str] | None = None, label: str = "") -> bool:
    """Pick `value` on a dropdown, a segmented control, a radio, a checkbox or a
    combobox, and return True ONLY once the control reads back as chosen.

    Every branch verifies. The version before this one returned True the moment a
    click did not raise, so a hidden mirror checkbox and a last-resort label
    lookup both reported success on a control that never changed. A driver that
    cannot tell filled from clicked is worse than one that fills nothing, because
    the refusal to press is built on what it reports.

    Matching is on the LABEL a human reads, not the vendor's value, because
    resolve.py already fitted the answer to the form's own option text.
    """
    want = _norm(value)
    notes = notes if notes is not None else []
    if not want:
        return False
    el = _resolve(page, selectors)
    if el is not None:
        try:
            tag = _norm(el.evaluate("e => e.tagName"))
        except Exception:
            tag = ""
        if tag == "select":
            try:
                el.select_option(label=value.strip(), timeout=5000)
                return bool(_norm(el.input_value()))
            except Exception:
                return False
        try:
            kind = _norm(el.get_attribute("type"))
            role = _norm(el.get_attribute("role"))
            auto = _norm(el.get_attribute("aria-autocomplete"))
        except Exception:
            kind = role = auto = ""
        if role == "combobox" or auto == "list":
            return _typeahead(page, el, value, notes, label)
        if kind in ("radio", "checkbox"):
            # The buttons first: where a segmented control mirrors into a hidden
            # input, the input is not the thing to click.
            if _press_group(page, el, want):
                return True
            try:
                el.check(timeout=5000)
            except Exception:
                return False
            return _is_checked(el)
        try:
            el.click(timeout=5000)
            page.get_by_role("option", name=value, exact=False).first.click(timeout=5000)
        except Exception:
            return False
        return True
    # Last resort: a radio anywhere on the page carrying that label. Verified the
    # same way, because an unverified last resort is how a blank field was
    # reported filled.
    try:
        radio = page.get_by_role("radio", name=value, exact=False).first
        radio.check(timeout=4000)
        return _is_checked(radio)
    except Exception:
        pass
    try:
        labelled = page.get_by_label(value, exact=False).first
        labelled.check(timeout=4000)
        return _is_checked(labelled)
    except Exception:
        return False


def _is_checked(el) -> bool:
    """A control's own answer to whether it is on.

    Defaults to True where the page cannot say, because a fake or a control with
    no is_checked is not evidence of failure; the branches that call it have all
    already acted successfully.
    """
    try:
        return bool(el.is_checked())
    except AttributeError:
        return True
    except Exception:
        return False


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


ASHBY_ENTRY = ".ashby-application-form-field-entry"


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
            if f.kind in CHOICE_KINDS or f.kind in TYPEAHEAD_KINDS:
                ok = _choose_one(self.page, [
                    f'select[name="{f.key}"]',
                    f'[name="{f.key}"]',
                    f'[aria-label="{label}"]',
                    f'[data-testid="{f.key}"]',
                    # Ashby wraps every field in a .ashby-application-form-field-entry
                    # carrying its own label, which is the only handle on a control
                    # that has no name, id or aria-label of its own.
                    f'{ASHBY_ENTRY}:has(label:text-is("{label}")) '
                    f'input[role="combobox"]',
                    f'{ASHBY_ENTRY}:has(label:text-is("{label}")) select',
                ], f.value, notes=self.notes, label=f.label)
            else:
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
            if f.kind in CHOICE_KINDS or f.kind in TYPEAHEAD_KINDS:
                ok = _choose_one(self.page, [
                    f'select#{f.key}', f'#{f.key}', f'[name="{f.key}"]',
                ], f.value, notes=self.notes, label=f.label)
            else:
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

# Text a human is being asked to read. Matched against the page HTML, because a
# challenge that has not rendered yet still carries its own copy.
CAPTCHA_MARKS = ("are you human", "verify you are human")

# Elements that exist only where a human has to do something. Deliberately NOT
# the bare word "recaptcha": Harvey's Ashby form loads recaptcha__en.js on every
# page for an invisible v3 score, so a substring search of the HTML called a
# workable form a captcha and refused to fill it. v3 asks nothing of anyone and
# is not a blocker. A container div or a challenge iframe is, and only when it is
# visible, since both libraries leave hidden scaffolding behind on pages that
# never challenge.
CAPTCHA_SELECTORS = (
    ".g-recaptcha", ".h-captcha", ".cf-turnstile",
    'iframe[src*="recaptcha/api2/bframe"]',
    'iframe[src*="hcaptcha.com/captcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
)
LOGIN_MARKS = ("sign in to apply", "log in to apply", "create an account to apply")

# The form controls whose presence means the application form has actually
# rendered. Ashby fetches its form after the document loads and shows "Fetching
# application form" in the meantime, so a goto that waits only for load returns a
# page with zero inputs, fills nothing, and photographs a spinner.
FORM_READY = "input, textarea, select"
FORM_READY_MS = 30000

# Ashby reads the uploaded CV and autofills from it, announcing itself while it
# works. That parse lands AFTER an upload, so a driver that filled the form and
# then attached the file had its answers overwritten by the vendor's guesses, and
# photographed the form mid-parse besides: Krish would have approved a picture
# showing an empty resume slot and fields that were about to change under him.
# So the file goes on first, the parse is waited out, and hunter's answers are
# typed last, over the top of whatever the parser decided.
PARSING_MARKS = ("parsing your resume", "autofilling key fields")
SETTLE_MS = 25000


def _settle(page) -> None:
    """Wait for an upload's side effects to finish, bounded."""
    deadline = time.monotonic() + SETTLE_MS / 1000
    while time.monotonic() < deadline:
        try:
            html = (page.content() or "").lower()
        except Exception:
            return
        if not any(m in html for m in PARSING_MARKS):
            return
        try:
            page.wait_for_timeout(500)
        except Exception:
            return


def _visible(page, selector: str) -> bool:
    try:
        loc = page.locator(selector).first
        return bool(loc.count()) and loc.is_visible()
    except Exception:
        return False


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
    for selector in CAPTCHA_SELECTORS:
        if _visible(page, selector):
            return "captcha"
    for mark in LOGIN_MARKS:
        if mark in html:
            return "login required"
    return ""


# ---------- the run ----------

def _launch(pw):
    """Chromium, honouring an explicitly provided executable.

    HUNTER_CHROMIUM_PATH exists because a pinned playwright and a preinstalled
    browser disagree about the build number more often than not: this sandbox
    carries chromium-1194 and the pip playwright wanted 1234, so the launch failed
    with "Executable doesn't exist" and no form could be filled. Unset, it behaves
    exactly as before and downloads or finds its own.
    """
    import os
    exe = (os.environ.get("HUNTER_CHROMIUM_PATH") or "").strip()
    if exe:
        return pw.chromium.launch(headless=True, executable_path=exe)
    return pw.chromium.launch(headless=True)


def _open_and_fill(pw, plan: FillPlan, attachments: dict, cls, names=None):
    """Load the form, fill it, attach the files, photograph it. Never presses.

    Shared by preview() and submit() so there is one idea of what filling means.
    Returns (browser, page, driver, blocker, png).
    """
    browser = _launch(pw)
    page = browser.new_page()
    driver = cls(page, plan)
    page.goto(driver.apply_url(), timeout=60000)
    try:
        page.wait_for_selector(FORM_READY, timeout=FORM_READY_MS)
    except Exception:
        # Not fatal on its own: page_blocker below names a captcha or a login
        # wall, and where it is neither the driver reports every field as missed,
        # which reads as an empty form rather than as a silent success.
        pass
    blocker = page_blocker(page)
    if blocker:
        return browser, page, driver, blocker, _png(page)
    paths = _attach(page, plan, attachments or {}, driver, names)
    _settle(page)
    _check_upload(page, driver, plan)
    driver.fill()
    png = _png(page)
    _drop(paths)
    return browser, page, driver, "", png


def _png(page) -> bytes:
    try:
        return page.screenshot(full_page=True)
    except Exception:
        return b""


def preview(plan: FillPlan, *, attachments: dict[str, bytes] | None = None,
            names: dict[str, str] | None = None, browser_factory=None) -> dict:
    """Fill the real form and photograph it. Cannot submit, by construction.

    No approval gate, because there is nothing to gate: this function never calls
    press_submit and holds no reference to it. It exists so the approval email can
    show Krish the actual completed form rather than a description of it, which is
    what collapsed the approve-then-check-then-approve-again loop into one email.

    The gate belongs on the press, not on the typing.
    """
    out = {"filled": [], "missed": [], "png": b"", "blocker": "", "error": "",
           "notes": []}
    cls = driver_for(plan)
    if browser_factory is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            out["error"] = "playwright missing"
            return out
        browser_factory = sync_playwright
    try:
        with browser_factory() as pw:
            browser, _page, driver, blocker, png = _open_and_fill(
                pw, plan, attachments or {}, cls, names)
            out.update(filled=driver.filled, missed=driver.missed,
                       png=png, blocker=blocker, notes=driver.notes)
            browser.close()
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
    return out


def submit(cfg: Config, token: str, plan: FillPlan, *,
           attachments: dict[str, bytes] | None = None,
           names: dict[str, str] | None = None,
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
                    "filled": [], "missed": [], "screenshot": "", "notes": []}
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
            browser = _launch(pw)
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

            # Attach, let the vendor's resume parser finish, then fill: the
            # other order lets that parser overwrite the approved answers.
            paths = _attach(page, plan, attachments or {}, driver, names)
            _settle(page)
            _check_upload(page, driver, plan)
            driver.fill()
            _drop(paths)
            result["filled"], result["missed"] = driver.filled, driver.missed
            result["notes"] = list(driver.notes)

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


def _attach(page, plan: FillPlan, attachments: dict[str, bytes], driver,
            names: dict[str, str] | None = None) -> list[str]:
    """Put the built PDFs on the form. Returns the temp paths to clean up after.

    One slot takes the merged file, per the audit's attachment_style: the same
    rule the approval email already told Krish it would follow, so what he
    approved and what the employer receives are the same document.

    The temp file OUTLIVES this call on purpose. The first version unlinked it in
    a finally block the moment set_input_files returned, and the browser reads the
    file later, when its upload request actually fires: Ashby answered "Oops!
    Failed to fetch", left the resume slot empty, and the driver reported the
    field filled. Caller deletes once the page is done with it.
    """
    import tempfile, os
    names = names or {}
    paths: list[str] = []
    wanted = [f for f in plan.fields if f.kind in ("file_resume", "file_cover")]
    for f in wanted:
        key = "file_resume" if plan.attachment_style == ATT_CV else f.kind
        blob = attachments.get(key) or attachments.get(f.kind)
        if not blob:
            driver.missed.append(f.label)
            continue
        # The employer sees this filename. mkstemp produced hunter_9ugny06m.pdf,
        # which is what Ashby showed in the resume slot, so the file goes into a
        # temp DIRECTORY under the name canon 9.12 gives it.
        holder = tempfile.mkdtemp(prefix="hunter_")
        path = os.path.join(holder, names.get(key) or names.get(f.kind)
                            or _default_name(f.kind))
        with open(path, "wb") as fh:
            fh.write(blob)
        paths.append(path)
        try:
            page.locator('input[type="file"]').first.set_input_files(path, timeout=15000)
            driver.filled.append(f.label)
        except Exception:
            driver.missed.append(f.label)
    return paths


def _default_name(kind: str) -> str:
    return ("KrishRaja_CoverLetter.pdf" if kind == "file_cover"
            else "KrishRaja_CV.pdf")


def _drop(paths) -> None:
    import os, shutil
    for path in paths or []:
        try:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        except OSError:
            pass


# What a form says when it would not take the file. Checked after the upload has
# had its chance, because set_input_files succeeding means the browser accepted
# the path, not that the employer received the document.
UPLOAD_ERROR_MARKS = ("failed to fetch", "failed to upload", "upload failed",
                      "could not upload")


def _upload_failed(page) -> bool:
    try:
        html = (page.content() or "").lower()
    except Exception:
        return False
    return any(m in html for m in UPLOAD_ERROR_MARKS)


def _check_upload(page, driver, plan: FillPlan) -> None:
    """Move every file field from filled to missed when the form rejected it."""
    if not _upload_failed(page):
        return
    for f in plan.fields:
        if f.kind in ("file_resume", "file_cover") and f.label in driver.filled:
            driver.filled.remove(f.label)
            driver.missed.append(f.label)
    driver.notes.append("the form rejected the upload: it answers "
                        "'failed to fetch' and the resume slot is empty")


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
