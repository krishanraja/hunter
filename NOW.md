---
repo: krishanraja/hunter
product: Hunter
as_of: 2026-10-24
head: 50946ae
lifecycle: live
production_url: none
state_doc: README.md
history_log: docs/history/LOG.md
truth_files: [docs/NORTH_STAR.md]
authority_order: [docs/NORTH_STAR.md, README.md]
steward: https://github.com/krishanraja/ai-harness/blob/main/docs/steward/RUNBOOK.md
---

# Hunter: where it is right now

## What it is

Krish's job search engine: it sources senior roles, scores them against his canon and builds the CV and cover letter for each. Krish sends.

## Who it is for and why it matters for Mindmake

Krish himself. It only surfaces roles that are literally the mission with a salary.

## Where it is right now (as of 2026-10-24)

<!-- north-star-state {"week_of": "2026-10-26", "week_target": 3, "current": null, "current_as_of": null, "current_source": null, "urgency": 4, "urgency_reason": "not measured yet"} --> **North Star number: U4, far behind.** Roles a week that are literally the mission with a salary, each packaged to pass the presentation bar first time: not measured yet, against 3 this week, on the way to 10 by 16 November 2026. A bold swing every day; work that does not move the number waits.

Lifecycle: live. The detail is in `README.md`.

## What changed recently

- 2026-11-04 **Hunter keeps the employer's own link, and fills Workday and iCIMS in his browser.** The LinkedIn sweep returned the real apply link on every role and hunter threw it away: 53 percent of the 10,082 roles swept in 60 days were LinkedIn links whose system nobody knew. It is kept now (`employer_url`, applied to the database and read back), read before any guess, and used by the Apply and by-hand emails. Workday was read on the wrong data centre (Sonos read as dead) and is fixed. Workday and iCIMS forms fill step by step after he signs in, extension 1.7.0. The live Sonos Workday posting reads end to end in a dry run; a live fill is unproven until he opens one. 2587 tests pass, each new one seen to fail on broken code.
- 2026-11-04 **Form answers could not be queued since 26 October, now fixed.** `hunter_drafts.kind` had a check constraint without `essay`, so every Prepare with an open question failed with a 400 and no Apply email went. The constraint now allows `essay` (applied to the database, read back by three Apply emails sent). A company named with a parenthesis ("Raptive (formerly AdThrive)") is now named by either part in a letter (`compose.name_forms`). ElevenLabs Enterprise Deployment Chief of Staff, marked dead on 14 October, was live on its board and is back on Pipeline.
- 2026-11-04 **Fresh postings build first, and the Apply email says how old a posting is.** Within one Krish Score, a posting the board dates as up for 14 days or fewer builds first, newest first (`router.build_order`); his score still comes first, and a last-changed date is never called fresh. The Apply email states the posting's age and, past 14 days, that the note to a person matters more. The 3-a-run cap is unchanged until `run outcomes` shows speed matters. Only 23 of the last 83 roles carry a posting date, so the reorder touches about a quarter of them. 2540 tests pass.
- 2026-11-04 **Applications now come with a note to a person, and replies reach the inbox hunter reads** (branch `claude/fervent-lovelace-jgntk2`). His forms gave employers hello@krishraja.com while hunter reads krish@mindmake.co; the Info Bank job search email is now krish@mindmake.co (read back). No employer receipt for any of the 69 applications was found in either inbox. `run outcomes` records rejections and next steps from his mail, hourly, counts only; today 0 next step, 0 rejected, 22 silent past 21 days, 47 too early. The letter writer gets his whole column M (the 1,500 character cut is gone) and writes a note for the hiring leader that the Apply email carries; he sends it. 2535 tests pass, each new one seen to fail on broken code. Waiting on Krish: merge, so the hourly step runs.
- 2026-10-28 **The Drive map keeps other systems' destinations and finds Bugs & Issues** (branch `claude/drive-map-other-writers`). `build_map` kept only Hunter's three folders, so any other writer's destination would vanish on the next `drive-map --apply`. It now keeps any destination the registry holds while it sits in a registered area, and finds `bugs_and_issues` (Ventures/Bugs & Issues, never made) for the feedback-video lane (Krish, 2026-10-28). 2 tests, each seen to fail on the old code; 2513 pass. Waiting on Krish: merge, then `drive-map --apply` writes the key.
- 2026-10-26 **Spend comes down by encoding, not by guessing.** The month's ledger read $62.6, $34.9 of it the judge, 82 percent of whose calls were rejections. The judge's record is now a screen (`screen.py`, shadow, refitted every run): measured oldest first it cuts 0.8 to 2.5 percent of judge calls with no role lost, rising as the record grows: small, because several roles he applied to read like rejects on their words. Form answers moved to the writing routine on his word (about $7 a month); a cold target search that found nobody is no longer repeated every run ($3.33 of $4.05 was repeats). Haiku is deferred on his word until this has been seen to work.
- 2026-10-24 **His Drive map follows the layout he set** (`drivemap.py`, the guard, the sweep). Ruling (Krish, 2026-10-24): folders are found by ID, never by path. CURRENT is gone, so the map now names Ventures, --ARCHIVE and Family Admin as the areas, FamilyContentLibrary and KeePass as immovable, and `Inbox from My AI` at the top as the inbox (filearchives D-033). A rebuild keeps every folder it already holds by id; Hunter's folders stay where he moved them (Family Admin/Krish/Job-Search/Hunter).
- 2026-10-23 **The North Star now has a number, a weekly ladder and an urgency rating.** Ruling (Krish, 2026-10-23): every repository chases one aggressive number on a weekly ladder, and urgency U1 to U5 sets how hard it pushes. The number is in `docs/NORTH_STAR.md`; contract: ai-harness `contract/north-star-metric.md`.

## What is next and what is waiting on Krish

- Measure the North Star number and record it on the first line under "Where it is right now".
- Next for the Drive map: run `drive-map --apply` once on main to rewrite the registry from his Drive, then read back that the hourly sweep files into Inbox from My AI.
- North Star check (2026-10-24): holds.

## Read next

- `docs/NORTH_STAR.md`: the North Star, its number and its weekly ladder.
- `README.md`: what this repository is and how it runs.

## Do not trust

Nothing at the moment.
