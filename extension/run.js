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
  if (out.required_missed.length) {
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
  const startedAt = location.href;
  const deadline = Date.now() + 30 * 60 * 1000;
  const seen = () => {
    const body = (document.body ? document.body.innerText : '').toLowerCase();
    if (SUBMITTED_MARKS.some((m) => body.includes(m))) return 'the form said so';
    if (location.href !== startedAt && !location.href.includes('/application')) {
      return 'the form closed and moved to ' + location.href;
    }
    return '';
  };

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500));
    const why = seen();
    if (!why) continue;
    try {
      await fetch(api.replace(/\/payload$/, '/submitted'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'omit',
        body: JSON.stringify({ token: cap.token, key: cap.key, evidence: why }),
      });
      banner('Submitted. Hunter has it: the sheet will move this role to '
             + 'Applied.', 'good');
    } catch (e) {
      banner('Submitted, but hunter could not be told (' + e.message +
             '). Tell Claude so the sheet gets updated.', 'bad');
    }
    return;
  }
})();
