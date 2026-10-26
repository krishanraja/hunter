# The writing routine

A Claude Code routine on Krish's subscription that writes the prose hunter
needs, so the API pays only for the judge. It runs after each batch (Sunday
and Thursday) and can be fired by hand. Hunter checks every answer before
using it (`src/hunter/drafts.py`); a request the routine never answers falls
back to the API after 48 hours, under the monthly ceiling.

It is switched on by `hunter_writer = routine` in `system_config`. With the
default, `api`, nothing is queued and the routine finds an empty queue.

## What the routine may write, and nothing else

| Table | Columns | Only on rows that are |
|---|---|---|
| `hunter_drafts` | `output`, `drafted_at`, `drafted_by` (`routine`), `status` (`queued` to `drafted`) | `status = 'queued'` |

It never writes the sheet, `hunter_seen_roles`, `pilot_deals`, or any other
table, and never sends, posts or submits anything. Using a draft is hunter's
job, after its own checks.

## The prompt the routine runs

> You are the hunter writing routine for Krish Raja. Load the krish-voice and
> krish-principles skills first and write in his voice.
>
> 1. Clone krishanraja/hunter (main). No install is needed: run hunter's
>    check from the checkout with `PYTHONPATH=src`.
> 2. Read the queue through the Supabase API (project Mindmaker OS,
>    `gojpffsrxybbpbdzzrvs`):
>    `select d.id, d.kind, d.ref, d.prompt, d.schema, d.evidence, d.meta, c.text as context
>    from hunter_drafts d left join hunter_draft_contexts c on c.key = d.context_key
>    where d.status = 'queued' order by d.requested_at limit 40`.
> 3. For each row, read `context` (everything he has written and every
>    ruling he has made) and `prompt`, and write the answer:
>    - `kind = case`: a JSON object matching `schema` exactly (mandate, fit,
>      risk, archetype, snippet). Every number must appear in the prompt or
>      the context.
>    - `kind = door_observation`: one sentence, the observation he would make
>      anyway, never an offer, under 280 characters, using only facts in
>      `evidence`.
>    - `kind = letter`: one cover letter, a JSON object matching `schema`
>      (hiring_lead, hook, identity, bullets, why_now, close, cv_summary,
>      asks). `context` holds the rules, his own model letter and his
>      evidence; `prompt` holds the role, its base (US or UK spelling), the
>      judge's read, his view of the role and the posting. When `prompt` has a
>      PERSONAL LINE, the letter carries it as he wrote it. `meta` is what the
>      answer is checked against; never change it. Write the `reader` brief
>      first (who reads it, what they are accountable for, the 3 things they
>      must believe with posting quotes, their doubts, their register), then
>      every part for one of those beliefs (`map`), then read the finished
>      letter as that reader and fill `review`; a part whose review is not
>      empty is rewritten before answering. Never a tool name, an agent or
>      workflow count or an internal the posting does not name, and his AI
>      system in at most one part outside an AI transformation seat.
>    - `kind = essay`: the answers to one application form's open questions,
>      a JSON object `{"answers": {"<question exactly as given>": "<answer>"}}`
>      with every question in `meta.questions` answered. `context` holds the
>      rules and his evidence; `prompt` holds the role, his view of it, the
>      reader brief, the questions and the posting. Each answer is first
>      person, 220 to 1400 characters, every figure and name from the
>      evidence, no tool or internal the posting does not name, and one proof
>      point once per form: never the same story or figure in two answers.
>      His AI system only where the prompt allows it. Never change `meta`.
> 4. Put your answers in a file as a JSON list of
>    `{ref, kind, prompt, context, evidence, meta, output}` and run
>    `PYTHONPATH=src python -m hunter.drafts check FILE` from the checkout.
>    Rewrite every answer it rejects
>    and check again. Drop an answer you cannot make pass; hunter will ask
>    the API for a case or an observation. A letter or a form is never asked
>    of the API: one you drop waits for the next run.
> 5. Write each passing answer back, one statement per row:
>    `update hunter_drafts set output = <json>, status = 'drafted',
>    drafted_by = 'routine', drafted_at = now() where id = <id> and status = 'queued'`.
> 6. Read back: `select status, count(*) from hunter_drafts where drafted_by =
>    'routine' and drafted_at > now() - interval '2 hours' group by 1`, and end
>    with one line: how many were queued, written, and dropped.

## Measuring it

On the first two runs, before relying on it:

- Plan usage: read it from the subscription usage page before and after the
  run, and record the difference in `docs/ONE_SYSTEM.md`.
- Pass rate: `select status, drafted_by, count(*) from hunter_drafts group by 1, 2`
  after hunter's next hourly drain. A routine answer that hunter marks
  `failed` and then rewrites through the API costs both.
- API spend for `case` and `door_observation` in `python -m hunter.run spend`
  should fall towards zero while the routine runs.

If the pass rate is poor or the plan usage too high, set `hunter_writer` back
to `api`. Nothing else changes.

## What stays on the API, and why

Letters moved to the routine on 2026-10-15 (his ruling: no API spend on
letters). Hunter queues one per role his Yes or his score asks for, holds the
build until it is written, checks it with `package/compose.py` and builds it
in his house style. Nothing falls back to the API.

Form answers moved on 2026-10-26 (his answer, asked whether to: "Yes, move
them"; about $7 a month on the API). Prepare queues one request per form
(`apply/essays.py`, `from_routine`) and holds the Apply email. Once the
routine's answers pass the same checks, the hourly drafts step asks for
Prepare itself, so the email goes with no second press: at most a day after
he pressed it. `hunter_essay_writer = api` puts them back on the API.

| Job | Why it stays |
|---|---|
| judge | a hundred roles a run, unattended, the reason the API exists here |
| cold_targets | runs inside each batch with web search; moves next if its spend grows |
| newsletter | one short call per post, hourly; too small to be worth the delay |
| lookalike | once per company per 30 days, inside the batch that needs the score |
