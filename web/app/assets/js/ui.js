// UI helpers. All DOM is built with textContent / attributes — never innerHTML with data.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  let deferredValue;
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'value') deferredValue = v;
      else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'required' || k === 'hidden' || k === 'open') {
        el[k] = Boolean(v);
      } else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset' && typeof v === 'object') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
  }
  append(el, children);
  if (deferredValue !== undefined) el.value = deferredValue;
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === '') continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...children) {
  clear(el);
  return append(el, children);
}

// ---------- Icons (inline SVG built via DOM) ----------
const ICONS = {
  overview: 'M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z',
  keys: 'M15 7a4 4 0 1 1-3.87 5H9v2H7v2H4v-3l5.13-5.13A4 4 0 0 1 15 7z',
  allocations: 'M20 12V8H6a2 2 0 0 1 0-4h12v4M4 6v12a2 2 0 0 0 2 2h14v-4M18 12a2 2 0 0 0 0 4h4v-4z',
  playground: 'M4 17l6-6-6-6M12 19h8',
  agents: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  fleet: 'M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z',
  vault: 'M3 11h18v10H3zM7 11V7a5 5 0 0 1 10 0v4',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  tools: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z',
  copy: 'M9 9h11v11H9zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
};

export function icon(name, size = 20) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', ICONS[name] || ICONS.overview);
  svg.appendChild(p);
  return svg;
}

