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
