# One system: the running record

What moved where in the restructure, and why. Newest entry first. The full
Phase 0 inventory (every command, model job, table writer and surface) is the
private page Krish was sent on 2026-10-08; this file keeps the decisions.

The target, four layers:

1. Scheduled and deterministic, no model calls (GitHub Actions from main).
2. API, unattended, only where volume demands it: the judge.
3. Subscription: a Claude Code routine writes the prose (case, CV and letter,
   essays, door-in openers, cold lookups, newsletter extraction). Every string
   still passes `package/voicegate.py`.
4. Control Center is the one place he acts. The sheet is the record.

---

## 2026-10-09: his yes to the merge and the routine

His words: "Merge to main and turn on the routine yourself. Do all of the rest
of it and I'll do number 5 later. Make sure we use all the different API keys
if needed (LLMs), so this doesn't cost Anthropic API credits when it's not
necessary."

- **GitHub Actions is locked for billing** since about 21:00 UTC on 8 October:
  every hourly drain since has failed before its first step, and the pull
  request checks could not start. Hunter is not running until the account is
  unlocked at github.com/settings/billing. This is his to fix; nothing in
  either repository can.
- **Retired** (his yes): migrate-columns, migrate-sheet, dedupe-db,
  recover-verdicts, clear-unverdicted, prune-orphans, disconnect (one-off
  repairs, done); apply-local and watch, with the windows/ scripts (his
  extension does this); submit, and approvals-drain --send (a datacentre
  press the bot check refused). submit.py stays as the module that previews
  and opens a form; nothing calls press_submit.
- **Provider per job** (`llm.PROVIDER_ORDER`, `hunter_provider_<purpose>`):
  newsletter extraction goes to OpenAI first, through llm.complete, and is
  priced at OpenAI's rate in the ledger. Prose in his name and anything
  measured against his rulings stays on Anthropic; moving the judge or
  lookalike is a measurement to run, not a key to switch. A job that needs
  web search never goes to OpenAI, which has no search tool here. The case and
  the door-in line leave the API for his subscription (below), which is the
  larger saving.
- **The routine is on**: hunter_writer = routine, and two routines on his
  subscription fire after each batch (Sunday 14:47 London, Thursday 10:17
  UTC) with the prompt in docs/ROUTINE_WRITER.md. Plan usage on the first two
  runs is still to be read from his usage page.
- **Door-in cards**: Ada's card (Siva Ganeshanandan, CCO at Ada) checks out and
  lands on the next door-cards run with --skip braintrust,olive. Braintrust
  stays held: the Radar row is braintrust.dev (AI evaluation) and the contact
  runs the talent marketplace of the same name. Olive stays held: the contact
  is at Zoa Research with a stale "@ Olive" title. The name matcher cannot
  tell either apart, so the skip is the rule until a better route appears.

---

## Where every command lives now

| Layer | Commands and jobs |
|---|---|
| 1. Scheduled, no model | drain (the clock), run and source (sweeps, gates, function bar, company bar, radar, staging), process (reconcile, learn, people, invariants, archive, layout), settle, close-submitted, confirmations, actions, drafts, watchdog, stats, spend, doctor, preflight, recon, amendments, portfolios, discover, companies, verify, invariants, layout, archive, door-coverage, door-cards, bridges, review-email |
| 2. API, unattended | judge (inside run and source); lookalike (once per company per 30 days); judge-eval, blind-set, blind-eval on demand; and, until the routine is on, every writing job as a fallback. All under the monthly ceiling |
| 3. Subscription routine | the case for each row, the opening line on each door-in card (drafts.py, docs/ROUTINE_WRITER.md). Stays on the API, with the reason in ROUTINE_WRITER.md: tailor, essay, cold_targets, newsletter |
| 4. Control Center | the Hunt lane: rule on roles, prepare and open applications, mark applied, record what happened; the Advisory lane: door-in cards with hunter's route and opening line. Emails link there |
| Repair kit, by hand | retire, unretire, restore, decline, prune-sheet, regate, build, bank-check, bank-seed, gtm-seed, audit-forms, simulate, set-dropdown, approvals, applied, packages, learn, newsletter, reconcile |
| Proposed for retirement, his call | migrate-columns, migrate-sheet, dedupe-db, recover-verdicts, clear-unverdicted, prune-orphans, disconnect (one-off repairs, done); apply-local and watch (a Windows script; the extension does this); submit (a headless press the bot check refuses; the last click is his) |

## 2026-10-08: Stages 0 to 4, built on branches

Krish approved the Monday fix and the four deletions, then said to carry on to
completion. Every merge to main, the routine and the writer switch still wait
for his yes.

