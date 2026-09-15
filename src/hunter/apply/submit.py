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

    def file_selectors(self, field) -> list[str]:
        """Where this file field's control is. One slot on most forms."""
        return ['input[type="file"]']

    def choice_selectors(self, field) -> list[str]:
        raise NotImplementedError

    def text_selectors(self, field) -> list[str]:
        raise NotImplementedError

    def _answerable(self):
        return [f for f in self.plan.fields
                if f.kind not in ("file_resume", "file_cover") and f.value]

    def _put(self, field) -> bool:
        if field.kind in CHOICE_KINDS or field.kind in TYPEAHEAD_KINDS:
            return _choose_one(self.page, self.choice_selectors(field),
                               field.value, notes=self.notes, label=field.label)
        return _fill_one(self.page, self.text_selectors(field), field.value)

    def fill(self) -> None:
        """One pass, then a second at anything a vendor emptied behind us.

        Greenhouse parses the uploaded CV and writes its own answers into the
        name and email boxes, and that write lands after the upload rather than
        with it: the first pass filled all four, the parser blanked all four, and
        the form went to the picture with no name on it. Waiting longer is not a
        fix, because the race is with a request whose timing is the vendor's.
        Filling again over the top is.
        """
        results = {id(f): self._put(f) for f in self._answerable()}
        for f in self._answerable():
            # Whatever reads empty now, whether the first pass thought it had
            # succeeded or not: the vendor's write can land during our own
            # read-back just as easily as after it.
            if self._emptied(f):
                results[id(f)] = self._put(f)
        for f in self._answerable():
            (self.filled if results[id(f)] else self.missed).append(f.label)

    def _emptied(self, field) -> bool:
        """Did something blank a text box we just filled?

        Only text: a chosen option reads back through its own control and the
        verification already happened in _choose_one.
        """
        if field.kind in CHOICE_KINDS or field.kind in TYPEAHEAD_KINDS:
            return False
        el = _resolve(self.page, self.text_selectors(field))
        if el is None:
            return False
        return not _reads_back(el, field.value)

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


