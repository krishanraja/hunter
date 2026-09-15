"""The submitter, offline. No browser, no network, no employer.

Every test here is about the same question: can hunter put an application in
front of a company when it should not. The happy path is one test; the refusals
are the rest, because a wrong submission cannot be taken back.
"""
import pytest

from hunter.apply import approval
from hunter.apply.fill import FillPlan, FilledField
from hunter.apply import submit as submit_mod
from hunter.apply.submit import (SubmitBlocked, check_gate, driver_for,
                                 page_blocker, submit)


def plan(*, ats="ashby", fields=(), style="CV+CL"):
    return FillPlan(ats=ats, slug="harvey", posting_id="p1", company="Harvey",
                    role="Head of GTM", fields=tuple(fields),
                    attachment_style=style)


def field(label="Email", value="hello@krishraja.com", required=True,
          kind="email", key="email"):
    return FilledField(key=key, label=label, kind=kind, required=required,
                       value=value, source="Info Bank")


def rows(cfg, table, params):
    return rows.data


class Cfg:
    pass


@pytest.fixture
def approved(monkeypatch):
    """An approved row whose hash matches the plan it approved."""
    state = {"row": None, "writes": []}
    p = plan(fields=[field()])
    state["row"] = {"token": "t1", "state": approval.APPROVED,
                    "plan_hash": approval.plan_hash(p.as_dict()),
                    "job_id": "harvey:x"}
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: state["row"])
    monkeypatch.setattr(approval, "set_state",
                        lambda cfg, token, st, **kw: state["writes"].append((st, kw)))
    state["plan"] = p
    return state


# ---------- the gate ----------

def test_an_approved_matching_plan_passes(approved):
    assert check_gate(Cfg(), "t1", approved["plan"])["state"] == approval.APPROVED


def test_a_token_that_was_never_sent_is_refused(monkeypatch):
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: None)
    with pytest.raises(SubmitBlocked, match="nothing was ever sent"):
        check_gate(Cfg(), "ghost", plan(fields=[field()]))


@pytest.mark.parametrize("state", [approval.AWAITING, approval.AMENDING,
                                   approval.CANCELLED, approval.QUEUED,
                                   approval.SUBMITTED, approval.FAILED])
def test_only_approved_may_submit(monkeypatch, state):
    p = plan(fields=[field()])
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: {
        "token": "t1", "state": state, "plan_hash": approval.plan_hash(p.as_dict())})
    with pytest.raises(SubmitBlocked, match="not 'approved'"):
        check_gate(Cfg(), "t1", p)


def test_a_rebuilt_package_voids_the_approval(monkeypatch):
    """He approved a specific set of answers. Change one and the approval is for
    an application that no longer exists."""
    approved_plan = plan(fields=[field(value="hello@krishraja.com")])
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: {
        "token": "t1", "state": approval.APPROVED,
        "plan_hash": approval.plan_hash(approved_plan.as_dict())})
    changed = plan(fields=[field(value="someone.else@example.com")])
    with pytest.raises(SubmitBlocked, match="changed since you approved"):
        check_gate(Cfg(), "t1", changed)


def test_an_unanswered_required_field_stops_it(monkeypatch):
    p = plan(fields=[FilledField(key="q", label="Why us?", kind="long_text",
                                 required=True, unresolved=True,
                                 reason="no stored answer")])
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: {
        "token": "t1", "state": approval.APPROVED,
        "plan_hash": approval.plan_hash(p.as_dict())})
    with pytest.raises(SubmitBlocked, match="Why us"):
        check_gate(Cfg(), "t1", p)


def test_an_unknown_ats_has_no_driver():
    with pytest.raises(SubmitBlocked, match="no driver"):
        driver_for(plan(ats="workday"))


# ---------- the browser path, faked ----------

class FakePage:
    def __init__(self, html="<form></form>", fails=()):
        self.html, self.fails = html, set(fails)
        self.clicked, self.filled, self.files = [], {}, []
        self.shots = 0

    def content(self): return self.html
    def goto(self, url, **kw): self.url = url
    def screenshot(self, **kw):
        self.shots += 1
        return b"\x89PNG"

    def locator(self, sel):
        page = self
        # A captcha widget selector is answered from the html, so the fake can
        # say a challenge is NOT on the page. The version before this returned a
        # match for every selector, which is why page_blocker could only be
        # tested in one direction.
        widget = sel in submit_mod.CAPTCHA_SELECTORS
        mark = sel.strip('.[]').split('[')[0] if widget else ""

        class L:
            first = None
            def count(self):
                if widget:
                    return 1 if mark in page.html else 0
                # Case insensitive: a field's selectors mix its key and its label,
                # so `input[name="email"]` and `input[aria-label="Email"]` are the
                # same field. Matching only the lowercase form let one selector
                # through and the fake reported a fill that had not happened.
                low = sel.lower()
                return 0 if any(f.lower() in low for f in page.fails) else 1
            def is_visible(self): return widget and mark in page.html
            def fill(self, v, **kw): page.filled[sel] = v
            def set_input_files(self, path, **kw): page.files.append(path)
        l = L(); l.first = l
        return l

    def get_by_role(self, role, name=""):
        page = self
        class B:
            def click(self, **kw): page.clicked.append(name)
        return B()


class FakeBrowser:
    def __init__(self, page): self.page, self.closed = page, False
    def new_page(self): return self.page
    def close(self): self.closed = True


def factory_for(page):
    class PW:
        def __enter__(self):
            outer = self
            class Chromium:
                def launch(self, **kw): return FakeBrowser(page)
            class Ctx: chromium = Chromium()
            return Ctx()
        def __exit__(self, *a): return False
    return PW


def test_the_default_fills_and_does_not_press(approved):
    page = FakePage()
    out = submit(Cfg(), "t1", approved["plan"], browser_factory=factory_for(page))
    assert out["state"] == "filled"
    assert out["pressed"] is False
    assert page.clicked == []
    # Nothing was recorded as submitted, because nothing was.
    assert approved["writes"] == []


def test_confirm_presses_once_and_records_submitted(approved):
    page = FakePage()
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["pressed"] is True
    assert page.clicked == ["Submit Application"]
    assert [s for s, _ in approved["writes"]] == [approval.SUBMITTED]


def test_a_captcha_queues_rather_than_clicking_into_it(approved):
    page = FakePage(html="<div class='g-recaptcha'></div>")
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.QUEUED and out["reason"] == "captcha"
    assert page.clicked == []
    assert approved["writes"][0][0] == approval.QUEUED


def test_a_login_wall_queues(approved):
    page = FakePage(html="<p>Sign in to apply</p>")
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["reason"] == "login required" and page.clicked == []


