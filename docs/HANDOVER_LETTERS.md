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

## Where it stands

- **The plan is approved by him.** Read `/tmp/plan.md` from the snippet above before anything else.
- **Builds are paused, as he chose.** `hunter_max_score_builds_per_run` went from 3 to 0 at 08:48 UTC on 15 October, read back.
  - The twice-weekly batch still builds column A Yes packages, and the send paths still work. Nothing yet refuses an old-style package.
  - The next batch is Sunday 13:00 London. L1 below must land before then, or the batch builds old-style packages again.
- **No code is written yet.** Both repos are clean on branch `claude/happy-bohr-xhlis9`, which was restarted from main: hunter c098ca7, control-center cf53e69.
- **15 Apply emails sent on 15 October are old-style.** Superseding them needs his yes.

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

- Apply the L0 migration.
- Write `hunter_allowed_recipients` before L1 merges.
- Merge L1, and each later piece.
- Supersede the old-style open Apply links (counts only).
- Write `hunter_contact_bases`.
- Create the v5 letter master and update the CV master in Drive, and update canon 9.9 and add 9.14.
- Rebuild live packages and send.

## How to talk to him

- Lead with the answer, in plain words.
- No architecture lectures.
- Never claim it works before reading it back.
- He has no terminal: anything he must do is a button or an email.
