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

## 2. What Krish downloads is `main`

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

## 3. Scheduled work runs from the default branch

GitHub Actions reads `.github/workflows/` from `main`, not from your branch. His
APPROVE reply sat unread for hours because `approvals-drain` existed only on a
branch. If a change needs to run on a schedule, it has to be merged.

---

## 4. Both halves ship, or neither works

The loop crosses two repositories:

- **hunter** builds the package, sends the email, and writes the Google Sheet.
- **control-center** serves `api/hunter/payload` to the extension and accepts
  `api/hunter/submitted` back from it.

`api/hunter/submitted.ts` was written, committed locally, and never pushed. The
extension POSTed to an endpoint that did not exist, so the sheet kept saying "Not
applied". Check both remotes before saying a loop closes. `python -m hunter.run
doctor` does exactly that and is the fastest way to be sure.

---

## 5. The last click is his

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

## 6. House rules

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

## 7. Talking to him

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

## 8. Where things are

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
  package/
    build.py          CV and letter, from the masters
    tailor.py         block selection, the hook
    voicegate.py      every generated string passes this
extension/            loaded unpacked in his Chrome. main is what he downloads
tests/                743 tests, offline
```