def test_a_required_field_the_driver_cannot_find_stops_the_press(approved):
    """A field hunter could not fill is a field the employer sees empty."""
    page = FakePage(fails=("email",))
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.QUEUED
    assert "field not found" in out["reason"] and "Email" in out["reason"]
    assert page.clicked == []


def test_the_gate_runs_before_the_browser_opens(monkeypatch):
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: None)
    opened = []
    def factory():
        opened.append(True)
        raise AssertionError("browser must not open on a refused token")
    with pytest.raises(SubmitBlocked):
        submit(Cfg(), "ghost", plan(fields=[field()]), browser_factory=factory)
    assert opened == []


def test_a_crash_mid_run_records_queued_rather_than_leaving_it_approved(approved):
    class Exploding(FakePage):
        def goto(self, url, **kw): raise RuntimeError("navigation died")
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(Exploding()))
    assert out["state"] == approval.QUEUED
    assert "RuntimeError" in out["reason"]
    # The row never sits in approved with nothing behind it.
    assert approved["writes"][0][0] == approval.QUEUED


def test_page_blocker_names_what_it_found():
    assert page_blocker(FakePage(html="<div class='h-captcha'></div>")) == "captcha"
    assert page_blocker(FakePage(html="<p>are you human</p>")) == "captcha"
    assert page_blocker(FakePage(html="<p>ordinary form</p>")) == ""


def test_an_invisible_recaptcha_v3_script_is_not_a_blocker():
    """The regression that stopped every Harvey run before it typed anything.

    Ashby loads recaptcha__en.js on every application page for a background v3
    score. page_blocker matched the bare word "recaptcha" anywhere in the HTML,
    called a perfectly workable form a captcha, refused to fill it, and mailed
    Krish a photograph of a spinner. v3 asks a human for nothing.
    """
    html = ('<script src="https://www.gstatic.com/recaptcha/releases/'
            'bnq/recaptcha__en.js"></script><form></form>')
    assert page_blocker(FakePage(html=html)) == ""


def test_a_captcha_that_appears_only_after_filling_still_stops_the_press(approved):
    """The common case, and the reason the blocker is checked twice. A page that
    is clean on arrival and shows a challenge once the form is complete would
    otherwise be discovered by clicking submit into it.

    Mutation-tested: deleting the second check leaves every other test green.
    """
    class LateCaptcha(FakePage):
        @property
        def html(self):
            # Clean until something has been filled, then challenged.
            return ("<div class='g-recaptcha'></div>" if self.filled
                    else "<form></form>")

        @html.setter
        def html(self, _value):
            pass

    page = LateCaptcha()
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.QUEUED
    assert out["reason"] == "captcha"
    assert page.clicked == []
    assert approved["writes"][0][0] == approval.QUEUED


# ---------- a form is not all text boxes ----------

class FormPage:
    """A page whose controls have types, because the first drivers assumed every
    field was a text box.

    Harvey's real form carries a boolean and two single_selects, all three
    required, so a text-only driver fills 12 of 15 fields, refuses to press, and
    could never complete a single application. The fake used until now had no
    notion of a control type at all, so it could not have caught that.

    `controls` maps a selector fragment to its tag/type.
    """

    def __init__(self, controls: dict, role_names=(), ready=True):
        self.controls = controls
        # A form that renders after the document loads, like Ashby's. Until
        # wait_for_selector runs, the page has no controls at all.
        self.ready, self.waited = ready, False
        # Labels a last-resort get_by_role/get_by_label lookup would actually find.
        self.role_names = set(role_names)
        self.selected: dict[str, str] = {}
        self.checked: list[str] = []
        self.typed: dict[str, str] = {}
        self.clicked: list[str] = []
        # Every action in the order it happened, because the order is the
        # correctness property once a vendor autofills from the upload.
        self.order: list[str] = []
        self.uploaded: list[str] = []
        self.upload_paths: list[str] = []
        self.upload_selectors: list[str] = []
        self.upload_alive = True

    def content(self): return "<form></form>"
    def goto(self, url, **kw): self.url = url

    def screenshot(self, **kw):
        # Playwright hands the browser a PATH and the browser reads that file
        # when its upload request actually fires, which is around when the
        # picture is taken, not at set_input_files. A caller that deletes it
        # straight away gets Ashby's "Oops! Failed to fetch" and an empty resume
        # slot, while the driver reports the field filled. Recorded rather than
        # raised, because _png swallows every exception.
        import os
        self.upload_alive = all(os.path.exists(p) for p in self.upload_paths)
        return b"\x89PNG"

    def wait_for_selector(self, sel, **kw):
        self.waited = True

    def wait_for_timeout(self, ms): pass

    def _spec(self, sel):
        if not (self.ready or self.waited):
            return None, None
        for frag, spec in self.controls.items():
            if frag in sel:
                return frag, spec
        return None, None

    def locator(self, sel):
        page, frag, spec = self, *self._spec(sel)
        # A control asked about its own siblings, which is how a segmented Yes/No
        # is found behind its hidden mirror checkbox. Only a spec that declares
        # `options` has any.
        class L:
            first = None
            def count(self): return 1 if spec else 0
            def evaluate(self, expr): return spec["tag"].upper()
            def get_attribute(self, name):
                return spec.get(name if name != "type" else "type")
            def select_option(self, label=None, **kw):
                # Playwright raises when no option carries that label, and the
                # fake must too: a select that silently accepts anything cannot
                # catch an answer the form does not offer.
                allowed = spec.get("options")
                if allowed is not None and label not in allowed:
                    raise RuntimeError(f"no option {label!r}")
                page.selected[frag] = label
            def input_value(self):
                # A select reports its chosen label, a text box what was typed.
                if frag in page.selected:
                    return page.selected[frag]
                return page.typed.get(frag, "")
            def check(self, **kw):
                if not spec.get("checkable", True):
                    raise RuntimeError("element is not visible")
                page.checked.append(frag)
            def is_checked(self):
                # A React-controlled input accepts the click and reverts. The
                # click not raising is not evidence the answer took.
                if spec.get("reverts"):
                    return False
                return frag in page.checked
            def click(self, **kw): page.clicked.append(frag)
            def fill(self, v, **kw):
                page.typed[frag] = v
                page.order.append(f"fill:{frag}")
            def type(self, v, **kw): page.typed[frag] = v
            def set_input_files(self, p, **kw):
                import os
                page.order.append("attach")
                page.upload_selectors.append(sel)
                page.uploaded.append(os.path.basename(p))
                page.upload_paths.append(p)
            def locator(self, subsel):
                # A control asking about its own siblings, which is how a
                # segmented Yes/No is found behind its hidden mirror checkbox.
                return page._buttons(frag, spec)
        l = L(); l.first = l
        return l

    def _buttons(self, frag, spec):
        """The visible buttons of a segmented control, with their pressed state."""
        page = self
        opts = list((spec or {}).get("options_pressed") or [])

        class Btns:
            def count(self): return len(opts)
            def nth(self, i):
                name = opts[i]

                class B:
                    def inner_text(self): return name
                    def get_attribute(self, a):
                        if a == "data-option": return name.lower()
                        if a == "aria-pressed":
                            if spec.get("press_reverts"):
                                return "false"
                            return ("true" if f"{frag}:{name}" in page.clicked
                                    else "false")
                        return None
                    def click(self, **kw): page.clicked.append(f"{frag}:{name}")
                return B()
        return Btns()

    def get_by_role(self, role, name="", exact=True):
        """Raises when the page has no such control, the way Playwright times out.

        The first version returned a happy object for any name, so _choose_one's
        last-resort lookups "succeeded" on a page with no control at all and a
        field that could not be filled was reported as filled. That is the same
        class of lie the selector fake told earlier: a test harness that cannot
        fail cannot catch anything.
        """
        page = self
        # An option only exists once a combobox has been opened; a submit button
        # always does; anything else has to be declared by the test.
        ok = (role == "option" and page.clicked) or name in page.role_names \
            or name == "Submit Application"
        if not ok:
            raise RuntimeError(f"no {role} named {name!r} on this page")

        class R:
            first = None
            def click(self, **kw): page.clicked.append(f"{role}:{name}")
            def check(self, **kw): page.checked.append(f"{role}:{name}")
        r = R(); r.first = r
        return r

    def get_by_label(self, name, exact=True):
        return self.get_by_role("label", name)