// ---------- Formatting ----------
export function fmtUSD(v, digits = 2) {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  return `$${n.toFixed(digits)}`;
}
export function fmtNum(v, digits = 2) {
  const n = Number(v);
  return isFinite(n) ? n.toFixed(digits) : '—';
}
export function fmtDate(v) {
  if (!v) return '—';
  if (typeof v === 'number' && v < 1e12) v *= 1000; // Python time.time() seconds
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
export function relTime(v) {
  if (!v) return '—';
  if (typeof v === 'number' && v < 1e12) v *= 1000; // Python time.time() seconds
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  const abs = Math.abs(s);
  const fmt = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (abs < 60) return fmt.format(-s, 'second');
  if (abs < 3600) return fmt.format(-Math.round(s / 60), 'minute');
  if (abs < 86400) return fmt.format(-Math.round(s / 3600), 'hour');
  return fmt.format(-Math.round(s / 86400), 'day');
}
export function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1048576) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1048576).toFixed(1)} MB`;
}

// ---------- Components ----------
const STATUS_CLASS = {
  HEALTHY: 'ok', ACTIVE: 'ok', ONLINE: 'ok', RUNNING: 'ok', READY: 'ok', OK: 'ok', TRUE: 'ok', CLAIMED: 'info',
  LOW: 'warn', PENDING: 'warn', UNPROBED: 'warn', STARTING: 'warn', DEPLETED: 'warn', EXHAUSTED: 'warn',
  OUT: 'bad', OFFLINE: 'bad', REVOKED: 'bad', EXPIRED: 'bad', ERROR: 'bad', STOPPED: 'bad', FALSE: 'bad', CRASHED: 'bad',
};
export function badge(status, label) {
  const key = String(status || 'UNKNOWN').toUpperCase();
  return h('span', { class: `badge badge-${STATUS_CLASS[key] || 'info'}` }, label || key.replace(/_/g, ' '));
}
export function yesNo(v, yes = 'Yes', no = 'No') {
  return badge(v ? 'OK' : 'FALSE', v ? yes : no);
}

export function card({ title, sub, actions, body, cls = '', id, headingLevel = 'h2' }) {
  const head = (title || actions) ? h('div', { class: 'card-head' },
    h('div', { class: 'grow' }, title ? h(headingLevel, { text: title }) : null, sub ? h('p', { class: 'sub', text: sub }) : null),
    actions ? h('div', { class: 'btn-group' }, actions) : null) : null;
  return h('section', { class: `card ${cls}`, id }, head, body);
}

export function pageHead(title, description, actions) {
  return h('div', { class: 'page-head' },
    h('div', { class: 'grow' }, h('h1', { text: title, id: 'page-title' }), description ? h('p', { text: description }) : null),
    actions ? h('div', { class: 'btn-group' }, actions) : null);
}

export function button(label, onClick, { variant = '', size = '', type = 'button', title, disabled, iconName, ariaLabel } = {}) {
  return h('button', {
    type,
    class: ['btn', variant && `btn-${variant}`, size && `btn-${size}`].filter(Boolean).join(' '),
    title, disabled, 'aria-label': ariaLabel,
    onclick: onClick,
  }, iconName ? icon(iconName, 16) : null, label);
}

export function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).map(([k, v]) =>
    h('div', null, h('dt', { text: k }), h('dd', null, v instanceof Node ? v : String(v === undefined || v === null || v === '' ? '—' : v)))));
}

export function stat(label, value, sub, href) {
  return h(href ? 'a' : 'div', { class: 'stat', href },
    h('div', { class: 'stat-label', text: label }),
    h('div', { class: 'stat-value', text: value }),
    sub ? h('div', { class: 'stat-sub', text: sub }) : null);
}

export function notice(text, type = '') {
  return h('div', { class: `notice ${type ? `notice-${type}` : ''}`, role: type === 'bad' ? 'alert' : null }, text);
}

export function progress(value, max) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const bar = h('span');
  bar.style.width = `${pct}%`;
  return h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': max, 'aria-valuenow': value }, bar);
}

export function skeleton(rows = 3) {
  return h('div', { class: 'stack', 'aria-hidden': 'true' },
    Array.from({ length: rows }, () => h('div', { class: 'skeleton skeleton-block' })));
}

export function emptyState(title, text, action) {
  return h('div', { class: 'empty' }, h('h3', { text: title }), text ? h('p', { text }) : null, action || null);
}

export function errorState(err, retry) {
  return h('div', { class: 'error-state' },
    notice(err && err.message ? err.message : String(err), 'bad'),
    retry ? button('Try again', retry, { size: 'sm', iconName: 'refresh' }) : null);
}

/** columns: [{ label, render(row) -> Node|string, cls }] */
export function table(columns, rows, { caption, empty } = {}) {
  if (rows !== undefined && rows !== null && !Array.isArray(rows)) {
    throw new TypeError('table(columns, rows, options): rows must be an array');
  }
  if (!rows || rows.length === 0) return empty || emptyState('Nothing here yet', '');
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'table' },
      caption ? h('caption', { class: 'visually-hidden', text: caption }) : null,
      h('thead', null, h('tr', null, columns.map((c) => h('th', { scope: 'col', text: c.label })))),
      h('tbody', null, rows.map((r) => h('tr', null, columns.map((c) => {
        const v = c.render(r);
        return h('td', { 'data-label': c.label, class: c.cls || null }, v instanceof Node ? v : String(v === undefined || v === null || v === '' ? '—' : v));
      }))))));
}

let fieldSeq = 0;
/** spec: { name, label, type, value, options:[{value,label}]|[str], help, required, placeholder, min, max, step, span, autocomplete, mono } */
export function field(spec) {
  const id = spec.id || `f-${spec.name}-${++fieldSeq}`;
  const helpId = spec.help ? `${id}-help` : null;
  let control;
  const common = {
    id, name: spec.name, required: spec.required, placeholder: spec.placeholder,
    'aria-describedby': helpId, autocomplete: spec.autocomplete || 'off',
  };
  if (spec.type === 'select') {
    control = h('select', { ...common, class: 'select', value: spec.value },
      (spec.options || []).map((o) => {
        const opt = typeof o === 'object' ? o : { value: o, label: o };
        return h('option', { value: opt.value, text: opt.label });
      }));
  } else if (spec.type === 'textarea') {
    control = h('textarea', { ...common, class: `textarea ${spec.mono ? 'mono' : ''}`, rows: spec.rows || 5, value: spec.value || '', spellcheck: spec.mono ? 'false' : null });
  } else if (spec.type === 'checkbox') {
    return h('label', { class: `check ${spec.span ? 'span-2' : ''}` },
      h('input', { type: 'checkbox', id, name: spec.name, checked: spec.value }), spec.label);
  } else {
    control = h('input', {
      ...common, class: `input ${spec.mono ? 'mono' : ''}`, type: spec.type || 'text', value: spec.value,
      min: spec.min, max: spec.max, step: spec.step, inputmode: spec.inputmode,
      spellcheck: spec.mono || spec.type === 'password' ? 'false' : null, autocapitalize: spec.mono ? 'off' : null,
    });
  }
  return h('div', { class: `field ${spec.span ? 'span-2' : ''}` },
    h('label', { class: 'label', for: id, text: spec.label + (spec.required ? ' *' : '') }),
    control,
    spec.help ? h('p', { class: 'help', id: helpId, text: spec.help }) : null);
}

export function formValues(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'number') out[el.name] = el.value === '' ? '' : Number(el.value);
    else out[el.name] = el.value.trim();
  }
  return out;
}

/** Runs fn while the button shows a spinner and is disabled. */
export async function busy(btn, fn) {
  if (!btn) return fn();
  const original = Array.from(btn.childNodes);
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  mount(btn, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', { text: 'Working…' }));
  try {
    return await fn();
  } finally {
    mount(btn, original);
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
  }
}

// ---------- Toasts ----------
export function toast(message, type = 'info', timeout = 4500) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const el = h('div', { class: `toast toast-${type}`, role: type === 'bad' ? 'alert' : 'status' },
    h('div', { class: 'grow', text: message }),
    h('button', { type: 'button', class: 'btn btn-ghost btn-sm', 'aria-label': 'Dismiss', onclick: () => el.remove() }, '×'));
  host.appendChild(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
}

export async function copyText(text, btn) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch (e) {
    const ta = h('textarea', { value: text, readonly: true, 'aria-hidden': 'true' });
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
    ta.remove();
  }
  if (btn) {
    const prev = btn.textContent;
    btn.textContent = ok ? 'Copied!' : 'Copy failed';
    setTimeout(() => { btn.textContent = prev; }, 1600);
  }
  toast(ok ? 'Copied to clipboard' : 'Could not copy — select and copy manually', ok ? 'ok' : 'warn', 2200);
  return ok;
}

export function copyButton(text, label = 'Copy') {
  return h('button', { type: 'button', class: 'btn btn-sm', onclick: (e) => copyText(text, e.currentTarget) }, label);
}

// ---------- Dialogs ----------
function openDialog(build, { wide = false } = {}) {
  return new Promise((resolve) => {
    const dlg = h('dialog', { class: `modal ${wide ? 'wide' : ''}`, 'aria-modal': 'true' });
    const done = (v) => { resolve(v); dlg.close(); };
    dlg.addEventListener('close', () => { dlg.remove(); resolve(null); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) done(null); });
    append(dlg, [build(done)]);
    document.body.appendChild(dlg);
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
    const first = dlg.querySelector('input, select, textarea, [data-autofocus]');
    if (first) first.focus();
  });
}

export function confirmDialog({ title, message, confirmText = 'Confirm', danger = false }) {
  return openDialog((done) => {
    const titleId = `dlg-${++fieldSeq}`;
    return h('form', { method: 'dialog', 'aria-labelledby': titleId, onsubmit: (e) => { e.preventDefault(); done(true); } },
      h('div', { class: 'modal-body' }, h('h2', { id: titleId, text: title }), message ? h('p', { class: 'text-2', text: message }) : null),
      h('div', { class: 'modal-foot' },
        h('button', { type: 'button', class: 'btn', onclick: () => done(false) }, 'Cancel'),
        h('button', { type: 'submit', class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`, 'data-autofocus': true }, confirmText)));
  }).then((v) => v === true);
}

