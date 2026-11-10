import {
  h, mount, card, pageHead, stat, kv, badge, yesNo, button, notice,
  table, field, formValues, fmtUSD, fmtNum, relTime, errorState, skeleton, toast, busy,
} from '../ui.js';

export async function render(root, ctx) {
  const sendBtn = button('Send report to Telegram', async (e) => {
    await busy(e.currentTarget, async () => {
      try {
        const res = await ctx.post('/api/reports/send', {});
        toast(res.success ? 'Inference report delivered to Telegram' : (res.error || 'Failed to send report'), res.success ? 'ok' : 'bad');
        await load();
      } catch (err) {
        toast(err.message, 'bad');
      }
    });
  }, { variant: 'primary', size: 'sm' });

  const refreshBtn = button('Refresh', () => load(), { iconName: 'refresh', size: 'sm' });
  const body = h('div', { class: 'stack' }, skeleton(3));

  mount(root,
    pageHead(
      'Agent inference reports',
      'Per-agent Venice inference telemetry, 7-day key spend, anti-abuse quotas, keep-alive health and scheduled Telegram updates.',
      [sendBtn, refreshBtn],
    ),
    body);

  async function load() {
    mount(body, skeleton(3));
    try {
      const rpt = await ctx.get('/api/reports/agent_usage');
      if (rpt.account) ctx.refreshBalance(rpt.account);
      mount(body, ...build(rpt));
    } catch (err) {
      mount(body, errorState(err, load));
    }
  }

  function build(rpt) {
    const acc = rpt.account || {};
    const summ = rpt.summary || {};
    const sched = rpt.schedule || {};
    const svc = rpt.service || {};
    const agents = Array.isArray(rpt.agents) ? rpt.agents : [];
    const topKeys = Array.isArray(rpt.top_keys) ? rpt.top_keys : [];
    const history = Array.isArray(rpt.history) ? rpt.history : [];
    const out = [];

    if (acc.status === 'OUT' || acc.status === 'LOW') {
      out.push(notice(acc.warning || `Inference credits status: ${acc.status}`, acc.status === 'OUT' ? 'bad' : 'warn'));
    }

    const deltaVal = acc.usd_delta_since_last_report;
    const deltaText = (deltaVal === null || deltaVal === undefined)
      ? 'Baseline snapshot'
      : (deltaVal < 0 ? `-${fmtUSD(Math.abs(deltaVal), 4)} spent` : `+${fmtUSD(deltaVal, 4)}`);

    // 1. Summary + Schedule Grid
    const schedForm = h('form', { class: 'form' });
    schedForm.append(
      field({
        name: 'interval_hours',
        label: 'Scheduled Telegram update interval',
        type: 'select',
        value: String(sched.interval_hours || 6),
        options: [
          { value: '1', label: 'Every 1 hour' },
          { value: '3', label: 'Every 3 hours' },
          { value: '6', label: 'Every 6 hours (default)' },
          { value: '12', label: 'Every 12 hours' },
          { value: '24', label: 'Every 24 hours (daily)' },
        ],
      }),
      field({
        name: 'enabled',
        label: 'Deliver automatic regular reports to Telegram (@v3n15PE_bot)',
        type: 'checkbox',
        value: sched.enabled !== false,
      }),
      h('div', { class: 'row' },
        h('button', { type: 'submit', class: 'btn btn-primary btn-sm' }, 'Save schedule')),
    );

    schedForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const submitBtn = schedForm.querySelector('button[type="submit"]');
      const vals = formValues(schedForm);
      await busy(submitBtn, async () => {
        try {
          await ctx.post('/api/reports/settings', {
            enabled: Boolean(vals.enabled),
            interval_hours: Number(vals.interval_hours || 6),
          });
          toast('Report schedule updated', 'ok');
          await load();
        } catch (err) {
          toast(err.message, 'bad');
        }
      });
    });

    out.push(h('div', { class: 'grid grid-2' },
      card({
        title: 'Inference spend & balance overview',
        sub: `Delta since last report: ${deltaText}`,
        actions: [badge(acc.status || 'UNKNOWN')],
        body: h('div', { class: 'stat-grid' },
          stat('USD balance', fmtUSD(acc.usd, 4), `Tier ${String(acc.api_tier || 'paid').toUpperCase()}`),
          stat('DIEM balance', fmtNum(acc.diem), 'Venice tokens'),
          stat('7-day key spend', fmtUSD(summ.spend_7d_usd, 4), summ.remote_keys_synced ? 'Live Venice API' : 'Local vault'),
          stat('24h local calls', String(summ.local_calls_24h || 0), `${summ.local_tokens_24h || 0} tokens`),
          stat('Active agents', String(summ.active_agents || 0), `${summ.total_keys || 0} keys · ${summ.total_subkeys || 0} sub-keys`),
          stat('Keep-alive', svc.port_listening ? 'ONLINE' : 'CHECK', `Uptime ${svc.uptime_human || '—'} · ${svc.restarts_count || 0} restarts`)),
      }),
      card({
        title: 'Regular Telegram update schedule',
        sub: `Reports delivered: ${sched.reports_sent_count || 0} · Last sent: ${relTime(sched.last_report_iso)}`,
        actions: [yesNo(sched.enabled !== false, `Every ${sched.interval_hours || 6}h`, 'Paused')],
        body: h('div', { class: 'stack-sm' },
          kv([
            ['Next scheduled delivery', sched.enabled !== false ? (sched.next_report_in_human || 'Due now') : 'Paused'],
            ['Watchdog daemon', yesNo(svc.supervisor_running || svc.autostart_installed, 'Active', 'Standby')],
          ]),
          schedForm),
      })));

    // 2. Per-Agent Usage Table
    const agentCols = [
      {
        label: 'Agent',
        render: (r) => h('div', null,
          h('strong', { text: r.agent }),
          r.bot_username ? h('div', { class: 'xsmall muted mono', text: r.bot_username }) : null),
      },
      {
        label: 'Tiers',
        render: (r) => (r.tiers && r.tiers.length ? r.tiers.join(', ') : '—'),
      },
      {
        label: 'Keys / sub-keys',
        render: (r) => `${r.active_keys_count || 0} / ${r.subkeys_count || 0}`,
      },
      {
        label: '7d Venice spend',
        render: (r) => fmtUSD(r.spend_7d_usd, 4),
      },
      {
        label: '24h tracked calls',
        render: (r) => `${r.local_calls_24h || 0} req (${r.local_tokens_24h || 0} tok)`,
      },
      {
        label: 'Allocations',
        render: (r) => r.allocations_total ? `${r.allocations_claimed || 0}/${r.allocations_total} claimed` : '—',
      },
      {
        label: 'Guard quota',
        render: (r) => r.guard
          ? `${r.guard.keys_created_1h}/${r.guard.max_keys_1h} hr · ${r.guard.keys_created_24h}/${r.guard.max_keys_24h} day`
          : (r.has_scoped_admin ? 'Authorized' : 'Inference-only'),
      },
    ];

    out.push(card({
      title: 'Agent usage breakdown',
      sub: 'Inference consumption, provisioned keys, allocation claims and anti-abuse quotas per agent.',
      body: table(agentCols, agents, { caption: 'Per-agent Venice inference usage' }),
    }));

    // 3. Key-level Telemetry Table
    const keyCols = [
      { label: 'Key', render: (k) => h('span', { class: 'mono', text: `${k.name} (${k.masked})` }) },
      { label: 'Agent', render: (k) => k.agent || 'shared' },
      { label: 'Tier', render: (k) => k.tier || 'XL' },
      { label: '7d spend', render: (k) => fmtUSD(k.spend_7d_usd, 4) },
      { label: 'Budget cap', render: (k) => (k.limit_usd ? `${fmtUSD(k.limit_usd)} / ${k.limit_period || 'MONTH'}` : 'Unlimited') },
      { label: 'Last used', render: (k) => relTime(k.last_used_at || k.created_at) },
    ];

    out.push(card({
      title: 'Key spend telemetry',
      sub: 'Redacted key inventory sorted by trailing 7-day consumption.',
      body: table(keyCols, topKeys, { caption: 'Top consuming Venice keys' }),
    }));

    // 4. Recent Report History
    if (history.length > 0) {
      const histCols = [
        { label: 'Sent', render: (s) => relTime(s.iso || s.timestamp) },
        { label: 'USD balance', render: (s) => fmtUSD(s.usd_balance, 4) },
        { label: 'Delta', render: (s) => (s.usd_delta < 0 ? `-${fmtUSD(Math.abs(s.usd_delta), 4)}` : `+${fmtUSD(s.usd_delta || 0, 4)}`) },
        { label: '7d spend', render: (s) => fmtUSD(s.spend_7d_usd || 0, 4) },
        { label: '24h calls', render: (s) => String(s.local_calls_24h || 0) },
      ];
      out.push(card({
        title: 'Recent report snapshots',
        sub: 'Historical snapshots recorded when reports were dispatched.',
        body: table(histCols, history, { caption: 'Dispatched inference report snapshots' }),
      }));
    }

    return out;
  }

  await load();
}
