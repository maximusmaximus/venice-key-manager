import { h, mount, card, pageHead, table, badge, button, field, formValues, busy, toast, notice, confirmDialog, infoDialog, errorState, skeleton, fmtDate, fmtUSD, emptyState, copyButton, copyText, kv, progress } from '../ui.js';
import { TIERS, PERIODS, localToIso } from '../constants.js';

function mcpSnippet(url, agent) {
  return JSON.stringify({ tool: 'venice_claim_allocated_key', arguments: { claim_token_or_url: url, agent_id: agent || '' } }, null, 2);
}

function agentInstructions(a) {
  return [
    `You have been allocated ${a.allocated_keys_count} Venice API key(s) (tier ${String(a.quality_tier || 's').toUpperCase()}, ${fmtUSD(a.budget_usd)}/${String(a.limit_period || 'DAY').toLowerCase()}).`,
    `Claim link: ${a.claim_url}`,
    'Inspect it first with the MCP tool "venice_inspect_allocation" (shows how many keys remain and when claiming opens),',
    'then claim with "venice_claim_allocated_key" using the claim link above. Do not share the claimed key.',
  ].join('\n');
}

export async function render(root, ctx) {
  const listBody = h('div', null, skeleton(2));
  const resultHost = h('div');
  const filterInput = h('input', { class: 'input', type: 'search', id: 'alloc-filter', placeholder: 'Filter by agent or label', 'aria-label': 'Filter allocations' });
  let allocs = [];

  mount(root,
    pageHead('Agent allocations',
      'Mint a claim link (https://venice.vmu.cash/claim/…) for an agent. The agent sees how many keys it gets and when it can claim them — the raw Venice key is never shared until it claims.',
      [button('Refresh', () => load(), { iconName: 'refresh', size: 'sm' })]),
    h('div', { class: 'grid grid-2' }, mintCard(), inspectCard()),
    resultHost,
    card({
      title: 'Allocations',
      actions: [filterInput],
      body: listBody,
    }));

  filterInput.addEventListener('input', () => renderList());

  // ---------------------------------------------------------------- mint
  function mintCard() {
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-grid' },
        field({ name: 'target_agent', label: 'Agent', required: true, placeholder: 'hermes-music', help: 'Name of the agent that will claim.' }),
        field({ name: 'label', label: 'Label', placeholder: 'Weekly music batch' }),
        field({ name: 'allocated_keys_count', label: 'Number of keys', type: 'number', value: 1, min: 1, max: 1000, step: 1, required: true, inputmode: 'numeric' }),
        field({ name: 'quality_tier', label: 'Max model tier', type: 'select', options: TIERS, value: 's' }),
        field({ name: 'budget_usd', label: 'Budget per key (USD)', type: 'number', value: 0.25, min: 0, step: '0.01', inputmode: 'decimal' }),
        field({ name: 'limit_period', label: 'Budget period', type: 'select', options: PERIODS, value: 'DAY' }),
        field({ name: 'valid_from', label: 'Claiming opens', type: 'datetime-local', help: 'Leave empty to open immediately.' }),
        field({ name: 'valid_until', label: 'Expires', type: 'datetime-local', help: 'Leave empty for no expiry.' })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Mint claim link')));

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      const v = formValues(form);
      const from = localToIso(v.valid_from);
      const until = localToIso(v.valid_until);
      if (from && until && new Date(until) <= new Date(from)) {
        toast('Expiry must be after the opening time', 'warn');
        return;
      }
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/allocations/mint', { ...v, valid_from: from || undefined, valid_until: until || undefined });
          toast(res.message || 'Allocation minted', 'ok');
          showMinted(res.allocation || res);
          form.reset();
          await load();
        } catch (err) { toast(err.message, 'bad', 7000); }
      });
    });
    return card({ title: 'Mint an allocation', sub: 'Creates a shareable claim link bound to your Cloud DNS domain.', body: form });
  }

  function showMinted(a) {
    mount(resultHost, card({
      title: 'Claim link ready',
      sub: `For ${a.target_agent} · ${a.allocated_keys_count} key(s) · tier ${String(a.quality_tier).toUpperCase()}`,
      cls: 'card-flat',
      actions: [button('Dismiss', () => mount(resultHost), { size: 'sm', variant: 'ghost' })],
      body: h('div', { class: 'stack' },
        h('div', { class: 'field' },
          h('div', { class: 'field-head' }, h('span', { class: 'label', text: 'Claim link — share this with the agent' }), copyButton(a.claim_url, 'Copy link')),
          h('code', { class: 'code-box', text: a.claim_url })),
        h('div', { class: 'field' },
          h('div', { class: 'field-head' }, h('span', { class: 'label', text: 'Instructions for the agent' }), copyButton(agentInstructions(a), 'Copy instructions')),
          h('pre', { class: 'code-box', text: agentInstructions(a) })),
        h('div', { class: 'field' },
          h('div', { class: 'field-head' }, h('span', { class: 'label', text: 'MCP call' }), copyButton(mcpSnippet(a.claim_url, a.target_agent), 'Copy')),
          h('pre', { class: 'code-box', text: mcpSnippet(a.claim_url, a.target_agent) })),
        h('div', { class: 'row' }, h('a', { class: 'btn btn-sm', href: a.claim_url.replace(/^https?:\/\/[^/]+/, ''), target: '_blank', rel: 'noopener' }, 'Preview claim portal'))),
    }));
    resultHost.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ---------------------------------------------------------------- inspect
  function inspectCard() {
    const out = h('div', { role: 'status', 'aria-live': 'polite' });
    const form = h('form', { class: 'form', novalidate: true },
      field({ name: 'token', label: 'Claim link or token', required: true, mono: true, placeholder: 'https://venice.vmu.cash/claim/vclm_…' }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn' }, 'Inspect')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      const { token } = formValues(form);
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.get(`/api/allocations/inspect?token=${encodeURIComponent(token)}`);
          mount(out, details(res));
        } catch (err) { mount(out, notice(err.message, 'bad')); }
      });
    });
    return card({ title: 'Inspect a claim link', sub: 'Exactly what an agent sees before claiming (public, never reveals the key).', body: h('div', { class: 'stack' }, form, out) });
  }

  function details(a) {
    return h('div', { class: 'stack-sm' },
      h('div', { class: 'row-between' }, h('strong', { text: a.label || a.id }), badge(a.status)),
      kv([
        ['Agent', a.target_agent],
        ['Keys', `${a.claimed_keys_count || 0} claimed of ${a.allocated_keys_count} (${a.remaining_claims} remaining)`],
        ['Claimable now', a.can_claim_now ? 'Yes' : 'No'],
        ['Opens', fmtDate(a.valid_from)],
        ['Expires', a.valid_until ? fmtDate(a.valid_until) : 'Never'],
        ['Tier / budget', `${String(a.quality_tier || 's').toUpperCase()} · ${fmtUSD(a.budget_usd)}/${String(a.limit_period || 'DAY').toLowerCase()}`],
        ['Node', a.target_node || 'local'],
      ]));
  }

  // ---------------------------------------------------------------- list
  async function load() {
    mount(listBody, skeleton(2));
    try {
      const res = await ctx.get('/api/allocations');
      allocs = res.allocations || [];
      renderList();
    } catch (err) { mount(listBody, errorState(err, load)); }
  }

  function renderList() {
    const q = filterInput.value.trim().toLowerCase();
    const rows = q ? allocs.filter((a) => `${a.target_agent} ${a.label}`.toLowerCase().includes(q)) : allocs;
    mount(listBody, table([
      { label: 'Allocation', render: (a) => h('div', null, h('strong', { text: a.label || a.id }), h('div', { class: 'xsmall muted', text: `Agent: ${a.target_agent}` })) },
      { label: 'Claims', render: (a) => h('div', { class: 'stack-sm' },
        h('span', { class: 'small', text: `${a.claimed_keys_count || 0} / ${a.allocated_keys_count}` }),
        progress(a.claimed_keys_count || 0, a.allocated_keys_count || 1)) },
      { label: 'Status', render: (a) => badge(a.status) },
      { label: 'Window', render: (a) => h('div', { class: 'xsmall' }, h('div', { text: `Opens ${fmtDate(a.valid_from)}` }), h('div', { class: 'muted', text: a.valid_until ? `Expires ${fmtDate(a.valid_until)}` : 'No expiry' })) },
      { label: 'Tier', render: (a) => `${String(a.quality_tier || 's').toUpperCase()} · ${fmtUSD(a.budget_usd)}` },
      { label: 'Actions', cls: 'actions', render: (a) => h('div', { class: 'btn-group' },
        button('Copy link', (e) => copyText(a.claim_url, e.currentTarget), { size: 'sm' }),
        button('Details', () => infoDialog({ title: a.label || a.id, body: h('div', { class: 'stack' }, details(a), h('code', { class: 'code-box', text: a.claim_url }), h('pre', { class: 'code-box', text: agentInstructions(a) }), h('div', { class: 'row' }, copyButton(agentInstructions(a), 'Copy agent instructions'))) }), { size: 'sm' }),
        a.status !== 'REVOKED' ? button('Revoke', () => revoke(a), { size: 'sm', variant: 'danger' }) : null,
        button('Delete', () => remove(a), { size: 'sm', variant: 'ghost' })) },
    ], rows, { caption: 'Allocations', empty: emptyState(q ? 'No matching allocations' : 'No allocations yet', q ? 'Try a different filter.' : 'Mint one above to give an agent a claim link.') }));
  }

  async function revoke(a) {
    const ok = await confirmDialog({ title: 'Revoke allocation?', message: `The claim link for ${a.target_agent} stops working immediately. Keys already claimed are not affected.`, confirmText: 'Revoke', danger: true });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/allocations/revoke', { id: a.id });
      toast(res.success ? 'Allocation revoked' : 'Allocation not found', res.success ? 'ok' : 'warn');
      await load();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function remove(a) {
    const ok = await confirmDialog({ title: 'Delete allocation?', message: 'This permanently removes the allocation and its claim history from the vault.', confirmText: 'Delete', danger: true });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/allocations/delete', { id: a.id });
      toast(res.success ? 'Allocation deleted' : 'Allocation not found', res.success ? 'ok' : 'warn');
      await load();
    } catch (err) { toast(err.message, 'bad'); }
  }

  await load();
}
