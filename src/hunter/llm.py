"""One door to a language model, with a second provider behind it.

Why this exists. On 2026-09-20, mid-way through rebuilding the sourcing
engine, every Anthropic call started answering:

    400 invalid_request_error: You have reached your specified API usage
    limits. You will regain access on 2026-10-01 at 00:00 UTC.

Four modules call a model directly (package/rationale.py, package/tailor.py,
apply/essays.py, sources/newsletter.py) and every one of them would have gone
quiet on Krish's Sunday run, ten days before the budget resets. Each had its
own client construction, its own key lookup and its own idea of what to do
when the call failed, so the fix had to be made four times or made once here.

The rule this module keeps is the repo's rule. A failed call returns an
honest empty answer and a note saying which provider failed and why. It never
returns a plausible string that no model produced, and the caller's existing
deterministic fallback stays the thing that runs when both providers are
gone.
"""
from __future__ import annotations

import json
import re
import threading

from .config import Config

# A usage-limit refusal is not a transient error and retrying it 150 times in
# one run just spends 150 round trips to be told the same thing. The first
# one disables the provider for the life of the process.
_DISABLED: set[str] = set()
_LOCK = threading.Lock()

USAGE_LIMIT = re.compile(
    r"usage limit|quota|insufficient[_ ]quota|billing|credit balance|"
    r"exceeded your current", re.I)

DEFAULT_ORDER = "anthropic,openai"
# Cheap, fast and good enough for the jobs hunter gives a model: summarise a
# posting, draft a paragraph, read a page and return JSON. Overridable with
# system_config hunter_openai_model without a deploy.
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"


class Unavailable(RuntimeError):
    pass


def _disable(provider: str, why: str) -> None:
    with _LOCK:
        _DISABLED.add(provider)


def available(provider: str) -> bool:
    return provider not in _DISABLED


def reset() -> None:
    """Tests share a process; a provider disabled by one must not leak."""
    with _LOCK:
        _DISABLED.clear()


def _text_from_anthropic(resp) -> str:
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", "") == "text")


def _anthropic(cfg: Config, prompt: str, *, max_tokens: int,
               schema: dict | None, system: str | None,
               web_search: bool, history: list | None) -> str:
    key = cfg.optional("hunter_anthropic_api_key")
    if not key:
        raise Unavailable("no hunter_anthropic_api_key in system_config")
    try:
        import anthropic
    except ImportError as e:
        raise Unavailable("anthropic sdk missing") from e
    client = anthropic.Anthropic(api_key=key)
    kwargs: dict = {
        "model": cfg.optional("hunter_anthropic_model", DEFAULT_ANTHROPIC_MODEL),
        "max_tokens": max_tokens,
        "messages": (history or []) + [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema",
                                              "schema": schema}}
    if web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search",
                            "max_uses": 4}]
    resp = client.messages.create(**kwargs)
    stop = getattr(resp, "stop_reason", "")
    if stop == "refusal":
        raise Unavailable("model refused")
    if stop == "max_tokens":
        raise Unavailable("the reply was cut off at the token limit")
    return _text_from_anthropic(resp)


