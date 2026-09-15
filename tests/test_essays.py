"""Drafting an answer to an application's open question.

Krish: "the two questions have no answer. I thought you had enough of a bank of
information to draft these answers." The material did exist and nothing was using
it: FillPlan carried an `essays` slot from the day it was written and nothing
ever filled it, so every open question on every form came back blank.

The risk in fixing that is the obvious one. A drafted answer goes out under his
name, so it must be traceable to his own record and must fail closed.
"""
from __future__ import annotations

import json

import pytest

from hunter.apply import essays


class Cfg:
    def __init__(self, key="k"): self._key = key
    def optional(self, name, default=None):
        if name == "hunter_anthropic_api_key":
            return self._key
        return default or "claude-opus-5"


from hunter.package import voicegate

# Built the way the real caller builds it: one lowercased haystack. Passing raw
# text instead would not loosen the gate, it would defeat it.
EVIDENCE = voicegate.build_evidence(
    "Krish Raja took Nine Entertainment's data and automation line from $9m to "
    "$61m over three years. He ran a 14-agent fleet at Mindmake. He worked at "
    "Microsoft. Nine was the employer.")


def fake_model(answer, monkeypatch, *, raw=None):
    class Block:
        type = "text"
        def __init__(self, t): self.text = t

    class Resp:
        def __init__(self, t): self.content = [Block(t)]

    class Messages:
        def create(self, **kw):
            Messages.seen = kw
            return Resp(raw if raw is not None else json.dumps({"answer": answer}))

    class Client:
        def __init__(self, **kw): self.messages = Messages()

    import sys, types
    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return Messages


def draft(answer, monkeypatch, evidence=EVIDENCE, **kw):
    fake_model(answer, monkeypatch, **kw)
    return essays.draft_one(Cfg(), question="Why us?", company="ElevenLabs",
                            role="GM UK", jd_text="jd", evidence=evidence)


def test_an_answer_traceable_to_his_record_is_kept(monkeypatch):
    got, why = draft("I took Nine's data line from $9m to $61m, and I have run "
                     "a 14-agent fleet in production since. That is the "
                     "unglamorous half of this and it is the half I care about, "
                     "which is why the enterprise side of your product reads as "
                     "the serious one to me.", monkeypatch)
    assert why == "" and "$61m" in got


def test_an_invented_number_is_rejected_rather_than_sent(monkeypatch):
    """The whole reason this goes through the voice gate. A number he never
    earned, under his name, on an application he cannot recall."""
    got, why = draft("I grew that business from $9m to $250m in eleven months "
                     "and led a team of four hundred people across nine markets, "
                     "which is the experience I would bring to this role here. "
                     "The same pattern held at every other company I have worked "
                     "for, and it is the reason I am writing to you about this "
                     "particular seat rather than any of the others open now.",
                     monkeypatch)
    assert got == ""
    assert "voice gate" in why


def test_no_evidence_means_no_draft(monkeypatch):
    """Passing an empty haystack would not loosen the gate, it would disable it."""
    got, why = draft("anything at all", monkeypatch, evidence="   ")
    assert got == "" and "no evidence" in why


def test_a_draft_that_runs_long_is_refused(monkeypatch):
    got, why = draft("I took Nine from $9m to $61m. " * 60, monkeypatch)
    assert got == ""
    assert "characters" in why


def test_a_reply_that_is_not_an_answer_fails_closed(monkeypatch):
    """Whatever the model returns, a blank or a scrap never reaches the form."""
    got, why = draft("", monkeypatch, raw="here you go, mate")
    assert got == "" and why


def test_a_missing_key_is_named_rather_than_crashing(monkeypatch):
    fake_model("x", monkeypatch)
    got, why = essays.draft_one(Cfg(key=""), question="q", company="c", role="r",
                                jd_text="", evidence=EVIDENCE)
    assert got == "" and "hunter_anthropic_api_key" in why


def test_every_question_gets_an_answer_or_a_reason(monkeypatch):
    """A silent failure here is a blank box on a real application."""
    fake_model("I took Nine's data and automation line from $9m to $61m, which "
               "is the closest thing I have to the job you are describing and "
               "the reason I am writing at all about this particular role. I ran "
               "a 14-agent fleet at Mindmake afterwards, so the operating half "
               "of this is familiar ground rather than something I would be "
               "learning on your time.",
               monkeypatch)
    notes: list[str] = []
    out = essays.draft_all(Cfg(), ["Why us?", "A hard problem?"],
                           company="c", role="r", jd_text="", evidence=EVIDENCE,
                           notes=notes)
    assert len(out) == 2
    assert len(notes) == 2 and all("drafted" in n for n in notes)