def _choice_plan():
    return plan(fields=[
        FilledField(key="email", label="Email", kind="email", required=True,
                    value="hello@krishraja.com", source="Info Bank"),
        FilledField(key="auth", label="Are you legally authorized to work?",
                    kind="boolean", required=True, value="Yes", source="Info Bank"),
        FilledField(key="sponsor", label="Will you require sponsorship?",
                    kind="single_select", required=True,
                    value="No, I do not require sponsorship", source="Info Bank"),
    ])


@pytest.fixture
def approved_choices(monkeypatch):
    state = {"writes": []}
    p = _choice_plan()
    row = {"token": "t1", "state": approval.APPROVED,
           "plan_hash": approval.plan_hash(p.as_dict()), "job_id": "harvey:x"}
    monkeypatch.setattr(approval, "get_row", lambda cfg, token: row)
    monkeypatch.setattr(approval, "set_state",
                        lambda cfg, token, st, **kw: state["writes"].append((st, kw)))
    state["plan"] = p
    return state


def test_a_dropdown_is_selected_and_a_checkbox_is_ticked(approved_choices):
    page = FormPage({
        "email": {"tag": "input", "type": "text"},
        "auth": {"tag": "input", "type": "checkbox"},
        "sponsor": {"tag": "select"},
    })
    out = submit(Cfg(), "t1", approved_choices["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["missed"] == []
    assert page.typed["email"] == "hello@krishraja.com"
    assert page.checked == ["auth"]
    # Chosen by the label a human reads, which is what resolve.py fitted it to.
    assert page.selected["sponsor"] == "No, I do not require sponsorship"
    assert out["state"] == approval.SUBMITTED


def test_a_radio_group_is_chosen_by_its_label(approved_choices):
    page = FormPage({
        "email": {"tag": "input", "type": "text"},
        "auth": {"tag": "input", "type": "radio"},
        "sponsor": {"tag": "input", "type": "radio"},
    })
    out = submit(Cfg(), "t1", approved_choices["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["missed"] == []
    assert set(page.checked) == {"auth", "sponsor"}


def test_a_combobox_is_opened_then_the_option_clicked(approved_choices):
    page = FormPage({
        "email": {"tag": "input", "type": "text"},
        "auth": {"tag": "div"},
        "sponsor": {"tag": "div"},
    })
    out = submit(Cfg(), "t1", approved_choices["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["missed"] == []
    # Opened the control, then took the option by its visible name.
    assert "auth" in page.clicked
    assert any(c.startswith("option:") for c in page.clicked)


def test_a_choice_field_the_driver_cannot_work_still_refuses_to_press(approved_choices):
    """The safe failure, and the one that would have happened on Harvey before
    the drivers learned anything but typing."""
    page = FormPage({"email": {"tag": "input", "type": "text"}})
    out = submit(Cfg(), "t1", approved_choices["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.QUEUED
    assert "field not found" in out["reason"]
    assert page.clicked == [] or "Submit Application" not in page.clicked


# ---------- preview cannot submit ----------

def test_preview_fills_and_photographs_without_an_approval(monkeypatch):
    """The approval email shows the real completed form, so approving the picture
    is approving what gets sent. That only works if filling needs no approval, and
    it is only safe if filling cannot possibly press."""
    called = []
    monkeypatch.setattr(approval, "get_row",
                        lambda cfg, token: called.append("gate") or None)
    page = FakePage()
    out = submit_mod.preview(plan(fields=[field()]), browser_factory=factory_for(page))
    assert out["filled"] == ["Email"]
    assert out["png"] == b"\x89PNG"
    assert page.clicked == []
    # The gate was never consulted, because there is nothing to gate.
    assert called == []


def test_preview_holds_no_way_to_press_submit():
    """Not a policy, a property of the compiled function.

    Checking the source text was the first attempt and it read the docstring,
    where both words appear precisely because the docstring explains why they are
    absent from the code. co_names is what the function actually references.
    """
    names = set(submit_mod.preview.__code__.co_names)
    assert "press_submit" not in names
    assert "confirm" not in names
    # And neither does the helper it delegates the page work to.
    assert "press_submit" not in set(submit_mod._open_and_fill.__code__.co_names)


def test_preview_reports_a_captcha_rather_than_pretending_it_filled():
    page = FakePage(html="<div class='h-captcha'></div>")
    out = submit_mod.preview(plan(fields=[field()]), browser_factory=factory_for(page))
    assert out["blocker"] == "captcha"
    assert out["filled"] == []


def test_preview_survives_a_dead_page_and_says_so():
    class Dead(FakePage):
        def goto(self, url, **kw): raise RuntimeError("navigation died")
    out = submit_mod.preview(plan(fields=[field()]),
                             browser_factory=factory_for(Dead()))
    assert "RuntimeError" in out["error"]
    assert out["png"] == b""


# ---------- a click is not an answer ----------

def test_a_segmented_yes_no_is_pressed_on_its_button_not_its_mirror():
    """Harvey's work-authorisation question, exactly as the live form renders it.

    Ashby draws Yes/No as two <button aria-pressed> and keeps a display:none
    checkbox behind them for serialisation. check() on that input fails
    actionability, so the driver fell through to an unverified label lookup,
    reported the field FILLED, and the approval picture went out with "are you
    legally authorized to work" blank. Nothing in the suite could see it, because
    the fake had no notion of a control that refuses to be checked.
    """
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox", "checkable": False,
                 "options_pressed": ["Yes", "No"]},
        "sponsor": {"tag": "select",
                    "options": ["No, I do not require sponsorship"]},
    })
    drv = submit_mod.AshbyDriver(page, _choice_plan())
    drv.fill()
    assert drv.missed == []
    assert "auth:Yes" in page.clicked
    assert "auth:No" not in page.clicked


def test_a_control_that_never_changes_is_reported_missed_not_filled():
    """The class of bug, not the instance.

    A checkbox that cannot be checked and has no buttons behind it has no way to
    carry the answer, and saying so is the whole point: submit() refuses to press
    on a required field it reports missed, and that refusal is worth nothing if
    the driver calls a failed click a fill.
    """
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox", "checkable": False},
        "sponsor": {"tag": "select",
                    "options": ["No, I do not require sponsorship"]},
    })
    drv = submit_mod.AshbyDriver(page, _choice_plan())
    drv.fill()
    assert "Are you legally authorized to work?" in drv.missed


def test_a_select_refuses_an_answer_the_form_does_not_offer():
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox"},
        "sponsor": {"tag": "select", "options": ["Something else entirely"]},
    })
    drv = submit_mod.AshbyDriver(page, _choice_plan())
    drv.fill()
    assert "Will you require sponsorship?" in drv.missed


# ---------- the typeahead that would have moved him to Minnesota ----------

class TypeaheadPage:
    """A combobox backed by a geocoder, like Ashby's Location field.

    `results` maps what is typed to the options offered back.
    """

    class _Keyboard:
        """Real key events, because that is what opens a react-select menu."""
        def __init__(self, page): self.page = page
        def type(self, text, **kw):
            self.page.typed.append(text)
            # A real box holds what was typed into it, which is what lets the
            # driver notice react-select eating the first characters.
            self.page.value = text
        def press(self, key, **kw):
            if key == "Backspace":
                self.page.value = ""

    def __init__(self, results: dict):
        self.results, self.typed, self.value = results, [], ""
        self.keyboard = TypeaheadPage._Keyboard(self)
        self.clicked_option = ""
        self.html = "<form></form>"

    def content(self): return self.html
    def goto(self, url, **kw): self.url = url
    def screenshot(self, **kw): return b"\x89PNG"

    def _options(self):
        return self.results.get(self.typed[-1] if self.typed else "", [])

    def locator(self, sel):
        page = self

        class L:
            first = None
            def count(self): return 1 if "combobox" in sel else 0
            def evaluate(self, expr): return "INPUT"
            def get_attribute(self, name):
                return {"role": "combobox", "aria-autocomplete": "list"}.get(name)
            def click(self, **kw): pass
            def fill(self, v, **kw): page.value = v
            def type(self, v, **kw):
                raise AssertionError(
                    "typed through the element; react-select needs key events")
            def input_value(self): return page.value
        l = L(); l.first = l
        return l

    def get_by_role(self, role, name="", exact=True):
        page = self
        opts = page._options()

        class Opt:
            def __init__(self, i): self.i = i
            def inner_text(self): return opts[self.i]
            def wait_for(self, **kw):
                if not opts:
                    raise RuntimeError("no options")
            def click(self, **kw):
                page.clicked_option = opts[self.i]
                page.value = opts[self.i]

        class R:
            first = Opt(0) if opts else Opt(-1)
            def count(self): return len(opts)
            def nth(self, i): return Opt(i)
        r = R()
        if not opts:
            class Empty:
                first = Opt(-1)
                def count(self): return 0
                def nth(self, i): raise IndexError
            return Empty()
        return r


def _location_plan():
    return plan(fields=[
        FilledField(key="_systemfield_location", label="Location", kind="location",
                    required=True, value="Brooklyn, New York, United States",
                    source="residence rule"),
    ])


def test_the_typeahead_never_takes_an_option_that_does_not_match():
    """The live near-miss that decided how strict this has to be.

    Ashby's geocoder holds no "Brooklyn, New York". Typing the whole answer
    offers New York City first; typing "brooklyn" alone offers BROOKLYN PARK,
    MINNESOTA first. A driver that clicks the first option would have put the
    wrong city on his application and reported the field filled.
    """
    page = TypeaheadPage({
        "Brooklyn, New York, United States": [
            "New York City, New York, United States", "New York, United States"],
        "brooklyn": ["Brooklyn Park, Minnesota, United States",
                     "Brooklyn Center, Minnesota, United States"],
    })
    drv = submit_mod.AshbyDriver(page, _location_plan())
    drv.fill()
    assert drv.missed == ["Location"]
    assert page.clicked_option == ""
    assert page.value == ""


def test_the_typeahead_records_what_the_form_actually_offered():
    """A field reported missed with no reason sends him back to the browser.

    The offered options are the fix that compounds: he corrects the answer bank
    once and every later run matches.
    """
    page = TypeaheadPage({
        "Brooklyn, New York, United States": [
            "New York City, New York, United States"],
        "brooklyn": ["Brooklyn Park, Minnesota, United States"],
    })
    drv = submit_mod.AshbyDriver(page, _location_plan())
    drv.fill()
    assert drv.notes
    assert "New York City, New York, United States" in drv.notes[0]
    assert "Location" in drv.notes[0]


def test_the_typeahead_takes_an_option_that_does_match():
    page = TypeaheadPage({
        "Brooklyn, New York, United States": [
            "Brooklyn, New York, United States", "New York, United States"],
    })
    drv = submit_mod.AshbyDriver(page, _location_plan())
    drv.fill()
    assert drv.filled == ["Location"]
    assert page.value == "Brooklyn, New York, United States"
    assert drv.notes == []


def test_a_checkbox_that_reverts_is_reported_missed():
    """A click that does not raise is not an answer.

    React-controlled inputs accept check() and revert on the next render, which
    is indistinguishable from success unless the control is read back. Mutation:
    returning True instead of _is_checked(el) leaves every other test green.
    """
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox", "reverts": True},
        "sponsor": {"tag": "select",
                    "options": ["No, I do not require sponsorship"]},
    })
    drv = submit_mod.AshbyDriver(page, _choice_plan())
    drv.fill()
    assert "Are you legally authorized to work?" in drv.missed


