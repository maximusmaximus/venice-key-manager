// API client: pairing-code storage, JSON fetch with timeout, 401 handling.

const KEY = 'vkm.pairing';

function safeGet(store) {
  try { return store.getItem(KEY) || ''; } catch (e) { return ''; }
}

export const session = {
  get code() {
    return safeGet(sessionStorage) || safeGet(localStorage);
  },
  set(code, remember) {
    try {
      sessionStorage.setItem(KEY, code);
      if (remember) localStorage.setItem(KEY, code);
      else localStorage.removeItem(KEY);
    } catch (e) { /* storage unavailable: keep in memory only */ }
    memoryCode = code;
  },
  update(code) {
    // Replace the stored code wherever it was stored (used after rotation).
    try {
      sessionStorage.setItem(KEY, code);
      if (localStorage.getItem(KEY)) localStorage.setItem(KEY, code);
    } catch (e) { /* ignore */ }
    memoryCode = code;
  },
  clear() {
    try { sessionStorage.removeItem(KEY); localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    memoryCode = '';
  },
};
let memoryCode = '';

export const events = new EventTarget();

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function api(path, { method = 'GET', body, timeout = 45000, signal } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const code = session.code || memoryCode;
  if (code) headers['X-Pairing-Code'] = code;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  if (signal) signal.addEventListener('abort', () => ctrl.abort(), { once: true });

  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: 'no-store',
      credentials: 'same-origin',
    });
  } catch (e) {
    const msg = e && e.name === 'AbortError'
      ? 'The request timed out. The Venice API may be slow — try again.'
      : 'Network error — is the Venice Key Manager service running?';
    throw new ApiError(msg, 0, null);
  } finally {
    clearTimeout(timer);
  }

  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }

  if (res.status === 401 && data && data.requires_pairing) {
    events.dispatchEvent(new CustomEvent('unauthorized'));
  }
  if (!res.ok) {
    throw new ApiError((data && (data.error || data.message)) || `Request failed (HTTP ${res.status})`, res.status, data);
  }
  return data || {};
}

export const get = (path, opts) => api(path, { ...opts, method: 'GET' });
export const post = (path, body, opts) => api(path, { ...opts, method: 'POST', body: body || {} });
