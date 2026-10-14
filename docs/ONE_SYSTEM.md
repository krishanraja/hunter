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
   Amended 2026-10-14: since 2026-10-13 he rules on the sheet, in K Krish
   Score and M Krish Reaction, and a score of 7 or more builds the pack (the
   entry below). Control Center is where he applies: Prepare, Open the filled
   form, I applied, What happened next.

---

## 2026-10-14: his reasons are in every package he wants

His words that day: "Make sure that, when you're creating all the packages,
you include all the rationales that I've put in the new column, as well as
the combined rationale ... more human without sounding arrogant, but
sounding confident and like I can deliver ASAP."

- **His view** (`package/his_view.py`): K, M and hunter's case in L, read off
  his row as it reads now, the record only when there is no row. Empty
  stays empty, and with no K and no M the tailor prompt is byte for byte
  what it was (`tests/fixtures/tailor_prompt_no_view.txt`).
- **Where it goes.** `build_one` reads it for every build (the hourly score
  builds, process's Yes builds, an amend, a rebuild), and the tailor prompt
  carries it in its own section of `prompts/tailor.md`, "Why he wants this
  role, in his own words", with the rules: his reasons are the spine; first
  person, confident and concrete about the first moves; never arrogant;
  never his note quoted or his score mentioned; nothing about location,
  salary, other offers, people he knows or pedigree. The essays get the same
  section. His note is not evidence: the gate traces every number and name
  against his CV and proof points alone, so a contact, an employer, an offer
  or a figure only his note carries is refused. Once he has written a
  reaction, no flag or reason that leaves tailor or the essays quotes any
  refused figure or name, his or not: it says "a figure the evidence does
  not support was left out" or the same of a name (`his_view.redact`),
  because his note's figure comes back from the model in other forms and
  build flags reach public logs. Which failure is a figure and which a
  name is read from the gate's own failure formats, never guessed from what
  a failure quotes. Only the retry message to the model names what failed.
  A run of seven of his words copied into the prose does not pass either,
  with curly and straight apostrophes read as one. The hiring lead is kept
  only when the JD names that person, word for word, and the JD mirror
  passes the numbers check, has every capitalised word past its first, not
  the company's, as a whole word of the JD or his evidence (the gate's own
  name check is a substring test, which let a contact through inside
  "market" and "working"), and must not copy his note; either one that
  fails becomes "Hiring Team" or the block's own clause, with a flag that
  names nothing. The arrogance phrases are one list of generic
  phrases (`voicegate.ARROGANCE_BANNED`, joined by `package_banned`) that the
  prompt names and the gate refuses; a phrase from his own notes belongs in
  his Profile's VOICE AND TONE list, never in this public repository.
- **What it records.** `package_his_view` on hunter_seen_roles: a hash of
  the view a package was written from, or null. Not package_trigger, which
  Control Center parses exactly. The column needs migration cc6; until it
  is there a build says it could not record it and `rebuild-packages`
  refuses to pay.
- **`rebuild-packages [--apply] [--max N] [--job-id J]`.** A package built
  without his reasons, for a role he wants (the roles `approvals --wanted`
  prepares) whose row carries his score or his reaction, rebuilt from them
  through build_one. A Yes he answered with only a reaction is not: his
  score wins over an older Yes, and that row leaves Pipeline (`leave.py`).
  His row is read again before each target is paid for (router.wants), and
  build_one refuses a rebuild whose row no longer asks for an application.
  Never one he has acted on (Applied, an
  Application Status, an approval opened, approved, submitted or waiting on
  him), and never one whose documents he has edited, cannot be read, or have
  no record of what hunter wrote into them, all checked again just before it
  pays. His newest AMEND instructions go into the rebuild. A rebuild whose
  prose did not come through, or whose letter failed verification, leaves
  the earlier package in place, and one the gate held or verification
  failed is recorded as tried and not paid for again until his view, the
  package or the link changes (`--job-id` tries it regardless). Counts only:
  a selection that cannot be read says the exception's class and builds
  nothing.
