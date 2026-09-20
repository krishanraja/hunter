# Every way this loop breaks, and what happens when it does

Krish, 2026-09-19: "map and proactively cater for any edge cases you can think
of, guess them all".

The loop is one sentence. He changes column A twice a week; everything else
happens without him. This file is the list of things that can stop that
sentence being true, what notices each one, and what it does about it.

Three outcomes, and every row below is one of them:

- **repaired** - hunter fixes it on the next run without being asked.
- **reported** - hunter cannot fix it without guessing, so it goes to him in
  the run email or the trouble email, named, with what he has to decide.
- **open** - known, not yet covered. Listed here so it is a decision rather
  than a surprise.

Nothing is silent. A case that is neither repaired nor reported is a bug in
this file.

---

## 1. The sheet

| # | What happens | What notices | What it does |
|---|---|---|---|
| 1.1 | He types a verdict the dropdown does not offer | `verdicts.parse` | Read as a rejection in his own words, quoted verbatim, no code inferred. **repaired** (it always worked) |
| 1.2 | He types something too short to learn from ("nah", "no") | `invariants.check_verdict_vocabulary` | Named in the run email with its row, so he can say why. **reported** |
| 1.3 | He deletes a row instead of declining it | `reconcile` direction 2, `presented_at` set and the row on neither tab | Recorded declined, reason "removed from the Pipeline tab by Krish", never re-appended. `restore <job_id>` reverses it. **repaired** |
| 1.4 | He reorders or sorts the tab by hand mid-run | every writer re-reads column A immediately before it writes | A row that moved fails the read-back and the batch aborts rather than writing to the wrong role. **repaired** |
| 1.5 | He edits column A while a run is in flight | `sheet.delete_rows` and `archive_rows` re-read and compare | Refuses the whole batch and says which rows changed. **repaired** |
| 1.6 | Rows appended below the validated range lose the dropdown | `invariants.check_dropdown_reaches_the_end` | Validation re-applied to the whole grid. This one cost weeks before it was found. **repaired** |
| 1.7 | A score is stored as text, so the sort reads 9, 9, 6, 10 | `invariants.check_scores_numeric` | Rewritten as an integer, column re-formatted as a number. **repaired** |
| 1.8 | A blank cell where canon 9.13 requires a default | `invariants.check_no_empty_cells` | Filled with the canon default. **repaired** |
| 1.9 | A blank row in the middle of the block from a paste | `sheet.delete_blank_rows` inside `sort_by_score` | Removed, and only after re-reading that it is still blank. **repaired** |
| 1.10 | The same posting on two rows under two spellings | `invariants.check_no_duplicate_postings`, keyed on ATS identity | The twin with the most standing survives; the rest are stamped `Declined - duplicate row` and archived. Only rows still reading New are touched. **repaired** |
| 1.11 | He drags a column wider, or unhides one | `layout.apply_layout` at the end of every run | Put back. The layout is absolute, not a diff. **repaired** |
| 1.12 | The header row is edited or renamed | `canon.load_canon` plus `sheet.read_pipeline` | The run aborts before touching anything and says which column disagrees with canon 9.13. **reported** |
| 1.13 | Applied tab and Pipeline tab drift to different widths | `migrate_columns` column state check | Named, with the migration to run. **reported** |
| 1.14 | The tab runs out of rows mid-append | `invariants.check_row_headroom` | Warned at under 100 spare rows, before an append can fail. **reported** |
| 1.15 | Column A says Applied but Application Status does not | `invariants.check_applied_state_agrees` | Column T rewritten to follow column A. Column A always wins. **repaired** |
| 1.16 | An archived row carries no date | `invariants.check_archive_stamped` | Counted and reported. Rows predating the Archived On column are the known 135. **reported** |
| 1.17 | He pastes a role he found himself, with just a link | `reconcile` direction 1 | Inserted into `hunter_seen_roles`, then scored, gated and built like any other. **repaired** |
| 1.18 | Sheets returns 429 or 500 mid-write | `requests` raises, the phase is caught, later phases still run | The run finishes what it can and says what failed. Next run picks it up. **repaired** |

## 1a. The quality bar

