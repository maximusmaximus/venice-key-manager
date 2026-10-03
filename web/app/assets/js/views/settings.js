import { h, mount, card, pageHead, badge, button, field, formValues, busy, toast, notice, confirmDialog, infoDialog, errorState, skeleton, kv, yesNo, copyButton, copyText, json, fmtUSD, relTime } from '../ui.js';
import { session } from '../api.js';

export async function render(root, ctx) {
  const serviceBody = h('div', null, skeleton(1));
  const tunnelBody = h('div', null, skeleton(1));
  const pairingBody = h('div', null, skeleton(1));
  const thresholdBody = h('div', null, skeleton(1));
  const configBody = h('div', null, skeleton(1));

  mount(root,
    pageHead('Service & settings', 'Supervisor and auto-start, public tunnel, pairing code, balance alerts and Venice/Telegram configuration.',
      [button('Refresh', () => loadAll(), { iconName: 'refresh', size: 'sm' })]),
    h('div', { class: 'grid grid-2' },
      card({ title: 'Service supervisor', body: serviceBody }),
      card({ title: 'Public tunnel', body: tunnelBody })),
    h('div', { class: 'grid grid-2' },
      card({ title: 'Pairing code', sub: 'Required by browsers, agents and peer nodes for privileged API calls.', body: pairingBody }),
      card({ title: 'Balance alerts', sub: 'The Telegram bot alerts when the balance crosses these thresholds.', body: thresholdBody })),
    card({ title: 'Configuration', body: configBody }));

  // ---------------------------------------------------------------- service
  async function loadService() {
    try {
      const s = await ctx.get('/api/service/status');
      const crashes = s.crash_history || [];
      mount(serviceBody, h('div', { class: 'stack' },
        kv([
          ['Status', badge(s.child_running || s.port_listening ? 'RUNNING' : 'STOPPED')],
          ['Port', `${s.port} ${s.port_listening ? '(listening)' : '(not listening)'}`],
          ['Supervisor', yesNo(s.supervisor_running, `Running${s.supervisor_pid ? ` · PID ${s.supervisor_pid}` : ''}`, 'Not running')],
          ['Service process', yesNo(s.child_running, `Running${s.child_pid ? ` · PID ${s.child_pid}` : ''}`, 'Not running')],
          ['Auto-start', yesNo(s.autostart_installed, (s.autostart_methods || []).join(', ') || 'Installed', 'Not installed')],
          ['Restarts', String(s.restarts_count || 0)],
          ['Last crash', crashes.length ? relTime(crashes[crashes.length - 1].timestamp) : 'None'],
        ]),
        h('div', { class: 'btn-group' },
          button('Restart service', restart, { variant: 'danger', size: 'sm' }),
          button('Install auto-start', install, { size: 'sm' }))));
    } catch (err) { mount(serviceBody, errorState(err, loadService)); }
  }

  async function restart(e) {
    const ok = await confirmDialog({
      title: 'Restart the service?',
      message: 'The supervisor restarts the web server, Telegram bot and tunnel. The dashboard reconnects automatically in a few seconds. The quick-tunnel URL may change.',
      confirmText: 'Restart', danger: true,
    });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/service/restart');
      toast(res.message || 'Restart requested', 'ok', 6000);
      setTimeout(loadService, 8000);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function install(e) {
    const btn = e.currentTarget;
    await busy(btn, async () => {
      try {
        const res = await ctx.post('/api/service/install');
        infoDialog({ title: res.success ? 'Auto-start installed' : 'Auto-start install', body: h('ul', { class: 'stack-sm' }, (res.messages || []).map((m) => h('li', { class: 'small', text: m }))) });
        await loadService();
      } catch (err) { toast(err.message, 'bad'); }
    });
  }

  // ---------------------------------------------------------------- tunnel
  async function loadTunnel() {
    try {
      const t = await ctx.get('/api/tunnel/status');
      mount(tunnelBody, h('div', { class: 'stack' },
        kv([
          ['Status', badge(t.status || 'OFFLINE')],
          ['Configured domain', t.configured_domain || '—'],
          ['Active URL', t.active_url || '—'],
          ['Type', (t.details && t.details.type) || '—'],
        ]),
        h('div', { class: 'btn-group' },
          t.active_url ? copyButton(t.active_url, 'Copy URL') : null,
          t.running ? null : button('Start tunnel', async (e) => {
            await busy(e.currentTarget, async () => {
              try {
                const res = await ctx.post('/api/tunnel/start', {});
                toast(res.success ? `Tunnel ${res.status}: ${res.public_url || ''}` : (res.error || 'Tunnel failed'), res.success ? 'ok' : 'bad', 7000);
                await loadTunnel();
              } catch (err) { toast(err.message, 'bad'); }
            });
          }, { size: 'sm', variant: 'primary' }))));
    } catch (err) { mount(tunnelBody, errorState(err, loadTunnel)); }
  }

  // ---------------------------------------------------------------- pairing
  async function loadPairing() {
    const codeEl = h('code', { class: 'code-box', text: '••••••••••' });
    let revealed = '';
    const revealBtn = button('Reveal', async () => {
      if (revealed) {
        revealed = '';
        codeEl.textContent = '••••••••••';
        revealBtn.lastChild.textContent = 'Reveal';
        return;
      }
      try {
        const res = await ctx.get('/api/pairing/code');
        revealed = res.pairing_code;
        codeEl.textContent = revealed;
        revealBtn.lastChild.textContent = 'Hide';
      } catch (err) { toast(err.message, 'bad'); }
    }, { size: 'sm' });
    mount(pairingBody, h('div', { class: 'stack' },
      codeEl,
      h('div', { class: 'btn-group' },
        revealBtn,
        button('Copy', async (e) => {
          const btn = e.currentTarget;
          try {
            const res = await ctx.get('/api/pairing/code');
            copyText(res.pairing_code, btn);
          } catch (err) { toast(err.message, 'bad'); }
        }, { size: 'sm' }),
        button('Rotate code', rotate, { size: 'sm', variant: 'danger' })),
      h('p', { class: 'help', text: 'Rotating signs out every other paired browser, agent and peer node until they use the new code.' })));
  }

  async function rotate() {
    const ok = await confirmDialog({
      title: 'Rotate the pairing code?',
      message: 'A new code is generated. This browser stays signed in; other browsers, agents and peer nodes must re-pair.',
      confirmText: 'Rotate', danger: true,
    });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/pairing/rotate');
      session.update(res.pairing_code);
      toast(res.message || 'Pairing code rotated', res.env_override ? 'warn' : 'ok', 7000);
      await loadPairing();
    } catch (err) { toast(err.message, 'bad'); }
  }

  // ---------------------------------------------------------------- thresholds
  async function loadThresholds() {
    try {
      const bal = await ctx.get('/api/balance');
      const t = bal.thresholds || {};
      const form = h('form', { class: 'form', novalidate: true },
        h('div', { class: 'form-grid' },
          field({ name: 'low_usd', label: 'Low warning below (USD)', type: 'number', value: t.low_usd, min: 0, step: '0.01', required: true, inputmode: 'decimal' }),
          field({ name: 'out_usd', label: 'Out of credits at or below (USD)', type: 'number', value: t.out_usd, min: 0, step: '0.01', required: true, inputmode: 'decimal' })),
        h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Save thresholds')));
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form.reportValidity()) return;
        const v = formValues(form);
        if (Number(v.out_usd) >= Number(v.low_usd)) { toast('The "out" threshold must be lower than the "low" threshold', 'warn'); return; }
        await busy(form.querySelector('button[type=submit]'), async () => {
          try {
            await ctx.post('/api/balance/thresholds', v);
            toast('Thresholds saved', 'ok');
            ctx.refreshBalance();
          } catch (err) { toast(err.message, 'bad'); }
        });
      });
      mount(thresholdBody, h('div', { class: 'stack' },
        kv([
          ['Current balance', `${fmtUSD(bal.usd, 4)} · ${bal.status}`],
          ['Last alert sent', bal.last_alert && bal.last_alert.status && (bal.last_alert.at || bal.last_alert.timestamp) ? `${bal.last_alert.status} · ${relTime(bal.last_alert.at || bal.last_alert.timestamp)}` : 'Never'],
        ]),
        form));
    } catch (err) { mount(thresholdBody, errorState(err, loadThresholds)); }
  }

  // ---------------------------------------------------------------- config
  async function loadConfig() {
    try {
      const c = await ctx.get('/api/config');
      const form = h('form', { class: 'form', novalidate: true, autocomplete: 'off' },
        h('div', { class: 'form-grid' },
          field({ name: 'inference_key', label: 'Venice inference key', type: 'password', mono: true, placeholder: c.inference_key_set ? `Stored (${c.inference_key_preview}) — leave blank to keep` : 'Not set', autocomplete: 'new-password' }),
          field({ name: 'admin_key', label: 'Venice admin key', type: 'password', mono: true, placeholder: c.admin_key_set ? `Stored (${c.admin_key_preview}) — leave blank to keep` : 'Not set', autocomplete: 'new-password' }),
          field({ name: 'authorized_chat_id', label: 'Authorised Telegram chat ID', value: c.authorized_chat_id || '', inputmode: 'numeric' }),
          field({ name: 'cloudflare_domain', label: 'Claim-link domain', value: c.cloudflare_domain || '', placeholder: 'venice.vmu.cash', help: 'Used for new allocation claim links.' })),
        h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Save configuration')));
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const v = formValues(form);
        const payload = {};
        for (const [k, val] of Object.entries(v)) if (val !== '' && val !== undefined) payload[k] = val;
        if (!Object.keys(payload).length) { toast('Nothing to save', 'info'); return; }
        await busy(form.querySelector('button[type=submit]'), async () => {
          try {
            await ctx.post('/api/config', payload);
            toast('Configuration saved', 'ok');
            await loadConfig();
          } catch (err) { toast(err.message, 'bad'); }
        });
      });
      mount(configBody, form);
    } catch (err) { mount(configBody, errorState(err, loadConfig)); }
  }

  async function loadAll() {
    await Promise.all([loadService(), loadTunnel(), loadPairing(), loadThresholds(), loadConfig()]);
  }
  await loadAll();
}