- **`approvals --wanted [--apply] [--max N]`.** The Apply email with the
  one-click link, one per posting, for every built package of a role he
  wants (a score of 7 or more, or Yes with K at 7 or more or blank), through
  cmd_approvals one role at a time, each found on his row by its link and
  sent only while that row still asks for it. Skips a posting with an
  approval in play, a dead posting, a role applied for, a Yes he called a
  Duplicate, and one already sent to apply by hand or refused for a missing
  answer for the same package (marked in hunter_alerts under `prepared:`), so
  a `--max` batch moves on to the rest. A refusal is marked against his
  answer bank as well, so the answer he adds re-opens it; a form waiting on
  an essay that did not draft is marked for a day only, so the next batch
  moves on and the posting is tried again after it. With the month at its
  ceiling the command waits before reading anything. A dry run prepares
  nothing. Counts only, and what the essays cost.
- **One posting is its posting's identity** (`run.posting_key`): the board
  and posting id where the link names one (without any query, so a Workday
  link with and without `?source=` is one), the Greenhouse id of a
  `?gh_jid=` careers page, a LinkedIn job by the number at the end of its
  path, otherwise the link with only the query parameters that name a
  posting kept (`POSTING_PARAMS`: id, jobId, reqId and the like). Cutting
  at "?" made every posting on one careers path one posting; keeping every
  parameter but a list of tracking ones made one LinkedIn job, swept twice
  with two `position` values, two postings. His AMEND words reach only a
  rebuild of the posting he wrote them for.
