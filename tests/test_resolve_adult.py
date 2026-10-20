"""His age answers every over-18 question; he is never asked it again.

His words, 2026-10-20: "why keep asking if I am 18? I am 39, you asked me this
last week too."
"""
from hunter.apply import resolve as R
from hunter.apply.resolve import Answer


def _label(text):
    return R._padded(text)


class _Bank:
    def get(self, name):
        return None


def _resolver():
    r = R.Resolver.__new__(R.Resolver)
    r.bank = _Bank()
    return r


def test_every_over_18_wording_is_answered_yes():
    r = _resolver()
    for q in ("Are you over the age of 18?", "Are you at least 18 years of age?",
              "Are you 18 years or older?", "Are you of legal working age?",
              "Confirm you are 18+", "Are you at least eighteen years old?"):
        got = r._adult(_label(q))
        assert isinstance(got, Answer) and got.value == R.YES, q


def test_an_under_18_wording_is_answered_no_and_experience_is_left_alone():
    r = _resolver()
    assert r._adult(_label("Are you under the age of 18?")).value == R.NO
    assert r._adult(_label("Do you have 18 years of experience?")) is None
    assert r._adult(_label("What is your phone number?")) is None


def test_the_supabase_question_resolves_through_the_whole_resolver():
    # The real form's field, end to end: no Info Bank row, no question to him.
    from hunter.apply.model import FormField, Option
    from test_apply_forms import build_bank
    f = FormField(key="k", label="Are you over the age of 18?", kind="boolean", required=True)
    got = R.Resolver(build_bank()).resolve(f)
    assert isinstance(got, Answer) and got.value == R.YES
    sel = FormField(key="k", label="Are you over the age of 18?", kind="single_select", required=True,
                    options=(Option("Yes", "Yes"), Option("No", "No")))
    assert R.Resolver(build_bank()).resolve(sel).value == "Yes"