def _resolve(page, selectors: list[str], *, exact: bool = True):
    """The first selector that resolves to a control, or None.

    Exactly one by default, not at least one, because a selector matching three
    elements is a selector that does not know which field it means.

    exact=False is for the file slots, where the driver's list is ordered from
    the specific to the general and the general one is deliberately broad. Ashby
    has TWO file inputs on its page, so demanding exactly one meant the CV was
    never uploaded at all while the run reported thirteen fields filled.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n == 1 or (not exact and n >= 1):
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
            _open_menu(page, el)
            if not _type_until_it_reads(page, el, term):
                continue
        except Exception:
            continue
        try:
            page.get_by_role("option").first.wait_for(timeout=8000)
        except Exception:
            continue
        texts = _option_texts(page)
        offered = offered or texts
        hit = _best_option(texts, want)
        if hit is not None:
            try:
                page.get_by_role("option").nth(hit).click(timeout=5000)
            except Exception:
                return False
            return bool(_norm(_combo_value(el)))
    # Leave nothing half typed behind. An unconfirmed combobox holding "Brooklyn"
    # reads on the approval picture as an answer, and is not one.
    try:
        el.fill("", timeout=5000)
    except Exception:
        pass
    if offered:
        notes.append(f"{label}: no option matched {value!r}. The form offers: "
                     + "; ".join(offered[:6]))
    return False


def _clear(page, el) -> None:
    """Empty a combobox without closing it.

    el.fill("") does close it: on Greenhouse it left aria-expanded false and no
    menu, so the second attempt of a two-term search always found nothing. Select
    all and delete keeps the focus and the menu where a real person would.
    """
    try:
        if not _norm(el.input_value()):
            return
    except Exception:
        pass
    keyboard = getattr(page, "keyboard", None)
    if keyboard is None:
        try:
            el.fill("", timeout=5000)
        except Exception:
            pass
        return
    try:
        keyboard.press("ControlOrMeta+a")
        keyboard.press("Backspace")
    except Exception:
        pass


MENU_OPEN_POLLS = 12

# Where a combobox keeps its answer once one is chosen. react-select CLEARS its
# search input on selection and renders the value as a label beside it, so
# input_value() reads empty on a control that is correctly filled: Greenhouse's
# country and city both picked the right option, then reported themselves missed.
_COMBO_VALUE_JS = """e => {
  let n = e;
  for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
    const v = n.querySelector('[class*="single-value"], [class*="singleValue"]');
    if (v && v.textContent.trim()) return v.textContent.trim();
    const d = n.getAttribute ? n.getAttribute('data-value') : '';
    if (d) return d;
  }
  return '';
}"""


def _combo_value(el) -> str:
    """What a combobox is holding, whether in its input or in its label."""
    try:
        typed = el.input_value()
    except Exception:
        typed = ""
    if _norm(typed):
        return typed
    try:
        return el.evaluate(_COMBO_VALUE_JS) or ""
    except Exception:
        return ""


def _open_menu(page, el) -> bool:
    """Get the menu open with a throwaway keystroke before typing the answer.

    Measured on Greenhouse across three runs: react-select opens its menu as a
    CONSEQUENCE of the first keystroke, and remounts its input while doing so, so
    the characters typed during that remount are lost. Typing the answer straight
    in gave "k, United States" once, "ed States" the next time and the whole
    string with the menu still shut the third, which is three different wrong
    answers from one sequence. Spending one key on opening it makes the remount
    happen before anything that matters is typed, and the next three runs filled
    both required comboboxes correctly.
    """
    keyboard = getattr(page, "keyboard", None)
    if keyboard is None:
        return True
    for _ in range(MENU_OPEN_POLLS):
        try:
            if _norm(el.get_attribute("aria-expanded")) == "true":
                return True
            keyboard.type("a", delay=30)
            page.wait_for_timeout(300)
        except Exception:
            return False
    try:
        return _norm(el.get_attribute("aria-expanded")) == "true"
    except Exception:
        return False


def _type_until_it_reads(page, el, term: str, attempts: int = 3) -> bool:
    """Type the whole term, and keep typing until the box actually holds it.

    Greenhouse's react-select remounts its input the moment the menu opens, and
    the characters typed during that remount are lost: "New York, United States"
    arrived as ", United States", which then searched for United and offered
    United States Air Force Academy, Colorado. Opening the menu is also the only
    way to get any options at all, so the remount cannot be avoided, only typed
    through.
    """
    for _ in range(attempts):
        _clear(page, el)
        _type(page, el, term)
        try:
            got = el.input_value()
        except AttributeError:
            return True
        except Exception:
            return False
        if _norm(got) == _norm(term):
            return True
    return False


def _type(page, el, text: str) -> None:
    keyboard = getattr(page, "keyboard", None)
    if keyboard is not None:
        keyboard.type(text, delay=30)
        return
    el.type(text, delay=20, timeout=10000)


def _best_option(texts: list[str], want: str) -> int | None:
    """The index of the option that is the stored answer, or None.

    Plain containment is not enough in either direction. "London, United Kingdom"
    is offered as "London, Greater London, England, United Kingdom", which does
    not contain it; and the same search offers "London, Ontario, Canada", which
    must never be taken. So an option matches when every comma-segment of the
    answer appears in it, in order: London then United Kingdom rules the Ontario
    one out, and "Brooklyn, New York, United States" still matches nothing on a
    geocoder that has no Brooklyn.

    An exact option anywhere in the list beats a segment match earlier in it.
    """
    lowered = [_norm(t) for t in texts]
    for i, text in enumerate(lowered):
        if text == want:
            return i
    segments = [seg.strip() for seg in want.split(",") if seg.strip()]
    if not segments:
        return None
    for i, text in enumerate(lowered):
        at, ok = 0, True
        for seg in segments:
            found = text.find(seg, at)
            if found < 0:
                ok = False
                break
            at = found + len(seg)
        if ok:
            return i
    return None


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
    el = _resolve(page, selectors)
    if el is None:
        return False
    try:
        el.fill(value, timeout=5000)
    except Exception:
        return False
    # Read it back. Greenhouse's own resume parser autofills the name and email
    # boxes after an upload and blanked every one of them, while this returned
    # True because .fill() had not raised.
    return _reads_back(el, value)


def _reads_back(el, value: str) -> bool:
    try:
        got = el.input_value()
    except AttributeError:
        return True
    except Exception:
        return False
    return _norm(got) == _norm(value)


ASHBY_ENTRY = ".ashby-application-form-field-entry"


class AshbyDriver(Driver):
    ats = "ashby"

    def apply_url(self) -> str:
        return (f"https://jobs.ashbyhq.com/{self.plan.slug}/"
                f"{self.plan.posting_id}/application")

    def _label(self, field) -> str:
        return field.label.replace('"', '\\"')

    def choice_selectors(self, field) -> list[str]:
        label = self._label(field)
        return [
            f'select[name="{field.key}"]',
            f'[name="{field.key}"]',
            f'[aria-label="{label}"]',
            f'[data-testid="{field.key}"]',
            # Ashby wraps every field in a .ashby-application-form-field-entry
            # carrying its own label, which is the only handle on a control that
            # has no name, id or aria-label of its own.
            f'{ASHBY_ENTRY}:has(label:text-is("{label}")) input[role="combobox"]',
            f'{ASHBY_ENTRY}:has(label:text-is("{label}")) select',
        ]

    def text_selectors(self, field) -> list[str]:
        label = self._label(field)
        return [
            f'input[name="{field.key}"]',
            f'textarea[name="{field.key}"]',
            f'input[aria-label="{label}"]',
            f'textarea[aria-label="{label}"]',
        ]

    def file_selectors(self, field) -> list[str]:
        # By key, because Ashby's page carries a SECOND file input: the "Autofill
        # from resume" box above the form. It is first in document order, so the
        # generic selector put the CV there and left the application's own
        # required Resume slot empty, which the page-level check caught and the
        # driver did not.
        return [f'input[type="file"]#{field.key}',
                f'input[type="file"][name="{field.key}"]',
                f'#{field.key}',
                'input[type="file"]']

    def press_submit(self) -> None:
        self.page.get_by_role("button", name="Submit Application").click(timeout=15000)


class GreenhouseDriver(Driver):
    ats = "greenhouse"

    def apply_url(self) -> str:
        # job-boards, not boards. Greenhouse moved, and the old host answers a
        # live posting with a redirect to the board carrying ?error=true, so
        # every Greenhouse run would have loaded a search box and filled nothing.
        return (f"https://job-boards.greenhouse.io/{self.plan.slug}/jobs/"
                f"{self.plan.posting_id}")

    def choice_selectors(self, field) -> list[str]:
        return [f'select#{field.key}', f'#{field.key}',
                f'[name="{field.key}"]']

    def text_selectors(self, field) -> list[str]:
        return [f'#{field.key}', f'input[name="{field.key}"]',
                f'textarea[name="{field.key}"]']

    def file_selectors(self, field) -> list[str]:
        # Greenhouse gives the resume and the cover letter their own inputs, so
        # `input[type=file]`.first put both documents in the resume slot and left
        # the cover letter empty.
        return [f'input[type="file"]#{field.key}',
                f'#{field.key}',
                'input[type="file"]']

    def press_submit(self) -> None:
        self.page.get_by_role("button", name="Submit application").click(timeout=15000)


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
    """Wait for an upload's side effects to finish, bounded.

    Two waits, because vendors announce themselves differently. Ashby prints
    "Parsing your resume"; Greenhouse prints nothing and simply issues the
    request, so only the network says it is done. Neither is sufficient alone and
    neither is reliable, which is why Driver.fill also fills a second time over
    anything emptied behind it.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:
        pass
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
    driver.fill()
    # After the fill, not before it: Ashby renders the filename only once its own
    # resume parse has finished, and checking straight after the upload called a
    # document it did accept missing.
    _check_upload(page, driver, plan, names)
    # What the page itself still calls empty and required, named in the email so
    # Krish sees a Greenhouse country box he was never told about.
    for name in empty_required(page):
        if name not in driver.missed:
            driver.missed.append(name)
    png = _png(page)
    _drop(paths)
    return browser, page, driver, "", png


