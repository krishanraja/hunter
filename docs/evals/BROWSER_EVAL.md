# Camoufox as a browser adapter: measured 2026-11-15, not adopted

**Verdict: no adapter.** On every site that allows an automated reader,
Camoufox read exactly what Hunter's Chromium reads, and Chromium exactly what
plain HTTP reads. It cost more CPU and memory than Chromium and was the only
reader with errors. Its one edge appeared in the first trial: getting past
Cloudflare challenges that had refused the other two readers. Section 9 of
CLAUDE.md and the brief for this evaluation both rule that out.

The record is `tests/fixtures/browser_eval.json`, pinned by
`tests/test_browser_eval.py`. The harness is `docs/evals/browser_compare.py`.
`tests/test_repo_guards.py` now fails if any evasion library (Camoufox among
them) reaches `src/`, `extension/`, `.github/` or `pyproject.toml`.

## Where a browser could have helped

Hunter collects through APIs and plain HTTP: the Greenhouse, Ashby, Lever and
Workday board APIs, iCIMS posting frames read as server HTML, Sequoia's board
API, the a16z index, Apify for LinkedIn. The
only browser in the repo is Playwright Chromium in `apply/submit.py`, for his
own applications. Two places read raw HTML that a rendering browser could in
principle improve:

- `companyintel.site_facts`: a company's homepage, then its about page. Its
  description is the category, the prerequisite for the company score.
  784 of the 2,463 companies on record (32 percent) have no category.
- `run.fetch_jd_plain`: a posting page off any known board.

The test targeted the first, where the record shows the gap.

## What camofox-browser is

| | |
|---|---|
| What | A Node.js REST server (Express, port 9377) driving Camoufox, a Firefox fork that spoofs its fingerprint in C++. Built for AI agents: tabs, accessibility snapshots, element refs, evaluate, cookie import. |
| Headline | "bypasses Google, Cloudflare, and most bot detection" (its README). |
| Licence | MIT (Jo, Inc). Camoufox itself is a separate project. |
| Maintenance | Active: v1.19.0 on 2026-11-10. 30 of the last 50 commits are one maintainer's. |
| Runtime | Node 22 or later; a peer dependency (openclaw) wants Node 24.16. 168 npm packages, 119 MB. |
| Browser | 663 MB zip, 1.2 GB unpacked, from the daijro/camoufox GitHub releases via api.github.com. |
| Integrity | None. camoufox-js downloads over TLS and checks no hash, although the README says "integrity verification handled by camoufox-js". The README also names a different release repository from the one the code uses. |
| Telemetry | On by default. Crash and "frustration" reports go to a Cloudflare Worker run by Jo and become GitHub issues, carrying the target URL (well-known domains verbatim, others hashed). Off with `CAMOFOX_CRASH_REPORT_ENABLED=false`. |
| Network exposure | Binds every interface by default. With no `CAMOFOX_ACCESS_KEY`, its access middleware passes every request and the evaluate route has no check of its own, so anyone who can reach the port can open tabs and run JavaScript in them. Read from `lib/auth.js` and `server.js`; not exercised. |
| State | The persistence plugin, on by default, writes cookies and localStorage to `~/.camofox/profiles`: state outside Supabase. |

## The comparison

- **Extraction:** `companyintel.site_facts`, unchanged. Each reader supplied
  only page HTML through the seam it already uses (`companyintel._fetch`).
- **Readers:** plain HTTP as it runs today; Playwright 1.63 Chromium launched
  headless as `apply/submit.py` launches it; camofox-browser 1.19.0 (engine
  152.0.4-beta.30) local only, telemetry off, behind a key. Both browsers wait
  for DOM ready, then up to 5 seconds of network idle.
- **Targets:** 43 of his named companies (`data/ai_universe.csv`,
  `data/a16z_consumer_top50.csv`). The gap set is 31 whose description Hunter
  did not take from their own homepage. The control set is 12 where it did.
- **Rules:** robots.txt read first. Any 401, 403, 407 or 429, `cf-mitigated`
  header or challenge page counted as access restricted, with nothing
  extracted. One reader at a time over the whole list, 1 second between
  requests, two passes.
- **Bar, set before the run:** on the gap set, excluding sites that refused an
  automated reader, Camoufox must describe at least 3 more sites AND 10 points
  more than Chromium, or match it at 30 percent less median time and peak
  memory.

| | plain HTTP | Chromium | Camoufox |
|---|---|---|---|
| Gap sites described, both passes (of 31) | 25 | 26 | 25 |
| Same, excluding refused sites (of 26) | 25 | 25 | 25 |
| Control sites described (of 12) | 12 | 12 | 11 |
| Sites refusing it | 5 | 4 | 5 |
| Errors | 0 | 0 | 2 (`session_expired`) |
| Median per site | 0.5 s | 5.2 s | 5.3 s |
| 90th percentile | 0.8 s | 6.9 s | 9.6 s |
| Wall time, 86 site reads | 168 s | 552 s | 597 s |
| CPU | negligible | 715 s | 881 s |
| Peak memory | negligible | 2.3 GB | 3.8 GB |
| Descriptions shorter than the plain read | | 10 | 10 |

- **Refusals:** OpenAI, Canva and Gamma answered every reader with a Cloudflare
  challenge (403, `cf-mitigated: challenge`), and Tempus with 429. Perplexity
  refused plain HTTP and Camoufox and let Chromium through.
- **The bypass:** in the first trial, minutes earlier, Camoufox described
  OpenAI and Canva while plain HTTP and Chromium were challenged. Whether it
  passes depends on the site's scoring at that moment. It is the capability the
  project advertises, and the one Hunter must not use.
- **Sourcegraph:** no reader found a description; rendering does not help.
- **Rendering hurts the extraction:** a browser re-serialises `&#39;` as a
  literal apostrophe, and `companyintel.META` stops at the first quote of
  either kind, so Anthropic, Together AI and others came back cut short.
  Chromium's read of Clay put a random token where the description was. A
  browser is not a drop-in reader for `site_facts`.

The bar is not met: the margin is 0 sites, and Camoufox is slower and heavier
than Chromium, not 30 percent lighter.

## What it would have taken to run

Every scheduled run on `ubuntu-latest` would need Node 22 or later, `npm ci`,
the 663 MB download (no hash to check it against), a background server with
the access key and telemetry flag set, and about 3.8 GB of memory at peak.
That adds a second language runtime and a long-lived process to a Python job,
for no site it can read that Chromium cannot.

## Rollback

Nothing in the reading path changed, so there is nothing to roll back in
production. What this adds is a fixture, two tests, a guard and these files.
Reverting the commit removes all of them.

## What would change the call

- A source he wants that serves its content only to a rendering browser, does
  not refuse automated readers, and that Chromium also fails on. Measure it
  with the harness first; Chromium is already in the stack.
- The company gap is real (784 with no category) but is not rendering: on his
  named companies plain HTTP already reads 37 of 43, and the misses are
  refusals. Domain resolution and companies never read are the next place to
  look.
