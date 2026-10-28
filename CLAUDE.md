# hunter: the operating contract

<!-- north-star:start -->
## North Star: read it first, every session

This repository's North Star is [`docs/NORTH_STAR.md`](docs/NORTH_STAR.md): an ambition Krish set that is not reached yet.

- Read it before any work, every session.
- Before you start, say which part of it the task moves. If it moves nothing, say so first and let Krish decide.
- Prefer the work that moves it furthest, and close by noting what moved.
- Check the North Star number and its urgency (U1 to U5) on the first line under "Where it is right now" in `NOW.md`, and push at the intensity [the harness contract](https://github.com/krishanraja/ai-harness/blob/main/contract/north-star-metric.md) sets for that level. Update it whenever you can measure it; a number nobody measures is U4.
- Only Krish changes it.
<!-- north-star:end -->

Read this before changing anything. Everything in it was paid for by a real
failure, and the failures are named so the rule is not abstract.

Krish is on Windows with no Python and no terminal. He reads email and presses
buttons in Chrome. Anything that needs him to type a command is not finished.

---

## 1. Never report an outcome you did not read back

This is the whole project. Every defect in its first week was one shape: code
asserting a result it had not observed.

| What claimed success | What was actually true |
|---|---|
| `press_submit()` recorded SUBMITTED on the next line | the application never reached Harvey; reCAPTCHA scored the automated browser and refused |
| `_choose_one` and `_fill_one` returned `True` | the work authorisation answer went out blank |
| the extension banner said "Filled all 17 fields" | the equal opportunity section was untouched, because that build could not fill it |
| the extension painted "Submitted. Hunter has it" | the POST had returned 500 and nothing was written |
| the receipt email said "The form acknowledged it" | the string was manufactured by hunter, not quoted from the form |
| `FillPlan.ready` was `True` | the posting was dead and the form had zero fields |
| an approval email went out with no screenshot | the browser had failed to launch, and the pass carried on |

The rule, in order of preference:

1. **Read the artifact back.** After a click, read the control. After a write,
   read the row. After an export, count the pages. `submit.py` does this on every
   branch, `extension/fill.js` acts then waits then re-reads, and
   `merge.page_count` measures the PDF instead of counting words.
2. **Where you cannot observe it, say so in those words.** "Pressed, but the form
   showed no confirmation" is a good outcome. "Submitted" would have been a lie.
3. **Never substitute a default for an absent observation.** `confirmation =
   failure_reason or "submitted"` turned every unconfirmed press into a claimed
   acknowledgement. An empty value must stay empty and mean empty.

A test that asserts the claim is not enough. Break the code deliberately and
check the test fails; if it still passes, the test is describing the code rather
than checking it.

---

## 2. A green test suite does not mean the output is good

Every test in this repository checks that the code does what the code says.
On 2026-09-20 there were 848 of them, all green, and Krish opened his sheet to
find SiriusXM, Citi, Omnicom, a $140,000 "Founding GTM Lead" scored 10, and
enough banks and health systems to write: "the drift has increased 100X".

Nothing was broken in the sense those 848 tests could see. The sourcing had
drifted to a LinkedIn keyword sweep, which structurally returns whoever posts
the most jobs. The domain gate that blocks banking was exempting anything whose
text said "artificial intelligence", which is every AI role at every bank. The
scorer had no view of the employer and paid a point for being listed on the
NYSE. Pay was invisible on 92 percent of roles, so the $200,000 floor never
fired.

**`tests/fixtures/krish_verdicts.json` is the answer, and it is ground truth.**
148 roles he personally ruled on, 44 yes and 104 no. `tests/test_taste.py`
measures the bar against them and prints the two numbers that matter: how many
of his approvals the bar blocks, and how many of his declines. Today that is 0
and 7.

The rules that follow from it:

- **Measure before you write the rule.** Two obvious fixes died on contact with
  his data. A sector blocklist for healthcare would have killed BioSpace,
  Recursion and Talkspace; one for consultancies would have killed Harvey. A
  junior-title rule for "Lead" and "Manager" seats would have killed Harvey at
  $240K to $360K, Writer at $205K to $259K, and every General Manager seat he
  has ever approved. Both were deleted, and the files say why so nobody writes
  them again.
- **Never block on no evidence.** A company hunter has no record of scores
  neutral and reaches him. Refusing a role needs a reason his verdicts support.
  Ranking does not.
- **One sector is a gate, and only one.** Banks, insurers and asset managers.
  Everything else about company quality is carried by the score.
- **No company gate is absolute.** His words, 2026-09-20: "citi is an example
  where I'd reject that company unless the role was ideal, which that one was".
  G12 and G13 open for a role scoring 9 or more on MERIT, which is its score
  with the employer component and penalty removed. Judging it on the ordinary
  score is circular: the penalty is what stops it clearing.
- **A verdict he gave must survive a pairing failure.** 89 of his declines had
  never become learning events, because an event was only written when
  reconcile had matched the sheet row to a database row. G12 was running on 11
  companies when it should have been running on 36. `learn.from_sheet_rows`
  reads his verdicts off the sheet itself, which is the actual record.
- **Refresh the fixture when he verdicts a batch**, and keep the old rows. It
  only becomes more useful. A change that makes those numbers worse is a
  regression however good it looks.

---

## 3. Two scores, and the company one comes first

Until 2026-09-20 hunter had one score, on the role. Nothing anywhere asked
whether the COMPANY was one he would join. Measured from his own column A,
accept rate fell 77 percent to 15 percent over six weeks, and 28 of the last
batch's 33 declines carried a company-level reason code. He was not rejecting
the seat. He was rejecting the business.

```
  SUPPLY                    COMPANY SCORE             ROLE SCORE
  where companies      ->   is this a business   ->   is this seat
  come from                 worth his time            his shape
  (5,540 candidates)        (company.py, 0 to 10)     (score.py + gates)
```

- **`company.py` scores the business**, out of 10, from five weighted
  components (`WEIGHTS`) and two penalties that are not averaged away. The
  points come from `WEIGHTS`, never from a literal, because gutting the
  declared category weight to 0.1 once left every calibration number
  unchanged while the functions handed out hardcoded fours.
- **`companyintel.py` gathers the evidence, free**: the company's own
  homepage, its about page, the opening of its own job postings, its job
  board, the a16z index. `llm.py` is behind it when a model is needed.
- **G14 applies it at staging.** A role stages only if its company clears
  `SWEEP_FLOOR`, unless the role scores `EXCEPTIONAL_MERIT`.
- **`prospect.py` finds companies he has never named** in what hunter
  already discards, and proposes the best of them under his own list on the
  Target Companies tab. It never bounds the funnel to his list: his words,
  "There are so many companies I am not thinking about".

The rules this layer must keep:

- **A Fact refuses to exist without a source URL.** Not filtered later,
  refused at construction, so there is no path that stores an uncited claim.
- **Unknown and zero are different answers.** A component with no evidence
  scores nothing AND is marked unknown, and the score is normalised over
  what was actually observed. Scoring out of a fixed 10 with unknowns worth
  zero admitted 11 percent of his own 53 named targets, because a homepage
  does not say who led the Series B.
- **One observation is not a judgement.** `MIN_DENOMINATOR` divides by at
  least the weight of category plus one more, so a company known by a single
  lucky match lands at Tier 3 rather than a perfect 10. Before it, a company
  whose board mentioned London scored 10 on that alone.
- **Nothing is scored without knowing what the business does.** Category is
  the prerequisite, not one consideration among five.
- **Never block on no evidence.** G14 blocks a company hunter has READ and
  found wanting. A company it could not read is ranked lower and still
  reaches him.
- **A guessed domain carries its doubt onto the sheet.** Guessing resolved
  Perplexity to a domain registrar, Gamma to a security vendor and Krea to a
  Slovak IT consultancy, and all three were scored. The resolved host must be
  the company's own, and a name-matched site says so in its evidence line.
- **`tests/test_company_taste.py` is the ground truth**, the sibling of
  `test_taste.py`. 138 labelled companies, his 52 named targets against the
  49 he declined. Today the bar sweeps 42 of his 52 and blocks 43 of his 49,
  and the mean score by the tier HE assigned comes out 8.2, 7.7, 6.1. The
  labels are his and the facts are the companies' own words, kept apart on
  purpose: if the tab supplied both, the test would measure his enthusiasm
  reflected back.

## 3b. The judge decides fit, and is measured before it decides anything

`judge.py` is one Claude call per role that reads everything he has written
(canon 2 to 6, Profile, Interview Answers, Scoring Reference, Target Companies)
and every ruling he has made, quotes the posting for each answer, and returns a
verdict and a fit. `judge_stage.py` runs it inside staging in one of three
modes, `hunter_judge_mode`: off, shadow (judges and records, changes nothing
he sees), gate (decides). It is in shadow until he says otherwise.

- **Measure on a window the prompt was not tuned on.** `judge-eval --dev`
  scores rulings 7 to 16 September with examples from before; the threshold is
  chosen there; `judge-eval --threshold N` scores the 17 September onward
  holdout once. v1 was scored on the holdout and rejected all 13 of his Yes
  roles; re-tuning on those same roles would have measured nothing.
- **The asymmetry is the design.** A decline costs him seconds on a list he
  reads easily; a hidden Yes may cost the job. v1 was told to present only
  likely Yes roles and hid every one. Requirements he lacks, the bottom of a
  pay range, unstated pay and quota lines lower fit; his rulings, mostly about
  the business, are what reject.
- **The record is the bar.** `tests/test_judge_eval.py` reads
  `tests/fixtures/judge_eval.json`: today 7 of 13 Yes shown at fit 6,
  precision 22 percent against 11 for the old system, AUC 0.75. A prompt change
  must re-run judge-eval (the test fails on a version mismatch) and must not do
  worse.
- **What it cannot know.** The six Yes it hides on the holdout are at kinds of
  business he had declined before 17 September (an agency holding company, a
  PE firm, finance software). Rulings lag taste. His new verdicts feed the next
  run's prompt automatically; the audit sample measures what the gate hides.

## 3c. Every list he reads is a Pipeline tab

His words, 2026-10-03, on a facts-only list: "We need this in the same column
format and rich formatting as the Pipeline tab, always ... You need to
convince me to say yes to these roles because of how well suited they are to
what I want".

- **Same columns, same look, read from Pipeline.** `blindset.write_tab` copies
  Pipeline's layout off Pipeline itself every time (widths, hidden columns,
  frozen panes, header, banding, colours, the column A dropdown) and reads it
  back. The format paste brings the colours and banding and does NOT bring
  the dropdown, whatever the API reference says; adding colours again
  duplicates them. Rows come from `sheet.make_row`, as on Pipeline.