- **Nothing of the application is printed.** `approvals`, on every path and
  in a dry run, prints counts and neutral outcomes ("Apply email sent: 1,
  fields 14, essays 2"), never the email, the open link, the token or its
  key, his address, an essay or a field value, and an error by its class
  alone (the send, the token's record and every token-keyed call in the
  reply drain included, so a Supabase error naming its request never
  reaches the log). Prepare from Control Center keeps its stdout out of the
  drain's log as well, and the note on his card says what happened, from
  what approvals counted: sent, already has an application waiting, refused
  for a missing answer, an essay did not draft, sent as by hand, not on
  Pipeline, his score took it back, or failed with the error's class. The
  reply drain prints the job and the length of his words, and the mail
  fallback prints a length. process prints a build that raised by its class
  and his edits by field and count. The drain's Actions log is public, and
  the open link hands the fill payload to whoever holds it.

## 2026-10-14: his score builds the pack

His rulings of 2026-10-13. Asked whether a high Krish Score should build the
package by itself, he answered "I'd take 7 or more"; and he rules on the
sheet in K and M. Column A Yes still builds.

- **What builds.** A whole number from 7 to 10 in K on a row column A leaves
  undecided, with M not reading Duplicate (`router.build_trigger`). "7.5"
  builds nothing: it is recorded as unreadable and never guessed. Applied or
  Declined in column A always wins over K.
- **Where.** A pass of its own, `score-builds --apply`, in the hourly drain
  after settle, building scores only: at most
  `hunter_max_score_builds_per_run` (3 unless set, 0 is off) a run, his score
  from highest, then the oldest on his sheet. Never inside `run` or
  `process`, which build Yes only, so the backlog of 32 scored rows found on
  13 October clears over about 11 hours instead of landing in the batch
  run. Each build is a paid tailor call; the pass stops at the month's
  ceiling, builds nothing until canon 9.13 carries the rule, and its log
  carries counts only.
- **What it records.** package_trigger on hunter_seen_roles, "column A Yes"
  or "Krish Score N", and the same on the rating that built it. Never column
  A, never krish_verdict: every taste verdict is his. A build refused at a
  gate, or a posting found gone, is recorded with what it saw and is not
  tried again until K, the link or the masters change; a package that
  failed verification gets one more try first. A build that raised is an
  error, never a refusal: it is tried again next hour. One that raised
  after its record was patched built is decided from his row as it reads
  then: the row links the new documents and it stays built; the row write
  never landed and the record is set to be built again; the row still links
  the package before and the record says so again. Each build goes onto
  the row it was chosen from, so two postings of one title in two countries
  each get their own.
- **When he takes it back.** Settle, hourly and before anything moves: a
  score-built role whose K is now below 7, cleared or Duplicate, or whose
  column A says Declined, has every open approval for its posting cancelled
  with the reason, read back, and so has one made after it. A K that is not
  a whole number is left alone. The documents stay; a score raised again
  puts the role back for Prepare. build_one re-reads A and K before it
  fetches, and verify and regate never archive a row his score asks for.
- **Control Center.** /api/hunter/roles returns a score-built role like a Yes,
  so Prepare and Open the filled form are on its card, and a score build
  that was refused or found the posting gone too, with the reason, so it
  never goes quiet; /api/hunter/review leaves it out of Roles to rule on.
  Bulk `approvals` refuses score-built rows; Prepare sends one only while
  its own row (by its link, never a twin's) still asks for it.
- **The review email** links to Pipeline and says to score each role 1 to 10
  in K, 7 or more builds the pack, a few an hour, and that a Yes builds at
  the next batch run or from Build packages; "to score" counts rows with K
  and M blank that he has not scored past.
- **gh_jid links.** A careers page link carrying a Greenhouse posting id and
  no board (`?gh_jid=`) was fetched as a board named by the posting id, which
  answers 404, so a live posting read as dead. Such a link now finds its
  board by its own page and is matched by the posting id alone, never by a
  title, which would have relinked a closed posting to the same seat in
  another country. Dead only when the board its own page names no longer
  lists the id; otherwise unverified (`run.board_key`). Two of his scored
  roles were in that state.
- **Low rows leave Pipeline** (`leave.py`, 2026-10-14, his choices of
  2026-10-13). What he scored 1 to 5, answered with only a reaction, scored
  past or called Duplicate moves to Applied with K and M kept and column A
  untouched, in the same hourly move as his Applied and Declined rows; a Yes
  he scored 1 to 5 moves too and its waiting application is cancelled, unless
  another row of the role on Pipeline still asks for it, and it is never
  built while it waits: build_one, the last read before paying, refuses it
  too, and Prepare refuses a Yes his score took back, whatever built its
  package, unless another row of the posting still asks; the bulk
  `approvals` run asks router.wants of every row. A row he scored whose
  posting hunter found gone leaves, whatever built its package; a Yes marked
  dead stays. A role is set aside only once no row of it is left on
  Pipeline. A row on Applied he later scores 7 or more comes back.
  Nothing moves for his score until canon 9.13 carries the rule; the first
  list is put to him first (`python -m hunter.run leave --out PATH`).

---

## 2026-10-11: the routine's first real batch, and a bug it exposed

The Sunday batch ran (265 candidates, 100 judged for $3.30, 17 presented;
the function bar spared 29 calls and the company bar 23). No case was queued:
staging hands the queue a ResolvedRole (jd_text, jd_url), not a judge.Role,
and queue_case read attributes it does not have. Every one of the 17 failed
with an AttributeError that the summary recorded and nothing else surfaced,
so the rows reached him with the judge's short reason. The API path was never
hit, so nothing was spent either way.

Fixed the same afternoon (PR 16): queue_case normalises what it is handed, a
test carries the exact shape staging uses, and `drafts --requeue N` is the
repair for a batch whose cases never queued. Then the loop ran end to end for
the first time on cases: 11 requeued, the routine wrote all 11 on his plan in
one pass, hunter's checks passed all 11, the rows and the sheet carry them, and
the ledger shows 0 API calls for cases today. The 7 door-in opening lines went
the same way on 9 October.

Still to read, by him: the plan usage those two routine runs cost, from his
subscription usage page.

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
| 2. API, unattended | judge (inside run and source); lookalike (once per company per 30 days); score-builds (hourly, his score of 7 or more, at most 3 a run, from 2026-10-14); judge-eval, blind-set, blind-eval on demand; and, until the routine is on, every writing job as a fallback. All under the monthly ceiling |
| 3. Subscription routine | the case for each row, the opening line on each door-in card (drafts.py, docs/ROUTINE_WRITER.md). Stays on the API, with the reason in ROUTINE_WRITER.md: tailor, essay, cold_targets, newsletter |
| 4. Control Center | the Hunt lane: prepare and open applications (for a Yes or a score of 7 or more), mark applied, record what happened, and rule on a role there if he wants to (since 2026-10-13 he rules on the sheet, in K and M); the Advisory lane: door-in cards with hunter's route and opening line. The new roles email links to the sheet |
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
