/* Fill one application from a hunter payload. Runs as page JavaScript.
 *
 * Never presses submit. There is no reference to a submit control anywhere in
 * this file, and a test asserts that by reading the file itself.
 *
 * Two passes, deliberately. React updates its own state after a click, so a
 * synchronous read straight afterwards sees the old value: the work
 * authorisation question was set correctly on the live Harvey form and reported
 * as unfilled, which is the same lie this project has spent its whole life
 * removing. So one pass acts, then it waits, then a second pass reads the DOM
 * and reports only what is actually there.
 */
(function () {
  'use strict';

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function setNative(el, value) {
    // React tracks its own value and ignores a plain assignment, so the change
    // never reaches its state and the box empties on the next render.
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  const CONTROL = 'input, textarea, select';

  function byLabel(field, want) {
    // The label a human reads, when no selector finds the control. Ashby's
    // Location box carries no name, no id and no aria-label: the only handle on
    // it is the field container's own label, and a container cannot be matched
    // by text in CSS. It can here.
    const target = norm(field.label);
    if (!target) return [];
    const boxes = document.querySelectorAll(
      '.ashby-application-form-field-entry, fieldset, .field, [class*="field"]');
    for (const box of boxes) {
      const lab = box.querySelector('label, legend');
      if (!lab) continue;
      const text = norm(lab.textContent).replace(/\s*\*$/, '');
      if (text !== target && !text.startsWith(target)) continue;
      const found = box.querySelectorAll(want || CONTROL);
      if (found.length) return Array.from(found);
    }
    return [];
  }

  function candidates(selectors, field, want) {
    // Every element the first matching selector finds, not just a lone one. A
    // radio group shares one name, so an exactly-one rule rejects the group.
    for (const sel of selectors || []) {
      let found;
      try { found = document.querySelectorAll(sel); } catch (e) { continue; }
      if (found.length) return Array.from(found);
    }
    return field ? byLabel(field, want) : [];
  }

  function labelOf(el) {
    if (el.labels && el.labels[0]) return norm(el.labels[0].textContent);
    const wrap = el.closest('label');
    return wrap ? norm(wrap.textContent) : '';
  }

  const sameAnswer = (label, want) =>
    label && (label === want || label.startsWith(want) || want.startsWith(label));

  function radiosFor(field) {
    // By selector first, then by label anywhere on the page. Ashby prefixes a
    // radio's name with its section id, so the field key alone matches nothing
    // and every radio question came back unfilled.
    const byName = candidates(field.selectors, field)
      .filter((e) => (e.getAttribute('type') || '').toLowerCase() === 'radio');
    if (byName.length) return byName;
    // Exact label equality only. A looser match found ANOTHER question's "Yes"
    // radio on the live Harvey form: the work authorisation question reported
    // itself answered because the hybrid question's Yes was ticked, and the act
    // pass had clicked that radio instead of its own control.
    const want = norm(field.value);
    return Array.from(document.querySelectorAll('input[type="radio"]'))
      .filter((r) => labelOf(r) === want);
  }

  // ---------- acting ----------

  function actText(field) {
    const el = candidates(field.selectors, field)[0];
    if (el) setNative(el, field.value);
  }

  function actChoice(field) {
    const want = norm(field.value);
    const els = candidates(field.selectors, field);

    const select = els.find((e) => e.tagName === 'SELECT');
    if (select) {
      for (const o of select.options) {
        if (norm(o.textContent) === want || norm(o.value) === want) {
          select.value = o.value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          return;
        }
      }
      return;
    }

    for (const r of radiosFor(field)) {
      if (!sameAnswer(labelOf(r), want)) continue;
      r.click();
      if (r.checked) return;
      if (r.labels && r.labels[0]) r.labels[0].click();
      return;
    }

    const el = els[0];
    if (!el) return;
    if ((el.getAttribute('type') || '').toLowerCase() !== 'checkbox') return;
    // A segmented Yes/No mirrors into a display:none checkbox. The buttons are
    // the control; the input is not the thing to click.
    const parent = el.parentElement;
    const buttons = parent
      ? parent.querySelectorAll('button[aria-pressed], button[data-option]') : [];
    for (const b of buttons) {
      if (want === norm(b.textContent) || want === norm(b.getAttribute('data-option'))) {
        b.click();
        return;
      }
    }
    if (el.offsetParent !== null) el.click();
  }

  function bestOption(texts, want) {
    // Every comma-segment of the answer, in order. Plain containment fails in
    // both directions: "London, United Kingdom" is offered as "London, Greater
    // London, England, United Kingdom", which does not contain it, and the same
    // search offers "London, Ontario, Canada", which must never be taken.
    const lower = texts.map(norm);
    const exact = lower.indexOf(want);
    if (exact >= 0) return exact;
    const segments = want.split(',').map((x) => x.trim()).filter(Boolean);
    if (!segments.length) return -1;
    for (let i = 0; i < lower.length; i++) {
      let at = 0, ok = true;
      for (const seg of segments) {
        const found = lower[i].indexOf(seg, at);
        if (found < 0) { ok = false; break; }
        at = found + seg.length;
      }
      if (ok) return i;
    }
    return -1;
  }

  async function actTypeahead(field) {
    const el = candidates(field.selectors, field, 'input[role="combobox"], input')[0];
    if (!el) return;
    const want = norm(field.value);
    for (const term of [field.value, field.value.split(',')[0].trim()]) {
      el.focus();
      setNative(el, term);
      let opts = [];
      for (let i = 0; i < 16; i++) {
        await sleep(250);
        opts = Array.from(document.querySelectorAll('[role="option"]'));
        if (opts.length) break;
      }
      if (!opts.length) continue;
      const hit = bestOption(opts.map((o) => o.textContent), want);
      if (hit >= 0) { opts[hit].click(); return; }
    }
    // Nothing matched. Leave the box empty rather than holding a half typed
    // place that reads on the page as an answer and is not one.
    setNative(el, '');
  }

  function actFile(file) {
    for (const sel of file.selectors || []) {
      let el;
      try { el = document.querySelector(sel); } catch (e) { continue; }
      if (!el) continue;
      const bin = atob(file.b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], file.name, { type: 'application/pdf' }));
      el.files = dt.files;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
  }

  // ---------- reading back ----------

  function holdsText(field) {
    const el = candidates(field.selectors, field)[0];
    return !!el && norm(el.value) === norm(field.value);
  }

  function holdsCombo(field) {
    // react-select CLEARS its search input on selection and renders the answer
    // as a label beside it, so reading .value says empty on a filled control.
    const el = candidates(field.selectors, field, 'input[role="combobox"], input')[0];
    if (!el) return false;
    if (norm(el.value)) return true;
    let n = el;
    for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
      const v = n.querySelector
        ? n.querySelector('[class*="single-value"], [class*="singleValue"]') : null;
      if (v && norm(v.textContent)) return true;
      const d = n.getAttribute ? n.getAttribute('data-value') : '';
      if (d) return true;
    }
    return false;
  }

  function holdsChoice(field) {
    const want = norm(field.value);
    const els = candidates(field.selectors, field);
    const select = els.find((e) => e.tagName === 'SELECT');
    if (select) {
      const opt = select.options[select.selectedIndex];
      return !!opt && (norm(opt.textContent) === want || norm(opt.value) === want);
    }
    for (const r of radiosFor(field)) {
      if (r.checked && sameAnswer(labelOf(r), want)) return true;
    }
    const el = els[0];
    if (el && (el.getAttribute('type') || '').toLowerCase() === 'checkbox') {
      // A segmented Yes/No answers "No" by pressing the No button, which
      // correctly leaves the mirror checkbox FALSE. Reading only the checkbox
      // called a correctly answered sponsorship question unfilled and told him
      // to do it himself.
      const parent = el.parentElement;
      const buttons = parent
        ? parent.querySelectorAll('button[aria-pressed], button[data-option]') : [];
      for (const b of buttons) {
        if (want !== norm(b.textContent) && want !== norm(b.getAttribute('data-option'))) {
          continue;
        }
        return norm(b.getAttribute('aria-pressed')) === 'true';
      }
      return el.checked === true;
    }
    return false;
  }

  function holdsFile(file) {
    for (const sel of file.selectors || []) {
      let el;
      try { el = document.querySelector(sel); } catch (e) { continue; }
      if (el && el.files && el.files.length) return true;
    }
    return false;
  }

  async function run(payload) {
    // Documents first: a vendor that autofills from the CV writes over the
    // fields afterwards, so anything typed before the upload is lost.
    for (const f of payload.files || []) actFile(f);
    await sleep(1200);
    for (const f of payload.fields || []) {
      if (f.kind === 'text') actText(f);
      else if (f.kind === 'choice') actChoice(f);
      else if (f.kind === 'typeahead') await actTypeahead(f);
    }
    await sleep(600);

    const out = { filled: [], missed: [], files: [], required_missed: [] };
    for (const f of payload.files || []) {
      (holdsFile(f) ? out.files : out.missed).push(f.label);
    }
    for (const f of payload.fields || []) {
      const ok = f.kind === 'text' ? holdsText(f)
        : f.kind === 'choice' ? holdsChoice(f)
        : holdsCombo(f);
      if (ok) out.filled.push(f.label);
      else {
        out.missed.push(f.label);
        if (f.required) out.required_missed.push(f.label);
      }
    }
    return out;
  }

  window.__hunterFill = run;
})();