# Reads the page itself rather than the plan. Greenhouse's board API omits the
# country and city controls entirely, so a plan built from it says every required
# field has an answer while the live form has two empty ones. A gate that trusts
# the vendor's own description of its form is not a gate.
_EMPTY_REQUIRED_JS = """() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('input, textarea, select').forEach(e => {
    if (e.type === 'hidden' || e.disabled) return;
    const label = (e.labels && e.labels[0]) ? e.labels[0].textContent.trim() : '';
    const required = e.required || e.getAttribute('aria-required') === 'true'
                     || label.includes('*');
    if (!required) return;
    if (e.offsetParent === null && e.type !== 'file') return;
    let empty;
    if (e.type === 'checkbox' || e.type === 'radio') {
      const group = e.name
        ? document.querySelectorAll(`[name="${e.name}"]`) : [e];
      empty = ![...group].some(x => x.checked);
    } else if (e.type === 'file') {
      empty = !(e.files && e.files.length);
    } else if (e.getAttribute('role') === 'combobox'
               || e.getAttribute('aria-autocomplete') === 'list') {
      // react-select clears its search input on selection and renders the
      // answer as a label, so e.value reads empty on a filled control.
      let n = e, held = '';
      for (let i = 0; i < 5 && n && !held; i++, n = n.parentElement) {
        const v = n.querySelector('[class*="single-value"], [class*="singleValue"]');
        if (v && v.textContent.trim()) { held = v.textContent.trim(); break; }
        const d = n.getAttribute ? n.getAttribute('data-value') : '';
        if (d) { held = d; break; }
      }
      empty = !(held || (e.value || '').trim());
    } else {
      empty = !(e.value || '').trim();
    }
    if (!empty) return;
    const name = (label.replace('*', '').trim() || e.id || e.name || 'a field');
    if (seen.has(name)) return;
    seen.add(name);
    out.push(name);
  });
  return out;
}"""


