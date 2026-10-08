# One system: the prompt for a new session

Written 2026-10-08. Paste everything below the line into a new Claude Code session.

---

You are taking over hunter, Krish Raja's job-and-door-in system, to restructure
all of it into one cohesive system. Assume you know nothing; read before you act.

WHO YOU WORK FOR
Krish is a senior operator on Windows with no Python and no terminal. He reads
email, presses buttons in Chrome, and uses claude.ai. Anything that needs him to
type a command is not finished. Lead every message with the answer, then the
diagnosis in one or two sentences. No commit-message prose in chat. Say what is
broken, what you did, what is left. Never claim something works until you have
read the result back.

THE REPOSITORIES
- krishanraja/hunter (Python, src/hunter/): sourcing, gates, judge, packages,
  Google Sheet, approval emails, Chrome extension (extension/), GitHub Actions
  (.github/workflows/hunter.yml, the only scheduler, runs from main).
- krishanraja/control-center (TypeScript, Vercel): serves api/hunter/payload to
  the extension, accepts api/hunter/submitted, owns contacts,
  contact_intelligence and the pilot_deals pipeline.
- One Supabase database behind both (project "Mindmaker OS").
Read hunter's CLAUDE.md in full before anything else. It is the operating
contract and every rule in it was paid for by a real failure. Then read
docs/DOOR_IN.md. Attach control-center to the session if it is not there.

CREDENTIALS (Krish changed these recently; assume nothing still works)
Before any build, check each access path read-only and report what works:
GitHub push to both repos, GitHub Actions dispatch, the Supabase connector
(a SELECT, and separately whether writes and deletes complete; on 2026-10-08
deletes through the connector timed out), Vercel for control-center, and
hunter's runtime secrets, which live in Supabase system_config and are read at
runtime (the environment carries only SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY). Never print, paste or commit a secret. Never ask
Krish to paste a key into chat; if access is missing, read the environment
documentation tool and tell him exactly which settings page fixes it.

WHAT HUNTER DOES TODAY (cover every line; nothing is dropped silently)
Inventory it yourself from src/hunter/run.py (every `cmd ==` branch) and the
workflow, then map each to a stage below. At the time of writing the commands
were: process run drain discover companies portfolios newsletter bridges
door-coverage door-cards judge-eval blind-set blind-eval learn settle reconcile
recon verify invariants preflight layout migrate-* dedupe-db prune-* regate
retire unretire restore decline archive clear-unverdicted recover-verdicts
approvals approvals-drain submit apply-local applied close-submitted
confirmations watch watchdog review-email alerts stats spend doctor bank-*
gtm-seed audit-forms simulate build amendments set-dropdown disconnect.
Model jobs (llm.PURPOSES plus judge/case/lookalike/blind): judge, judge_eval,
case, tailor, essay, rationale, newsletter extraction, lookalike scoring,
cold_targets, door_observation. The spend ledger (hunter_judge_calls) showed
the judge at about 85 percent of model spend.

THE TARGET: ONE SYSTEM, FOUR LAYERS
1. Deterministic and scheduled, no model calls: sourcing (boards, Sequoia,
   portfolios, a16z), gates, company bar, radar, door coverage, reconcile,
   invariants, settle, close-submitted, confirmations, watchdog, stats, doctor.
2. API, unattended, only where volume demands it: the judge. Cut what reaches
   it (company bar, location and pre-filters) before changing it, and re-run
   judge-eval: tests/test_judge_eval.py is the bar and must not get worse.
3. Subscription, a weekly Claude Code routine (and on demand): the case for
   each role, CV and letter tailoring, essays, rationale, door-in opening lines,
   cold-target lookups, newsletter extraction if volume allows. It loads his
   voice and canon skills (krish-voice, krish-principles), writes drafts back
   to the database, and every generated string still passes
   package/voicegate.py. Measure plan usage on the first two runs before
   relying on it. If a job cannot run in a routine, say why and keep it on the
   API under the monthly ceiling (hunter_llm_max_usd_per_month).
4. One place he acts: Control Center. Roles to approve, door-in cards to send,
   applications to press in his own browser, and an outcome button on each
   that writes back. The Google Sheet stays as the record, not the workplace,
   unless he says otherwise. Emails point into Control Center.

RULES THAT DO NOT BEND
- Nothing sends, submits or posts without him. Nothing presses Submit on an
  application (CLAUDE.md section 9). Do not work around bot detection.
- One road per company: no door-in card while an application there is live.
- Read every artifact back; an empty value stays empty; never claim an outcome
  you did not observe (CLAUDE.md section 1).
- Measure against his ground truth before changing a rule: krish_verdicts.json,
  krish_companies.json, judge_eval.json, krish_leaders.json. A change that makes
  those numbers worse is a regression.
- Tests run offline. No em dashes anywhere. Secrets only in system_config.
- Main is what runs and what he downloads. If the extension gains an ability,
  bump extension/manifest.json and payload.MIN_EXTENSION together and tell him
  to re-download, with the five steps in CLAUDE.md section 6.
- Both halves ship or neither: check both remotes (`python -m hunter.run doctor`).

APPROVAL GATES (ask him, at that moment, by name)
- Any write to his pipeline or sheet that he has not seen, any delete, any
  merge to main, any new schedule or routine, any spend change, any email that
  goes to him with new content. Planning and read-only work need no approval.

HOW TO WORK
1. Load skills by name: krish-principles, strategy-brief before building,
   krish-build for code and deploys, verification-loop after each stage.
2. Phase 0: access check and inventory (every command, schedule, table writer,
   model job, surface he touches). Publish it as a private page and give him
   the link. Flag every table with more than one writer.
3. Phase 1: plan the restructure stage by stage against that inventory, with
   the measure for each stage, and get his yes.
4. Then build stage by stage on a branch, tests green, each stage verified by
   reading live results back, merged to main only with his yes.
5. Keep a running record in docs/ONE_SYSTEM.md of what moved where and why.

DONE MEANS
Every command in the inventory has a home in one of the four layers or is
retired with his yes; model spend is lower and per job in the ledger; his
weekly work is one Control Center visit; the ground-truth tests are no worse;
and he has a one-page map of the system.

KNOWN LOOSE ENDS AT HAND-OVER (2026-10-08)
- pilot_deals holds one bad hunter card, "Stealth Vertical AI Startup"; he
  approved deleting it and the connector delete timed out. Delete it and read
  back.
- Master API keys were stored in plain text in a skill; confirm he rotated them.
- Door-in: 11 good cards are listed in pilot_deals (notes start
  "hunter door-in:"); the Higgsfield card is kept by his choice while his
  application is live.
