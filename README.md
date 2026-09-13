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

GitHub Actions, `.github/workflows/hunter.yml`. The two repo secrets are
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; everything else is read from
Supabase `system_config` at run time.

- Mon and Thu 08:27 UTC: `python -m hunter.run run` (process, then source).
- Hourly: `python -m hunter.run drain`, the fallback for the Control Center
  buttons. A button press normally arrives as a `repository_dispatch`
  (`hunter-process`, `hunter-source`, `hunter-packages`) carrying the
  `hunter_commands` id and runs within a minute.
- `workflow_dispatch` runs any command by hand.
- One job at a time (`concurrency: hunter`), so two runs never touch the
  sheet together.

The Claude Routines that ran this before 2026-09-07 are retired.

## The loop

Krish changes column A on the Pipeline tab (Yes, Applied, Declined - reason).
`process` does the rest, in order: reconcile the sheet with
`hunter_seen_roles`, record verdict events, build a CV and cover letter for
every Yes without a package (a URL with no ATS key is checked through board
discovery, then the page itself; unknown liveness is built and marked
"Materials staged - liveness unverified", verifiably dead is marked "Posting
dead, cannot build" and the row stays), write the warm path person and
evidence for every Yes, move Applied and Declined rows to the Applied tab,
sort by score, and print the learning report. `run` is `process` followed by
sourcing. Companies Krish declined as business uninteresting or domain
expertise are gate G12 from then on; `hunter_company_allow` in system_config
reverses one.

Sheet layout is `sheet.HEADERS` (30 columns), asserted against canon 9.13 at
every start. `migrate-columns [--apply]` moves a tab from the old 28-column
layout to it.

## The apply layer

`src/hunter/apply/` reads a posting's real application form and resolves every
field against the answer tabs Krish already maintains in the workbook. It fills;
it never submits and never contacts a company.

- `bank-check` (read only) reports the state of `Application Info Bank`,
  `Profile` and `Interview Answers`: how many answers are stored, which declared
  cells are still empty, which are Krish's alone to give, and any master doc
  pointer that canon 9.9 lists as superseded.
- `audit-forms [--apply] [--limit N]` reads the form for every Yes row and
  derives the six columns that were dead fields until now (`Application Format`,
  `Attachment Style`, `Additional Questions`, `Form Complexity`,
  `Autonomy Score`, `Form Audit Date`). Dry run by default. It never writes
  column A, `Application Status` or `Applied Date`.

Form contracts come from public endpoints, the same ones a candidate sees before
typing anything: Ashby via `jobs.ashbyhq.com/api/non-user-graphql` (where `field`
is a JSON scalar and must be requested bare, and introspection is disabled, so
`apply/ashby_form.QUERY` is a captured contract), Greenhouse via
`boards-api.greenhouse.io/.../jobs/{id}?questions=true`. Google Careers and
LinkedIn need a signed in session, so their forms are recorded as unreadable
rather than guessed at.

Two rules the resolver will not bend. An answer to a select must be one of that
select's own option labels, so a stored "No" that matches nothing on the form is
refused rather than forced. And a question that enumerates places is answered by
testing the list, never by assuming: Socure asks whether Krish resides in one of
eleven states that do not include New York, and the answer is No.

Fixtures are written with `json.dump(..., ensure_ascii=True)`. The em dash guard
scans every `.json` in the tree with no exemption, and the live Aptos Labs
response really does contain em dashes.

## Tests

```
pip install -e ".[dev]"
pytest            # offline suite, no network, no credentials
HUNTER_LIVE=1 pytest -m live   # live checks, needs the two env vars
```

`tests/fixtures/letter_master.json` is a captured Docs API response of the
live letter master used by the bold-run retention tests. Recapture it after
Krish edits the master in place.
