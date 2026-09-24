# hunter: the operating contract

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

## 4. The Target Companies tab is his, and it is policy

53 companies, five categories, a tier each, and a TIER LEGEND in his own
words saying what each tier means for sweeping. No code read any of it while
canon 9.1 held a second copy that had already drifted by three names.

- `targets.py` reads it every run and it wins over canon 9.1. The drift is
  filed as a `workflow_proposals` row; canon is never edited from code.
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
  that.
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
  company.py        is the BUSINESS worth his time. Five components, evidenced
  companyintel.py   where those facts come from, free, and always cited
  prospect.py       companies he has not named, found in what hunter discards
  targets.py        his Target Companies tab, read as policy and written back
  batchstats.py     accept rate per batch. The number nobody was watching
  llm.py            one door to a model, with a second provider behind it
  employer.py       what kind of company, on evidence not on a taxonomy
  comp.py           reading pay out of a posting that had no pay field
  invariants.py     what must always be true of the sheet, and the repairs
  amend.py          learning from what he changed, not only what he rejected
  alerts.py         the two emails he gets, deduplicated on the condition
  package/
    build.py        CV and letter, from the masters
    tailor.py       block selection, the hook
    voicegate.py    every generated string passes this
extension/            loaded unpacked in his Chrome. main is what he downloads
tests/                1041 tests, offline
  test_taste.py       the bar, measured against his own verdicts
  test_company_taste.py          the company bar, against the companies he chose
  fixtures/krish_verdicts.json   154 roles he ruled on. Ground truth
  fixtures/krish_companies.json  138 companies he named or declined. Ground truth
```