export function formDialog({ title, description, fields, submitText = 'Save' }) {
  return openDialog((done) => {
    const titleId = `dlg-${++fieldSeq}`;
    const form = h('form', { 'aria-labelledby': titleId, novalidate: true });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      done(formValues(form));
    });
    append(form, [
      h('div', { class: 'modal-body' },
        h('h2', { id: titleId, text: title }),
        description ? h('p', { class: 'muted small', text: description }) : null,
        h('div', { class: 'form' }, fields.map(field))),
      h('div', { class: 'modal-foot' },
        h('button', { type: 'button', class: 'btn', onclick: () => done(null) }, 'Cancel'),
        h('button', { type: 'submit', class: 'btn btn-primary' }, submitText)),
    ]);
    return form;
  });
}

export function infoDialog({ title, body }) {
  return openDialog((done) => {
    const titleId = `dlg-${++fieldSeq}`;
    return h('div', { 'aria-labelledby': titleId },
      h('div', { class: 'modal-body' }, h('h2', { id: titleId, text: title }), body),
      h('div', { class: 'modal-foot' }, h('button', { type: 'button', class: 'btn btn-primary', onclick: () => done(true), 'data-autofocus': true }, 'Close')));
  }, { wide: true });
}

export function json(obj) {
  return h('pre', { class: 'code-box', text: JSON.stringify(obj, null, 2) });
}
