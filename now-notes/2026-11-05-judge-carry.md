# 2026-11-05: pending roles brought back to the judge

Status: on branch ccr-675fac90-vsqqa2, tests green (2596), not yet on main.

- 395 roles sat in judge_pending, the oldest from 27 August: a pending role
  was judged again only if a sweep found it again. Read on their own boards
  that day, 213 had closed and 180 were open.
- `carry.py`: each sourcing run reads up to `hunter_judge_carry_per_run` (40)
  pending roles on their boards, the least recently read first, records a
  closed one dead, and judges the open ones with the run.
- `stage-picks JOB_IDS --apply`: stored roles a session picked with him go on
  Pipeline, read open first, keeping their own source so they build on his
  score, never as postings he sent.
- `clear-pipeline` and `stage-picks` added to the workflow's manual commands.

Next step: merge to main, dispatch `clear-pipeline --apply`, then
`stage-picks` with the eight roles chosen in the session, and read Pipeline back.

North Star check: moves it. Eight open roles at his altitude reach him that
the funnel had parked, and the carry-over stops the next ones being parked.
