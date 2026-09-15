"""The submitter, offline. No browser, no network, no employer.

Every test here is about the same question: can hunter put an application in
front of a company when it should not. The happy path is one test; the refusals
are the rest, because a wrong submission cannot be taken back.
"""
import pytest

from hunter.apply import approval
from hunter.apply.fill import FillPlan, FilledField
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
        class L:
            first = None
            def count(self):
                # Case insensitive: a field's selectors mix its key and its label,
                # so `input[name="email"]` and `input[aria-label="Email"]` are the
                # same field. Matching only the lowercase form let one selector
                # through and the fake reported a fill that had not happened.
                low = sel.lower()
                return 0 if any(f.lower() in low for f in page.fails) else 1
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
    page = FakePage(html="<div class='g-recaptcha'>recaptcha</div>")
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
    assert page_blocker(FakePage(html="<div>hcaptcha</div>")) == "captcha"
    assert page_blocker(FakePage(html="<p>ordinary form</p>")) == ""


def test_a_captcha_that_appears_only_after_filling_still_stops_the_press(approved):
    """The common case, and the reason the blocker is checked twice. A page that
    is clean on arrival and shows a challenge once the form is complete would
    otherwise be discovered by clicking submit into it.

    Mutation-tested: deleting the second check leaves every other test green.
    """
    class LateCaptcha(FakePage):
        def content(self):
            # Clean until something has been filled, then challenged.
            return ("<div>recaptcha</div>" if self.filled else "<form></form>")

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

    def __init__(self, controls: dict, role_names=()):
        self.controls = controls
        # Labels a last-resort get_by_role/get_by_label lookup would actually find.
        self.role_names = set(role_names)
        self.selected: dict[str, str] = {}
        self.checked: list[str] = []
        self.typed: dict[str, str] = {}
        self.clicked: list[str] = []

    def content(self): return "<form></form>"
    def goto(self, url, **kw): self.url = url
    def screenshot(self, **kw): return b"\x89PNG"

    def _spec(self, sel):
        for frag, spec in self.controls.items():
            if frag in sel:
                return frag, spec
        return None, None

    def locator(self, sel):
        page, frag, spec = self, *self._spec(sel)

        class L:
            first = None
            def count(self): return 1 if spec else 0
            def evaluate(self, expr): return spec["tag"].upper()
            def get_attribute(self, name):
                return spec.get("type") if name == "type" else None
            def select_option(self, label=None, **kw): page.selected[frag] = label
            def check(self, **kw): page.checked.append(frag)
            def click(self, **kw): page.clicked.append(frag)
            def fill(self, v, **kw): page.typed[frag] = v
            def set_input_files(self, p, **kw): pass
        l = L(); l.first = l
        return l

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
