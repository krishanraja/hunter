/* Read the capability out of the link, fetch the payload, fill the form.
 *
 * The capability lives in the URL FRAGMENT, after the #. A fragment is never
 * sent to the server, so the employer's site never receives it and neither does
 * anything between here and there; only this script, running in Krish's own
 * browser, ever sees it.
 *
 * Nothing here presses submit. It fills, it reports, it stops.
 */
(async function () {
  'use strict';

  const DEFAULT_API = 'https://controlcenter.krishraja.com/api/hunter/payload';

  // What a form says once it has the application. The same list hunter uses on
  // its own side, so both halves agree on what "submitted" looks like.
  const SUBMITTED_MARKS = [
    'application submitted', 'thanks for applying', 'thank you for applying',
    'application received', 'we have received your application',
    "we've received your application", 'your application has been submitted',
    'successfully submitted', 'application complete',
  ];

  function capability() {
    const m = /(?:^|[#&])hunter=([^&]+)/.exec(location.hash || '');
    if (!m) return null;
    const [token, key] = decodeURIComponent(m[1]).split('.');
    return token && key ? { token, key } : null;
  }

  function banner(text, tone) {
    const el = document.createElement('div');
    el.style.cssText =
      'position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:12px 18px;' +
      'font:600 14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;' +
      'color:#fff;background:' + (tone === 'bad' ? '#a4262c' : tone === 'work' ? '#444' : '#1a7f37');
    el.textContent = text;
    const old = document.getElementById('hunter-banner');
    if (old) old.remove();
    el.id = 'hunter-banner';
    document.documentElement.appendChild(el);
    document.body.style.marginTop = '46px';
    return el;
  }

  // Chrome never updates an unpacked extension, so this folder is frozen at
  // whatever Krish last downloaded while hunter moves on. The payload states the
  // oldest build that can fill it; below that, a green "filled everything"
  // banner is a lie, which is the exact failure that left an equal opportunity
  // section empty and looked like success.
  function older(mine, need) {
    const a = String(mine || '0').split('.').map(Number);
    const b = String(need || '0').split('.').map(Number);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const x = a[i] || 0, y = b[i] || 0;
      if (x !== y) return x < y;
    }
    return false;
  }

  const MINE = (chrome.runtime.getManifest() || {}).version || '0';
  const STALE =
    'Your Hunter extension is version ' + MINE + ' and this application needs ' +
    '%NEED%. It has filled what it can, and parts of this form are probably ' +
    'empty. Download https://github.com/krishanraja/hunter/releases/download/' +
    'extension/hunter-extension.zip, extract it over your hunter folder, then ' +
    'press the reload arrow on the Hunter card at chrome://extensions and ' +
    'reopen this link.';

  // ---- The watch, and why it lives in storage ------------------------------
  //
  // A content script dies with its document. Greenhouse posts the form and loads
  // its own confirmation page, which destroys this script before the next tick,
  // and the copy Chrome injects into the new document finds no #hunter= fragment
  // in the new URL and gives up. So a fresh copy would never report the
  // submission, the sheet would go on saying "Not applied", and the one thing
  // that closes the loop would be the employer's own receipt email, which only
  // some employers send.
  //
  // Extension storage survives the navigation. Local rather than session, because
  // a content script cannot read session storage without a service worker to
  // widen its access level, and this needs no service worker at all.
  // One slot held one job, and every form opened overwrote it. Working through
  // a batch is exactly the usage that broke it: Krish applied to six more roles
  // on 2026-09-24 and not one was recorded, because each fill replaced the watch
  // belonging to the application before it, and the re-injected copy on the
  // confirmation page then read a watch for a different job, failed the origin
  // and path check, and reported nothing. A map, keyed by the job's own page.
  const WATCH = 'hunter_watches';
  // Enough for any batch he will do in half an hour, and bounded so storage
  // cannot grow for ever.
  const MAX_WATCHES = 40;
  const basePath = () => location.pathname.replace(/\/application\/?$/, '');
  const watchKey = (w) => (w.origin || '') + (w.path || '');

  function marksIn() {
    const body = (document.body ? document.body.innerText : '').toLowerCase();
    return SUBMITTED_MARKS.filter((m) => body.includes(m));
  }

  async function allWatches() {
    try {
      const got = await chrome.storage.local.get([WATCH]);
      const all = got && got[WATCH];
      return (all && typeof all === 'object') ? all : {};
    } catch (e) { return {}; }
  }
  async function putWatches(all) {
    try { await chrome.storage.local.set({ [WATCH]: all }); } catch (e) { /* best effort */ }
  }
  async function saveWatch(w) {
    const all = await allWatches();
    all[watchKey(w)] = w;
    // Expired entries go on the way past, so a batch cannot leave them behind.
    const now = Date.now();
    const live = Object.keys(all)
      .filter((k) => all[k] && all[k].until > now)
      .sort((a, b) => all[b].until - all[a].until)
      .slice(0, MAX_WATCHES);
    const kept = {};
    for (const k of live) kept[k] = all[k];
    await putWatches(kept);
  }
  async function clearWatch(w) {
    const all = await allWatches();
    if (w) delete all[watchKey(w)];
    await putWatches(all);
  }

  // A watch only resumes on the SAME job. Same origin and same path prefix, so a
  // watch left over from one application cannot fire on a different job he opens
  // on the same board within the half hour. The longest matching path wins, so a
  // watch on /jobs/4 cannot answer for the page at /jobs/42.
  async function resumable() {
    const all = await allWatches();
    let best = null;
    for (const k of Object.keys(all)) {
      const w = all[k];
      if (!w || !w.token || !w.key) continue;
      if (w.origin !== location.origin) continue;
      if (!w.path || location.pathname.indexOf(w.path) !== 0) continue;
      if (!w.until || Date.now() > w.until) { await clearWatch(w); continue; }
      if (!best || w.path.length > best.path.length) best = w;
    }
    return best;
  }

  const cap = capability();
  const resumed = cap ? null : await resumable();
  if (!cap && !resumed) return;

  const store = await chrome.storage.sync.get(['api']);
  const api = (store && store.api) || DEFAULT_API;

  if (resumed) {
    // The page after the submit. Nothing to fill, nothing to say unless a
    // confirmation appears, and no payload is fetched: the capability is used
    // only to report, which is all a confirmation page needs it for.
    return watchFor(resumed);
  }

  banner('Hunter is filling this form...', 'work');
  let payload;
  try {
    const res = await fetch(
      api + '?token=' + encodeURIComponent(cap.token) + '&key=' + encodeURIComponent(cap.key),
      { method: 'GET', credentials: 'omit' });
    if (res.status === 410) {
      const said = await res.json().catch(() => ({}));
      banner(said.message || 'This application was replaced by a newer one. '
        + 'Open the most recent email for this role.', 'bad');
      return;
    }
    if (!res.ok) throw new Error('the server said ' + res.status);
    payload = await res.json();
  } catch (e) {
    banner('Hunter could not fetch this application: ' + e.message +
           '. Fill the form yourself, or ask Claude.', 'bad');
    return;
  }

  // Wait for the form itself. Ashby fetches it after the page loads and shows
  // "Fetching application form" in the meantime, so a script that runs on
  // document_idle finds nothing to fill.
  for (let i = 0; i < 60 && !document.querySelector('input, textarea, select'); i++) {
    await new Promise((r) => setTimeout(r, 500));
  }

  const out = await window.__hunterFill(payload);
  const done = out.filled.length + out.files.length;
  const stale = older(MINE, payload.needs_extension);
  if (stale) {
    banner(STALE.replace('%NEED%', payload.needs_extension), 'bad');
  } else if (out.required_missed.length) {
    banner('Filled ' + done + '. DO THESE YOURSELF before submitting: ' +
           out.required_missed.join('; '), 'bad');
  } else if (out.missed.length) {
    banner('Filled ' + done + '. Not filled (none required): ' +
           out.missed.join('; ') + '. Read it and press Submit.', 'work');
  } else {
    banner('Filled all ' + done + ' fields and attached your documents. ' +
           'Read it and press Submit.', 'good');
  }

  // ---- Then watch for him pressing it -------------------------------------
  //
  // Hunter cannot see the click from anywhere else: it runs in the cloud and the
  // click happens here.
  //
  // Two things this gets wrong if written the obvious way, both found by review
  // before he hit either.
  //
  // ONE. What counts as pressed is only the form saying so in its OWN words, and
  // only words that were not already there. An earlier version treated any
  // navigation off the /application path as a submission, so clicking back to the
  // job description recorded an application that was never sent. Dropping that
  // left a subtler version of the same bug: "Application complete" is an ordinary
  // step label on a multi step form, and matching the whole body meant the first
  // tick, 1.5 seconds after filling, could report a submission before he had read
  // anything. So the marks present at the start are the baseline and are ignored
  // for ever after; only a mark that APPEARS counts.
  //
  // TWO. A content script dies with its document. Greenhouse posts the form and
  // loads its own confirmation page, which destroys this script before the next
  // tick, and the re-injected copy finds no #hunter= fragment in the new URL and
  // gives up. So the watch is written to extension storage, which survives the
  // navigation, and a fresh copy of this script on the same job path picks it up
  // and looks for a new mark without filling anything.
  // Tell hunter, and say honestly whether it heard. A 4xx or 5xx does not throw,
  // so an earlier version painted the green "Hunter has it" banner over a write
  // that never happened, which is the same lie in the opposite direction.
  async function report(watch, why) {
    await clearWatch(watch);
    try {
      const res = await fetch(watch.api.replace(/\/payload$/, '/submitted'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'omit',
        body: JSON.stringify({ token: watch.token, key: watch.key, evidence: why }),
      });
      if (res.status === 410) {
        const said = await res.json().catch(() => ({}));
        banner(said.message || 'This application was replaced, so submitting it '
          + 'was not recorded. Open the most recent email for this role.', 'bad');
        return;
      }
      if (!res.ok) throw new Error('the server said ' + res.status);
      banner('Submitted. Hunter has it: the sheet will move this role to '
             + 'Applied.', 'good');
    } catch (e) {
      banner('Submitted, but hunter could not be told (' + e.message +
             '). Tell Claude so the sheet gets updated.', 'bad');
    }
  }

  // A mark that was NOT on the page when the watch started.
  function fresh(watch) {
    const before = watch.baseline || [];
    const hit = marksIn().find((m) => before.indexOf(m) < 0);
    // The words themselves, so the receipt email quotes something real rather
    // than hunter asserting a press nobody observed.
    return hit ? 'the form said "' + hit + '"' : '';
  }

  async function watchFor(watch) {
    while (Date.now() < watch.until) {
      const why = fresh(watch);
      if (why) return report(watch, why);
      await new Promise((r) => setTimeout(r, 1500));
    }
    // Out of time. Clear it so a stale watch cannot fire on some later page.
    await clearWatch(watch);
  }

  const watch = {
    token: cap.token,
    key: cap.key,
    api: api,
    origin: location.origin,
    path: basePath(),
    baseline: marksIn(),
    until: Date.now() + 30 * 60 * 1000,
  };
  await saveWatch(watch);
  return watchFor(watch);
})();