def empty_required(page) -> list[str]:
    """Required controls the page still shows empty, named as a human reads them."""
    try:
        return list(page.evaluate(_EMPTY_REQUIRED_JS) or [])
    except Exception:
        # A page that cannot be questioned is not evidence of a complete form,
        # but it is not evidence of an incomplete one either, and the plan-level
        # required check still stands.
        return []


def _png(page) -> bytes:
    try:
        return page.screenshot(full_page=True)
    except Exception:
        return b""


# ---------- the form, filled, left for Krish to press ----------

DEBUG_PORT = 9222
LOCAL_START_S = 6


def _chrome_binary(explicit: str = "") -> str:
    """The Chrome on this machine, or the one named."""
    import os, shutil
    if explicit:
        return explicit
    env = (os.environ.get("HUNTER_CHROME_PATH") or "").strip()
    if env:
        return env
    candidates = [
        # Windows first: that is the machine this actually runs on.
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "google-chrome", "chromium", "chromium-browser", "chrome.exe",
        "msedge.exe",
    ]
    for c in candidates:
        found = c if os.path.exists(c) else shutil.which(c)
        if found:
            return found
    raise SubmitBlocked(
        "no Chrome found. Install it, or set HUNTER_CHROME_PATH to the binary")


def _start_local_chrome(chrome: str, profile_dir: str, port: int):
    """A NORMAL Chrome, started the way a person starts one, with its debugging
    port open. Detached, so it outlives this process and stays on screen.

    This is the whole point of the local flow. Playwright LAUNCHING a browser
    sets the automation flag and navigator.webdriver reads true, which is what an
    invisible bot check scores and refuses. Attaching to a browser that was
    started normally does not, because it genuinely was not started by
    automation: measured on this machine, webdriver reads false. Nothing is
    masked or spoofed. The score ends up reflecting what is actually true, which
    is a person at their own computer about to press a button themselves.
    """
    import socket, subprocess
    if _port_open(port):
        # A Chrome is already listening. Use it: starting a second one on the
        # same profile fails, and reusing his open browser is the point.
        return
    args = [chrome, f"--remote-debugging-port={port}", "--no-first-run",
            "--no-default-browser-check"]
    if profile_dir:
        args.append(f"--user-data-dir={profile_dir}")
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    # Wait for the port, not a guess. A fixed sleep is either slower than it
    # needs to be or shorter than a cold start on a laptop, and the second one
    # fails with ECONNREFUSED and no explanation.
    for _ in range(LOCAL_START_S * 4):
        if _port_open(port):
            return
        time.sleep(0.25)
    raise SubmitBlocked(
        f"Chrome did not open its debugging port on {port}. Close every Chrome "
        f"window and try again, or pass --port with a different number.")