def test_a_segmented_button_that_does_not_stay_pressed_is_reported_missed():
    """Same rule one layer out: the button group is read back too.

    Mutation: returning True from _press_group instead of reading aria-pressed
    leaves every other test green.
    """
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox", "checkable": False,
                 "options_pressed": ["Yes", "No"], "press_reverts": True},
        "sponsor": {"tag": "select",
                    "options": ["No, I do not require sponsorship"]},
    })
    drv = submit_mod.AshbyDriver(page, _choice_plan())
    drv.fill()
    assert "Are you legally authorized to work?" in drv.missed


def test_the_form_is_waited_for_before_it_is_filled():
    """Ashby fetches its form after the document loads.

    Without the wait, goto returns a page showing "Fetching application form",
    the driver finds zero controls, every field is missed, and the approval email
    carries a photograph of a spinner. That is exactly what the first live run
    produced. Mutation: deleting the wait_for_selector leaves every other test
    green.
    """
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox",
                 "options_pressed": ["Yes", "No"], "checkable": False},
        "sponsor": {"tag": "select",
                    "options": ["No, I do not require sponsorship"]},
    }, ready=False)
    out = submit_mod.preview(_choice_plan(), browser_factory=factory_for(page))
    assert page.waited
    assert out["missed"] == []


