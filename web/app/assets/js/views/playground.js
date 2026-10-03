import { h, mount, card, pageHead, table, badge, button, field, formValues, busy, toast, notice, errorState, skeleton, fmtUSD, emptyState, kv } from '../ui.js';

export async function render(root, ctx) {
  const output = h('div', { class: 'output', role: 'status', 'aria-live': 'polite', text: 'Responses appear here.' });
  const meta = h('div');
  const modelSelect = h('select', { class: 'select', name: 'model', id: 'pg-model' }, h('option', { value: 'deepseek-v4-flash', text: 'deepseek-v4-flash' }));
  const keySelect = h('select', { class: 'select', name: 'key_id', id: 'pg-key' }, h('option', { value: '', text: 'Vault default (no tier gate)' }));
  const tempOut = h('output', { for: 'pg-temp', class: 'small muted', text: '0.7' });
  const tempInput = h('input', { type: 'range', class: 'range', id: 'pg-temp', name: 'temperature', min: 0, max: 2, step: 0.1, value: 0.7, oninput: (e) => { tempOut.textContent = e.target.value; } });

  const form = h('form', { class: 'form', novalidate: true },
    field({ name: 'prompt', label: 'Prompt', type: 'textarea', rows: 5, required: true, value: 'Say hello from Venice in one sentence.' }),
    h('div', { class: 'form-grid' },
      h('div', { class: 'field' }, h('label', { class: 'label', for: 'pg-model', text: 'Model' }), modelSelect),
      h('div', { class: 'field' }, h('label', { class: 'label', for: 'pg-key', text: 'Gate by key tier' }), keySelect,
        h('p', { class: 'help', text: 'Select a key to enforce its max model tier (403 if the model exceeds it).' })),
      field({ name: 'max_tokens', label: 'Max tokens', type: 'number', value: 160, min: 1, max: 4096, step: 1, inputmode: 'numeric' }),
      h('div', { class: 'field' }, h('div', { class: 'field-head' }, h('label', { class: 'label', for: 'pg-temp', text: 'Temperature' }), tempOut), tempInput)),
    h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Run inference')));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const v = formValues(form);
    await busy(form.querySelector('button[type=submit]'), async () => {
      output.textContent = 'Running…';
      mount(meta);
      try {
        const res = await ctx.post('/api/infer', { ...v, temperature: Number(tempInput.value) }, { timeout: 90000 });
        if (!res.success) {
          output.textContent = '';
          mount(meta, notice(res.error || 'Inference failed', res.out_of_credits ? 'bad' : 'warn'));
          if (res.out_of_credits) ctx.refreshBalance();
          return;
        }
        output.textContent = res.reply || res.content || '(empty response)';
        const u = res.usage || {};
        mount(meta, kv([
          ['Model', res.model],
          ['Latency', `${res.latency_ms} ms`],
          ['Tokens', `${u.prompt_tokens || 0} in · ${u.completion_tokens || 0} out`],
          ['Finish reason', res.finish_reason || '—'],
        ]));
        ctx.refreshBalance();
      } catch (err) {
        output.textContent = '';
        mount(meta, notice(err.message, 'bad'));
      }
    });
  });

  const modelsBody = h('div', null, skeleton(1));
  const tiersBody = h('div', null, skeleton(1));
  const limitsBody = h('div', null, skeleton(1));

  mount(root,
    pageHead('Inference & models', 'Run a test prompt, browse available models, the tier map used for gating, and your account rate limits.'),
    h('div', { class: 'grid grid-2' },
      card({ title: 'Playground', body: form }),
      card({ title: 'Response', body: h('div', { class: 'stack' }, output, meta) })),
    disclosure('Model tiers', tiersBody, true),
    disclosure('Available models', modelsBody, false),
    disclosure('Rate limits', limitsBody, false));

  function disclosure(title, body, open) {
    return h('details', { class: 'disclosure', open }, h('summary', { text: title }), h('div', { class: 'disclosure-body' }, body));
  }

  async function loadTiers() {
    try {
      const res = await ctx.get('/api/model_tiers');
      const rows = (res.tier_order || []).map((t) => res.tiers[t]);
      mount(tiersBody, table([
        { label: 'Tier', render: (t) => h('strong', { text: String(t.tier).toUpperCase() }) },
        { label: 'Label', render: (t) => t.label },
        { label: 'Default model', render: (t) => h('span', { class: 'mono', text: t.default_model }) },
        { label: 'Models', render: (t) => (t.models || []).join(', ') },
        { label: 'Cost / M tokens', render: (t) => `${fmtUSD(t.cost_per_m_in)} in · ${fmtUSD(t.cost_per_m_out)} out` },
      ], rows, { caption: 'Model tiers' }));
    } catch (err) { mount(tiersBody, errorState(err, loadTiers)); }
  }

  async function loadModels() {
    try {
      const res = await ctx.get('/api/models');
      if (!res.success) throw new Error(res.error || 'Could not list models');
      const models = res.models || [];
      const ids = models.map((m) => m.id).filter(Boolean).sort();
      if (ids.length) {
        const current = modelSelect.value;
        mount(modelSelect, ids.map((id) => h('option', { value: id, text: id })));
        modelSelect.value = ids.includes(current) ? current : (ids.includes('deepseek-v4-flash') ? 'deepseek-v4-flash' : ids[0]);
      }
      mount(modelsBody, table([
        { label: 'Model', render: (m) => h('span', { class: 'mono', text: m.id }) },
        { label: 'Type', render: (m) => m.type || (m.model_spec && m.model_spec.type) || '—' },
        { label: 'Context', render: (m) => (m.model_spec && m.model_spec.availableContextTokens) || m.context_length || '—' },
        { label: 'Owner', render: (m) => m.owned_by || '—' },
      ], models, { caption: 'Models', empty: emptyState('No models returned', '') }));
    } catch (err) { mount(modelsBody, errorState(err, loadModels)); }
  }

  async function loadKeys() {
    try {
      const res = await ctx.get('/api/keys');
      for (const k of res.keys || []) {
        keySelect.appendChild(h('option', { value: k.id, text: `${k.description || k.id} — max ${String(k.maxModelTier || 'xl').toUpperCase()}` }));
      }
    } catch (err) { /* optional */ }
  }

  async function loadLimits() {
    try {
      const res = await ctx.get('/api/rate_limits');
      const rows = [];
      for (const rl of res.rateLimits || []) {
        for (const lim of rl.rateLimits || []) {
          rows.push({ model: rl.apiModelId || rl.model || '—', type: lim.type, amount: lim.amount });
        }
      }
      mount(limitsBody,
        res.nextEpochBegins ? h('p', { class: 'small muted', text: `Next epoch: ${new Date(res.nextEpochBegins).toLocaleString()}` }) : null,
        table([
          { label: 'Model', render: (r) => h('span', { class: 'mono', text: r.model }) },
          { label: 'Limit', render: (r) => r.type },
          { label: 'Amount', render: (r) => String(r.amount) },
        ], rows, { caption: 'Rate limits', empty: emptyState('No rate limits reported', '') }));
    } catch (err) { mount(limitsBody, errorState(err, loadLimits)); }
  }

  await Promise.all([loadTiers(), loadModels(), loadKeys(), loadLimits()]);
}