def _port_open(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def open_for_human(plan: FillPlan, *, attachments: dict[str, bytes] | None = None,
                   names: dict[str, str] | None = None,
                   cdp_url: str = "", chrome: str = "", profile_dir: str = "",
                   port: int = DEBUG_PORT, keep_dir: str = "",
                   connector=None) -> dict:
    """Fill the real form in Krish's own browser and LEAVE IT THERE.

    Cannot submit, by construction: it never calls press_submit and holds no
    reference to it, exactly like preview(). The difference is where the browser
    is and who closes it. Nothing here presses anything; the last click is his,
    which is the only version of this that an invisible bot check should pass,
    because it is the only version that is true.

    The attached PDFs are written somewhere durable and deliberately NOT deleted:
    this process exits while the browser is still holding the form open, and the
    file is read when he presses submit, not when it is attached.
    """
    import os, tempfile
    out = {"filled": [], "missed": [], "notes": [], "error": "", "blocker": "",
           "url": "", "files": []}
    try:
        cls = driver_for(plan)
    except SubmitError as e:
        out["error"] = str(e)
        return out
    if connector is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            out["error"] = "playwright missing"
            return out
        connector = sync_playwright

    keep = keep_dir or tempfile.mkdtemp(prefix="hunter_apply_")
    try:
        with connector() as pw:
            if not cdp_url:
                # The port first, the binary only if nothing is listening. Asking
                # where Chrome is installed while Chrome is already running and
                # waiting on that port is a refusal with no cause.
                if not _port_open(port):
                    _start_local_chrome(_chrome_binary(chrome), profile_dir, port)
                cdp_url = f"http://127.0.0.1:{port}"
            browser = pw.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            driver = cls(page, plan)
            page.goto(driver.apply_url(), timeout=60000)
            try:
                page.wait_for_selector(FORM_READY, timeout=FORM_READY_MS)
            except Exception:
                pass
            blocker = page_blocker(page)
            if blocker:
                out["blocker"] = blocker
                out["url"] = driver.apply_url()
                return out
            paths = _attach_kept(page, plan, attachments or {}, driver,
                                 names or {}, keep)
            _settle(page)
            driver.fill()
            _check_upload(page, driver, plan, names or {})
            for name in empty_required(page):
                if name not in driver.missed:
                    driver.missed.append(name)
            out.update(filled=driver.filled, missed=driver.missed,
                       notes=driver.notes, url=page.url, files=paths)
            # No browser.close(). The window stays on his screen, on the finished
            # form, with the Submit button untouched.
            return out
    except SubmitError:
        raise
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
        return out


def _attach_kept(page, plan: FillPlan, attachments: dict, driver, names: dict,
                 keep: str) -> list[str]:
    """_attach, writing into a directory that outlives this process."""
    import os
    paths: list[str] = []
    for f in plan.fields:
        if f.kind not in ("file_resume", "file_cover"):
            continue
        key = "file_resume" if plan.attachment_style == ATT_CV else f.kind
        blob = attachments.get(key) or attachments.get(f.kind)
        if not blob:
            driver.missed.append(f.label)
            continue
        path = os.path.join(keep, names.get(key) or names.get(f.kind)
                            or _default_name(f.kind))
        with open(path, "wb") as fh:
            fh.write(blob)
        paths.append(path)
        target = _resolve(page, driver.file_selectors(f), exact=False)
        if target is None:
            driver.missed.append(f.label)
            continue
        try:
            target.set_input_files(path, timeout=15000)
            driver.filled.append(f.label)
        except Exception:
            driver.missed.append(f.label)
    return paths


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
    # Inside the guard, not before it. An ATS with no driver raised straight out
    # of preview and killed the whole approvals batch on its first LinkedIn row,
    # so twenty-three applications were not sent because one of them was not a
    # kind of form this can open.
    try:
        cls = driver_for(plan)
    except SubmitError as e:
        out["error"] = str(e)
        return out
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
                    "filled": [], "missed": [], "screenshot": "", "notes": [],
                    "confirmation": "", "after_png": b""}
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
            # The SHARED open-and-fill, not a second copy of it. This block used
            # to re-implement the flow and had drifted: it never waited for the
            # form to render, so on Ashby the resume slot did not exist yet when
            # the file was attached, the CV went nowhere, and the gate refused
            # the send with "field not found: Resume" on a form it could have
            # filled. One idea of what filling means, or the two diverge again.
            browser, page, driver, blocker = _open_and_fill(
                pw, plan, attachments or {}, cls, names)[:4]
            if blocker:
                shot = _shoot(page, token, shot_sink)
                record_outcome(cfg, token, approval.QUEUED, reason=blocker,
                               screenshot_url=shot)
                result.update(state=approval.QUEUED, reason=blocker, screenshot=shot)
                browser.close()
                return result
            result["filled"], result["missed"] = driver.filled, driver.missed
            result["notes"] = list(driver.notes)

            # A field we could not find is a field the employer will see empty.
            # Required and missing is a refusal to press, not a warning.
            required_missed = [f.label for f in plan.fields
                               if f.required and f.label in driver.missed]
            # And whatever the PAGE says is still required and empty, which is
            # the only check that sees a control the vendor's API never
            # mentioned.
            for name in empty_required(page):
                if name not in required_missed:
                    required_missed.append(name)
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

            before_url = ""
            try:
                before_url = page.url
            except Exception:
                pass
            driver.press_submit()
            result["pressed"] = True
            # Look at the page before believing anything. The state stays
            # SUBMITTED either way, because the button WAS pressed and a retry on
            # an application that did land is the one unrecoverable mistake here.
            # What changes is what Krish is told: an unconfirmed press is reported
            # as an unconfirmed press, with a picture of whatever the form showed.
            confirmation = submission_confirmed(page, before_url)
            result["confirmation"] = confirmation
            result["after_png"] = _png(page)
            after = _shoot(page, token, shot_sink) or shot
            why = confirmation or "pressed, no confirmation seen"
            if not confirmation and scored_by_bot_check(page):
                why += (" (this form scores the visitor with an invisible bot "
                        "check, which a headless run in a datacentre fails)")
            record_outcome(cfg, token, approval.SUBMITTED, screenshot_url=after,
                           reason=why)
            result["confirmation"] = confirmation
            result["why"] = why
            result.update(state=approval.SUBMITTED, screenshot=after)
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
        target = _resolve(page, driver.file_selectors(f), exact=False)
        if target is None:
            driver.missed.append(f.label)
            continue
        try:
            target.set_input_files(path, timeout=15000)
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


