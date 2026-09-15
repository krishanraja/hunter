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
    'empty. Download https://github.com/krishanraja/hunter/archive/refs/heads/' +
    'main.zip, extract it over your hunter folder, then press the reload arrow ' +
    'on the Hunter card at chrome://extensions and reopen this link.';

  const cap = capability();
  if (!cap) return;

  const store = await chrome.storage.sync.get(['api']);
  const api = (store && store.api) || DEFAULT_API;

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

  // Then watch for him pressing it. Hunter cannot see the click from anywhere
  // else: it runs in the cloud and the click happens here. Without this the
  // sheet keeps saying "Not applied" on a role that is applied for, which is
  // exactly the silence this whole system was built to stop.
  //
  // What counts as pressed is ONLY the form saying so in its own words. An
  // earlier version also treated any navigation off the /application path as a
  // submission, which made clicking back to the job description record an
  // application that was never sent: the sheet would move the role to Applied,
  // the receipt email would claim the form acknowledged it, and hunter would
  // then skip his real APPROVE for that role. A navigation is not evidence.
  //
  // Nothing is lost by dropping it. A real submission that only redirects is
  // caught either when the new page carries one of these marks, since the check
  // runs on every tick and reads the live body, or by hunter's own watcher on
  // the employer's "thanks for applying" email, which is better evidence than
  // anything this script can see because it comes from the employer.
  const deadline = Date.now() + 30 * 60 * 1000;
  const seen = () => {
    const body = (document.body ? document.body.innerText : '').toLowerCase();
    const hit = SUBMITTED_MARKS.find((m) => body.includes(m));
    // The words themselves, so the receipt email quotes something real rather
    // than hunter asserting a press nobody observed.
    return hit ? 'the form said "' + hit + '"' : '';
  };

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500));
    const why = seen();
    if (!why) continue;
    // Read the answer. A 4xx or 5xx does not throw, so the previous version
    // painted the green "Hunter has it" banner over a write that never happened,
    // which is the same lie in the opposite direction.
    try {
      const res = await fetch(api.replace(/\/payload$/, '/submitted'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'omit',
        body: JSON.stringify({ token: cap.token, key: cap.key, evidence: why }),
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
    return;
  }
})();