- **Why It Fits makes the case.** `judge.make_the_case` writes mandate, FIT
  and RISK from everything he has written, names his proof points and the
  rulings that match, and says so plainly when the case is thin. Figures are
  checked against the posting, its pay field and his record. It writes
  Pipeline's rows too whenever the judge has run.
- **A blind test hides the judgement, not the information.** The case writer
  is never told the judge's call, and Score stays empty on the blind set.

## 3d. Companies first, and quality is not only the judge's job

His words, 2026-10-03: "why can't you identify the next unicorns in the
creative industries that are well backed?" and "i need your judging
methodology to be more cost efficient, and it not to be the only thing holding
up quality". In the thirty days before, hunter saw 249 roles at the hundred AI
companies he listed and showed him 3: the location rule blocked 103 before
anything with judgement read them, and Runway's board was never found.

The layers, cheapest first, each with its own measurement:

1. **Companies he wants** (`universe.py`, `lookalike.py`, `radar.py`). His
   list (`data/ai_universe.csv`, plus the companies he has sent since in
   `universe.SENT_FILES`), his Target Companies, his Yes companies, and
   lookalikes from the a16z, Sequoia, Accel, General Catalyst and Thrive
   portfolios, each scored once against his list (about a third of a cent,
   kept 30 days).
   A one-off Yes makes a company top only if it also scores 5 or more: one Yes
   had made Citi, PayPal and a job-ad network "top". Keys are whole names,
   never a single token. The Company Radar tab shows the trail.
