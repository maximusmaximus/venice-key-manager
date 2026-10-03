// App bootstrap: guest gate, pairing, hash router, navigation drawer, balance pill.
import { api, get, post, session, events } from './api.js';
import { h, mount, icon, toast, fmtUSD, fmtNum, notice, busy, errorState, skeleton, append } from './ui.js';

const ROUTES = [
  { path: 'overview', label: 'Overview', icon: 'overview', section: 'Monitor', load: () => import('./views/overview.js') },
  { path: 'keys', label: 'Keys & sub-keys', icon: 'keys', section: 'Keys', load: () => import('./views/keys.js') },
  { path: 'allocations', label: 'Agent allocations', icon: 'allocations', section: 'Keys', load: () => import('./views/allocations.js') },
  { path: 'playground', label: 'Inference & models', icon: 'playground', section: 'Keys', load: () => import('./views/playground.js') },
  { path: 'agents', label: 'Telegram agents', icon: 'agents', section: 'Agents', load: () => import('./views/agents.js') },
  { path: 'fleet', label: 'Fleet mesh', icon: 'fleet', section: 'Agents', load: () => import('./views/fleet.js') },
  { path: 'tools', label: 'MCP console', icon: 'tools', section: 'Agents', load: () => import('./views/tools.js') },
  { path: 'vault', label: 'Vault & backups', icon: 'vault', section: 'System', load: () => import('./views/vault.js') },
  { path: 'settings', label: 'Service & settings', icon: 'settings', section: 'System', load: () => import('./views/settings.js') },
];

const $ = (id) => document.getElementById(id);
const state = { authed: false, balanceTimer: null, renderSeq: 0 };

// ---------------------------------------------------------------- theme
function initTheme() {
  $('btn-theme').addEventListener('click', () => {
    const root = document.documentElement;
    const current = root.getAttribute('data-theme')
      || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    const next = current === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('vkm.theme', next); } catch (e) { /* ignore */ }
    toast(`${next === 'light' ? 'Light' : 'Dark'} theme`, 'info', 1500);
  });
}

// ---------------------------------------------------------------- guest gate
function initGuest() {
  const keyInput = $('input-guest-key');
  const toggle = $('btn-guest-toggle-key');
  toggle.addEventListener('click', () => {
    const show = keyInput.type === 'password';
    keyInput.type = show ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', String(show));
    toggle.setAttribute('aria-label', show ? 'Hide key' : 'Show key');
  });

  $('guest-validate-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const out = $('guest-validate-result');
    const key = keyInput.value.trim();
    if (!key) {
      keyInput.setAttribute('aria-invalid', 'true');
      mount(out, notice('Paste a Venice API key to validate.', 'warn'));
      keyInput.focus();
      return;
    }
    keyInput.removeAttribute('aria-invalid');
    await busy($('btn-guest-validate-key'), async () => {
      try {
        const res = await post('/api/validate_key', { api_key: key });
        if (!res.valid) {
          mount(out, notice(res.error || 'Key is not valid.', 'bad'));
          return;
        }
        const b = res.balances || {};
        mount(out, h('div', { class: 'stack-sm' },
          notice(res.message || 'Valid Venice key.', 'ok'),
          h('div', { class: 'result-grid' },
            resultStat('Key', res.masked_key || '—'),
            resultStat('USD', fmtUSD(b.USD, 4)),
            resultStat('DIEM', fmtNum(b.DIEM)),
            resultStat('Tier', String((res.apiTier && res.apiTier.id) || 'paid').toUpperCase()),
            resultStat('Test prompt', res.small_test_passed ? `Passed · ${res.latency_ms || 0} ms` : 'Failed'))));
        if (!res.small_test_passed && res.small_test_error) {
          append(out, [notice(`Test prompt error: ${res.small_test_error}`, 'warn')]);
        }
      } catch (err) {
        mount(out, notice(err.message, 'bad'));
      }
    });
  });

  $('guest-pair-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const out = $('guest-pair-result');
    const input = $('input-pairing-code');
    const code = input.value.trim();
    if (!code) {
      input.setAttribute('aria-invalid', 'true');
      mount(out, notice('Enter the pairing code.', 'warn'));
      input.focus();
      return;
    }
    input.removeAttribute('aria-invalid');
    await busy($('btn-pair'), async () => {
      try {
        const res = await post('/api/pair', { code });
        if (res.success) {
          session.set(code, $('input-remember').checked);
          input.value = '';
          mount(out);
          toast('Browser paired — dashboard unlocked', 'ok');
          enterApp();
        }
      } catch (err) {
        input.setAttribute('aria-invalid', 'true');
        mount(out, notice(err.message, 'bad'));
      }
    });
  });
}

function resultStat(label, value) {
  return h('div', { class: 'stat' }, h('div', { class: 'stat-label', text: label }), h('div', { class: 'stat-sub', text: value }));
}

function showGuest(message) {
  state.authed = false;
  stopBalancePolling();
  $('authenticated-view').hidden = true;
  $('guest-view').hidden = false;
  document.querySelector('.skip-link').setAttribute('href', '#main-guest');
  document.title = 'VENICE // TG ENGINE';
  if (message) mount($('guest-pair-result'), notice(message, 'warn'));
}

