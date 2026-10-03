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

  // A banner, optionally with one button. The button is created with
  // type="button" and lives on this banner, outside the employer's form, so it
  // can never submit the form; it only tells hunter something.
  function banner(text, tone, action) {
    const el = document.createElement('div');
    el.style.cssText =
      'position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:12px 18px;' +
      'font:600 14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;' +
      'color:#fff;background:' + (tone === 'bad' ? '#a4262c' : tone === 'work' ? '#444' : '#1a7f37');
    el.textContent = text;
    if (action) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = action.label;
      b.style.cssText =
        'margin-left:12px;padding:4px 10px;border:1px solid #fff;border-radius:4px;' +
        'background:transparent;color:#fff;font:inherit;cursor:pointer';
      b.addEventListener('click', action.onPress);
      el.appendChild(b);
    }
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
    'extension/hunter-extension.zip and extract it: inside is a folder called ' +
    'extension. Copy it over the one Chrome points at, press the reload arrow ' +
    'on the Hunter card at chrome://extensions, check the card now reads ' +
    '%NEED%, then reopen this link.';

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
  // Enough for any batch, and bounded so storage cannot grow for ever.
  const MAX_WATCHES = 40;
  // How long a stored watch can still be resumed. It was 30 minutes, and
  // openrouter on 2026-09-25 is what that cost: he read the form properly,
  // pressed Submit later, and the watch had already gone, so nothing was ever
  // reported. Seven days covers any real delay between opening and sending.
  const WATCH_TTL = 7 * 24 * 60 * 60 * 1000;
  // How long one open page keeps looking for a confirmation. A page left open
  // stops polling after this; the stored watch lives on for the next page.
  const POLL_PER_PAGE = 30 * 60 * 1000;
  // Ashby and Greenhouse put the form at /application, Lever at /apply, and all
  // three load their confirmation under the posting's own path, so the watch
  // is keyed on that path. Lever's /apply was not trimmed, so a Lever
  // confirmation page never matched its watch.
  const basePath = () => location.pathname.replace(/\/(application|apply)\/?$/, '');
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
  // on the same board. The longest matching path wins, so a
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

  // A report that could not be delivered is kept on its watch and sent again
  // from the next page he opens on any board. It used to be deleted before it
  // was sent, so one network blip lost an application for good.
  async function flushPending() {
    const all = await allWatches();
    for (const k of Object.keys(all)) {
      const w = all[k];
      if (w && w.pending) await deliver(w, w.pending.why, w.pending.saidSo, false);
    }
  }
  await flushPending();

  const cap = capability();
  const resumed = cap ? null : await resumable();
  if (!cap && !resumed) return;

  const store = await chrome.storage.sync.get(['api']);
  const api = (store && store.api) || DEFAULT_API;

  if (resumed) {
    // A later page on the same job. Nothing to fill and no payload fetched:
    // the capability is used only to report. If the form never says anything
    // recognisable, he can say so himself.
    banner('Hunter is watching for this application to be confirmed. Already sent it?',
           'work', markButton(resumed));
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

  // The watch is stored before any banner offers the button, so a press always
  // has something to report against.
  const watch = {
    token: cap.token,
    key: cap.key,
    api: api,
    origin: location.origin,
    path: basePath(),
    baseline: marksIn(),
    until: Date.now() + WATCH_TTL,
  };
  await saveWatch(watch);
  const mark = markButton(watch);

  if (stale) {
    banner(STALE.replace('%NEED%', payload.needs_extension), 'bad');
  } else if (out.required_missed.length) {
    banner('Filled ' + done + '. DO THESE YOURSELF before submitting: ' +
           out.required_missed.join('; '), 'bad', mark);
  } else if (out.missed.length) {
    banner('Filled ' + done + '. Not filled (none required): ' +
           out.missed.join('; ') + '. Read it and press Submit.', 'work', mark);
  } else {
    banner('Filled all ' + done + ' fields and attached your documents. ' +
           'Read it and press Submit.', 'good', mark);
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
    return deliver(watch, why, false, true);
  }

  // His own word, for when the form never says anything the extension
  // recognises. Hunter records it as his word and never as the form speaking.
  function markButton(watch) {
    return {
      label: 'I applied, mark it',
      onPress: () => deliver(watch, '', true, true),
    };
  }

  // The report is written onto the watch BEFORE it is sent, and the watch is
  // cleared only once the server has it (or has said the application was
  // replaced). A failed send stays pending and goes again from the next page.
  async function deliver(watch, why, saidSo, loud) {
    watch.pending = { why: why, saidSo: !!saidSo };
    await saveWatch(watch);
    try {
      const res = await fetch(watch.api.replace(/\/payload$/, '/submitted'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'omit',
        body: JSON.stringify({ token: watch.token, key: watch.key, evidence: why,
                               said_so: !!saidSo }),
      });
      if (res.status === 410) {
        await clearWatch(watch);
        const said = await res.json().catch(() => ({}));
        if (loud) banner(said.message || 'This application was replaced, so submitting it '
          + 'was not recorded. Open the most recent email for this role.', 'bad');
        return;
      }
      if (res.status === 404 || res.status === 409) {
        // Final answers: sending again cannot change them, so the watch goes
        // and he is told rather than retried at for a week.
        await clearWatch(watch);
        if (loud) banner('Hunter did not record this (the server said ' + res.status +
          '). Type Applied in column A of its Pipeline row.', 'bad');
        return;
      }
      if (!res.ok) throw new Error('the server said ' + res.status);
      await clearWatch(watch);
      if (loud) banner(saidSo
        ? 'Marked as applied on your word. Hunter will move this role to Applied.'
        : 'Submitted. Hunter has it: the sheet will move this role to Applied.', 'good');
    } catch (e) {
      if (loud) banner('Submitted, but hunter could not be told (' + e.message +
             '). It will try again the next time you open a job page.', 'bad');
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
    const stop = Math.min(watch.until, Date.now() + POLL_PER_PAGE);
    while (Date.now() < watch.until && Date.now() < stop) {
      const why = fresh(watch);
      if (why) return report(watch, why);
      await new Promise((r) => setTimeout(r, 1500));
    }
    // A stored watch past its life is cleared, so it cannot fire on some later
    // page. One that is merely past this page's polling stays for the next page.
    if (Date.now() > watch.until) await clearWatch(watch);
  }

  return watchFor(watch);
})();