def test_the_file_goes_on_before_the_fields_are_typed():
    """Ashby parses the uploaded CV and autofills from it.

    That parse lands after the upload, so filling first and attaching second let
    the vendor's guesses overwrite the answers Krish approved, and photographed
    the form mid-parse besides. Mutation: swapping _attach and driver.fill back
    leaves every other test green.
    """
    page = FormPage({
        "email": {"tag": "input", "type": "email"},
        "auth": {"tag": "input", "type": "checkbox",
                 "options_pressed": ["Yes", "No"], "checkable": False},
        "sponsor": {"tag": "select",
                    "options": ["No, I do not require sponsorship"]},
        'type="file"': {"tag": "input", "type": "file"},
    })
    fields = list(_choice_plan().fields) + [
        FilledField(key="resume", label="Resume", kind="file_resume",
                    required=True, value="the built PDF", source="package")]
    submit_mod.preview(plan(fields=fields), attachments={"file_resume": b"%PDF"},
                       browser_factory=factory_for(page))
    assert page.order[0] == "attach", page.order
    assert "fill:email" in page.order


def test_the_parse_is_waited_out_before_the_fields_are_typed():
    """A form still announcing "Parsing your resume" is a form about to change."""
    class Parsing(FormPage):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.polls = 0

        def content(self):
            self.polls += 1
            return ("<div>Parsing your resume. Autofilling key fields...</div>"
                    if self.polls < 3 else "<form></form>")

    page = Parsing({"email": {"tag": "input", "type": "email"},
                    'type="file"': {"tag": "input", "type": "file"}})
    fields = [field(), FilledField(key="resume", label="Resume",
                                   kind="file_resume", required=True,
                                   value="the built PDF", source="package")]
    submit_mod.preview(plan(fields=fields), attachments={"file_resume": b"%PDF"},
                       browser_factory=factory_for(page))
    assert page.polls >= 3


def _upload_plan():
    return plan(fields=[
        field(),
        FilledField(key="resume", label="Resume", kind="file_resume",
                    required=True, value="the built PDF", source="package")])


def _upload_page():
    return FormPage({"email": {"tag": "input", "type": "email"},
                     'type="file"': {"tag": "input", "type": "file"}})


def test_the_employer_sees_the_document_named_as_canon_names_it():
    """mkstemp produced hunter_9ugny06m.pdf and Ashby showed exactly that in the
    resume slot. The filename is part of what the employer receives."""
    page = _upload_page()
    submit_mod.preview(_upload_plan(), attachments={"file_resume": b"%PDF"},
                       names={"file_resume": "KrishRaja_Application_Harvey.pdf"},
                       browser_factory=factory_for(page))
    assert page.uploaded == ["KrishRaja_Application_Harvey.pdf"]
    assert page.upload_alive, "the file was deleted before the browser read it"


def test_an_unnamed_attachment_still_gets_a_readable_name():
    page = _upload_page()
    submit_mod.preview(_upload_plan(), attachments={"file_resume": b"%PDF"},
                       browser_factory=factory_for(page))
    assert page.uploaded == ["KrishRaja_CV.pdf"]
    assert page.upload_alive, "the file was deleted before the browser read it"


def test_a_rejected_upload_is_reported_missed_not_filled():
    """set_input_files succeeding means the browser took the path, not that the
    employer has the document. Ashby answered "Oops! Failed to fetch" and the
    driver counted the resume filled."""
    class Rejecting(FormPage):
        def content(self):
            return ("<div>Oops! Failed to fetch</div>" if self.uploaded
                    else "<form></form>")

    page = Rejecting({"email": {"tag": "input", "type": "email"},
                      'type="file"': {"tag": "input", "type": "file"}})
    out = submit_mod.preview(_upload_plan(), attachments={"file_resume": b"%PDF"},
                             browser_factory=factory_for(page))
    assert "Resume" in out["missed"]
    assert "Resume" not in out["filled"]
    assert out["notes"]


# ---------- which option is the answer ----------

def _london_plan():
    return plan(fields=[
        FilledField(key="_systemfield_location", label="Location", kind="location",
                    required=True, value="London, United Kingdom",
                    source="residence rule")])


def test_london_matches_the_geocoders_longer_name():
    """Ashby offers "London, Greater London, England, United Kingdom", which does
    not contain "London, United Kingdom" as text at all. Every segment of the
    answer is in it, in order, which is what makes it the same place."""
    page = TypeaheadPage({"London, United Kingdom": [
        "London, Greater London, England, United Kingdom", "United Kingdom"]})
    drv = submit_mod.AshbyDriver(page, _london_plan())
    drv.fill()
    assert drv.filled == ["Location"]
    assert page.value == "London, Greater London, England, United Kingdom"


def test_london_ontario_is_never_taken():
    """The same search offers London, Ontario, Canada. Segments in order rule it
    out: it has London and no United Kingdom after it."""
    page = TypeaheadPage({"London, United Kingdom": [
        "London, Ontario, Canada", "London, Kentucky, United States"]})
    drv = submit_mod.AshbyDriver(page, _london_plan())
    drv.fill()
    assert drv.missed == ["Location"]
    assert page.clicked_option == ""


