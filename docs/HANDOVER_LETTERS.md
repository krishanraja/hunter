# Handover: letters and CVs he would send as they are (2026-10-15)

The session that started this ran out of weekly usage. This file is enough to
pick it up. The repo is public, so everything private (the specimen letter, his
phone numbers, his personal line, the full plan and the 73 review findings) is
in Supabase `system_config`, readable with hunter's service key:

```
python - <<'EOF'
import sys, json; sys.path.insert(0, "src")
from hunter import config
cfg = config.load()
row = config.db_get(cfg, "system_config", {"select": "value", "key": "eq.hunter_handover_letters"})[0]
h = json.loads(row["value"])
open("/tmp/plan.md", "w").write(h["plan_markdown"])          # the approved plan
print(json.dumps(h["private_notes"], indent=1))               # specimen, contact bases, state
EOF
```

The review findings are under the key `hunter_handover_letters_review` (plain text).
Never copy any of it into a repo file, a test, a fixture or a log line.

## Where it stands (updated 2026-10-15, after L0 to L3)

- **The plan is approved by him.** Read `/tmp/plan.md` from the snippet above before anything else.
- **L1 is live on main** ([krishanraja/hunter#25](https://github.com/krishanraja/hunter/pull/25), merge e82f8f5), on his yes.
  - `package/style.py` holds `HOUSE_STYLE` and `COMPOSER_LIVE = False`. Every build waits: build_one, process, score-builds, rebuild-packages and the AMEND rebuild.
  - Every send path goes through `cmd_approvals`, and it refuses any record without the current `package_style`. On 15 October, 68 built packages were on record and none was stamped.
  - The Sunday batch stages roles and builds no package.
  - A rebuild that does not land now blocks the old package (`REBUILD_REFUSED`). `rebuild_targets` may still choose it.
  - AMEND keeps his doc edits in `hunter_amendments` before it supersedes.
  - Tests: `tests/conftest.py` runs every older test as if the composer were live and every record stamped. `tests/test_house_style.py` (marked `house_style_paused`) tests the real pause. `test_production_builds_wait_until_a_composer_writes_his_style` goes red the day `COMPOSER_LIVE` flips; that is deliberate.
- **Privacy:**
  - His phone, his personal email and his Section G answers are out of the source.
  - `hunter_allowed_recipients` is written (one address) and read back; doctor checks it.
  - `hunter_private_bank_seeds` is NOT written. bank-seed leaves those rows alone without it, and the workbook still holds them.
  - Git history still holds the old values. Rewriting it is his call; ask once.
- **L0 is applied live** on his yes and read back: 14 columns, 3 tables, the widened checks.
  - The file is on control-center main ([krishanraja/control-center#458](https://github.com/krishanraja/control-center/pull/458), merged on his yes).
- **Old links:** the 14 open Apply links from 15 October were superseded on his yes (state cancelled, read back). The live payload endpoint answers 410 for them. The one he had submitted is untouched.
- **Score builds:** the cap is still 0. Restore it to 3 only after he approves the scratch preview.
- **L2 is live on main** ([krishanraja/hunter#26](https://github.com/krishanraja/hunter/pull/26), merge 27ebdc5), on his yes.
  - `hunter_contact_bases` is written on his yes and read back through `load_bases`; doctor says ok.
    - us: place, display phone, international phone (in the format forms got before) and country. City, postal code, state and street fall back to his Info Bank rows.
    - uk: place London, city London, his UK number as he typed it, the international form, and country.
    - No UK postcode is recorded, so a UK form asking for one asks him.
  - `package/contact.py` holds `base_for` (the location field, then only explicit phrases in the posting), `load_bases`, `Base.header_line()` for L3, and `check_plan`.
  - The resolver has one phone path ahead of everything. The address fields come from the base; the US base falls back to his Info Bank rows, and the UK base never does.
  - `build_fill_plan` uses the base recorded on the role (`header_base`), never a fresh guess. It stops before drafting essays when a form needs his phone and the role has no base, and raises `ContactMismatch` when the plan carries the other base's number or place.
  - Prepare's note for a role with no base: "which header, US or UK?". The `header` button kind is handled in `actions.py`. The Control Center buttons are NOT built yet.
  - Measured on the 80 roles he built or wants, 15 October: 71 US, 6 UK, 3 hold (no place given). The posting phrases placed one more role; one held role would have read as US from the posting's prose.
  - L3's composer must record `header_base` at build, from `base_for`, and hold when it is empty.
- **L3 is live on main** ([krishanraja/hunter#27](https://github.com/krishanraja/hunter/pull/27) and #28, and the reorder fix after them), on his yes.
  - `package/house.py`: his typography as data, `snapshot()` with effective styles (run, then named style, then normal text, then Docs' built-in Arial 11pt; his letter sets no font anywhere), and `check()` for gates A6, A7, A9 to A12.
  - `tests/fake_docs.py` carries whole styles, images and masks, and refuses what the API refuses.
  - `reorder_paragraphs` restores whole run styles and indents, read back.
  - **The masters were made in his Drive on his yes, 15 October**:
    - **Letter master v5** `1PPtRc1YN9z9kIQQIcMSvUWrDnw5OjO5VSuy4mdU1H6E`:
      - a copy of his Sonos letter, with 14 slots;
      - margins 36pt top and bottom, 38pt sides;
      - `letter-master --check` read back 0 problems;
      - the rendered PDF is one page.
    - **CV master (house style)** `1TC_X8eWe5jS423LHPpG5-iyhZAmgzdva2lY2Df6CLb8`:
      - 100 runs restyled to Arial and 5 headings kept;
      - his letter's header, link row and banner, with no email;
      - two pages at the original margins, banner kept.
  - **Nothing switches to them yet.** `config.LETTER_MASTER_ID`, `config.CV_MASTER_ID` and canon 9.9 change together, on his yes, when the composer (L5) can fill them. The old builder cannot.
  - Both masters carry `{{PLACE}}  ·  {{PHONE}}`. The letter also carries `{{DATE}}`, `{{HIRING_LEAD}}` (twice), `{{COMPANY}}`, `Re: {{ROLE}}`, `{{HOOK}}`, `{{IDENTITY}}`, `{{BULLET_1}}` to `{{BULLET_4}}`, `{{WHY_NOW}}` and `{{CLOSE}}`. The composer bolds bullet labels, figures and the inline role and company, as his letter does.
- **Header buttons are live** in Control Center ([krishanraja/control-center#459](https://github.com/krishanraja/control-center/pull/459), deployed). They show on a role whose reason carries `contact.HEADER_ASK`.
- **His UK postcode is recorded** in `hunter_contact_bases` (KT6 7NW), read back.
- **Two separate sessions are queued as task cards:**
  - make Control Center private safely;
  - remove his personal Gmail from hunter's git history. This session's proxy refused the force-push; the rewrite itself was verified (Gmail gone from all 258 commits, main's files byte-identical).
  - He chose to keep his phone numbers, postcodes and equal-opportunity answers in history.
- **For L5 and L6:**
  - The new composer must write `package_style = style.HOUSE_STYLE` in build_one's `built` patch, and set `COMPOSER_LIVE = True`.
  - The style rebuild (`rebuild-packages --reason style`) must choose every unstamped built package he still wants. Today `rebuild_targets` requires his score or reaction, and skips packages that "carry his view".

## His rulings (all binding)

1. **House style for letters and CV.**
   - One font, Arial.
   - Name 23pt, title 11pt, everything else 9pt.
   - Header: place and phone, no email, then the link row "Bio | LinkedIn | Media channel ⬇️", then his banner, then the rights line.
   - No footer contact line.
   - The CV takes the same font, header, link row and banner, with a 9pt body and its own heading sizes.
2. **Page limits.** The cover letter is always one page; "you can narrow the margins". The CV is two pages at most; the banner stays only while it fits.
3. **City and phone.**
   - US or US-remote roles: Brooklyn and his US number.
   - UK, any European country, Europe or EMEA: London and his UK number.
   - Anywhere else: hold for him.
   - The letter, the CV and the extension's form fields all agree.
4. **Spelling.** UK spelling for UK and Europe roles, in the letter and the CV; US spelling elsewhere.
5. **Personal touch.** Any voice, audio, speech, mindset, future of work, personal development, emerging media, or content or IP business gets one personal line from a bank in canon (new section 9.14).
   - If nothing matches, the package holds, hunter asks him, and his answer is written to canon verbatim.
   - The first entry (his own words from the specimen) is in the private notes.
6. **Per-role writing.** Everything except the header and sign-off is written for the job description, with his edited specimen as the model.
7. **Learning.** Every edit he makes (words and formatting) is captured and learned from. The false "fed back into the scorer" line in the review email goes.
8. **Guaranteed gates.** All of the above are read back from the built document and refuse the package on failure.
9. **No more API spend on this.** The writing routine on his subscription writes the briefs and the letters (`hunter_writer` is already `routine`; see docs/ROUTINE_WRITER.md and `drafts.py`).
   - There is no API fallback for letters: an unanswered draft waits.
   - The judge stays on the API.
10. **Keep usage low.** No multi-agent fan-out. Build directly, in few large pieces, committing as you go.

## The work, in order

Each item lists what the review proved about today's code (file:line), so you
do not have to rediscover it.

**L0. Control Center migration** (`supabase/migrations/20261027090000_hunter_letters.sql`, apply needs his yes):
- `hunter_seen_roles` gains `header_base`, `package_style`, `package_personal` (a hash) and `package_asks`.
- New tables: `hunter_fact_requests`, `hunter_doc_edits`, `hunter_jd_briefs`.
- `hunter_row_state` gains style columns. It has no migration in the repo, so read its live columns from the PostgREST OpenAPI document first.
- `hunter_actions` widens its kind CHECK and gets a nullable `ref`. Kinds are CHECKed and `job_id` is NOT NULL today, in 20261008170000_hunter_drafts_and_actions.sql:49-50.

**L1. Nothing old-style reaches him** (urgent, before Sunday):
- **A `package_style` stamp,** checked at every send path: `cmd_approvals` (run.py:6400, export near 6653), `wanted_for_approval` (7374), `send_apply_by_hand` (8110), Prepare (actions.py:122), and the AMEND rebuild in `cmd_approvals_drain` (7720).
  - The refusal is its own outcome, never marked against his answer bank.
  - It is the pause until the new composer writes the stamp.
- **A failed rebuild becomes `blocked`.** Today it stays `built` and sendable (run.py:2566-2572).
- **AMEND stores his edit pair before it supersedes** (docs_edited is at run.py:7080).
- **Privacy:**
  - his real US phone in `tests/fixtures/letter_master.json`, `tests/test_apply_forms.py` and `tests/test_simulate.py`, and a second real-shaped number at `src/hunter/apply/simulate.py:162`: replace them with 555-01xx;
  - his personal gmail in `notify.py`'s `ALLOWED_RECIPIENTS` (also read by `inbox.is_from_krish`, inbox.py:188): move it to `system_config hunter_allowed_recipients`, fail closed, check it in doctor;
  - a demographic literal in a seed module: move it out of the source;
  - a strict phone guard in `tests/test_repo_guards.py`, which must not flag UUIDs or epoch timestamps in the fixtures.
- **Delete the false email line** at run.py:5865-5867.

**L2. City and phone.**
- **`package/contact.py` `base_for`.** Never run `sources.country_of` on JD prose: "Join us" reads as the US. Map Europe and EMEA to uk.
- **One phone path at the top of `Resolver.resolve`.** Kind phone is answered from `_DIRECT` (resolve.py:367-372, 422-428) as well as by the label rule (400-403), and `_location` runs first (657-661). City, ZIP, state and country come per base. `role_base` stays a city classifier.
- **`header_base`** is recorded and backfilled. `hunter_contact_bases` gets display and international forms, written from a session through PostgREST, never through workflow_dispatch. A missing base gets its own Prepare note.

**L3. House style, read back.**
- **docbuild:**
  - explicit styles with `*` masks, output-only keys stripped;
  - a link set with its underline and colour in one request;
  - per-section batches, from the end backwards;
  - backoff on 429;
  - header operations anchored on tokens.
- **`paragraphs()` and the new `style_snapshot()`** must read inline images. The banner is an `inlineObjectElement` that every current layer ignores (docbuild.py:78-100, fake_docs.py:40-63).
- **The emulator** (`tests/fake_docs.py`): field masks, inline objects, an inserted newline copying its paragraph's style and bullet, and the default link style.
- **`reorder_paragraphs`** keeps font, weight, size and indents.
- **Letter master v5** is a copy of his edited specimen, with slots. **The CV master** gets the same header, banner and Arial. [DRIVE] [CANON 9.9]
- **Gates A7 and A10 to A12** apply to both documents, plus A9 (one page for the letter, two for the CV). The margins narrow first.
- **Switching the master:** `config.LETTER_MASTER_ID` and canon 9.9 must change together just after :13, or every hourly job aborts.

**L4. Personal connections.**
- **Canon 9.14,** with the first entry from the private notes. `REQUIRED_SECTIONS` adds it (canon.py:22), and a pure `parse_canon` is extracted.
- **`personal.py`:**
  - It reads only business sentences: `companyintel.from_posting` (companyintel.py:541-579), the judge's business quotes, and the homepage description. `hunter_company_intel` stores labels, not what a company does.
  - Strict strong-phrase rule: 10 of 138 labelled companies, against 33 to 49 for naive keywords.
- **The hold** uses the existing `blocked` status, which Control Center already renders.
- **`facts.py`:** its own subject mark. `inbox.fetch_replies` keeps only `[hunter #` subjects today (inbox.py:169-171), so a `token_of` parameter is needed.
- **His answer** is written verbatim on first reply, through `canonwrite.py`: version lock via `db_patch_where` requiring 1 row, read back, restore on mismatch. This is the one named exception to "canon is never edited from code" (CLAUDE.md 4).
- **The Control Center card** never echoes his words in a note.

**L5. Per-role letters and CVs** (written by the subscription routine):
- **The JD.** A structured JD goes to the writer, and `jd_text` stays unchanged. On a thin JD, column L never becomes evidence (run.py:2124-2127).
- **The brief** and the letter fields are queued as `hunter_drafts` kinds.
- **The Proof Bank tab** is seeded at runtime from a private store.
  - Proofs are keyed by employer.
  - Bullets default to his own wording, and the model adds at most one gated clause.
  - 4 bullets when the page fits.
- **His AI-native rule stays** (tailor.py:62-68), on a narrower trigger.
- **Each field is gated on its own,** fixing the fail-together at tailor.py:690-705. One budget table drives the prompt and the gate (today 150-650 in tailor.md:32 against 190 in tailor.py:193).
- **Register and spelling** must pass his own specimen.
- **Gates A13 to A15:**
  - figures whole-token and per proof;
  - names traced against the full evidence;
  - G8 made real (gates.py:386-389 always passes today).
- **The page-fit ladder:** margins, then spacing, then the 4th bullet, then why-now, then the hook, then the close. Never below 9pt. Still over one page means hold.
- **Failure lines** print codes and counts only on every path.

**L6. Learning, then resume.**
- **An hourly `doc-edits` pass,** before approvals-drain:
  - it covers packages built in the last 30 days;
  - formatting edits count;
  - pairs are stripped of personal lines.
- **An edit made after an Apply email re-prepares that email from his edit.** The extension uploads PDFs frozen at send time (payload.py:142, run.py:6874).
- **`batchstats`** gains the edit metrics.
- **Resuming,** after he reviews a scratch preview written in-session:
  1. merge Control Center first, then hunter;
  2. rebuild-packages for style;
  3. restore the cap to 3.

## Approvals still owed to him (ask at the moment of each)

Done on 15 October, each on his yes:
- write `hunter_allowed_recipients`;
- merge L1;
- supersede the old links;
- apply the L0 migration and merge its file;
- write `hunter_contact_bases`;
- merge L2.

Still owed:
- Merge L3, and each later piece.
- Create the v5 letter master and update the CV master in Drive, and update canon 9.9 and add 9.14.
- Rebuild live packages and send.

## How to talk to him

- Lead with the answer, in plain words.
- No architecture lectures.
- Never claim it works before reading it back.
- He has no terminal: anything he must do is a button or an email.