2. **Their own boards, free, every run.** A board found by guessing must name
   the company in its postings, or its roles do not count, and a posting it
   does not list is not dead (`run.board_names`): on 2026-10-20 a one-job
   Lever board called "horizon" marked his Horizon Media posting dead. Sequoia's board
   (`sources/consider.py`, added 2026-10-05 at his request) is read through
   its own API: every open role, paged, with pay. Its apply links name each
   company's own board, which is learned and swept in full; a role at a
   company whose board hunter cannot read is taken from Sequoia's board.
3. **His rules at top companies**, decided 2026-10-03 (`gates.run_gates`
   `top_company=True`): the Bay Area and remote-eligible postings pass, Director
   and Manager seats in commercial functions pass, pay may start under the floor
   when the top or OTE reaches $250,000. Nowhere else changes. The gate code
   already had a variable called `top`; the flag is `top_company` for that
   reason.
4. **The function bar, then the company bar** (`companybar.py`). A title naming
   only functions he has never taken (engineering, finance, legal, HR, design)
   costs no call: on 8 October it would have spared 25 of 100 judge calls, all
   rejected at fit 3 or below, and it cuts none of his Yes titles
   (`tests/fixtures/function_bar_eval.json`). Then a role at a company scoring 0 or 1
   is cut with no judge call, except an AI transformation seat and a
   recruiter's posting. Measured on 242 of his rulings: it loses none of his
   Yes roles beyond his own cut. At 3 it would lose ColdIQ, which he applied to.