def test_new_york_prefers_the_exact_option():
    """Both are the right place. The exact one is the safer of the two."""
    page = TypeaheadPage({"New York, United States": [
        "New York City, New York, United States", "New York, United States"]})
    drv = submit_mod.AshbyDriver(page, plan(fields=[
        FilledField(key="_systemfield_location", label="Location",
                    kind="location", required=True,
                    value="New York, United States", source="residence rule")]))
    drv.fill()
    assert drv.filled == ["Location"]
    assert page.value == "New York, United States"


def test_an_exact_option_beats_a_segment_match_earlier_in_the_list():
    assert submit_mod._best_option(
        ["New York City, New York, United States", "New York, United States"],
        "new york, united states") == 1
    # With no exact option, the geocoder's own top-ranked segment match wins.
    assert submit_mod._best_option(
        ["New York City, New York, United States",
         "New York metropolitan area, United States"],
        "new york, united states") == 0
    assert submit_mod._best_option(
        ["New York metropolitan area, United States", "New York, United States"],
        "new york, united states") == 1


def test_a_failed_typeahead_leaves_nothing_half_typed():
    """An unconfirmed combobox holding "Brooklyn" reads on the approval picture
    as an answer, and is not one."""
    page = TypeaheadPage({
        "Brooklyn, New York, United States": ["New York City, New York, United States"],
        "Brooklyn": ["Brooklyn Park, Minnesota, United States"]})
    drv = submit_mod.AshbyDriver(page, _location_plan())
    drv.fill()
    assert drv.missed == ["Location"]
    assert page.value == ""


# ---------- Greenhouse ----------

def test_greenhouse_loads_the_host_greenhouse_actually_serves():
    """boards.greenhouse.io answers a live posting with a redirect to the board
    carrying ?error=true, so every Greenhouse run loaded a search box and filled
    nothing. The postings are on job-boards.greenhouse.io."""
    drv = submit_mod.GreenhouseDriver(FormPage({}), plan(ats="greenhouse"))
    url = drv.apply_url()
    assert url.startswith("https://job-boards.greenhouse.io/")
    assert "boards.greenhouse.io/" in url and "//boards.greenhouse.io" not in url


def test_greenhouse_puts_each_document_in_its_own_slot():
    """Greenhouse gives the resume and the cover letter separate inputs, so
    `input[type=file]`.first put both in the resume slot."""
    page = FormPage({"#resume": {"tag": "input", "type": "file"},
                     "#cover_letter": {"tag": "input", "type": "file"}})
    fields = [
        FilledField(key="resume", label="Resume/CV", kind="file_resume",
                    required=True, value="the built PDF", source="package"),
        FilledField(key="cover_letter", label="Cover Letter", kind="file_cover",
                    required=True, value="the built letter", source="package")]
    submit_mod.preview(
        plan(ats="greenhouse", fields=fields),
        attachments={"file_resume": b"%PDF-cv", "file_cover": b"%PDF-letter"},
        names={"file_resume": "KrishRaja_CV.pdf",
               "file_cover": "KrishRaja_CoverLetter.pdf"},
        browser_factory=factory_for(page))
    assert sorted(page.uploaded) == ["KrishRaja_CV.pdf",
                                     "KrishRaja_CoverLetter.pdf"]


class PageWithHiddenRequired(FormPage):
    """A form carrying a required control the vendor's API never reported."""

    def __init__(self, controls, still_empty):
        super().__init__(controls)
        self.still_empty = still_empty

    def evaluate(self, expr):
        return list(self.still_empty)


def test_a_required_control_the_api_never_mentioned_still_stops_the_press(approved):
    """Greenhouse's board API omits `country` and `candidate-location`, both
    required on the live page. A plan built from that API says every required
    field has an answer while the form has two empty ones, which is exactly the
    condition submit.py exists to prevent. The page is asked, not the plan."""
    page = PageWithHiddenRequired({"email": {"tag": "input", "type": "email"}},
                                  ["Country"])
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.QUEUED
    assert "Country" in out["reason"]
    assert page.clicked == []


def test_a_page_with_nothing_empty_is_not_stopped(approved):
    page = PageWithHiddenRequired({"email": {"tag": "input", "type": "email"}}, [])
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["pressed"] is True


def test_a_field_a_vendor_blanks_behind_us_is_filled_again():
    """Greenhouse parses the uploaded CV and writes its own answers into the name
    and email boxes, and that write lands after the upload rather than with it.
    The first pass filled all four, the parser blanked all four, and the form went
    to the approval picture with no name on it while the driver reported success.
    """
    class Clearing(FormPage):
        """Blanks every text box once, the way a resume parser does."""
        def __init__(self, controls):
            super().__init__(controls)
            self.wiped = False

        def locator(self, sel):
            loc = super().locator(sel)
            page = self
            inner = loc.input_value

            def input_value():
                if not page.wiped:
                    page.wiped = True
                    page.typed.clear()
                    return ""
                return inner()
            loc.input_value = input_value
            return loc

    page = Clearing({"email": {"tag": "input", "type": "email"}})
    drv = submit_mod.GreenhouseDriver(page, plan(ats="greenhouse", fields=[field()]))
    drv.fill()
    assert drv.filled == ["Email"], drv.missed
    assert page.typed["email"] == "hello@krishraja.com"


def test_a_field_that_stays_empty_after_the_second_pass_is_reported_missed():
    class Dead(FormPage):
        def locator(self, sel):
            loc = super().locator(sel)
            loc.input_value = lambda: ""
            return loc

    page = Dead({"email": {"tag": "input", "type": "email"}})
    drv = submit_mod.GreenhouseDriver(page, plan(ats="greenhouse", fields=[field()]))
    drv.fill()
    assert drv.missed == ["Email"]


def test_a_form_that_shows_no_filename_did_not_take_the_document():
    """Greenhouse refuses silently: the bytes sit on a visually-hidden input its
    own React never reads, the Attach button still reads Attach, the filename
    appears nowhere, and the application would go out with no CV. set_input_files
    returning without raising is not the form having the document."""
    page = FormPage({'type="file"': {"tag": "input", "type": "file"}})
    out = submit_mod.preview(_upload_plan(), attachments={"file_resume": b"%PDF"},
                             names={"file_resume": "KrishRaja_CV.pdf"},
                             browser_factory=factory_for(page))
    assert "Resume" in out["missed"]
    assert "Resume" not in out["filled"]
    assert "appears nowhere" in out["notes"][0]


def test_a_form_that_shows_the_filename_kept_the_document():
    class Showing(FormPage):
        def content(self):
            return ("<div>KrishRaja_CV.pdf</div>" if self.uploaded
                    else "<form></form>")

    page = Showing({'type="file"': {"tag": "input", "type": "file"}})
    out = submit_mod.preview(_upload_plan(), attachments={"file_resume": b"%PDF"},
                             names={"file_resume": "KrishRaja_CV.pdf"},
                             browser_factory=factory_for(page))
    assert out["filled"] == ["Resume"]
    assert out["notes"] == []