def _openai(cfg: Config, prompt: str, *, max_tokens: int,
            schema: dict | None, system: str | None,
            web_search: bool, history: list | None) -> str:
    key = cfg.optional("hunter_openai_api_key")
    if not key:
        raise Unavailable("no hunter_openai_api_key in system_config")
    try:
        import openai
    except ImportError as e:
        raise Unavailable("openai sdk missing") from e
    client = openai.OpenAI(api_key=key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages += (history or []) + [{"role": "user", "content": prompt}]
    kwargs: dict = {
        "model": cfg.optional("hunter_openai_model", DEFAULT_OPENAI_MODEL),
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if schema:
        # OpenAI's strict mode refuses a schema that does not close itself,
        # and hunter's schemas are written for Anthropic. Ask for a JSON
        # object and validate at the call site, which every caller already
        # does because the Anthropic path can return malformed JSON too.
        kwargs["response_format"] = {"type": "json_object"}
        kwargs["messages"] = list(messages)
        kwargs["messages"][-1] = {
            "role": "user",
            "content": prompt + "\n\nAnswer with one JSON object matching this "
                                "schema and nothing else:\n"
                       + json.dumps(schema)}
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", "") == "length":
        raise Unavailable("the reply was cut off at the token limit")
    return choice.message.content or ""


PROVIDERS = {"anthropic": _anthropic, "openai": _openai}


def complete(cfg: Config, prompt: str, *, max_tokens: int = 1500,
             schema: dict | None = None, system: str | None = None,
             web_search: bool = False, history: list | None = None,
             ) -> tuple[str, list[str]]:
    """Ask whichever provider answers. Returns (text, notes).

    An empty string means nobody answered, and notes says who failed and why.
    The caller keeps its deterministic fallback: a model being unavailable is
    a reason to say less, never a reason to invent.
    """
    order = [p.strip() for p in
             cfg.optional("hunter_model_order", DEFAULT_ORDER).split(",")
             if p.strip() in PROVIDERS]
    notes: list[str] = []
    for name in order:
        if not available(name):
            continue
        try:
            text = PROVIDERS[name](cfg, prompt, max_tokens=max_tokens,
                                   schema=schema, system=system,
                                   web_search=web_search, history=history)
        except Unavailable as e:
            notes.append(f"{name}: {e}")
            # Only a condition that cannot change within the process is
            # worth remembering. A missing key looked like one and is not:
            # disabling on it meant the first caller with a different config
            # silenced the provider for every caller after it, which is a
            # whole run producing fallback text with nothing saying why.
            if "sdk missing" in str(e):
                _disable(name, str(e))
            continue
        except Exception as e:
            msg = str(e)[:200]
            notes.append(f"{name}: {e.__class__.__name__} {msg}")
            if USAGE_LIMIT.search(msg):
                # Out of budget until a date, not a blip. Stop asking.
                _disable(name, msg)
            continue
        if text.strip():
            return text, notes
        notes.append(f"{name}: empty answer")
    return "", notes


def json_object(text: str) -> dict | None:
    """The first JSON object in a model's answer, or None.

    Both providers wrap JSON in prose often enough that every caller was
    writing this regex. None means the answer was not usable, which callers
    must treat as a failure rather than as an empty result.
    """
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# An authentication error echoes the request, and the request carries the key.
# Truncating the message is not redaction: "invalid x-api-key: sk-ant-..." is
# well inside any sane truncation, so a check written to make a dead key
# visible would have written the live one into a GitHub Actions log instead.
SECRETISH = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})")


def redact(text: str) -> str:
    """Anything key shaped, reduced to its first eight characters."""
    return SECRETISH.sub(lambda m: m.group(1)[:8] + "...redacted", text or "")


def probe(cfg: Config, provider: str) -> tuple[bool, str]:
    """Ask one provider, by name, whether its key actually works.

    complete() exists to get an answer from whoever will give one, which is
    right for the work and useless for a check: with a fallback behind it, a
    dead Anthropic key is invisible. Every call quietly goes to OpenAI, every
    run looks healthy, and the reason Krish split the key in the first place,
    "need to split all usage of Anthropic API out for better monitoring",
    silently stops being true.

    Deliberately not routed through complete(), and deliberately never
    returns or logs the key: the point is to name WHICH provider answered.
    """
    fn = PROVIDERS.get(provider)
    if fn is None:
        return False, f"no provider called {provider!r}"
    try:
        text = fn(cfg, "Reply with the single word: ok", max_tokens=8,
                  schema=None, system=None, web_search=False, history=None)
    except Unavailable as e:
        return False, redact(str(e))
    except Exception as e:
        # The SDK raises its own types for a bad key, and the message is the
        # useful part. Truncated, because an auth error can echo the request.
        return False, redact(f"{e.__class__.__name__}: {str(e)}")[:160]
    got = (text or "").strip()
    if not got:
        return False, "answered with nothing"
    return True, got[:40]