Added 2026-09-20, after Krish opened the sheet to SiriusXM, Citi, Omnicom and a
$140,000 Lead seat scored 10. Every case here was measured against
`tests/fixtures/krish_verdicts.json`, 148 roles he ruled on, before it was
written.

| # | What happens | What notices | What it does |
|---|---|---|---|
| 1a.1 | Sourcing drifts to whoever posts the most jobs | the source mix in `workflow_runs` | The a16z portfolio index is probed directly for readable boards and swept in full, so the primary source is 859 AI-native companies rather than a keyword search. **repaired** |
| 1a.2 | A bank hiring for AI passes the gate that blocks banks | G7 asks about the employer, not the posting text | "Artificial intelligence" in a JD is no longer an exemption. Citi, BNY, TIAA, New York Life all fail now. **repaired** |
| 1a.3 | A megacap scores higher for being a megacap | `STAGE_OK` no longer matches public, nasdaq, nyse, ipo | Being listed earns nothing. **repaired** |
| 1a.4 | A $140,000 seat looks like a $350,000 seat | 92 percent of roles arrived with no pay at all | `comp.py` reads the band out of the posting body, where US transparency law puts it. **repaired** |
| 1a.5 | A revenue or funding figure is read as pay | `comp.NOT_SALARY`, plus a plausibility band | Left alone. A wrong number auto-rejects a role he wants, which is worse than reading nothing. **repaired** |
| 1a.6 | A band whose bottom dips under the floor | G2 reads the ceiling, not the bottom | Phantom at $165K to $280K passes; AKASA at $150K to $185K fails. **repaired** |
| 1a.7 | A base band with variable stacked on top | `band_tops_out_at` returns None | Flagged for review rather than failed. Talkspace at $170K to $190K base plus variable is a role he approved. **repaired** |
| 1a.8 | A sector rule blocks roles he wants | the measurement, before the rule ships | Healthcare, consultancy and agency blocklists were all written and deleted: they would have killed BioSpace, Recursion, Talkspace, Harvey and Razorfish. Only banks and insurers are a gate. **repaired** |
| 1a.9 | A title rule blocks seats he wants | the measurement, before the rule ships | A junior-seat rule for Lead and Manager was written and deleted: he approved three Lead seats at $205K to $360K and every General Manager seat on the sheet. **repaired** |
| 1a.10 | A company hunter knows nothing about | `employer.UNKNOWN`, worth zero | Reaches him. Refusing needs evidence; ranking does not. **repaired** |
| 1a.11 | A company he declined posts an ideal role | G12 and G13 read the role's merit, its score with the employer left out | Shown anyway above merit 9, with the reason on the row. An absolute block would have hidden the Citi role he approved, and judging it on the ordinary score is circular because the company penalty is what stops it clearing. **repaired** |
| 1a.12 | A verdict of his is lost because the row never paired | `learn.from_sheet_rows` reads the sheet, which is the record, rather than requiring a database match | 89 of his declines had no learning event at all and G12 was running on 11 companies instead of 36, Citi among the missing. **repaired** |
| 1a.13 | The bar drifts again | `tests/test_taste.py` | Prints how many of his approvals and declines the bar blocks, and fails when it blocks an approval. **repaired** |
| 1a.14 | His taste changes | the fixture goes stale | Refresh it when he verdicts a batch. Doing so on 2026-09-20 broke the institution gate within the hour, which is the file working. Still a manual step. **open** |
| 1a.15 | Merit 9 is a judgement, not a derivation | nothing | One example of an ideal role at a declined company is all his data holds. The threshold is one constant in `gates.EXCEPTIONAL_MERIT` and wants tuning once real runs have produced more. **open** |

## 2. Sourcing