def test_a_page_with_two_file_inputs_still_gets_the_document():
    """Ashby's page carries two file inputs. Requiring a file selector to resolve
    to exactly one meant the CV was never uploaded at all, while the run reported
    thirteen fields filled and mailed a picture of a form with an empty slot."""
    class TwoSlots(FormPage):
        def locator(self, sel):
            loc = super().locator(sel)
            if 'type="file"' in sel:
                loc.count = lambda: 2
            return loc

        def content(self):
            return ("<div>KrishRaja_CV.pdf</div>" if self.uploaded
                    else "<form></form>")

    page = TwoSlots({'type="file"': {"tag": "input", "type": "file"}})
    out = submit_mod.preview(_upload_plan(), attachments={"file_resume": b"%PDF"},
                             names={"file_resume": "KrishRaja_CV.pdf"},
                             browser_factory=factory_for(page))
    assert out["filled"] == ["Resume"], out["missed"]


def test_ashby_puts_the_cv_in_the_application_slot_not_the_autofill_box():
    """Ashby's page carries a second file input: the "Autofill from resume" box
    above the form. It is first in document order, so a generic selector put the
    CV there and left the application's own required Resume slot empty. The
    autofill box then copied the file into the visible slot, so the picture
    looked right while the required input held nothing."""
    class TwoSlots(FormPage):
        def locator(self, sel):
            loc = super().locator(sel)
            if sel == 'input[type="file"]':
                loc.count = lambda: 2   # the autofill box and the real slot
            return loc

        def content(self):
            return ("<div>KrishRaja_CV.pdf</div>" if self.uploaded
                    else "<form></form>")

    page = TwoSlots({'#_systemfield_resume': {"tag": "input", "type": "file"},
                     'type="file"': {"tag": "input", "type": "file"}})
    fields = [FilledField(key="_systemfield_resume", label="Resume",
                          kind="file_resume", required=True,
                          value="the built PDF", source="package")]
    out = submit_mod.preview(plan(fields=fields),
                             attachments={"file_resume": b"%PDF"},
                             names={"file_resume": "KrishRaja_CV.pdf"},
                             browser_factory=factory_for(page))
    assert out["filled"] == ["Resume"], out["missed"]
    assert "_systemfield_resume" in page.upload_selectors[0], page.upload_selectors


def test_submit_waits_for_the_form_like_preview_does(approved):
    """submit() used to re-implement the open-and-fill flow instead of calling
    it, and the copy had drifted: it never waited for the form to render. On
    Ashby the resume slot did not exist yet when the file was attached, the CV
    went nowhere, and the gate refused the send with "field not found: Resume"
    on a form it could have filled. One idea of what filling means.
    """
    page = FormPage({"email": {"tag": "input", "type": "email"}}, ready=False)
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert page.waited
    assert out["pressed"] is True, out.get("reason")


# ---------- the press is not the proof ----------

class PressPage(FormPage):
    """A page that says something, or nothing, once the button is pressed."""

    def __init__(self, controls, after="", url="https://x/apply"):
        super().__init__(controls)
        self.after, self.url, self.pressed = after, url, False

    def content(self):
        return self.after if self.pressed else "<form></form>"

    def get_by_role(self, role, name="", exact=True):
        page = self

        class R:
            first = None
            def click(self, **kw):
                page.clicked.append(f"{role}:{name}")
                page.pressed = True
        r = R(); r.first = r
        return r


def test_an_acknowledged_submission_quotes_what_the_form_said(approved):
    page = PressPage({"email": {"tag": "input", "type": "email"}},
                     after="<h1>Thanks for applying!</h1>")
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.SUBMITTED
    assert out["confirmation"] == "thanks for applying"


def test_a_press_with_no_acknowledgement_says_so(approved):
    """"The click did not raise" is not evidence the employer has anything.
    press_submit() returned and the very next line recorded SUBMITTED.

    The state stays SUBMITTED, because the button WAS pressed and retrying an
    application that did land is the one unrecoverable mistake here. What changes
    is what Krish is told.
    """
    page = PressPage({"email": {"tag": "input", "type": "email"}},
                     after="<form>still the form</form>")
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["state"] == approval.SUBMITTED
    assert out["confirmation"] == ""
    assert (approved["writes"][-1][1]["failure_reason"]
            == "pressed, no confirmation seen")


def test_leaving_the_form_behind_counts_as_acknowledgement(approved):
    class Navigating(PressPage):
        def content(self):
            return "<div>done</div>" if self.pressed else "<form></form>"

        @property
        def url(self):
            return "https://x/confirmation" if self.pressed else "https://x/apply"

        @url.setter
        def url(self, _v):
            pass

    page = Navigating({"email": {"tag": "input", "type": "email"}})
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert "confirmation" in out["confirmation"]


def test_an_unconfirmed_press_on_a_scored_form_explains_itself(approved):
    """Harvey's form runs invisible reCAPTCHA v3, which scores the visitor rather
    than asking anything. A headless run in a datacentre fails that score and is
    refused server side with nothing shown, which is what the first pressed
    application looked like from here. Say so rather than leaving a mystery."""
    page = PressPage({"email": {"tag": "input", "type": "email"}},
                     after="<form><script src='recaptcha__en.js'></script></form>")
    out = submit(Cfg(), "t1", approved["plan"], confirm=True,
                 browser_factory=factory_for(page))
    assert out["confirmation"] == ""
    assert "invisible bot check" in approved["writes"][-1][1]["failure_reason"]


# ---------- the form, left on screen for him to press ----------

class LocalBrowser:
    """A Chrome that was already running, attached to rather than launched."""

    def __init__(self, page):
        self.page, self.closed, self.contexts = page, False, []

    def close(self):
        self.closed = True


class LocalCtx:
    def __init__(self, page): self.page = page
    def new_page(self): return self.page


def connector_for(page, seen=None):
    class PW:
        def __init__(self):
            self.chromium = self
        def connect_over_cdp(self, url):
            if seen is not None:
                seen.append(url)
            b = LocalBrowser(page)
            b.contexts = [LocalCtx(page)]
            return b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return PW


def test_the_local_form_is_filled_and_left_open(tmp_path):
    page = FormPage({"email": {"tag": "input", "type": "email"},
                     'type="file"': {"tag": "input", "type": "file"}})
    page.content = lambda: "<div>KrishRaja_CV.pdf</div>" if page.uploaded else "<form></form>"
    out = submit_mod.open_for_human(
        _upload_plan(), attachments={"file_resume": b"%PDF"},
        names={"file_resume": "KrishRaja_CV.pdf"},
        cdp_url="http://127.0.0.1:9222", keep_dir=str(tmp_path),
        connector=connector_for(page))
    assert out["error"] == "" and out["blocker"] == ""
    assert "Resume" in out["filled"]
    assert page.clicked == [], "nothing may be pressed"


