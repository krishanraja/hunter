"""A cheap first read, so the judge only reads what could be his.

His words, 2026-10-03: "I still feel as though this burns so many anthropic
API credits, is there no way of making this cheaper?" The judge is Opus at
high effort, about five cents a role, and the company lane alone turns up a
thousand roles that pass his rules on a first sweep. Most are plainly not his:
a quota seat at a company he does not know, a renewals manager, an enterprise
AE wearing a Head title.

So a small model reads each role first, from the title, the company, the pay,
the place and the opening of the posting, against a compact statement of what
he wants and every ruling he has made, and drops only the ones it is sure he
would refuse. What it keeps goes to the judge as before.

The rule it is held to is the one the plan set for any first pass: it must keep
essentially all of his Yes roles. It is measured on his rulings (measure(),
`python -m hunter.run triage-eval`), and the keep line is the strictest that
does; a triage that drops one of his Yes roles is the expensive mistake, and
an extra role for the judge costs five cents.
"""
from __future__ import annotations

import concurrent.futures as cf
import json

from .config import Config

MODEL = "claude-haiku-4-5-20251001"
PRICES = {"in": 1.0, "out": 5.0, "cache_read": 0.10, "cache_write": 1.25}
KEEP_MIN = 3          # chosen by measure(); see tests/fixtures/triage_eval.json
POSTING_CHARS = 1500

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["score", "reason"],
          "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}}}

INSTRUCTIONS = """You are the first reader of job postings for Krish Raja, a senior commercial
leader (16 years scaling commercial businesses at Microsoft, Nine, SingTel,
Captify; runs a 14-agent AI operating system in production). A stronger model
reads every role you pass on. Your only job is to drop roles he would plainly
refuse, and to pass on everything that might be his.

What he wants: a senior seat in one of five families (country or regional GM;
chief commercial or strategy; corporate development and strategy;
partnerships and alliances; AI chief of staff and AI transformation), at a
fast-growing, well-backed, AI-native or internet-native company. He applies
for stretch roles at companies he admires. Revenue-carrying seats are fine.

His rules: New York, London, UK or US remote; the San Francisco Bay Area only
at one of his top companies. At a top company, Director and Manager titles in
commercial, partnerships, business development, GTM and strategy are in scope,
and pay may start below $200,000 if the top or on-target earnings reach
$250,000. He never wants banks, insurers, asset managers, consultancies,
agencies, measurement or research firms, or services firms, unless the seat is
an explicit AI transformation mandate at a household name.

Score 0 to 10 how likely he is to want it. 0 to 2 only when you are sure: the
function is not his (engineering, research, legal, finance, HR, customer
support, an individual contributor seat), the employer is one he never wants,
or the place breaks his rules. When unsure, score 4 or more. "reason" is one
short clause."""


def system_text(rulings: list[str]) -> str:
    return INSTRUCTIONS + "\n\nHIS RULINGS (his own Yes and No, oldest first):\n" + "\n".join(rulings)


def role_text(company: str, title: str, location: str, comp: str, note: str,
              posting: str) -> str:
    return (f"Company: {company}\nTitle: {title}\nLocation: {location or 'not stated'}\n"
            f"Pay: {comp or 'not stated'}\nTop company: {note or 'no'}\n\n"
            f"Opening of the posting:\n{(posting or '')[:POSTING_CHARS]}")


def ruling_line(label: str, company: str, title: str, words: str) -> str:
    return f"{'YES' if label == 'yes' else 'NO'} | {company} | {title} | {words[:60]}"


def score(cfg: Config, system: str, items: list[dict], *, client=None,
          model: str | None = None, workers: int = 8) -> tuple[dict, float, list[str]]:
    """{item id: (score, reason)}, usd, problems. An item the model did not
    answer is left out, and the caller keeps it: no answer is never a drop."""
    from . import judge
    client = client or judge._client(cfg)
    model = model or cfg.optional("hunter_triage_model", MODEL)

    def one(it):
        msg = client.messages.create(
            model=model, max_tokens=300,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": it["text"]}])
        return it["id"], msg
    out, usd, problems = {}, 0.0, []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, it) for it in items]
        for fut in futures:
            try:
                iid, msg = fut.result()
            except Exception as e:
                problems.append(f"a call failed: {e.__class__.__name__}")
                continue
            u = judge.usage_of(msg)
            usd += (u["input_tokens"] * PRICES["in"] + u["output_tokens"] * PRICES["out"]
                    + u["cache_read_input_tokens"] * PRICES["cache_read"]
                    + u["cache_creation_input_tokens"] * PRICES["cache_write"]) / 1e6
            try:
                a = json.loads(judge.text_of(msg))
                n = int(a["score"])
            except (ValueError, TypeError, KeyError):
                problems.append("an answer was not valid JSON")
                continue
            if 0 <= n <= 10:
                out[iid] = (n, str(a.get("reason") or "")[:200])
    return out, round(usd, 4), problems


def keep(result: tuple[int, str] | None, keep_min: int = KEEP_MIN) -> bool:
    """Unanswered is kept: the judge reads it."""
    return result is None or result[0] >= keep_min
