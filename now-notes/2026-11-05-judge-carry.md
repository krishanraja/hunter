# 2026-11-05: pending roles brought back to the judge

Status: done, on main (PR #63, #64). Tests: 2598 passed offline.

- 395 roles sat in judge_pending, the oldest from 27 August: a pending role
  was judged again only if a sweep found it again. Read on their own boards
  that day, 213 had closed and 180 were open.
- `carry.py`: each sourcing run takes up to `hunter_judge_carry_per_run` (40)
  pending roles with posting text, not read in 3 days, in an order shuffled
  by the day; records a closed one dead and judges the open ones with the run.
- `stage-picks JOB_IDS --apply`: stored roles picked in a session go on
  Pipeline, read open first, keeping their own source so they build on his
  score. `clear-pipeline` and `stage-picks` can be dispatched from the workflow.
- Done the same day: Pipeline cleared (14 rows recorded applied on his word,
  read back empty), then 8 picked roles staged and read back.
- Incident: the 09:13 UTC sourcing run aborted (23502, audit_sample NOT NULL)
  because one audited row left the rest of its batch with the column NULL.
  Fixed by `run.insert_defaults`; replaying a batch of that shape gave 1058
  NULLs before the fix and 0 after. That run's roles were not recorded; he
  chose not to re-run it.
- Open question for him: Ralph Lauren, Head of AI Strategy and Product
  Management, was recorded applied by the clear but was not in the Apply
  emails of 4 November.

Next step: read the next scheduled sourcing run's log for "judge carry-over"
and the hunter_seen_roles write, and confirm it records without RUN ABORTED.

North Star check: moves it. Eight open roles at his altitude reached him that
the funnel had parked, and the carry-over stops the next ones being parked.
