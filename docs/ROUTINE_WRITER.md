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
> 4. Put your answers in a file as a JSON list of
>    `{ref, kind, prompt, context, evidence, meta, output}` and run
>    `PYTHONPATH=src python -m hunter.drafts check FILE` from the checkout.
>    Rewrite every answer it rejects
>    and check again. Drop an answer you cannot make pass; hunter will ask
>    the API for it.
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

| Job | Why it stays |
|---|---|
| judge | a hundred roles a run, unattended, the reason the API exists here |
| tailor (CV and letter) | he presses Yes and expects the package that day; a weekly routine would make him wait |
| essay | drafted when an application is prepared, minutes before he presses it |
| cold_targets | runs inside each batch with web search; moves next if its spend grows |
| newsletter | one short call per post, hourly; too small to be worth the delay |
| lookalike | once per company per 30 days, inside the batch that needs the score |