def test_a_banned_phrase_is_refused(monkeypatch):
    """The naming law applies to a drafted answer exactly as it does to the
    letter."""
    fake_model("I took Nine from $9m to $61m while at Mindmaker, which is the "
               "work closest to what you are hiring for in this particular role. "
               "I ran a 14-agent fleet there and the operating half of that is "
               "the part I would bring to you, rather than the strategy deck "
               "that usually arrives instead of it.",
               monkeypatch)
    got, why = essays.draft_one(Cfg(), question="q", company="c", role="r",
                                jd_text="", evidence=EVIDENCE,
                                banned_phrases=("Mindmaker",))
    assert got == "" and "voice gate" in why



def test_a_draft_nine_characters_over_is_asked_for_a_shorter_one(monkeypatch):
    """The OpenAI "Additional Information" box was left empty because a draft
    came back at 909 characters against a 900 cap and the whole answer was
    thrown away. A cap is a shape to aim at, not a reason to send nothing."""
    good = ("I took Nine's data and automation line from $9m to $61m over three "
            "years, and ran a 14-agent fleet at Mindmake after that. ")
    long_one = good + "x" * (essays.MAX_CHARS + 9 - len(good))
    seen = []

    class Block:
        type = "text"
        def __init__(self, t): self.text = t

    class Resp:
        def __init__(self, t): self.content = [Block(t)]

    class Messages:
        def create(self, **kw):
            seen.append(kw["messages"])
            body = long_one if len(seen) == 1 else good * 2
            return Resp(json.dumps({"answer": body}))

    class Client:
        def __init__(self, **kw): self.messages = Messages()

    import sys, types
    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)

    got, why = essays.draft_one(Cfg(), question="q", company="c", role="r",
                                jd_text="", evidence=EVIDENCE)
    assert why == "" and got
    assert len(seen) == 2, "it should have asked once more"
    said = str(seen[1][-1]["content"])
    assert "characters" in said, "the retry has to say what was wrong"


def test_it_gives_up_rather_than_looping_for_ever(monkeypatch):
    fake_model("short", monkeypatch)
    got, why = essays.draft_one(Cfg(), question="q", company="c", role="r",
                                jd_text="", evidence=EVIDENCE)
    assert got == "" and "characters" in why


def test_the_banned_words_are_in_the_prompt_not_only_the_gate(monkeypatch):
    """The OpenAI answer died three times on the word "solutions" because
    nothing had ever told the model not to use it. Telling it only after it has
    already used one wastes a retry, and sometimes all of them."""
    seen = fake_model("I took Nine's data and automation line from $9m to $61m "
                      "over three years and ran a 14-agent fleet at Mindmake "
                      "after that, which is the operating half of this job and "
                      "the part I would bring to you first.", monkeypatch)
    essays.draft_one(Cfg(), question="q", company="c", role="r", jd_text="",
                     evidence=EVIDENCE, banned_phrases=("solutions", "leverage"))
    sent = str(seen.seen["messages"][0]["content"])
    assert "solutions" in sent and "leverage" in sent
    assert "NEVER use" in sent


def test_an_answer_wrapped_in_chatter_is_still_used(monkeypatch):
    """A strict json.loads of the whole reply threw away a good answer because
    the model put a sentence in front of it."""
    good = ("I took Nine's data and automation line from $9m to $61m over three "
            "years and ran a 14-agent fleet at Mindmake after that, which is the "
            "operating half of this job and the part I would bring first. The "
            "commercial side of a new technology is where I have spent sixteen "
            "years, and it is the half that usually goes missing.")
    fake_model("", monkeypatch,
               raw='Sure, here you go:\n{"answer": %s}\nHope that helps.'
                   % json.dumps(good))
    got, why = essays.draft_one(Cfg(), question="q", company="c", role="r",
                                jd_text="", evidence=EVIDENCE)
    assert why == "" and got == good


def test_a_plain_text_answer_is_accepted(monkeypatch):
    good = ("I took Nine's data and automation line from $9m to $61m over three "
            "years and ran a 14-agent fleet at Mindmake after that, which is the "
            "operating half of this job and the part I would bring first. The "
            "commercial side of a new technology is where I have spent sixteen "
            "years, and it is the half that usually goes missing.")
    fake_model("", monkeypatch, raw=good)
    got, why = essays.draft_one(Cfg(), question="q", company="c", role="r",
                                jd_text="", evidence=EVIDENCE)
    assert why == "" and got == good
