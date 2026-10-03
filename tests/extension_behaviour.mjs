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

function world({ hash, bodyText, watch, url = 'https://job-boards.greenhouse.io/acme/jobs/42/application', postResults = [] }) {
  const u = new URL(url);
  // Two virtual seconds per Date.now() call, so run.js's 30 minute deadline is
  // reached after ~900 ticks, which is under a second of real time.
  const base = Date.now();
  let tick = 0;
  const seed = {};
  for (const v of [].concat(watch || [])) seed[(v.origin || '') + (v.path || '')] = v;
  const store = { local: watch ? { hunter_watches: seed } : {}, sync: {} };
  // Every watch ever saved, not just the one left at the end. The loop clears the
  // watch when it reports and when it expires, so the end state cannot show
  // whether it was ever written, and a version that never persisted it would look
  // identical.
  const saved = [];
  // Every map written, not only the entries in it: the loop clears a watch
  // when it expires, so the end state cannot show what was stored together.
  const writes = [];
  const posts = [];
  const banners = [];
  // Every element created, so a banner's button can be found and pressed.
  const created = [];
  const g = {
    location: { href: url, hash: hash || '', origin: u.origin, pathname: u.pathname },
    document: {
      body: { innerText: bodyText, style: {} },
      documentElement: { appendChild() {} },
      createElement: (tag) => {
        const el = {
          tag, style: {}, remove() {}, children: [],
          appendChild(c) { this.children.push(c); },
          addEventListener(type, fn) { if (type === 'click') this.press = fn; },
        };
        created.push(el);
        return el;
      },
      getElementById: () => null,
      querySelector: () => ({}),
    },
    chrome: {
      runtime: { getManifest: () => ({ version: '1.2.0' }) },
      storage: {
        sync: { get: async () => ({}) },
        local: {
          get: async (k) => { const o = {}; for (const key of k) if (key in store.local) o[key] = store.local[key]; return o; },
          set: async (o) => {
            writes.push({ ...(o.hunter_watches || {}) });
            for (const v of Object.values(o.hunter_watches || {})) saved.push(v);
            Object.assign(store.local, o);
          },
          remove: async (k) => { delete store.local[k]; },
        },
      },
    },
    fetch: async (u2, opts) => {
      if (String(u2).includes('/submitted')) {
        posts.push(JSON.parse(opts.body));
        const r = postResults.length ? postResults.shift() : 200;
        if (r === 'network') throw new Error('Failed to fetch');
        return { ok: r >= 200 && r < 300, status: r, json: async () => ({}) };
      }
      return { ok: true, status: 200, json: async () => ({ version: 1, fields: [], files: [], demographics: [] }) };
    },
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 1)),
    Date: { now: () => base + (tick++ * 2000) },
    window: {},
  };
  g.window = g;
  g.window.__hunterFill = async () => ({ filled: [], files: [], missed: [], required_missed: [] });
  return { g, store, saved, writes, posts, banners, created };
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
  assert.deepEqual(w.store.local.hunter_watches, {}, 'and clear that watch');
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
  assert.deepEqual(w.store.local.hunter_watches, {}, 'and must be removed');
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


// 7. A batch. Opening the next application must not throw away the watch on the
//    one before it. One storage slot held one job, every fill overwrote it, and
//    six applications Krish sent on 2026-09-24 were never recorded because of it.
{
  const earlier = {
    token: 'higgsfield:head-of-entertainment-gtm', key: 'b'.repeat(32),
    api: 'https://controlcenter.krishraja.com/api/hunter/payload',
    origin: 'https://jobs.ashbyhq.com', path: '/higgsfield/111',
    baseline: [], until: Date.now() + 600000,
  };
  // He fills a second application on another board while the first is still open.
  const filling = world({
    hash: CAP, bodyText: 'Apply for this role', watch: earlier,
    url: 'https://job-boards.greenhouse.io/acme/jobs/42/application',
  });
  await run(filling);
  const kept = filling.writes[0];
  assert.ok(kept['https://jobs.ashbyhq.com/higgsfield/111'],
            'the earlier application is still watched');
  assert.ok(kept['https://job-boards.greenhouse.io/acme/jobs/42'],
            'and so is the one just filled');

  // Now the first one lands on its confirmation page, minutes later.
  const confirming = world({
    hash: '', bodyText: 'Thanks for applying, we have your details.',
    watch: Object.values(kept),
    url: 'https://jobs.ashbyhq.com/higgsfield/111/confirmation',
  });
  await run(confirming);
  assert.equal(confirming.posts.length, 1,
               'the earlier application still reports its submission');
  assert.equal(confirming.posts[0].token, 'higgsfield:head-of-entertainment-gtm');
  assert.ok(confirming.store.local.hunter_watches[
              'https://job-boards.greenhouse.io/acme/jobs/42'],
            'and reporting one does not clear the other');
  console.log('ok  a batch keeps a watch per application');
}

// 8. A report that fails to send is kept, and the next page he opens sends it.
//    It used to be deleted before the send, so one blip lost an application.
{
  const w = world({ hash: CAP, bodyText: 'Apply for this role', postResults: ['network'] });
  const started = run(w);
  setTimeout(() => { w.g.document.body.innerText = 'Thanks for applying to Acme'; }, 20);
  await started;
  assert.equal(w.posts.length, 1, 'one attempt was made');
  const kept = Object.values(w.store.local.hunter_watches || {});
  assert.equal(kept.length, 1, 'the watch is kept after a failed send');
  assert.ok(kept[0].pending, 'carrying the report that did not go');

  // Later, any page on any board: the pending report goes first.
  const later = world({
    hash: '', bodyText: 'Some other job', watch: kept,
    url: 'https://jobs.ashbyhq.com/someone/999',
  });
  await run(later);
  assert.equal(later.posts.length, 1, 'the pending report is sent again');
  assert.match(later.posts[0].evidence, /thanks for applying/);
  assert.deepEqual(later.store.local.hunter_watches, {}, 'and cleared once delivered');
  console.log('ok  a failed report is kept and sent from the next page');
}

// 9. Lever. The form is at /<company>/<id>/apply and the confirmation under the
//    posting's own path. Only /application was trimmed, so a Lever watch never
//    matched its confirmation page.
{
  const filling = world({ hash: CAP, bodyText: 'Apply for this role',
                          url: 'https://jobs.lever.co/versapay/abc-123/apply' });
  await run(filling);
  assert.equal(filling.saved[0].path, '/versapay/abc-123', 'the watch is keyed on the posting');
  const confirming = world({
    hash: '', bodyText: 'Application submitted! Thanks for applying.',
    watch: filling.saved[0], url: 'https://jobs.lever.co/versapay/abc-123/thanks',
  });
  await run(confirming);
  assert.equal(confirming.posts.length, 1, 'the Lever confirmation is reported');
  console.log('ok  a Lever confirmation page resumes its watch');
}

// 10. The form never says anything he recognises: he says so himself. The press
//     is recorded as his word, never as the form speaking.
{
  const w = world({ hash: CAP, bodyText: 'Apply for this role' });
  const started = run(w);
  await new Promise((r) => setTimeout(r, 30));
  const button = w.created.find((el) => el.tag === 'button');
  assert.ok(button, 'the banner offers the button');
  assert.equal(button.type, 'button', 'and it can never submit the employer form');
  await button.press();
  await started;
  const said = w.posts.find((p) => p.said_so === true);
  assert.ok(said, 'pressing it reports said_so');
  assert.equal(said.evidence, '', 'with no words attributed to the form');
  console.log('ok  "I applied, mark it" reports his word');
}

console.log('\nall ten hold');