# Polls, not a wall-clock deadline: page.wait_for_timeout is what makes a poll
# cost anything, and a page that does not wait should finish the loop at once
# rather than spin for twelve seconds doing nothing.
UPLOAD_SHOWN_POLLS = 24

# What a form says once it has the application. Checked AFTER the button is
# pressed, because "the click did not raise" is not evidence that anything was
# received: press_submit() returned and the very next line recorded SUBMITTED,
# which is the same reporting-success-without-checking that this module spent a
# whole session removing from every other step.
SUBMITTED_MARKS = ("application submitted", "thanks for applying",
                   "thank you for applying", "application received",
                   "we have received your application",
                   "we've received your application",
                   "your application has been submitted",
                   "successfully submitted", "application complete")
CONFIRM_POLLS = 20

# Why a press can go unacknowledged. Harvey's Ashby form runs invisible reCAPTCHA
# v3, which scores the visitor rather than asking anything, and this runner is a
# headless Chromium in a datacentre behind a proxy: navigator.webdriver reads
# true and the user agent says HeadlessChrome. A low score is refused server side
# with nothing shown, which is exactly what the first pressed application looked
# like from here, right down to the missing confirmation email.
#
# Deliberately NOT worked around. Masking those signals is circumventing the
# gate the employer chose, it is an arms race, and being flagged as a bot by
# Ashby would follow Krish across every employer that uses it. The honest fixes
# are to run the press from his own browser on his own machine, where the score
# is genuinely his, or to hand him a filled form and let him press it. Named
# here so an unconfirmed press explains itself instead of looking like a mystery.
SCORED_MARKS = ("recaptcha", "cf-turnstile", "hcaptcha")


