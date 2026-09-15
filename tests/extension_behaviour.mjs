/* Run extension/run.js against a fake page and a fake browser.
 *
 * Whether a mark on the page counts as a submission decides whether a role is
 * recorded as applied, moved off his Pipeline tab, and reported to him as done.
 * Reading the source for the right strings cannot answer that. So this builds a
 * page, a chrome API and a fetch, runs the real file, and checks what it decides.
 *
 * Driven from tests/test_extension.py so it runs with the rest of the suite.
 * Offline: no sockets, and fetch is a stub.
 */
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const SRC = readFileSync(process.argv[2] || 'extension/run.js', 'utf8');

function world({ hash, bodyText, watch, url = 'https://job-boards.greenhouse.io/acme/jobs/42/application' }) {
  const u = new URL(url);
  // Two virtual seconds per Date.now() call, so run.js's 30 minute deadline is
  // reached after ~900 ticks, which is under a second of real time.
  const base = Date.now();
  let tick = 0;
  const store = { local: watch ? { hunter_watch: watch } : {}, sync: {} };
  // Every watch ever saved, not just the one left at the end. The loop clears the
  // watch when it reports and when it expires, so the end state cannot show
  // whether it was ever written, and a version that never persisted it would look
  // identical.
  const saved = [];
  const posts = [];
  const banners = [];
  const g = {
    location: { href: url, hash: hash || '', origin: u.origin, pathname: u.pathname },
    document: {
      body: { innerText: bodyText, style: {} },
      documentElement: { appendChild() {} },
      createElement: () => ({ style: {}, remove() {} }),
      getElementById: () => null,
      querySelector: () => ({}),
    },
    chrome: {
      runtime: { getManifest: () => ({ version: '1.2.0' }) },
      storage: {
        sync: { get: async () => ({}) },
        local: {
          get: async (k) => { const o = {}; for (const key of k) if (key in store.local) o[key] = store.local[key]; return o; },
          set: async (o) => { saved.push(o.hunter_watch); Object.assign(store.local, o); },
          remove: async (k) => { delete store.local[k]; },
        },
      },
    },
    fetch: async (u2, opts) => {
      if (String(u2).includes('/submitted')) {
        posts.push(JSON.parse(opts.body));
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return { ok: true, status: 200, json: async () => ({ version: 1, fields: [], files: [], demographics: [] }) };
    },
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 1)),
    Date: { now: () => base + (tick++ * 2000) },
    window: {},
  };
  g.window = g;
  g.window.__hunterFill = async () => ({ filled: [], files: [], missed: [], required_missed: [] });
  return { g, store, saved, posts, banners };
}

async function run(w) {
  const keys = Object.keys(w.g);
  const fn = new Function(...keys, `return (async () => { ${SRC} })();`);
  await fn(...keys.map((k) => w.g[k]));
  // The watch loop resolves on its own because setTimeout is squashed to 1ms.
  await new Promise((r) => setTimeout(r, 1500));
}

const CAP = '#hunter=acme:role-abc123.' + 'a'.repeat(32);

// 1. A confirmation phrase ALREADY on the page is the baseline, never a report.
{
  const w = world({ hash: CAP, bodyText: 'Step 3 of 3: Application complete' });
  await run(w);
  assert.equal(w.posts.length, 0, 'a phrase already on the page must not report');
  console.log('ok  a mark already on the page is ignored');
}

// 2. A mark that APPEARS after the fill is a submission.
{
  const w = world({ hash: CAP, bodyText: 'Apply for this role' });
  const started = run(w);
  setTimeout(() => { w.g.document.body.innerText = 'Thanks for applying to Acme'; }, 20);
  await started;
  assert.equal(w.posts.length, 1, 'a new mark must report exactly once');
  assert.match(w.posts[0].evidence, /thanks for applying/);
  assert.equal(w.posts[0].token, 'acme:role-abc123');
  console.log('ok  a mark that appears is reported, quoting the words');
}

// 3. The watch survives the document: a fresh page, no hash, same job.
{
  const stored = {
    token: 'acme:role-abc123', key: 'a'.repeat(32),
    api: 'https://controlcenter.krishraja.com/api/hunter/payload',
    origin: 'https://job-boards.greenhouse.io', path: '/acme/jobs/42',
    baseline: [], until: Date.now() + 60000,
  };
  const w = world({
    hash: '', bodyText: 'Application received. We will be in touch.',
    watch: stored, url: 'https://job-boards.greenhouse.io/acme/jobs/42/confirmation',
  });
  await run(w);
  assert.equal(w.posts.length, 1, 'the new document must report the submission');
  assert.match(w.posts[0].evidence, /application received/);
  assert.equal(w.store.local.hunter_watch, undefined, 'and clear the watch');
  console.log('ok  a submission that navigates is still reported');
}

// 4. A watch from another job on the same board does not fire.
{
  const stored = {
    token: 'acme:role-abc123', key: 'a'.repeat(32),
    api: 'https://controlcenter.krishraja.com/api/hunter/payload',
    origin: 'https://job-boards.greenhouse.io', path: '/acme/jobs/42',
    baseline: [], until: Date.now() + 60000,
  };
  const w = world({
    hash: '', bodyText: 'Thanks for applying',
    watch: stored, url: 'https://job-boards.greenhouse.io/other/jobs/99',
  });
  await run(w);
  assert.equal(w.posts.length, 0, 'a different job must not consume the watch');
  console.log('ok  a watch does not fire on a different job');
}

// 5. An expired watch is dropped rather than fired.
{
  const stored = {
    token: 'acme:role-abc123', key: 'a'.repeat(32),
    api: 'https://controlcenter.krishraja.com/api/hunter/payload',
    origin: 'https://job-boards.greenhouse.io', path: '/acme/jobs/42',
    baseline: [], until: Date.now() - 1,
  };
  const w = world({
    hash: '', bodyText: 'Thanks for applying',
    watch: stored, url: 'https://job-boards.greenhouse.io/acme/jobs/42/confirmation',
  });
  await run(w);
  assert.equal(w.posts.length, 0, 'an expired watch must not fire');
  assert.equal(w.store.local.hunter_watch, undefined, 'and must be removed');
  console.log('ok  an expired watch is dropped');
}

// 6. The watch is written BEFORE anything navigates, or the resume above has
//    nothing to resume from. This is the half that makes case 3 reachable in a
//    real browser rather than only in a harness that pre-seeded storage.
{
  const w = world({ hash: CAP, bodyText: 'Apply for this role' });
  await run(w);
  assert.equal(w.saved.length, 1, 'exactly one watch is stored');
  const s = w.saved[0];
  assert.equal(s.token, 'acme:role-abc123');
  assert.equal(s.key, 'a'.repeat(32));
  assert.equal(s.origin, 'https://job-boards.greenhouse.io');
  assert.equal(s.path, '/acme/jobs/42', 'the job path, with /application stripped');
  assert.ok(Array.isArray(s.baseline), 'and the baseline it will compare against');
  assert.ok(s.until > Date.now(), 'and an expiry');
  console.log('ok  the watch is stored before the page can navigate');
}

console.log('\nall six hold');
