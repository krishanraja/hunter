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

## 3. What Krish downloads is `main`

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

1. Download <https://github.com/krishanraja/hunter/archive/refs/heads/main.zip>
2. Extract it over the `hunter` folder, replacing the files
3. At `chrome://extensions`, press the reload arrow on the Hunter card
4. Reopen the link from the email

---

## 4. Scheduled work runs from the default branch

GitHub Actions reads `.github/workflows/` from `main`, not from your branch. His
APPROVE reply sat unread for hours because `approvals-drain` existed only on a
branch. If a change needs to run on a schedule, it has to be merged.

---

## 5. Both halves ship, or neither works

The loop crosses two repositories:

- **hunter** builds the package, sends the email, and writes the Google Sheet.
- **control-center** serves `api/hunter/payload` to the extension and accepts
  `api/hunter/submitted` back from it.

`api/hunter/submitted.ts` was written, committed locally, and never pushed. The
extension POSTed to an endpoint that did not exist, so the sheet kept saying "Not
applied". Check both remotes before saying a loop closes. `python -m hunter.run
doctor` does exactly that and is the fastest way to be sure.

---

## 6. The last click is his

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

## 7. House rules

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

## 8. Talking to him

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

## 9. Where things are

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
tests/                905 tests, offline
  test_taste.py       the bar, measured against his own verdicts
  fixtures/krish_verdicts.json   148 roles he ruled on. Ground truth
```
