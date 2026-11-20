# Firecrawl and Parallel keys: where they may help hunter

Status: A page-reading key (Firecrawl) and a web-search key (Parallel) were added on 2026-11-20 to the edge function secrets of the Supabase project this repository runs on. Checked by name only; no value was read. Nothing uses them yet and no spend has happened.

Where they are readable: from that project's edge functions. They are NOT readable from GitHub Actions, a Vercel function or Python code unless mirrored there, which is a secret write and needs Krish's approval first. Control Center's API gateway has no adapter for either yet.

Rules that still apply: measure before adopting, record the cost of every call, never block on no evidence, and a site that refuses an automated reader is an answer, not an obstacle to route around.

## Map for hunter (inbound data is its weak point)

Hunter reads secrets from `system_config` with only the two Supabase variables in its environment, so it cannot see edge secrets. Proposed route, needing Krish's yes: a small edge function in the same project that does one fetch or one search and returns cited text, called with the service-role key. The alternative is mirroring the keys into `system_config`.

1. **companyintel.py fallback reader.** When plain HTTP and Chromium return an empty or script-only page, ask Firecrawl for the rendered text. Only where reading is allowed (robots.txt first). It must not be used to pass a challenge a site raised: section 9 and the Camoufox eval rule that out. Measure it on the same 43 companies in `docs/evals/BROWSER_EVAL.md` before adopting.
2. **Boards never found.** The Runway case: Parallel search for a company's own careers or ATS page, then the existing board readers take over. Counts toward the 249 seen, 3 shown gap in section 3d.
3. **Company facts for the universe, lookalike, radar and prospect layers.** Parallel search with the source URL attached fits the rule that a Fact refuses to exist without one. Check it against `tests/test_company_taste.py` first.
4. **The employer's own posting (section 3k).** For LinkedIn rows with no `employer_url`, search for the same title on the employer's own site. Older rows cannot be backfilled free today.
5. **Posted dates.** Read a date off the employer's own page for `posted-refresh` where the board gives none.

Not for: LinkedIn pages, anything behind a sign-in, or ranking. Every call goes in the `spend.py` ledger with a purpose.

Next: Krish decides the route (edge function or mirror). Then run item 1 as a measured trial on the 43 companies, counts only.
North Star check (2026-11-20): holds. It moves the number only through more and better roles reaching Krish. Nothing here is measured yet.
