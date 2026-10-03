import { h, mount, card, pageHead, table, badge, button, field, formValues, busy, toast, notice, confirmDialog, formDialog, errorState, skeleton, fmtDate, fmtUSD, emptyState } from '../ui.js';
import { TIERS, PERIODS, KEY_TYPES } from '../constants.js';

export async function render(root, ctx) {
  const keysBody = h('div', null, skeleton(1));
  const subBody = h('div', null, skeleton(1));
  let keys = [];

  mount(root,
    pageHead('Keys & sub-keys', 'Import or issue Venice API keys, cap spend with sub-keys and deploy them into agent configs.',
      [button('Refresh', () => loadAll(), { iconName: 'refresh', size: 'sm' })]),
    h('div', { class: 'grid grid-2' }, addKeyCard(), createSubkeyCard()),
    card({ title: 'Venice keys', sub: 'Keys stored in the vault (merged with the remote list when an admin key is set).', body: keysBody }),
    card({ title: 'Sub-keys', sub: 'Budget- and tier-capped keys assigned to agents.', body: subBody }));

  // ---------------------------------------------------------------- add key
  function addKeyCard() {
    const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Add key mode' });
    const formHost = h('div');
    let mode = 'import';
    const setMode = (m) => {
      mode = m;
      for (const t of tabs.children) t.setAttribute('aria-selected', String(t.dataset.mode === m));
      mount(formHost, m === 'import' ? importForm() : generateForm());
    };
    for (const [m, label] of [['import', 'Import existing'], ['generate', 'Issue via admin key']]) {
      tabs.appendChild(h('button', { type: 'button', class: 'tab', role: 'tab', 'data-mode': m, onclick: () => setMode(m) }, label));
    }
    setMode(mode);
    return card({ title: 'Add a Venice key', body: h('div', { class: 'stack' }, tabs, formHost) });
  }

  function importForm() {
    const form = h('form', { class: 'form', novalidate: true },
      field({ name: 'key_string', label: 'API key', type: 'password', required: true, mono: true, placeholder: 'VENICE_...', help: 'Validated live against Venice before it is stored.' }),
      h('div', { class: 'form-grid' },
        field({ name: 'description', label: 'Description', value: 'Imported Venice Key' }),
        field({ name: 'key_type', label: 'Type', type: 'select', options: KEY_TYPES, value: 'INFERENCE' }),
        field({ name: 'max_model_tier', label: 'Max model tier', type: 'select', options: TIERS, value: 'xl' })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Import & verify')));
    form.addEventListener('submit', (e) => submitKey(e, form, 'import'));
    return form;
  }

  function generateForm() {
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-grid' },
        field({ name: 'description', label: 'Description', value: 'Agent Key', required: true }),
        field({ name: 'key_type', label: 'Type', type: 'select', options: KEY_TYPES, value: 'INFERENCE' }),
        field({ name: 'limit_usd', label: 'Spend limit (USD)', type: 'number', min: 0, step: '0.01', placeholder: 'No limit', inputmode: 'decimal' }),
        field({ name: 'limit_period', label: 'Limit period', type: 'select', options: PERIODS, value: 'MONTH' }),
        field({ name: 'max_model_tier', label: 'Max model tier', type: 'select', options: TIERS, value: 'xl' }),
        field({ name: 'expires_at', label: 'Expires', type: 'date' }),
        field({ name: 'admin_key', label: 'Admin key (only if not saved yet)', type: 'password', mono: true, span: true, help: 'Stored in the vault for future remote key management.' })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Issue key')));
    form.addEventListener('submit', (e) => submitKey(e, form, 'generate'));
    return form;
  }

  async function submitKey(e, form, mode) {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const v = formValues(form);
    const btn = form.querySelector('button[type=submit]');
    await busy(btn, async () => {
      try {
        const res = await ctx.post('/api/create_key', { ...v, mode });
        if (!res.success) {
          toast(res.error || 'Key operation failed', 'bad', 7000);
          return;
        }
        toast(res.message || 'Key saved', 'ok');
        form.reset();
        await loadKeys();
      } catch (err) { toast(err.message, 'bad', 7000); }
    });
  }

  // ---------------------------------------------------------------- sub-key create
  function createSubkeyCard() {
    const parentSelect = h('select', { class: 'select', name: 'parent_key_id', id: 'sk-parent' }, h('option', { value: '', text: 'Default vault key' }));
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-grid' },
        field({ name: 'name', label: 'Name', required: true, placeholder: 'e.g. hermes-music daily' }),
        h('div', { class: 'field' }, h('label', { class: 'label', for: 'sk-parent', text: 'Parent key' }), parentSelect),
        field({ name: 'budget_usd', label: 'Budget (USD)', type: 'number', value: 0.25, min: 0, step: '0.01', inputmode: 'decimal' }),
        field({ name: 'limit_period', label: 'Reset period', type: 'select', options: PERIODS, value: 'DAY' }),
        field({ name: 'max_model_tier', label: 'Max model tier', type: 'select', options: TIERS, value: 's' }),
        field({ name: 'target_agent', label: 'Assign to agent', placeholder: 'hermes-music' }),
        field({ name: 'deploy', label: 'Write the key into the agent config now', type: 'checkbox', value: false, span: true })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Create sub-key')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      const v = formValues(form);
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/subkeys/create', v);
          toast(res.message || 'Sub-key created', 'ok');
          if (res.deployed_to) toast(`Deployed to ${res.deployed_to}`, 'ok');
          form.reset();
          await loadSubkeys();
        } catch (err) { toast(err.message, 'bad', 7000); }
      });
    });
    createSubkeyCard.parentSelect = parentSelect;
    return card({ title: 'Create a sub-key', sub: 'Never hand an agent your master key — give it a capped sub-key or an allocation link.', body: form });
  }

  function fillParents() {
    const sel = createSubkeyCard.parentSelect;
    if (!sel) return;
    mount(sel, h('option', { value: '', text: 'Default vault key' }),
      keys.filter((k) => k.source === 'vault' || k.has_apiKey).map((k) => h('option', { value: k.id, text: `${k.description || k.id} (${k.id})` })));
  }

  // ---------------------------------------------------------------- lists
  async function loadKeys() {
    mount(keysBody, skeleton(1));
    try {
      const res = await ctx.get('/api/keys');
      keys = res.keys || [];
      fillParents();
      mount(keysBody,
        res.requires_admin ? notice('Showing vault keys only. Save an admin key to list and revoke keys on your Venice account.', 'info') : null,
        table([
          { label: 'Description', render: (k) => h('div', null, h('strong', { text: k.description || k.name || '—' }), h('div', { class: 'xsmall muted mono', text: k.id || '' })) },
          { label: 'Type', render: (k) => k.apiKeyType || 'INFERENCE' },
          { label: 'Tier', render: (k) => String(k.maxModelTier || 'xl').toUpperCase() },
          { label: 'Key', render: (k) => h('span', { class: 'mono', text: k.apiKey_preview || k.last6Chars ? (k.apiKey_preview || `…${k.last6Chars}`) : '—' }) },
          { label: 'Status', render: (k) => badge(k.status || 'ACTIVE') },
          { label: 'Created', render: (k) => fmtDate(k.createdAt || k.created_at) },
          { label: 'Actions', cls: 'actions', render: (k) => h('div', { class: 'btn-group' },
            button('Deploy', () => deployKey(k), { size: 'sm' }),
            button('Revoke', () => revokeKey(k), { size: 'sm', variant: 'danger' })) },
        ], keys, { caption: 'Venice keys', empty: emptyState('No keys yet', 'Import an existing Venice key to get started.') }));
    } catch (err) { mount(keysBody, errorState(err, loadKeys)); }
  }

  async function loadSubkeys() {
    mount(subBody, skeleton(1));
    try {
      const res = await ctx.get('/api/subkeys');
      mount(subBody, table([
        { label: 'Name', render: (s) => h('div', null, h('strong', { text: s.name || s.label || '—' }), h('div', { class: 'xsmall muted mono', text: s.id })) },
        { label: 'Budget', render: (s) => `${fmtUSD(s.budget_usd)} / ${String(s.limit_period || s.period || 'DAY').toLowerCase()}` },
        { label: 'Tier', render: (s) => String(s.quality_tier || s.maxModelTier || 's').toUpperCase() },
        { label: 'Agent', render: (s) => s.target_agent || s.assigned_agent || '—' },
        { label: 'Status', render: (s) => badge(s.status || 'ACTIVE') },
        { label: 'Actions', cls: 'actions', render: (s) => h('div', { class: 'btn-group' },
          button('Apply to agent', () => applySubkey(s), { size: 'sm' }),
          button('Remove', () => removeSubkey(s), { size: 'sm', variant: 'danger' })) },
      ], res.subkeys || [], { caption: 'Sub-keys', empty: emptyState('No sub-keys yet', 'Create one above to give an agent a capped key.') }));
    } catch (err) { mount(subBody, errorState(err, loadSubkeys)); }
  }

  // ---------------------------------------------------------------- actions
  async function deployKey(k) {
    const v = await formDialog({
      title: 'Deploy key to an agent',
      description: `Writes ${k.description || k.id} into the agent's config (a .bak backup is created first).`,
      fields: [
        { name: 'agent_name', label: 'Agent name', placeholder: 'hermes-music', help: 'Known agents resolve to their config automatically.' },
        { name: 'target_path', label: 'Or explicit config path', placeholder: 'C:\\path\\to\\config.yaml', mono: true },
      ],
      submitText: 'Deploy',
    });
    if (!v) return;
    try {
      const res = await ctx.post('/api/deploy_key', { key_string: k.id, agent_name: v.agent_name, target_path: v.target_path || undefined });
      toast(res.success ? `Deployed to ${res.target}` : (res.error || 'Deploy failed'), res.success ? 'ok' : 'bad', 7000);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function revokeKey(k) {
    const ok = await confirmDialog({
      title: 'Revoke this key?',
      message: `${k.description || k.id} will be removed from the vault${k.id && !String(k.id).startsWith('vk_') ? ' and deleted on Venice' : ''}. Agents using it will stop working.`,
      confirmText: 'Revoke key', danger: true,
    });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/revoke_key', { key_id: k.id });
      toast(res.success ? 'Key revoked' : (res.error || 'Revoke failed'), res.success ? 'ok' : 'bad');
      await loadKeys();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function applySubkey(s) {
    const v = await formDialog({
      title: 'Apply sub-key to agent',
      fields: [
        { name: 'agent_name', label: 'Agent name', value: s.target_agent || 'hermes-music', required: true },
        { name: 'target_path', label: 'Explicit config path (optional)', mono: true },
      ],
      submitText: 'Apply',
    });
    if (!v) return;
    try {
      const res = await ctx.post('/api/subkeys/apply', { subkey_id: s.id, agent_name: v.agent_name, target_path: v.target_path || undefined });
      toast(res.success ? `Applied to ${res.target}` : (res.error || 'Apply failed'), res.success ? 'ok' : 'bad', 7000);
      await loadSubkeys();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function removeSubkey(s) {
    const ok = await confirmDialog({ title: 'Remove sub-key?', message: `${s.name || s.id} will be removed from the vault.`, confirmText: 'Remove', danger: true });
    if (!ok) return;
    try {
      await ctx.post('/api/subkeys/remove', { id: s.id });
      toast('Sub-key removed', 'ok');
      await loadSubkeys();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function loadAll() { await Promise.all([loadKeys(), loadSubkeys()]); }
  await loadAll();
}