5. **The screen** (`screen.py`, added 2026-10-26: "encode as much as
   possible into followable, replicable steps ... so the cost should keep
   coming down"). A logistic model over title, posting and company, fitted
   on every role the judge ruled on with his Yes, Applied and 7-or-more
   scores above it (86 of them roles the judge never read), refitted every
   run, weights in system_config `hunter_screen_weights`. Its line sits under
   every positive scored out of fold. Measured oldest first (`screen-eval`,
   `tests/fixtures/screen_eval.json`): it cuts 0.8 to 2.5 percent of judge
   calls, more as the record grows, and on no window a role the judge
   presented or one he wanted. Small, and free. Text alone cannot cut more
   safely: several roles he applied to read like rejects on their words.
   Shadow until he says otherwise (`hunter_screen_mode`); it cuts only in gate
   and only while the judge itself decides, and even then sends 2 a run to the
   judge as an audit (`hunter_screen_audit_per_run`).
6. **The judge**, last, on what survives, top companies first, at low effort
   (measured equal to high on the dev window, 24 percent cheaper), at most 100
   roles and $5 a run, in gate mode since 2026-10-03.
7. **The case** for each row that reaches him, at medium effort since
   2026-10-07 (`hunter_case_effort`), its figures still checked.

Measured and not used: Haiku as a first reader (`tests/fixtures/triage_eval.json`;
the module was removed 2026-10-08, its measurement kept). At the only line that keeps his Yes roles
it saves about a fifth of the judge's work; one notch stricter it loses a third
of them, and exactly the less obvious ones (later, Syntrace, Innovamat).

## 3e. Every model call is in the ledger, and the month has a ceiling

His words, 2026-10-07: "we absolutely need to get hunter's API costs down by
about 3X". Only the judge had a record: $8.17 of the $21 the 4 October run cost
on hunter's key. Half of that $8.17 was the same 100 roles judged twice,
because the first scheduled attempt failed a minute after judging and the
retry started again.

- **`spend.py` records every call** in `hunter_judge_calls` with a purpose,
  and the run summary says what each job cost. `python -m hunter.run spend`
  reads it back. One price table; a model with no price is costed at the
  dearest, never at zero.
- **Each job has its own model and effort** (`llm.PURPOSES`): prose in his
  name on Opus 5.5 at medium, extraction and lookups on Sonnet 5.5 at low.
  `hunter_model_<purpose>` and `hunter_effort_<purpose>` move one job.
- **Text that repeats is cached**: the judge's context, the essays' evidence,
  the tailor prompt on its retry. Retries never resend what the cache holds.
- **A judgement is reused, not paid for again**: a rejection for a week, any
  other verdict for a day, so a role he might see is read against his newest
  rulings. 13 of the 100 roles judged on 8 October had been rejected on the 4th.
- **The month has a ceiling**, `hunter_llm_max_usd_per_month` ($150 by
  default). Over it, calls return an honest empty answer and the judge
  leaves roles pending.
- **The Batches API is not the saving it looks.** It halves the price but
  reads the cache only some of the time; the batched holdout runs cost 8 to
  15 cents a role against 4 to 6 direct. Measure before moving a call to it.
- **Where it went, 30 days to 2026-10-26: $62.6.** The judge $34.9 (82
  percent of its calls a rejection), the tailor $11.2 (one day, unreachable
  since the house builder), essays $7.4, cold targets $4.1, lookalike $2.7.
  The judge is lean already: its context is cached, the pitch it writes on a
  rejection is 12 percent of its output, and a rejection re-read after a
  week came back presented 5 times in 35, so the week stays. What cuts it is
  fewer calls (the screen) or a cheaper model, which he has deferred: "move
  stuff to Haiku later if this stuff doesn't make a difference ... you can
  use Opus if it's genuinely a required task."
- **An empty answer is recorded too.** A cold target search that found
  nobody was run again every run (37 searches on 6 roles, $3.33 of $4.05);
  the ledger row now says it ran, and it waits 30 days (`bridges.searched_recently`).

## 3f. His score is the signal, and column A is the older record

On 2026-10-11 he inserted K Krish Score (1 to 10) and M Krish Reaction (his
own words) on Pipeline, and asked that hunter learn from them rather than from
column A, which he barely uses. Measured that day over the 61 roles he scored,
the AI Score (score.py) ranked them at Spearman -0.19 against his scores and the judge's fit at
about 0: what reached him was ordered no better than chance.

- **K and M are his** (`sheet.HIS_COLS`). Hunter never writes, defaults,
  repairs, prunes or deletes them; a move to Applied carries them RAW and
  reads them back. A header he changes stops every write and is mailed once,
  in plain words.
- **`ratings.py` records them** in `hunter_krish_ratings`, hourly. One kind
  per role: scored, score_unreadable (never guessed into a number),
  reaction_only, unscored_after_review (a blank he has scored past, under a
  batch rule with a 24 hour wait), yes_unscored, duplicate, cleared. The
  unique key holds a hash of his words, never the words.
- **His reactions never reach the verdict paths**: not `verdicts.parse`,
  `learn.*`, `hunter_verdict_events`, `krish_verdict` or G12. Fed to the
  free-text inference, six of them would have become company blocks,
  including one at a company he scored 7 for another role.
- **The repo is public.** No reaction text and no real company beside one of
  his scores in any repo file, fixture, test or log line. Counts only.
  `approvals` prints counts and neutral outcomes on every path, never the
  Apply email, its open link, the token, his address, an essay or a field
  value, and with a reaction on the row no flag or reason quotes a refused
  figure or name (`his_view.redact`); only the retry message to the model
  names it.
- His rulings of 2026-10-13: a score of 7 or more builds the package, his
  score wins over an older column A Yes, rows scored 1 to 5 and blanks he has
  scored past leave Pipeline, and he rules on the sheet in K and M.
- **A score of 7 or more builds** (`router.build_trigger`): a whole number 7
  to 10 in K on a row column A leaves undecided, M not Duplicate. Column A
  Yes still builds; Applied or Declined always wins. Score builds run in
  their own hourly pass after settle, `score-builds --apply`, scores only, at
  most `hunter_max_score_builds_per_run` (3, and 0 is off) a run, K highest
  first, then oldest; never inside `run` or `process`, which build Yes only.
  The pass builds nothing until canon 9.13 carries the rule. Measured on
  2026-10-13: 32 scored rows with no package, which inside the batch run
  would have added over an hour to it. Its log carries counts only, and no
  line anywhere prints his score beside a role ("built on your score").
- **A role is its row, never its twin.** `build_one` builds onto the row the
  selection chose (`run.own_sheet_row`), Prepare and settle read the row by
  its link (`run._rows_for`), and a gh_jid link is matched by its posting id
  alone. The live list had one title in two countries scored 10 and 9.
- **The reason is `package_trigger`, never column A.** "column A Yes" or
  "Krish Score N" on hunter_seen_roles (and on the rating that built it).
  Control Center shows a score-built role with Prepare, a refused one with
  its reason, and leaves both out of Roles to rule on. Bulk `approvals` never mails one; Prepare sends it only
  while the row still asks for it.
- **A score taken back takes the application back.** Settle cancels every
  open approval for the posting when K goes below 7, is cleared or reads
  Duplicate, or column A says Declined, records why, and reads it back, and
  cancels an approval made after that too. It watches by the trigger and
  the documents, not package_status, which reconcile rewrites on a Decline.
  A K that is not a whole number cancels nothing: unknown is not lower. The
  documents stay. A row his score asks for is never stamped or archived by
  `verify` or `regate`. `build_one` re-reads A and K before it fetches or
  pays. A refused score build is not retried until K, the link or the
  masters change; a verification failure gets one more try first, and a
  build that raised is an error, tried again next hour.
- **Low rows leave Pipeline, and his score wins over an older Yes**
  (`leave.py`, his choices 2026-10-13: "Move 1-5 and skipped blanks", "My
  score wins"). One predicate, `leave.should_leave`, for settle, process and
  `archive`: column A New or blank with K 1 to 5, only a reaction, a blank
  he has scored past or a Duplicate under 6; column A Yes with K 1 to 5 or
  only a reaction that is not Duplicate. A Yes with K and M blank stays, a
  Yes he has only called Duplicate stays, K 6 stays, a K that is not a whole
  number stays, and a row his score built a package on stays unless K falls
  to 5 or below. A row leaves only when the latest rating of its role on
  record is what the row says now, so it is recorded first; a twin is found
  under its own company, title and location. Decided rows and these move in
  one `archive_rows` call after the ratings sync, before the invariants:
  every target re-read before the copy and before the delete, a changed row
  refused, his cells and Archived On RAW and read back, every other cell as
  the text it was, nothing copied twice, column A never written. The role
  becomes `set_aside` (seen by staging dedupe and reconcile, out of Roles to
  rule on and approvals) only once no row of it is left on Pipeline, read
  by link and by company, title and country (`run._pipeline_holds`), not by
  his record, which never holds a pending blank; a role set aside that a
  row on Pipeline holds again goes back to staging. Its rating gets
  `moved_at`, and a leaving Yes has its waiting application cancelled with
  his score as the reason unless a row of that role still on Pipeline asks
  for one. Under the rule a Yes his score took back is never built, from
  the packages button or process (`select_for_build(score_wins=True)`),
  nor by `build_one`, the last read before paying, nor prepared by Prepare
  whatever built its package, and a rebuild needs a row that still asks for
  an application (`router.wants`). A row on Applied he later scores 7 or more comes back,
  its dates RAW and its cells as the text they were. Nothing moves for his score until canon 9.13
  carries `leave.CANON_RULE`; `leave` lists the move as counts, with `--out`
  for the rows.
- **A posting he sent asks for its own package** (his words 2026-10-20,
  retiring column A: "Just produce the actively, passionfroot and horizon
  packages"). Source "sent by Krish" (`sheet.is_sent`) builds as his Yes
  does, `router.SENT_TRIGGER`, until he scores it: then his score decides, a
  1 to 5 moves it off Pipeline, and a blank on it is never one he scored past.
- **A batch he has applied to is cleared in one pass** (his words 2026-10-20:
  "all jobs have been applied, clear the pipeline"): `clear-pipeline --apply`.
  A row with a package is recorded applied on his word (`SAID_SO`), by its
  own row number; every other row leaves with no verdict and its role is set
  aside. One `archive_rows` move, Pipeline read back empty, no email.
- **A role he scored whose posting is gone leaves too** (his choice
  2026-10-14, "Move the gone ones"): Package Status "Posting dead" on a row
  column A leaves undecided, whatever built its package. A Yes marked dead
  stays; the gh_jid bug once marked a live Yes dead, and his Yes is his to
  clear. A gone row never comes back. `system_config hunter_reviewed_through` (a date) counts every batch
  staged on or before it that he scored in as finished, so its blanks move
  without the 24 hour wait and without a later batch scored: the table first
  saw every score he typed from 11 to 13 October on the 14th, the hour it was
  created, so the wait ran from the wrong moment.

## 3g. His letters and CVs: his house style, written by the routine, read back

His words, 2026-10-15, on the packages before this: "not much of this is
actually written personally for the role?" and "are you sure its ok that the
CV's professional summary has close to zero personalization?". Then, on the
rebuilt previews: "I am now happy with the engine, make it durable".
docs/HANDOVER_LETTERS.md is the full record.

- **His rulings, all binding.** Arial; name 23pt, title 11pt, everything else
  9pt. The header is place and phone (no email), the link row "Bio | LinkedIn |
  Media channel", the banner, the rights line; no footer contact line. The
  letter is one page, the CV two at most; the margins narrow first, then the
  4th bullet, then why-now, never below 9pt, and the banner leaves the CV before
  anything of his. US roles take Brooklyn and the US number, UK, Europe or
  EMEA take London and the UK number and UK spelling; anywhere else holds for
  his pick (`package/contact.py`).
- **The stamp is the pause.** A package carries `package_style =
  style.HOUSE_STYLE`, and every send path refuses one without it. A change to
  his house style is a new HOUSE_STYLE value: every older package is then
  refused, and the hourly `rebuild-packages --reason style --apply` rebuilds
  the ones he still wants. `style.COMPOSER_LIVE = False` makes every build
  wait, for the day his style needs a composer that does not exist yet.
- **No API spend on letters.** The writing routine on his subscription writes
  them (`drafts.py` kind `letter`, `NO_API`, docs/ROUTINE_WRITER.md), daily at
  12:47 London plus the Sunday and Thursday runs. An unanswered letter waits.
  The watchdog mails him when letters have waited 36 hours with none written,
  or one role's letters fail their check 3 times (`alerts.writer_problems`).
- **Form answers too, since 2026-10-26** (his answer: "Yes, move them").
  Prepare queues one request per form (`essays.from_routine`, kind `essay`)
  and holds the Apply email; once the answers pass the API path's checks and
  the form's, the hourly drafts step asks for Prepare itself
  (`drafts.essays_ready`), so it goes with no second press, and only while
  his row still asks for it (`require_wanted`). Any answer pending holds the
  email, optional questions included. Reading an answer never marks it used,
  and a dry run queues nothing. A form refused 3 times for the same
  questions is not asked again; his card and the watchdog say so.
  `hunter_essay_writer = api` puts them back on the API.
- **Holds cost nothing.** `letters.plan` reads the record before any fetch: a
  role with no header base, a business of his kind with no line of his, or a
  letter not yet written is SKIPPED, which no cap counts.
- **His personal lines are canon 9.14.** A voice, audio, speech, mindset,
  future of work, personal development, emerging media, content or IP, live
  entertainment or health business gets one of his lines (`package/personal.py`,
  read from what the business sells, never the posting). With none, `facts.py`
  emails him once per kind of business and `canonwrite.py` appends his reply
  verbatim, version locked and read back, and takes it back on UNDO: the one
  exception to canon never being edited from code. His lines never reach the
  repo, a log, the writer's examples or the evidence.
- **Written for the role, checked** (`package/compose.py`, A13 to A16): no
  8-word run copied from his model letter; the hook and why-now name the
  company; the identity and close carry the company or the posting's own terms;
  at least 2 bullets tie to the role. The CV summary is required, written for
  the role, copies no 10-word run of his own summary, and names the company or
  2 posting terms; its figures and his own bold phrases are bold, a money range
  whole. His CV highlights come in the role's order (canon 9.12), his words and
  his bold unchanged. Every figure and name traces to evidence (voicegate).
- **His company is "mind/make"**, lowercase with the slash, as his CV writes it
  (his answer, 2026-10-15). `gtmseed.name_problems` refuses every other
  spelling, case and all, inside `voicegate.check`, so every letter, summary,
  essay and form answer obeys it. The site, mindmake.co, is an address.
- **Every edit he makes is learned from** (`docedits.py`, hourly, before the
  replies): words and formatting, stored with both versions. His newest letter
  edits go to the writer as examples; sentences he writes in the first person
  become HIS OWN WORDS and count as evidence. An edit after the Apply email
  cancels it, so Prepare sends his version. batchstats records letters built,
  edited and the median edit size: a falling edit size is the measure that the
  letters are getting closer to his.
- **A question only he can answer is asked in chat, never by email** (his
  ruling 2026-10-20: "just give me the questions here, stop asking me those
  by email, what a waste of life to email that to me"). A form that stops on
  one records it (`record_missing_answers`, system_config
  `hunter_open_questions`), and a personal-line ask stays queued; a session
  runs `python -m hunter.run questions` and puts them to him, writes an Info
  Bank answer to the tab and a personal line with `facts.answer`. Never his US
  or UK pick and never an essay. Facts about him are not questions: an over-18
  question answers itself from his age (`resolve._adult`; "I am 39, you asked
  me this last week too"). The log stays counts.
- **No question twice, ever** (his words, 2026-10-20). Every answer he gives
  in chat goes where hunter reads it: an Info Bank row with the form's own
  wording, or canon 9.14. A form that words it differently gets his answer
  from the closest Info Bank row (`AnswerBank.closest`, flagged so he sees it
  in the Apply email), and a kind of business he has given any line for uses
  that line rather than asking again (`personal.match`).
- **A form he opened on an older package is a question, never silence**
  (2026-10-21, Replit: "how comes I never got the updated Replit role to apply
  to?"). The style rebuild leaves it alone and records `FORM_OPENED_QUESTION`
  for chat: has he applied, or should the rewritten one go? A stored date
  number (46311) comes back from Applied as the date (`sheet.iso_date`).
- **A form is read from the link a board can serve** (`run.form_url`; his
  words 2026-10-21, "they were just one click away"). An embedded Greenhouse
  posting's own address is the company's careers page, which no adapter reads;
  Actively, iFIT and Nebius went to him as apply by hand while their record
  held the board link. A LinkedIn posting is Easy Apply only when its page
  says so (`linkedin.apply_mode`); an offsite one says it sends him to the
  company's site.
- **The masters change with canon 9.9, together**, or every run aborts. Drains
  start at :13 and finish by :15; switch just after.
- **Previews before changes he will read.** `build-preview --queue`, then the
  writer answers, then `build-preview --apply --preview-folder --label vN`
  builds into Previews/vN under his Hunter folder and touches no role. Read the built
  documents back from Drive before telling him they are right.

## 3i. A package is written for its reader, at his altitude

His words, 2026-10-19, on the Replit Head of Corporate Development package:
"it drones on and on about Supabase and N8N ... the less specificity the
better" and "this is a Corporate Development role, and you are goin on and on
about me building widgets and AI agents". Then: "you're fixing symptoms, not
causes". Every check before then asked whether a letter was true and well
formed; none asked whether it persuades the person who reads it, and 28 of
the 31 letters written in the house style named a tool or an internal.

- **The reader comes first.** Every letter answer carries `reader` (who reads
  it, what they are accountable for, the 3 things they must believe, each
  quoted from the posting, and their doubts), `map` (the belief each part
  builds) and `review` (read as that reader, the words in any part that feed a
  doubt). `compose.reader_problems` (A17) refuses an answer without them, a
  belief no part builds, and a part whose review is not empty. The form
  answers get the same brief (`run.essay_reader`).
- **Altitude, not a list of banned words** (`package/proof.py`). Facts keep
  their truth for the gate and are marked for the writer with what they prove
  and the mechanics that stay out of prose: tools, vendors, agent and workflow
  counts, internals. A18 refuses mechanics the posting does not name, and his
  AI system in more than one part outside an AI transformation seat (his
  ruling that day), per letter and per form (`essays.form_problems`).
- **What kind of seat** comes from the judge's family, or from the title when
  the judge never read the role (`proof.family_of`); `compose.ROLE_TYPES`
  says what each kind of seat buys.
- **His rulings and his cuts are kept** (`package/hisrules.py`, system_config
  `hunter_package_rules`, never this public repo; `docedits.his_cuts`). Every
  brief carries every rule as a doubt. Record a ruling when he makes one.
- **The bar is `tests/test_package_taste.py`**, against
  `tests/fixtures/package_verdicts.json`: what he rejected must be refused,
  the lines he writes himself must pass. Refresh it when he rules on a package.

## 3j. The order he reads is fitted to his own scores

His words, 2026-10-20, sending four roles hunter had missed: "I feel at this
stage we need to create a more sophisticated algorithmic scoring mechanism."
The AI Score (score.py, a hand-written point table) ranked his 61 scores at
Spearman -0.19.

- **`rankscore.py`** is a ridge regression on signals he named or the record
  carries (New York, in person, first of the role, reporting to a founder,
  seat family, AI-native, pay, the judge's fit), fitted to his Krish Scores.
  No signal he named may count against a role (`HIS_SIGNALS`).
- **Measured before it moved anything**: fitted on his earlier scores, it
  ranks the 22 later ones at 0.33 against -0.15 for the AI Score and 0.27 for
  the judge (`tests/test_rank_eval.py`, `tests/fixtures/rank_eval.json`,
  signals and scores only). `rerank --apply` refits on every score, switches
  on only if it still wins, rescores Pipeline and sorts.
- **What it moves is the AI Score he sees** (`run.shown_score`); gates and
  merit still read score.py. The weights live in system_config
  `hunter_rank_weights`. Refit when he has scored a new batch.
- **Postings he sends himself** go through `stage-sent`, past the gates (his
  ruling on the seat), and a person he names for a role becomes a Control
  Center card (`doorin.named_card`). Freshness is recorded
  (`posted_at`, `posted_source`) and shown at AG, Posted (canon 9.13 since
  2026-10-20): FRESH and the date for 7 days, "updated" for a date that is
  only a last change, unknown otherwise, kept true hourly (`posted-refresh`).
  Applied moved to Archived On at AH (`sheet.LAYOUTS`, the "before_posted"
  migration), and the Blind Set reads its Ref at AH, or AG on an older tab.

## 3h. Every Drive file has a registered home

His words, 2026-10-18: "ensure it is impossible to save files randomly in
sprawl in my Drive - they must go in specific folders, subfolders, always".
Until then `DocBuild.folder` defaulted to the top of his Drive, `copy_master`
filed a copy wherever Drive put it, and live applications went to a folder his
estate clean-up had filed under ARCHIVE.

- **The registry is `system_config drive_destinations`** (`drivemap.py`), never
  this public repo: his top-level items, the roots (Ventures, --ARCHIVE, Family
  Admin), the protected folders (FamilyContentLibrary, KeePass: "KeePass and
  FamilyContentLibrary are immovable"), his inbox `Inbox from My AI` at the top
  ("just reinstate that in the root folder then", 2026-10-24), and Hunter's
  three folders under his Hunter folder (Family Admin/Krish/Job-Search/Hunter
  since October 2026). The layout is krishanraja/filearchives D-033; CURRENT
  is gone (D-031). `drive-map --apply` writes it from his Drive and reads it
  back, keeping every folder the registry already holds at its id while it
  still sits where it must (Ruling, Krish, 2026-10-24: folders are found by ID,
  never by path), so a rename or move inside a registered area loses nothing.
  Another system's destination is kept the same way, and `AREA_FOLDERS` names
  his areas other systems write into, found by name and never made:
  `bugs_and_issues` is Ventures/Bugs & Issues, where a feedback video's stills
  go (content-engine api/ux-feedback, 2026-10-28).
- **DocBuild creates nothing without it.** `copy_master`, `upload_pdf` and
  `folder` take a parent with no default, refuse one `drivemap.allow` did not
  prove inside a registered root, and read back where the new file landed.
  Drive writes live only in DocBuild's four private helpers; a test reads the
  source to keep it so.
- **One folder per application** (his choice), `Applications/<YYYY-MM> <Company>
  - <Role>`, holding the CV, the letter and both PDFs; templates in `Masters/`,
  previews in `Previews/<label>`.
- **The hourly `drive-sweep`** files anything new at the top of his Drive, from
  any app or session, into Inbox from My AI, reads it back and tells him in one
  line. It never deletes and never touches his own top-level items,
  FamilyContentLibrary or KeePass, and refuses to run while the inbox is not a
  folder at the top. Its log carries counts only.
- **Claude sessions** in this repo and control-center run
  `.claude/hooks/drive_guard.py` before any Google Drive create, copy or move: no
  parent, the top of his Drive, a top-level area itself or a protected folder is
  refused, and an unreadable registry refuses too. Surfaces with no hooks
  (claude.ai chat, Cowork) are covered by the sweep.

## 4. The Target Companies tab is his, and it is policy

53 companies, five categories, a tier each, and a TIER LEGEND in his own
words saying what each tier means for sweeping. No code read any of it while
canon 9.1 held a second copy that had already drifted by three names.

- `targets.py` reads it every run and it wins over canon 9.1. The drift is
  filed as a `workflow_proposals` row; canon is never edited from code. The
  one exception is his own words: `canonwrite.py` appends his reply to a
  personal-line ask to canon 9.14 exactly as he wrote it, version locked and
  read back, and takes it back on his UNDO (his ruling 2026-10-15).
- **Columns A to H are his.** Hunter writes Score, Computed Tier, Board, Last
  Swept, Roles Seen, Yes, No and Evidence to the right of them, and reads
  them back.
- Proposals go in a capped block BELOW his list. He adopts one by typing a
  tier.

## 5. Watch the accept rate, or the drift comes back

`batchstats.py`, and `python -m hunter.run stats`. Every batch was recorded
while the rate fell 77 to 6 percent; the rate itself was not, so nothing
could see the line going down.

- The batch is `presented_at`, never `status`: a row's status keeps moving
  after he sees it, and filtering on "staging" reported the 6 August batch at
  100 percent because its declines had moved on.
- The verdict comes from `hunter_verdict_events`, never
  `hunter_seen_roles.krish_verdict`. That column is only written when
  reconcile pairs a sheet row to a database row, which is the same failure
  that left 89 declines out of the learning loop.
- A batch he has barely judged has NO rate, not a rate of zero.
- Applied counts as accepted (`verdicts.is_accept`). Until 2026-10-14 it
  did not, so every batch he applied from read lower than it was.
- Since he scores instead of ruling, the score is watched too: share scored
  7 or more per batch, its floor (`batchstats.SCORE_FLOOR`), and the Spearman
  of the AI Score he was shown against his score. That last one, on batches
  scored after they were shown, is the prospective measure of the predictor.
- The invariants check for it has no repair, deliberately. What to change
  when the funnel drifts is a judgement, and it is his.

---

## 6. What Krish downloads is `main`

`extension/` is loaded unpacked in his Chrome. **Chrome never updates an unpacked
extension.** His folder is frozen at whatever ZIP he last downloaded, and the ZIP
comes from `main`.

This cost a full round: the demographics code sat on a branch, `main` was two
commits behind it, and his extension physically had no `actDemographic` in it
while the banner told him everything was filled.

So:

- **Merge to `main` before telling him to download.** A fix on a branch does not
  reach him.
- **Bump `extension/manifest.json` version and `payload.MIN_EXTENSION` together**
  whenever the extension gains an ability a payload relies on. A test ties the two
  numbers so they cannot drift. Below the floor the banner goes red and names the
  versions, which is the only way a stale copy can announce itself.
- **Tell him in chat, every time, that he needs to re-download.** His standing
  instruction, in his words: "always let me know when I need to redownload the
  extension". The banner is the backstop, not the notice.

The steps to give him, every time:

1. Download <https://github.com/krishanraja/hunter/releases/download/extension/hunter-extension.zip>
2. Extract it. Inside is a folder called `extension`, holding `manifest.json`
3. Copy that `extension` folder over the one Chrome already points at and press
   the reload arrow on the Hunter card at `chrome://extensions`. Extracted
   somewhere new instead: remove the card, Load unpacked, pick the new folder
4. Check the card reads the new version. Still the old one means Chrome is
   pointing at the old copy and the reload did nothing
5. Reopen the link from the email

The zip holds five text files and no scripts, on purpose. The whole repo
archive stopped being downloadable on 2026-09-24 when Windows Defender refused
it, and `windows/setup.bat` copying itself into the Startup folder is the
likely reason. Nothing executable may reach him through that link, and the
publishing workflow refuses to publish if anything does.

---

## 7. Scheduled work runs from the default branch

GitHub Actions reads `.github/workflows/` from `main`, not from your branch. His
APPROVE reply sat unread for hours because `approvals-drain` existed only on a
branch. If a change needs to run on a schedule, it has to be merged.

---

## 8. Both halves ship, or neither works

The loop crosses two repositories:

- **hunter** builds the package, sends the email, and writes the Google Sheet.
- **control-center** serves `api/hunter/payload` to the extension and accepts
  `api/hunter/submitted` back from it.

`api/hunter/submitted.ts` was written, committed locally, and never pushed. The
extension POSTed to an endpoint that did not exist, so the sheet kept saying "Not
applied". Check both remotes before saying a loop closes. `python -m hunter.run
doctor` does exactly that and is the fastest way to be sure.

---

## 9. The last click is his

Nothing in this repository may press Submit on a job application. `submit.py`
presses only with `confirm=True`, `open_for_human` is asserted not to contain a
press by reading its own bytecode, and a test reads `extension/fill.js` and
`extension/run.js` to prove neither file references a submit control.

Related, and settled: **do not work around bot detection.** Masking
`navigator.webdriver` or spoofing a user agent was built once and reverted. Ashby
fingerprints, and a flagged fingerprint would follow him across every employer
using Ashby. The extension filling his own browser is the answer, and it is also
what he asked for.

---

## 10. House rules

- **Secrets live in Supabase `system_config`** and are read at runtime. The
  environment carries exactly two values, `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY`. Never commit a secret, never print one.
- **No em dashes anywhere, including in code comments.** Enforced by
  `tests/test_repo_guards.py`.
- **Generated prose passes the voice gate.** Every number and every company name
  in generated text must trace to recorded evidence (`package/voicegate.py`). An
  untraceable claim is a hard failure, not a warning. Banned phrases go in the
  prompt as well as the gate, or the model cannot avoid them.
- **The tests run offline.** No sockets. `pytest` must pass with no network.
- **One approval per posting, not per row.** The job id scheme changed once and he
  received the same application twice. `posting_key` and `live_postings` exist for
  that. The key is the posting's identity (board and posting id, a gh_jid, a
  LinkedIn job number, or the link with only the query parameters that name a
  posting), never the link cut at "?", which made every posting on one careers
  path one posting, and never the link less a list of tracking parameters, which
  made one LinkedIn job two.
- **A role is company, title and country** (`sources.placed_identity_keys`). On
  company and title alone, Sierra's London Regional VP was filed as the Seoul one,
  and the Suno London role he asked about as the New York one he had declined:
  22 roles in all, none ever shown. A different city in the same country stays one
  role, an unknown place matches anything, and a second country gets its own row
  id (`place_suffix`), because the bare job id is company and title only.
- **Dates written to Sheets use `valueInputOption: RAW`.** An ISO date otherwise
  arrives as the number 46280.

---

## 11. Talking to him

He is a senior operator and he is paying for this in hours of his life.

- Lead with the answer. The diagnosis goes second, in one or two sentences.
- No commit-message prose in chat. He said "dont understand your last point" and
  "huh?" to exactly that.
- Do not explain the architecture when he asked for the step. He said "It's insane
  that I have to keep saying this" after four hours of it. Remove the step instead
  of describing it.
- Never claim it works before checking the last mile on his machine. He said "Why
  do you keep pretending?" and he was right.
- When something is broken, say what is broken, what you did, and what is left.

---

## 12. Where things are

```
src/hunter/
  run.py              every command, and the wiring between them
  config.py           Supabase reads, the two credential planes
  apply/
    submit.py         the ONLY module that can submit. Playwright drivers
    payload.py        what the browser extension needs, and MIN_EXTENSION
    fill.py           FillPlan: one posting's resolved answer set
    essays.py         drafts the open questions
    approval.py       tokens, states, the plan hash
    infobank.py       his recorded answers
  drafts.py         prose queued for the writing routine on his subscription, checked before use
  actions.py        what he pressed in Control Center, applied to the sheet by hunter
  judge.py          would he want this role: one model call, quoted, checked
  judge_stage.py    the judge inside staging: off, shadow or gate
  judge_eval.py     the judge measured against his rulings, chronologically
  judgedata.py      every role he ruled on, with the posting, for the judge
  blindset.py       roles he has never seen, ruled on blind, to measure the judge
  universe.py       the companies he wants, and his rules for a role at one
  lookalike.py      venture-backed companies scored against his list, once
  radar.py          the Company Radar tab: companies, boards, open roles
  companybar.py     a business scored once, so a plainly wrong one costs no call
  data/ai_universe.csv  the hundred AI companies he listed, 2026-10-03
  data/a16z_consumer_top50.csv  32 more he sent 2026-10-07, same standing as his list
  schedule.py       which batch is owed, decided from the run record
  company.py        is the BUSINESS worth his time. Five components, evidenced
  companyintel.py   where those facts come from, free, and always cited
  sources/consider.py  Sequoia's board: every open role, with pay, and its boards
  prospect.py       companies he has not named, found in what hunter discards
  targets.py        his Target Companies tab, read as policy and written back
  batchstats.py     accept rate per batch. The number nobody was watching
  ratings.py        his Krish Score and Reaction, recorded per role, never read as verdicts
  leave.py          what leaves Pipeline for Applied on his score, and what comes back
  llm.py            one door to a model, with a second provider behind it
  spend.py          what every model call cost, and the month's ceiling
  employer.py       what kind of company, on evidence not on a taxonomy
  comp.py           reading pay out of a posting that had no pay field
  invariants.py     what must always be true of the sheet, and the repairs
  amend.py          learning from what he changed, not only what he rejected
  alerts.py         the two emails he gets, deduplicated on the condition
  letters.py        a package in his house style, from hold to build
  docedits.py       every edit he makes to a letter or CV, learned from
  drivemap.py       where every file in his Drive goes, the guard and the hourly sweep
  facts.py          asks him for a personal line, writes his reply to canon 9.14
  canonwrite.py     the one code path that edits canon: his words into 9.14
  package/
    compose.py      his letter and CV for one role: the brief, the checks, the build
    contact.py      which city, phone and spelling a role takes
    personal.py     which of his personal lines a business needs
    house.py        his house style, read back from the built document
    masters.py      the letter and CV masters made from his own letter
    style.py        the house style stamp, and the pause
    build.py        CV and letter, from the masters (before his house style)
    tailor.py       block selection, the hook (before his house style)
    voicegate.py    every generated string passes this
extension/            loaded unpacked in his Chrome. main is what he downloads
docs/DOOR_IN.md       leaders first, roles second: the thesis and plan
docs/ONE_SYSTEM.md    the four layers, where every command lives, and the record of the move
docs/ROUTINE_WRITER.md  the subscription routine that writes the prose, and what it may touch
tests/                1400 tests, offline
  test_taste.py       the bar, measured against his own verdicts
  test_company_taste.py          the company bar, against the companies he chose
  fixtures/krish_verdicts.json   154 roles he ruled on. Ground truth
  fixtures/krish_companies.json  138 companies he named or declined. Ground truth
```