def scored_by_bot_check(page) -> bool:
    try:
        return any(m in (page.content() or "").lower() for m in SCORED_MARKS)
    except Exception:
        return False


def submission_confirmed(page, before_url: str = "") -> str:
    """The form's own acknowledgement, or "" when it never gave one.

    Two signals, because vendors differ: the words a human would read, and
    leaving the application form behind. Returns what was seen, so the receipt
    can quote it rather than assert success.
    """
    for _ in range(CONFIRM_POLLS):
        try:
            html = (page.content() or "").lower()
        except Exception:
            return ""
        for mark in SUBMITTED_MARKS:
            if mark in html:
                return mark
        try:
            if before_url and page.url and page.url != before_url:
                return f"navigated to {page.url}"
        except Exception:
            pass
        try:
            page.wait_for_timeout(500)
        except Exception:
            return ""
    return ""


def _wait_for_names(page, wanted: list[str]) -> str:
    """The page's HTML, once it shows every filename or the wait runs out."""
    html = ""
    for _ in range(UPLOAD_SHOWN_POLLS):
        try:
            html = page.content() or ""
        except Exception:
            return html
        if not wanted or all(w in html for w in wanted):
            return html
        try:
            page.wait_for_timeout(500)
        except Exception:
            return html
    return html


def _check_upload(page, driver, plan: FillPlan, names=None) -> None:
    """Move a file field from filled to missed unless the form really took it.

    Two ways a form can refuse a document while set_input_files succeeds, and
    both were live. Ashby says so out loud ("Oops! Failed to fetch"). Greenhouse
    says nothing at all: the bytes sit on a visually-hidden input its own React
    never reads, the Attach button still reads Attach, the filename appears
    nowhere on the page, and the application would be submitted with no CV.

    So the test is the vendor's own acknowledgement: the filename, on the page.
    A form that has the document shows it, and one that shows nothing does not
    have it.
    """
    names = names or {}
    wanted = [names.get(f.kind) or names.get("file_resume") or ""
              for f in plan.fields
              if f.kind in ("file_resume", "file_cover") and f.label in driver.filled]
    html = _wait_for_names(page, [w for w in wanted if w])
    rejected = any(m in html.lower() for m in UPLOAD_ERROR_MARKS)
    for f in plan.fields:
        if f.kind not in ("file_resume", "file_cover") or f.label not in driver.filled:
            continue
        shown = names.get(f.kind) or names.get("file_resume") or ""
        if not rejected and (not shown or shown in html):
            continue
        driver.filled.remove(f.label)
        driver.missed.append(f.label)
        driver.notes.append(
            f"{f.label}: the form did not take the upload. "
            + ("it answers 'failed to fetch'" if rejected
               else f"{shown} appears nowhere on the page after attaching it"))


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
