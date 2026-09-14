"""A dummy end to end rehearsal of the application loop, touching nothing real.

Krish's instruction 2026-09-14: a test dummy simulation before the first real dry
run. This is that. It uses a fabricated posting at a fabricated company, so:

  - no company is contacted, and none could be: the form is a literal, not a fetch
  - no document is built in Drive
  - no row on the Pipeline sheet is read or written
  - the email, if sent, goes to Krish's own mailbox like every other one

What it DOES exercise for real is the chain that matters: resolving every field
type against the live answer bank, matching answers onto a select's own options,
the residence rule, the flagged items, the plan hash, the approval email in both
HTML and text, the outbound marker, and the reply parser on approve, amend and
ambiguous replies. If the rehearsal is clean, the only untested thing left in the
real run is the document build.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import approval, fill, inbox
from .infobank import AnswerBank
from .model import FormField, FormSpec, Option

DUMMY_COMPANY = "Testco (simulation, not a real company)"
DUMMY_ROLE = "Head of Nothing At All"
DUMMY_URL = "https://example.invalid/simulation/not-a-real-posting"

# One field of every kind the harvest actually found across the 20 live forms, so
# the rehearsal covers the same surface a real posting would.
DUMMY_FIELDS = (
    FormField("_systemfield_name", "Legal First and Last Name", "name", True),
    FormField("pref", "Preferred First Name", "name", False),
    FormField("_systemfield_email", "Email", "email", True),
    FormField("phone", "Phone Number", "phone", True),
    FormField("_systemfield_location", "Location", "location", True),
    FormField("_systemfield_resume", "Resume", "file_resume", True),
    FormField("cl", "Cover Letter", "file_cover", False),
    FormField("li", "LinkedIn", "url", True),
    FormField("auth", "Are you legally authorized to work in the United States?",
              "boolean", True),
    FormField("spon", "Will you now or in the future require visa sponsorship?",
              "single_select", True,
              options=(Option("Yes, I will require sponsorship", "y"),
                       Option("No, I do not require sponsorship now or in the "
                              "future.", "n"))),
    FormField("office", "Are you able to work from our US office three days per "
                        "week?", "boolean", True),
    FormField("states", "Do you currently reside in any of the following states: "
                        "DE, HI, IA, KY, MS, NE, NM, SD, VT, WV, WY?",
              "boolean", True),
    FormField("salary", "What are your salary expectations?", "number", True),
    FormField("start", "When can you start a new role?", "date", True),
    FormField("heard", "How did you hear about us?", "single_select", False,
              options=(Option("LinkedIn", "1"), Option("Referral", "2"),
                       Option("Other", "3"))),
    FormField("why", "Why do you want to work at Testco?", "long_text", True),
    FormField("consent", "Applicant Arbitration Agreement Acknowledgement",
              "consent", True),
    FormField("race", "Race / ethnicity", "demographic", False,
              options=(Option("Asian (Not Hispanic or Latino)", "1"),
                       Option("Two or More Races (Not Hispanic or Latino)", "2"),
                       Option("Decline To Self Identify", "3"))),
    FormField("gender", "Gender", "demographic", False),
    FormField("vet", "Veteran status", "demographic", False),
    FormField("dis", "Disability status", "demographic", False),
)

DUMMY_SPEC = FormSpec(ats="ashby", slug="testco-simulation",
                      posting_id="00000000-0000-0000-0000-000000000000",
                      title=DUMMY_ROLE, fields=DUMMY_FIELDS)


@dataclass
class Result:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    plan: fill.FillPlan | None = None
    email: approval.ApprovalEmail | None = None

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, bool(ok), detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]


def run(bank: AnswerBank, *, to: str, role_location: str = "New York, NY") -> Result:
    """Rehearse the whole chain against the live answer bank. No network."""
    r = Result()

    plan = fill.build_payload(
        DUMMY_SPEC, bank, company=DUMMY_COMPANY, role=DUMMY_ROLE,
        jd_url=DUMMY_URL, role_location=role_location,
        attachment_style="CV+CL",
        essays={"Why do you want to work at Testco?":
                "(simulation placeholder, a real role gets a drafted answer)"})
    r.plan = plan

    by_label = {f.label: f for f in plan.fields}

    def answered(label: str) -> str:
        f = by_label.get(label)
        return (f.value if f else "")

    r.add("every field on the form is accounted for",
          len(plan.fields) == len(DUMMY_FIELDS),
          f"{len(plan.fields)} of {len(DUMMY_FIELDS)}")
    r.add("legal name resolved", answered("Legal First and Last Name") != "",
          answered("Legal First and Last Name"))
    r.add("email resolved", "@" in answered("Email"), answered("Email"))
    r.add("phone resolved", answered("Phone Number") != "",
          answered("Phone Number"))
    r.add("US work authorisation is Yes",
          answered("Are you legally authorized to work in the United States?")
          == "Yes")
    spon = answered("Will you now or in the future require visa sponsorship?")
    r.add("sponsorship answered with one of the form's own options",
          spon.startswith("No, I do not require sponsorship"), spon[:60])
    r.add("residence resolved from the role's location",
          "New York" in answered("Location"), answered("Location"))
    states = answered("Do you currently reside in any of the following states: "
                      "DE, HI, IA, KY, MS, NE, NM, SD, VT, WV, WY?")
    r.add("the state list that excludes NY answers No", states == "No", states)
    r.add("a required salary field gets the recorded floor",
          answered("What are your salary expectations?").isdigit(),
          answered("What are your salary expectations?"))
    heard = answered("How did you hear about us?")
    r.add("how did you hear matched an option the form offers",
          heard in ("LinkedIn", "Referral", "Other"), heard)
    race = answered("Race / ethnicity")
    r.add("race matched the form's own wording",
          race.startswith("Two or More Races"), race)
    r.add("consent answered from the bank, not assumed",
          answered("Applicant Arbitration Agreement Acknowledgement") != ""
          or by_label["Applicant Arbitration Agreement Acknowledgement"].unresolved,
          answered("Applicant Arbitration Agreement Acknowledgement")
          or "left to Krish")
    r.add("both attachments are accounted for",
          all(by_label[k].value for k in ("Resume", "Cover Letter")))
    r.add("nothing required is left unanswered", plan.ready,
          ", ".join(f.label[:40] for f in plan.blocking) or "none")
    r.add("flagged items exist and will be shown",
          len(plan.flagged) > 0, f"{len(plan.flagged)} flagged")

    # The plan hash has to be stable over reserialisation and sensitive to values.
    h1 = approval.plan_hash(plan.as_dict())
    h2 = approval.plan_hash(fill.build_payload(
        DUMMY_SPEC, bank, company=DUMMY_COMPANY, role=DUMMY_ROLE,
        jd_url=DUMMY_URL, role_location=role_location,
        attachment_style="CV+CL",
        essays={"Why do you want to work at Testco?":
                "(simulation placeholder, a real role gets a drafted answer)"}
    ).as_dict())
    r.add("plan hash is stable across two identical builds", h1 == h2, h1)
    mutated = dict(plan.as_dict())
    mutated["fields"] = dict(mutated["fields"], phone="+1 000 000 0000")
    r.add("plan hash changes when an answer changes",
          approval.plan_hash(mutated) != h1)

    token = approval.new_token("testco:simulation")
    email = approval.render(
        company=plan.company, role=plan.role, jd_url=plan.jd_url,
        autonomy="Full (simulated)", token=token, to=to,
        lines=plan.field_lines(), essays=plan.essays,
        summary="(simulation: a real role gets prose generated per JD)",
        hook="(simulation: a real role gets prose generated per JD)",
        cv_url="https://example.invalid/cv", letter_url="https://example.invalid/cl",
        notes=["SIMULATION. Testco is not a real company and this posting does "
               "not exist. Replying APPROVE to this email does nothing."])
    r.email = email

    r.add("the email carries the outbound marker",
          inbox.OUTBOUND_MARKER in email.text and inbox.OUTBOUND_MARKER in email.html)
    r.add("hunter skips its own email rather than reading it as feedback",
          inbox.is_our_own_email(email.text))
    r.add("the token survives a reply prefix",
          approval.token_from_subject("Re: " + email.subject) == token)
    r.add("every field value appears in the email body",
          all(f.value in email.text for f in plan.fields if f.value))
    r.add("both buttons are present",
          email.html.count("mailto:") == 2)
    em_dash = "\u2014"  # escaped: a literal one here trips the repo guard
    r.add("the email refuses an em dash anywhere",
          em_dash not in email.html and em_dash not in email.text)

    quoted = "\n".join("> " + l for l in email.text.splitlines())
    r.add("APPROVE on its own line approves",
          inbox.read_instruction(f"APPROVE\n\nOn Sun, hunter wrote:\n{quoted}")[0]
          == "approve")
    r.add("Approved! does NOT approve",
          inbox.read_instruction("Approved!")[0] != "approve")
    r.add("feedback is read as an amend and passed through verbatim",
          inbox.read_instruction(
              f"Make the hook about pricing.\n\nOn Sun:\n{quoted}")
          == ("amend", "Make the hook about pricing."))
    r.add("an empty reply is unclear, not approval",
          inbox.read_instruction("")[0] == "unclear")
    return r