| # | What happens | What notices | What it does |
|---|---|---|---|
| 2.1 | A sourcing run stages nothing at all | `cmd_run` marks the run failed if nothing was recorded | Failed run, visible in `workflow_runs`. **reported** |
| 2.2 | Sourcing reads postings and stages none, twice running | `alerts.trouble_checks` | Trouble email: either the gates are too tight or a source changed shape. This is the exact bug that cost a week in September. **reported** |
| 2.3 | The Apify actor changes its output shape | postings resolve to nothing, so 2.2 fires | As 2.2. **reported** |
| 2.4 | The Apify token is missing or out of credit | `preflight.check_sourcing_budget` | Warned before the run starts rather than after it finds nothing. **reported** |
| 2.5 | A role he already declined comes back next week | dedupe reads the archive too, not only Pipeline | Never re-staged. **repaired** |
| 2.6 | A company he declined at company level posts again | gate G12 | Blocked before any fetch, with the date and code he declined it on. `hunter_company_allow` reverses. **repaired** |
| 2.7 | A role he already applied to appears under a new job_id | ATS key dedupe plus `learn.open_applications` | Collapsed, or surfaced with a note that an application is already open there. **repaired** |
| 2.8 | A posting dies between sourcing and his review | `verify`, and `build_one` re-checks liveness | Row marked `Posting dead, cannot build` and archived if he has not ruled on it. **repaired** |
| 2.9 | A posting with no comp or no location | canon defaults | "Not disclosed", "Not stated". Never blank. **repaired** |
| 2.10 | A LinkedIn posting with no ATS board behind it | `verify` reports UNVERIFIABLE, never LIVE | Staged with the description the sweep already carries, and honestly labelled. 99 rows are in this state today. **reported** |
| 2.11 | A posting hunter can stage but cannot fill a form for | Package Status carries the reason | He applies on the site himself; the pack is still built. **reported** |

## 3. Packages and applications

| # | What happens | What notices | What it does |
|---|---|---|---|
| 3.1 | Two roles at one company collide on the document title | `_unique_title`, three fallbacks ending in a date stamp | Built anyway. **repaired** |
| 3.2 | The Google OAuth refresh token is revoked | `preflight.check_documents` | The run stops before spending anything and names the command to re-consent. **reported** |
| 3.3 | The service account loses write access to the workbook | `preflight.check_sheets` | Same. **reported** |
| 3.4 | A master CV or letter is moved or deleted | `preflight.check_documents` plus canon 9.9 id check | Same, and canon is checked against the constants so neither side silently wins. **reported** |
| 3.5 | He edits the generated letter before sending | `amend.detect_docs` | The diff is recorded as a correction and quoted back. Three of them in one field becomes a question. **repaired** |
| 3.6 | An approval email sits unanswered | `alerts.trouble_checks`, over 2 days | Trouble email naming each waiting application. **reported** |
| 3.7 | He approves a role whose posting has since closed | `submit` re-verifies liveness at send time | Refused, with the reason. **repaired** |
| 3.8 | The browser extension is older than the payload needs | `payload.MIN_EXTENSION`, `doctor.check_extension_on_main` | The form shows the out of date banner instead of half filling. **reported** |
| 3.9 | A form's fields changed since the audit | `fill` reports what it could not fill | Named in the approval email before he presses anything. **reported** |
| 3.10 | He applies outside the system | `confirmations` reads the employer receipt from Gmail | Row marked Applied and archived. **repaired** |
| 3.11 | Gmail send scope is not granted | `notify.send_email` falls back, `preflight.check_mail` warns | Work still happens, and he is told the channel is down. **reported** |
| 3.12 | A build throws halfway | each build is caught individually | That role is marked blocked with the error class; the other builds still run. **repaired** |

## 4. Learning

| # | What happens | What notices | What it does |
|---|---|---|---|
| 4.1 | Hunter learns from a verdict it wrote itself | `learn.is_auto`, `verdict_source` | Excluded. Forty auto verdicts were once about to blocklist Sierra, Decagon, Cloudflare and Synthesia on no input from him. **repaired** |
| 4.2 | Hunter reads its own edit as his amendment | `amend.sync_after` re-snapshots at the end of every pass | Impossible by construction. **repaired** |
| 4.3 | He changes a cell then changes it back | unique on (job_id, field, the text he ended up with) | Both changes recorded, each once. **repaired** |
| 4.4 | A whitespace or smart quote difference | `amend.norm` | Not an amendment. **repaired** |
| 4.5 | One correction becomes a standing rule | `amend.proposals` needs three | One is quoted as evidence, three becomes a question, and a question still waits for him. **repaired** |
| 4.6 | His scores disagree with the scorer consistently | `amend.score_calibration` | Reported with the average gap and which way. **reported** |
| 4.7 | A rejection the gates should have caught | `learn.system_findings` | Becomes a named gate fix and a regression test, without asking. **repaired** |

