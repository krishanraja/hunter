"""One door to a model, with a second provider behind it.

On 2026-09-20, mid-way through rebuilding the sourcing engine, every
Anthropic call started answering "You have reached your specified API usage
limits. You will regain access on 2026-10-01". Four modules called a model
directly, and every one of them would have gone quiet on Krish's Sunday run
ten days before the budget reset, each falling back to deterministic prose
with nothing saying why.
"""
import sys
import types

import pytest

from hunter import llm


@pytest.fixture(autouse=True)
def _clean():
    llm.reset()
    yield
    llm.reset()


class Cfg:
    def __init__(self, **kw):
        self.raw = {"hunter_anthropic_api_key": "a", "hunter_openai_api_key": "o"}
        self.raw.update(kw)

    def optional(self, key, default=""):
        return self.raw.get(key, default)

    def require(self, key):
        return self.raw[key]


def fake_anthropic(monkeypatch, *, text="", raise_with=None, stop="end_turn"):
    class Block:
        type = "text"
        def __init__(self, t): self.text = t

    class Resp:
        def __init__(self): self.content = [Block(text)]; self.stop_reason = stop

    class Msgs:
        seen = None
        def create(self, **kw):
            Msgs.seen = kw
            if raise_with:
                raise raise_with
            return Resp()

    class Client:
        def __init__(self, **kw): self.messages = Msgs()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return Msgs


def fake_openai(monkeypatch, *, text="", raise_with=None, finish="stop"):
    class Msg:
        def __init__(self, c): self.content = c

    class Choice:
        def __init__(self, c): self.message = Msg(c); self.finish_reason = finish

    class Resp:
        def __init__(self): self.choices = [Choice(text)]

    class Completions:
        seen = None
        def create(self, **kw):
            Completions.seen = kw
            if raise_with:
                raise raise_with
            return Resp()

    class Chat:
        def __init__(self): self.completions = Completions()

    class Client:
        def __init__(self, **kw): self.chat = Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = Client
    monkeypatch.setitem(sys.modules, "openai", mod)
    return Completions


def test_the_first_provider_answers_and_the_second_is_never_asked(monkeypatch):
    fake_anthropic(monkeypatch, text="ok")
    second = fake_openai(monkeypatch, raise_with=AssertionError("asked twice"))
    text, notes = llm.complete(Cfg(), "hi")
    assert text == "ok" and notes == []
    assert second.seen is None


def test_a_provider_out_of_budget_hands_over(monkeypatch):
    """The exact shape of the failure that started this: a 400 saying the
    budget is gone until a date."""
    fake_anthropic(monkeypatch, raise_with=RuntimeError(
        "400 invalid_request_error: You have reached your specified API "
        "usage limits. You will regain access on 2026-10-01"))
    fake_openai(monkeypatch, text="second provider")
    text, notes = llm.complete(Cfg(), "hi")
    assert text == "second provider"
    assert any("usage limits" in n for n in notes)


def test_an_out_of_budget_provider_is_not_asked_again(monkeypatch):
    """Asking 150 times in one run spends 150 round trips to be told the
    same thing."""
    first = fake_anthropic(monkeypatch, raise_with=RuntimeError(
        "You have reached your specified API usage limits"))
    fake_openai(monkeypatch, text="ok")
    llm.complete(Cfg(), "one")
    assert not llm.available("anthropic")
    first.seen = None
    llm.complete(Cfg(), "two")
    assert first.seen is None, "a provider known to be out of budget was asked again"


def test_a_missing_key_never_silences_a_provider_for_the_whole_process(monkeypatch):
    """This looked like a condition that could not change and is not: one
    caller with a different config silenced the provider for every caller
    after it, which is a whole run of fallback text with nothing saying why."""
    fake_anthropic(monkeypatch, text="ok")
    fake_openai(monkeypatch, text="ok")
    llm.complete(Cfg(hunter_anthropic_api_key=""), "hi")
    assert llm.available("anthropic")


def test_both_providers_failing_returns_nothing_and_says_who_failed(monkeypatch):
    """An empty answer is the honest outcome. The caller keeps its
    deterministic fallback; a model being gone is never a reason to invent."""
    fake_anthropic(monkeypatch, raise_with=RuntimeError("boom"))
    fake_openai(monkeypatch, raise_with=RuntimeError("bang"))
    text, notes = llm.complete(Cfg(), "hi")
    assert text == ""
    assert len(notes) == 2 and any("anthropic" in n for n in notes)


def test_a_truncated_answer_is_named_as_that_rather_than_as_malformed(monkeypatch):
    """The two need different fixes and only one of them is the model's."""
    fake_anthropic(monkeypatch, text='{"answer": "half a sen', stop="max_tokens")
    fake_openai(monkeypatch, raise_with=RuntimeError("no"))
    text, notes = llm.complete(Cfg(), "hi")
    assert text == "" and any("cut off" in n for n in notes)


def test_a_schema_reaches_both_providers_in_the_shape_each_one_wants(monkeypatch):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    seen_a = fake_anthropic(monkeypatch, text="{}")
    llm.complete(Cfg(), "hi", schema=schema)
    assert seen_a.seen["output_config"]["format"]["schema"] == schema

    llm.reset()
    fake_anthropic(monkeypatch, raise_with=RuntimeError("out of budget: usage limit"))
    seen_o = fake_openai(monkeypatch, text="{}")
    llm.complete(Cfg(), "hi", schema=schema)
    assert seen_o.seen["response_format"] == {"type": "json_object"}
    assert "a" in seen_o.seen["messages"][-1]["content"], (
        "the schema must reach a provider that cannot take it as a parameter")


def test_the_configured_order_is_honoured(monkeypatch):
    fake_anthropic(monkeypatch, raise_with=AssertionError("wrong provider first"))
    fake_openai(monkeypatch, text="openai first")
    text, _ = llm.complete(Cfg(hunter_model_order="openai"), "hi")
    assert text == "openai first"


def test_json_object_finds_the_object_a_model_wrapped_in_prose():
    assert llm.json_object('Sure! {"a": 1} hope that helps') == {"a": 1}
    assert llm.json_object("no object here") is None
    assert llm.json_object('{"a": broken}') is None
    assert llm.json_object("") is None


def test_a_key_shaped_string_never_survives_an_error_message():
    """An authentication error echoes the request and the request carries the
    key. Truncating is not redaction: "invalid x-api-key: sk-ant-..." sits well
    inside any sane truncation, so the check written to make a dead key visible
    would have written the live one into a run log instead. Caught by its own
    test before it shipped, 2026-09-24."""
    from hunter import llm
    anth = "sk-ant-api03-" + "q" * 95
    oai = "sk-proj-" + "z" * 150
    out = llm.redact(f"invalid x-api-key: {anth} and also {oai}")
    assert anth not in out and oai not in out
    # Enough left to tell which key it was, never enough to use.
    assert "sk-ant-a...redacted" in out
    assert "sk-proj-...redacted" in out


def test_redaction_leaves_ordinary_text_alone():
    """A blunt scrub that eats the error is a different way to be unreadable."""
    from hunter import llm
    msg = "AuthenticationError: your credit balance is too low"
    assert llm.redact(msg) == msg
