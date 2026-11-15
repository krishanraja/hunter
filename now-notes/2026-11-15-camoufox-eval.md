# 2026-11-15: Camoufox measured as a reading browser, not adopted

Status: done on branch `claude/great-pasteur-0i16oj`, committed locally, not
pushed (his brief: no remote push without approval). Tests: 2610 passed offline.

- Asked: evaluate jo-inc/camofox-browser as an optional, replaceable browser
  adapter for Hunter's collection, compare it with the existing stack on
  permitted targets, implement only if justified.
- Hunter reads through APIs and plain HTTP; its only browser is Chromium in
  `apply/submit.py`. The place a renderer could help is
  `companyintel.site_facts` (784 of 2,463 companies have no category).
- Bar set before the run. Result: on the 38 sites that allowed an automated
  reader, Camoufox described no company plain HTTP and Chromium missed
  (37 each; Camoufox 36, one lost to an error). Camoufox: 2 errors,
  3.8 GB peak, 881 CPU seconds; Chromium 0, 2.3 GB, 715.
  Its one edge (first trial: OpenAI and Canva) was passing Cloudflare
  challenges. Not adopted; no adapter built.
- Also found: a browser's re-serialised HTML shortens 10 descriptions under
  `companyintel.META`, so no browser is a drop-in reader for `site_facts`.
- Camoufox itself: telemetry carrying target URLs on by default, binds all
  interfaces with no key by default, no hash check on its 663 MB download,
  Node 22+ beside a Python job.
- Added: `tests/fixtures/browser_eval.json`, `tests/test_browser_eval.py`,
  an evasion guard in `tests/test_repo_guards.py`, the harness and record in
  `docs/evals/`, a paragraph in CLAUDE.md section 9.

Next step: none for Camoufox. For the company gap, look at domain resolution
and companies never read: on his named companies plain HTTP already reads 37
of 43, and every miss but one is a refusal.

North Star check: moves nothing directly. It tested whether better reading
would raise supply quality, found it would not, and keeps an evasion tool out
of the stack.