// ---------------------------------------------------------------- shell
function buildNav() {
  const nav = $('nav');
  const items = [];
  let section = '';
  for (const r of ROUTES) {
    if (r.section !== section) {
      section = r.section;
      items.push(h('div', { class: 'nav-section', text: section }));
    }
    items.push(h('a', { href: `#/${r.path}`, 'data-route': r.path }, icon(r.icon), h('span', { text: r.label })));
  }
  mount(nav, items);
}

function setDrawer(open) {
  const sb = $('sidebar');
  sb.classList.toggle('open', open);
  $('scrim').classList.toggle('show', open);
  $('btn-menu').setAttribute('aria-expanded', String(open));
  if (open) {
    const first = sb.querySelector('a, button');
    if (first) first.focus();
  }
}

function initShell() {
  buildNav();
  $('btn-menu').addEventListener('click', () => setDrawer(true));
  $('btn-menu-close').addEventListener('click', () => { setDrawer(false); $('btn-menu').focus(); });
  $('scrim').addEventListener('click', () => setDrawer(false));
  $('nav').addEventListener('click', (e) => { if (e.target.closest('a')) setDrawer(false); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('sidebar').classList.contains('open')) { setDrawer(false); $('btn-menu').focus(); }
  });
  $('btn-lock').addEventListener('click', () => {
    session.clear();
    showGuest();
    toast('Signed out of this browser', 'info');
  });
  window.addEventListener('hashchange', () => { if (state.authed) renderRoute(); });
  events.addEventListener('unauthorized', () => {
    if (!state.authed) return;
    session.clear();
    showGuest('Your session is no longer authorised (the pairing code may have been rotated). Please pair again.');
  });
}

async function enterApp() {
  state.authed = true;
  $('guest-view').hidden = true;
  $('authenticated-view').hidden = false;
  document.querySelector('.skip-link').setAttribute('href', '#main');
  if (!location.hash || location.hash === '#' || location.hash === '#/') {
    history.replaceState(null, '', '#/overview');
  }
  renderRoute();
  refreshBalance();
  startBalancePolling();
  get('/api/version').then((v) => {
    $('version-label').textContent = `v${v.version || '?'} · ${v.branch || 'main'}@${v.commit || '?'}`;
  }).catch(() => {});
}

// ---------------------------------------------------------------- balance pill
export async function refreshBalance(data) {
  try {
    const bal = data || await get('/api/balance');
    const pill = $('balance-pill');
    const status = bal.status || 'UNKNOWN';
    pill.dataset.status = status;
    $('balance-pill-value').textContent = status === 'UNKNOWN' ? '—' : fmtUSD(bal.usd, 2);
    pill.title = bal.warning || bal.message || `Balance status: ${status}`;
    pill.setAttribute('aria-label', `Venice balance ${fmtUSD(bal.usd, 2)}, status ${status}`);
    if ((status === 'LOW' || status === 'OUT') && state.lastBalanceStatus !== status) {
      toast(bal.warning || `Venice balance is ${status}`, status === 'OUT' ? 'bad' : 'warn', 8000);
    }
    state.lastBalanceStatus = status;
  } catch (e) { /* non-fatal */ }
}

function startBalancePolling() {
  stopBalancePolling();
  state.balanceTimer = setInterval(() => {
    if (document.visibilityState === 'visible' && state.authed) refreshBalance();
  }, 120000);
}
function stopBalancePolling() {
  if (state.balanceTimer) clearInterval(state.balanceTimer);
  state.balanceTimer = null;
}

// ---------------------------------------------------------------- router
function currentRoute() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  const [path, qs] = raw.split('?');
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  return { route, params: new URLSearchParams(qs || '') };
}

export function navigate(path) {
  location.hash = `#/${path}`;
}

async function renderRoute() {
  const seq = ++state.renderSeq;
  const { route, params } = currentRoute();
  const root = $('view-root');
  for (const a of document.querySelectorAll('#nav a')) {
    if (a.dataset.route === route.path) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
  document.title = `${route.label} · VENICE // TG ENGINE`;
  root.setAttribute('aria-busy', 'true');
  mount(root, skeleton(3));
  try {
    const mod = await route.load();
    if (seq !== state.renderSeq) return;
    const view = h('div', { class: 'stack' });
    mount(root, view);
    await mod.render(view, { api, get, post, params, navigate, refreshBalance });
  } catch (err) {
    if (seq !== state.renderSeq) return;
    mount(root, errorState(err, () => renderRoute()));
  } finally {
    if (seq === state.renderSeq) {
      root.setAttribute('aria-busy', 'false');
      const main = $('main');
      if (document.activeElement && document.activeElement !== document.body && !main.contains(document.activeElement)) {
        main.focus({ preventScroll: true });
      }
      window.scrollTo(0, 0);
    }
  }
}

// ---------------------------------------------------------------- boot
async function boot() {
  initTheme();
  initGuest();
  initShell();
  try {
    const st = await get('/api/pairing_status');
    if (st.authenticated) enterApp();
    else showGuest(session.code ? 'Stored pairing code is no longer valid. Please pair again.' : '');
    if (!st.authenticated && session.code) session.clear();
  } catch (err) {
    showGuest(err.message);
  }
}

boot();
