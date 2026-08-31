# hunter

Krish's job search engine. Sources senior roles, gates and scores them against
canon, builds per-role CV and cover letter packages by lossless tailoring of
the two Google Docs masters, stages rows into the Pipeline sheet, and reports.
The system drafts; Krish sends.

## Governing spec

The canon (Supabase `canon_documents`, slug `krish-canon`) governs. Section 9
is the operating spec: 9.1 sourcing universe, 9.2 presentation bar, 9.3 the two
auto-rejects, 9.4 gates G1 to G10, 9.9 artifact registry, 9.12 template
contract, 9.13 Pipeline tab spec including the mandatory two-way
reconciliation. hunter never edits canon; changes are filed to
`workflow_proposals` with status `proposed`.

## Ground rules

- No em dashes anywhere, enforced by `tests/test_repo_guards.py`.
- Never send anything to a human. The only outbound channel is `notify.py`,
  which can only message Krish.
- Never commit a secret. Secrets live in Supabase `system_config` and are read
  at runtime. The environment carries exactly two values: `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY`.
- Per-role copies only (`KrishRaja_CV_{Company}`, `KrishRaja_CoverLetter_{Company}`).
  Never a new master, never a version number on a copy.
- The masters are read live at run time. Cached descriptions of them are never
  trusted; `read_master_facts` refuses to build when the live structure moves.

## Runtime

No VPS dependency. A fresh-session Claude Routine runs
`python -m hunter.run run` Mon and Thu 08:27 UTC (created at the P4 gate).
The code is runtime-agnostic plain Python and moves to any cron host with
zero changes.

## Tests

```
pip install -e ".[dev]"
pytest            # offline suite, no network, no credentials
HUNTER_LIVE=1 pytest -m live   # live checks, needs the two env vars
```

`tests/fixtures/letter_master.json` is a captured Docs API response of the
live letter master used by the bold-run retention tests. Recapture it after
Krish edits the master in place.
