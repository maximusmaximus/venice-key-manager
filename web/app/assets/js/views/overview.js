import { h, mount, card, pageHead, stat, kv, badge, yesNo, button, notice, fmtUSD, fmtNum, relTime, fmtBytes, copyButton, errorState, skeleton, toast, busy } from '../ui.js';

export async function render(root, ctx) {
  const refreshBtn = button('Refresh', () => load(), { iconName: 'refresh', size: 'sm' });
  const body = h('div', { class: 'stack' }, skeleton(3));
  mount(root,
    pageHead('Overview', 'Live health of your Venice balance, keys, agent allocations, service and backups.', [refreshBtn]),
    body);

  async function load() {
    mount(body, skeleton(3));
    try {
      const o = await ctx.get('/api/overview');
      ctx.refreshBalance(o.balance);
      mount(body, ...build(o));
    } catch (err) {
      mount(body, errorState(err, load));
    }
  }

  function build(o) {
    const b = o.balance || {};
    const c = o.counts || {};
    const svc = o.service || {};
    const tun = o.tunnel || {};
    const vault = o.vault || {};
    const ver = o.version || {};
    const out = [];

    if (b.status === 'OUT' || b.status === 'LOW') {
      out.push(h('div', { class: `notice notice-${b.status === 'OUT' ? 'bad' : 'warn'}`, role: 'alert' },
        h('strong', { text: b.status === 'OUT' ? 'Inference credits are depleted. ' : 'Inference credits are running low. ' }),
        b.warning || '', ' ',
        h('a', { href: b.recharge_url || 'https://venice.ai/settings/api', target: '_blank', rel: 'noopener noreferrer', text: 'Add credits →' })));
    }
    if (!o.has_inference_key && !o.has_admin_key) {
      out.push(notice('No Venice key is configured yet. Import one under Keys & sub-keys.', 'warn'));
    }

    out.push(h('div', { class: 'grid grid-2' },
      card({
        title: 'Venice balance',
        sub: `Thresholds: low < ${fmtUSD((b.thresholds || {}).low_usd)} · out ≤ ${fmtUSD((b.thresholds || {}).out_usd)}`,
        actions: [badge(b.status || 'UNKNOWN')],
        body: h('div', { class: 'stack-sm' },
          h('div', { class: 'hero-balance' },
            h('span', { class: 'amount', text: fmtUSD(b.usd, 4) }), h('span', { class: 'muted', text: 'USD' })),
          h('div', { class: 'stat-grid' },
            stat('DIEM', fmtNum(b.diem)),
            stat('Bundled credits', fmtNum(b.bundled_credits)),
            stat('API tier', String(b.api_tier || '—').toUpperCase())),
          b.error ? notice(`Balance check failed: ${b.error}`, 'warn') : null,
          h('div', { class: 'row' },
            h('a', { class: 'btn btn-sm', href: b.recharge_url || 'https://venice.ai/settings/api', target: '_blank', rel: 'noopener noreferrer' }, 'Venice billing'),
            h('a', { class: 'btn btn-sm btn-ghost', href: '#/settings' }, 'Alert thresholds'))),
      }),
      card({
        title: 'Inventory',
        sub: 'Everything stored in the local vault on this machine.',
        body: h('div', { class: 'stat-grid' },
          stat('Venice keys', String(c.keys || 0), o.has_admin_key ? 'Admin key set' : 'No admin key', '#/keys'),
          stat('Sub-keys', String(c.subkeys || 0), 'Budget-capped', '#/keys'),
          stat('Allocations', String(c.allocations || 0), `${c.allocations_active || 0} claimable`, '#/allocations'),
          stat('Telegram agents', String(c.agent_bots || 0), 'Registered bots', '#/agents'),
          stat('Fleet nodes', String(c.fleet_nodes || 0), 'Tailscale mesh', '#/fleet')),
      })));

    out.push(h('div', { class: 'grid grid-3' },
      card({
        title: 'Service',
        actions: [badge(svc.child_running || svc.port_listening ? 'RUNNING' : 'STOPPED')],
        body: kv([
          ['Port', `${svc.port || '—'} ${svc.port_listening ? '(listening)' : ''}`],
          ['Supervisor', yesNo(svc.supervisor_running, 'Running', 'Not running')],
          ['Auto-start', yesNo(svc.autostart_installed, 'Installed', 'Not installed')],
          ['Restarts', String(svc.restarts_count || 0)],
          ['Platform', svc.platform || '—'],
        ]),
      }),
      card({
        title: 'Public endpoint',
        actions: [badge(tun.status || 'OFFLINE')],
        body: h('div', { class: 'stack-sm' },
          kv([
            ['Claim domain', o.domain || 'venice.vmu.cash (default)'],
            ['Tunnel URL', tun.active_url || '—'],
          ]),
          tun.active_url ? h('div', { class: 'row' }, copyButton(tun.active_url, 'Copy tunnel URL')) : null),
      }),
      card({
        title: 'Vault backups',
        actions: [badge(vault.backup_mirror_exists ? 'OK' : 'WARN', vault.backup_mirror_exists ? 'Mirrored' : 'No mirror')],
        body: h('div', { class: 'stack-sm' },
          kv([
            ['Last backup', relTime(vault.last_backup_at)],
            ['Snapshots', String(vault.total_snapshots || 0)],
            ['Vault size', fmtBytes(vault.vault_size_bytes)],
          ]),
          h('div', { class: 'row' },
            button('Back up now', async (e) => {
              await busy(e.currentTarget, async () => {
                try {
                  const r = await ctx.post('/api/vault/backup', { label: 'manual' });
                  toast(r.success ? `Backup saved: ${r.filename}` : 'Backup failed', r.success ? 'ok' : 'bad');
                } catch (err) { toast(err.message, 'bad'); }
              });
            }, { size: 'sm' }),
            h('a', { class: 'btn btn-sm btn-ghost', href: '#/vault' }, 'Manage'))),
      })));

    out.push(card({
      title: 'Quick actions',
      body: h('div', { class: 'btn-group' },
        h('a', { class: 'btn btn-primary', href: '#/allocations' }, 'Mint agent allocation'),
        h('a', { class: 'btn', href: '#/keys' }, 'Import / create key'),
        h('a', { class: 'btn', href: '#/playground' }, 'Test inference'),
        h('a', { class: 'btn', href: '#/tools' }, 'Run an MCP tool')),
    }));

    out.push(h('p', { class: 'xsmall muted', text: `Version ${ver.version || '?'} · ${ver.branch || 'main'}@${ver.commit || '?'} · ${ver.os || ''}` }));
    return out;
  }

  await load();
}