## 5. People and the Hunt lane

| # | What happens | What notices | What it does |
|---|---|---|---|
| 5.1 | A bridge into a role he has since declined or applied to | `bridges.purge_orphan_bridges` | Deleted. 126 went on the first pass. **repaired** |
| 5.2 | A bridge into a job_id that no longer exists | same | Deleted. **repaired** |
| 5.3 | A bridge he marked reached out or not a path | the purge only ever touches `state = proposed` | Kept. His history is his. **repaired** |
| 5.4 | An approved role falls outside the bridge window and churns | `target_roles` caps only the unjudged half | Every Yes is a target, with no cap. **repaired** |
| 5.5 | `warm_path_person` holds a sentence rather than a person | `router.is_warm_path`, `clear_junk_warm_paths` | "None identified with a current connection" is not a person. **repaired** |
| 5.6 | A cold target the model invented | a LinkedIn URL and a source URL are both required | Dropped without them. **repaired** |
| 5.7 | The same person in both graphs | dedupe on normalised LinkedIn URL | One row. **repaired** |
| 5.8 | A contact whose job changed months ago | freshness line on the lane | Shown with its date, not presented as current. **reported** |

## 6. The runtime

| # | What happens | What notices | What it does |
|---|---|---|---|
| 6.1 | The schedule stops firing | `alerts.trouble_checks`, no successful run in 8 days | Trouble email. Eight days means a whole cycle was missed. **reported** |
| 6.2 | Two runs overlap | the Actions `concurrency: hunter` group | The second waits. Two hunters on one sheet is how rows get duplicated. **repaired** |
| 6.3 | A run dies halfway | every phase is independent and idempotent | The next run picks up where it stopped. **repaired** |
| 6.4 | Runs fail repeatedly | `alerts.trouble_checks`, 2 of the last 3 | Trouble email carrying the first error. **reported** |
| 6.5 | British Summer Time moves 13:00 London | two Sunday crons, and the job reads the real London clock | The wrong one exits before doing anything. Without this the Sunday run silently becomes a midday run every winter. **repaired** |
| 6.6 | A repository secret is rotated | the run fails at preflight | 6.1 and 6.4 carry it to him. **reported** |
| 6.7 | Actions disables the schedule after 60 days of no repo activity | 6.1 | The eight day stall alert fires first. **reported** |
| 6.8 | The same alert fires every hour | `hunter_alerts`, keyed on the condition not the moment | Sent once. An alert that repeats stops being read. **repaired** |
| 6.9 | A deployed half is behind the built half | `doctor` | Three drifts of this kind cost a day each. **reported** |

---

## Still open

Named here rather than pretended away.

- **The archive has no date on 135 rows.** They predate the Archived On
  column. Back-filling would invent a date, so they stay blank and counted.
- **99 rows cannot be verified live.** They are LinkedIn postings with no ATS
  board behind them. Hunter says UNVERIFIABLE rather than LIVE, which is
  honest but still leaves him judging a row that may be gone. Closing this
  needs a per-posting liveness check against LinkedIn itself.
- **A role can be dead and approved at once.** Hunter never archives a role he
  approved, so it sits with `Posting dead, cannot build` until he decides.
  That is deliberate and it is still a row he has to clear by hand.
- **The Applied tab grows without bound.** At the current rate the row limit
  is years away, and 1.14 warns before it matters.
- **Nothing watches the Apify balance directly.** 2.4 sees a missing token, not
  an empty account; an empty account presents as 2.2 a week later.
- **The labelled set is a snapshot.** 148 roles from 2026-09-20. If his taste
  moves, the tests keep enforcing the old taste and nothing says so. Refreshing
  the fixture after each batch of verdicts is currently a manual step.
- **The job description is still not stored.** `hunter_seen_roles` keeps the
  score and the rationale but not the text they came from, so a role cannot be
  replayed through the full scorer offline and `test_taste.py` can only measure
  the employer, the seat and the band. Storing it would make the whole scorer
  testable against his verdicts.
- **About 30 percent of the a16z portfolio boards somewhere hunter cannot
  read.** Ashby, Greenhouse and Lever are covered; Workday, Rippling, Gem and
  in-house boards are not. Those companies are in the index and invisible to
  the sweep.