- **Stage 0.** Control Center's Monday drafter skips hunter's door-in cards
  (merged, PR 403, his approval). Four door-in cards that named the wrong
  person were deleted with his approval and read back: seven remain. The cause
  was the name matcher: one shared word ("Intelligence", "Data") or a short
  extra word ("Together - NZ") counted as the same company. Names must now be
  the same words. On a snapshot of the live tables the cards fall from 18 to 12
  and none of the four returns.
- **Stage 1, one writer per table.** A bridge he acted on keeps its state and
  ask (all 335 rows read "proposed" today). A cold lookup no longer zeroes a
  real connection (one had been). An approval Control Center recorded as
  submitted cannot be cancelled by hunter. The canon drift proposal finally
  lands: it had written a column the table does not have and a status its
  constraint refuses, and swallowed the error. Lookalike, the blind set and
  judge-eval now stop at the monthly ceiling. Dead model code removed (triage,
  companyintel web facts). The spend report reads Control Center's own meter
  for its pilot drafting ($0.01 in 31 days), rather than recording it twice.
- **Stage 2, fewer roles reach the judge.** A function bar cuts titles naming
  only functions he has never taken. On the 8 October run it would have spared
  25 of the 100 judge calls, every one rejected at fit 3 or below, and it cuts
  none of the 83 titles he marked Yes or Applied nor any Yes row in either
  ground truth (tests/fixtures/function_bar_eval.json). A rejection is reused
  for a week: 13 of those 100 had been paid for on 4 October. Together, about
  38 fewer calls in 100, roughly $1.50 a run at today's $0.04 a call. The judge
  prompt is unchanged, so judge-eval is unchanged.
- **Stage 3, the writing routine.** hunter_drafts and hunter_draft_contexts
  (migration in control-center, applied and read back). With hunter_writer at
  "routine", the case and the door-in line are queued with the exact API
  prompt; the routine answers; the hourly drain checks each answer with the
  same validator plus the voice gate, asks the API once for any that fails,
  and falls back to the API for any unanswered after 48 hours. Essays are no
  longer redrafted when he presses an application: paid once, and the plan
  hash stays the one he approved.
- **Stage 4, one place he acts.** hunter_actions: Control Center inserts a
  press, hunter applies it before settle in the same hourly pass and records
  the result. A verdict is written to column A as exactly the words he would
  type. The Hunt lane gains "Roles to rule on" with the case for each, and
  Prepare, Open the filled form, I applied and What happened next on each Yes
  role. Door-in cards show hunter's route and checked opening line. The status
  card's schedule is now Sunday 13:00 London and Thursday 08:27 UTC. The new
  roles email opens the Hunt lane.

Ground truth after all of it, the same as main: of 47 roles he approved the
bar blocks 0; of 107 he declined it blocks 39; it sweeps 42 of his 52 named
companies and blocks 43 of the 49 he declined. 1,519 hunter tests and 4 new
Control Center checks pass offline; 13 Hunt lane browser tests pass.

Still his to decide: merging both branches, switching hunter_writer to
"routine" and creating the routine, the retirements above, and revoking the
two keys left in a synced skill.

---

## 2026-10-08: Phase 0, read only

- Access, checked read only: GitHub push to both repos works; Actions dispatch
  works (`stats` run 314 green); Supabase connector reads work and its deletes
  hang without reaching Postgres, while Supabase's own API completes them;
  the Vercel connector is refused on deployments and Vercel's API works;
  Control Center's three hunter endpoints are deployed and guarded.
- Deleted, with his approval given before this session: the pilot_deals card
  "Stealth Vertical AI Startup". Read back: 0 rows, 11 hunter cards left.
- Found, not yet acted on (each needs his yes):
  - Two credentials still sit in plain text in a synced skill, and the GitHub
    one still authenticates. He revokes them.
  - Control Center's Monday drafter will pick five hunter door-in cards first,
    overwrite their checked opening lines and move them to drafted.
  - Four of the 11 door-in cards pair a company with a person at a different,
    similarly named company (Together AI, Fragment Data Technologies, Physical
    Intelligence, Series Entertainment).
  - `approvals` is never scheduled; the "Apply:" email goes out only by hand.
  - Cross-repo tables with competing writers: pilot_deals, bridge_candidates,
    hunter_application_approvals, workflow_proposals, network_contacts.
  - The spend ledger begins 2026-10-04. In its first four days the judge was
    73 percent of $16.52. Control Center's pilot drafter spends outside it.
- Baseline: 1,483 tests pass offline.