def test_the_local_flow_holds_no_way_to_press_submit():
    """Same guarantee preview() carries, and for the same reason: the last click
    on a job application is Krish's. Read off the bytecode rather than the
    docstring, which a comment could otherwise satisfy."""
    names = submit_mod.open_for_human.__code__.co_names
    assert "press_submit" not in names
    assert "_open_and_fill" not in names


def test_the_attached_file_outlives_the_process(tmp_path):
    """hunter exits while the browser is still holding the form open, and the
    file is read when he presses submit, not when it was attached. Deleting it on
    the way out is how the upload becomes "failed to fetch" minutes later."""
    import os
    page = FormPage({"email": {"tag": "input", "type": "email"},
                     'type="file"': {"tag": "input", "type": "file"}})
    page.content = lambda: "<div>KrishRaja_CV.pdf</div>" if page.uploaded else "<form></form>"
    out = submit_mod.open_for_human(
        _upload_plan(), attachments={"file_resume": b"%PDF"},
        names={"file_resume": "KrishRaja_CV.pdf"},
        cdp_url="http://x", keep_dir=str(tmp_path),
        connector=connector_for(page))
    assert out["files"] and all(os.path.exists(f) for f in out["files"])
    assert out["files"][0].endswith("KrishRaja_CV.pdf")


def test_an_existing_browser_is_attached_to_not_launched(tmp_path):
    """Attaching is the whole point: a browser Playwright LAUNCHES reports
    navigator.webdriver true, which is what an invisible bot check scores and
    refuses. One started normally does not."""
    seen = []
    page = FormPage({"email": {"tag": "input", "type": "email"}})
    submit_mod.open_for_human(plan(fields=[field()]), cdp_url="http://127.0.0.1:9222",
                              keep_dir=str(tmp_path),
                              connector=connector_for(page, seen))
    assert seen == ["http://127.0.0.1:9222"]


def test_a_blocked_page_is_reported_rather_than_filled(tmp_path):
    page = FormPage({"email": {"tag": "input", "type": "email"}})
    page.content = lambda: "<div class='h-captcha'></div>"
    page.locator = lambda sel: FakePage(html="<div class='h-captcha'></div>").locator(sel)
    out = submit_mod.open_for_human(plan(fields=[field()]), cdp_url="http://x",
                                    keep_dir=str(tmp_path),
                                    connector=connector_for(page))
    assert out["blocker"] == "captcha"


def test_an_already_running_chrome_is_reused(monkeypatch):
    """Starting a second Chrome on the same profile fails, and reusing the
    browser he already has open is the whole point."""
    started = []
    monkeypatch.setattr(submit_mod, "_port_open", lambda port: True)
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: started.append(a) or None)
    submit_mod._start_local_chrome("chrome", "", 9222)
    assert started == []


def test_a_browser_that_never_opens_its_port_says_so(monkeypatch):
    """ECONNREFUSED with no explanation is what a fixed sleep gives you on a
    cold start."""
    monkeypatch.setattr(submit_mod, "_port_open", lambda port: False)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr(submit_mod.time, "sleep", lambda s: None)
    with pytest.raises(SubmitBlocked) as e:
        submit_mod._start_local_chrome("chrome", "", 9222)
    assert "debugging port" in str(e.value)


def test_a_running_browser_needs_no_chrome_on_disk(tmp_path, monkeypatch):
    """Asking where Chrome is installed while Chrome is already running and
    waiting on that port is a refusal with no cause."""
    monkeypatch.setattr(submit_mod, "_port_open", lambda port: True)
    def no_chrome(_=""):
        raise SubmitBlocked("no Chrome found")
    monkeypatch.setattr(submit_mod, "_chrome_binary", no_chrome)
    page = FormPage({"email": {"tag": "input", "type": "email"}})
    out = submit_mod.open_for_human(plan(fields=[field()]), keep_dir=str(tmp_path),
                                    connector=connector_for(page))
    assert out["error"] == "" and out["filled"] == ["Email"]


def test_an_unknown_ats_does_not_kill_the_batch():
    """driver_for was called before preview's guard, so one LinkedIn row raised
    straight out and twenty-three applications went unsent because one of them
    was not a kind of form this can open."""
    out = submit_mod.preview(plan(ats="linkedin", fields=[field()]))
    assert "no driver" in out["error"]
    assert out["filled"] == []


def test_an_unknown_ats_does_not_kill_the_local_flow(tmp_path):
    out = submit_mod.open_for_human(plan(ats="linkedin", fields=[field()]),
                                    keep_dir=str(tmp_path))
    assert "no driver" in out["error"]


def test_it_finds_the_chromium_that_is_actually_installed(tmp_path, monkeypatch):
    """A pinned playwright and a preinstalled browser disagree about the build
    number more often than not, and this has now cost two runs: the first failed
    loudly, the second quietly sent an approval email with no screenshot and no
    "still empty and required" list. So an unset HUNTER_CHROMIUM_PATH no longer
    means give up.

    Newest build wins, compared as a number: chromium-1194 sorts before
    chromium-234 as text, which would pick the older browser.
    """
    for build in ("234", "1194"):
        exe = tmp_path / f"chromium-{build}" / "chrome-linux" / "chrome"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert submit_mod._chromium_on_disk() == str(
        tmp_path / "chromium-1194" / "chrome-linux" / "chrome")


def test_nothing_on_disk_raises_the_real_error(tmp_path, monkeypatch):
    """A missing browser must not be swallowed into a mystery. The fallback is a
    second chance, not a way to lose the reason."""
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.delenv("HUNTER_CHROMIUM_PATH", raising=False)
    assert submit_mod._chromium_on_disk() == ""

    class Boom:
        class chromium:
            @staticmethod
            def launch(**kw):
                raise RuntimeError("Executable doesn't exist at /opt/nope")

    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        submit_mod._launch(Boom)


def test_an_explicit_executable_still_wins(monkeypatch):
    """HUNTER_CHROMIUM_PATH is how a deliberate choice is made, so the search
    must never override it."""
    monkeypatch.setenv("HUNTER_CHROMIUM_PATH", "/my/own/chrome")
    seen = {}

    class Stub:
        class chromium:
            @staticmethod
            def launch(**kw):
                seen.update(kw)
                return "browser"

    assert submit_mod._launch(Stub) == "browser"
    assert seen["executable_path"] == "/my/own/chrome"
