import { h, mount, card, pageHead, table, badge, button, field, formValues, busy, toast, notice, confirmDialog, formDialog, errorState, skeleton, fmtDate, emptyState } from '../ui.js';

export async function render(root, ctx) {
  const listBody = h('div', null, skeleton(1));
  const wizardOut = h('div', { role: 'status', 'aria-live': 'polite' });

  mount(root,
    pageHead('Telegram agents', 'Register Telegram bot tokens for your agents, test delivery and deploy tokens into agent configs.',
      [button('Check live status', () => load(true), { size: 'sm' }), button('Refresh', () => load(false), { iconName: 'refresh', size: 'sm' })]),
    h('div', { class: 'grid grid-2' }, registerCard(), wizardCard()),
    card({ title: 'Registered agent bots', body: listBody }));

  function registerCard() {
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-grid' },
        field({ name: 'agent_name', label: 'Agent name', required: true, placeholder: 'hermes-music' }),
        field({ name: 'bot_token', label: 'Bot token', type: 'password', required: true, mono: true, placeholder: '123456:ABC…', help: 'Validated with Telegram getMe before saving.' }),
        field({ name: 'config_path', label: 'Agent config path (optional)', mono: true, span: true }),
        field({ name: 'notes', label: 'Notes', span: true })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Register bot')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/agent_bots/register', formValues(form));
          if (res.success === false) { toast(res.error || 'Registration failed', 'bad', 7000); return; }
          toast('Agent bot registered', 'ok');
          form.reset();
          await load(false);
        } catch (err) { toast(err.message, 'bad', 7000); }
      });
    });
    return card({ title: 'Register a bot', body: form });
  }

  function wizardCard() {
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-grid' },
        field({ name: 'agent_name', label: 'Agent name', required: true, value: 'hermes-agent' }),
        field({ name: 'suggested_name', label: 'Display name (optional)' })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn' }, 'Generate steps')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/agent_bots/botfather_wizard', formValues(form));
          const w = res.wizard || {};
          mount(wizardOut, h('div', { class: 'stack-sm' },
            h('ol', { class: 'stack-sm small' }, (w.steps || []).map((s) => h('li', { text: String(s).replace(/^\d+\.\s*/, '') }))),
            h('a', { class: 'btn btn-primary btn-sm', href: w.botfather_deep_link || 'https://t.me/BotFather', target: '_blank', rel: 'noopener noreferrer' }, 'Open @BotFather')));
        } catch (err) { mount(wizardOut, notice(err.message, 'bad')); }
      });
    });
    return card({ title: 'Create a new bot', sub: 'Guided @BotFather steps with a suggested name and username.', body: h('div', { class: 'stack' }, form, wizardOut) });
  }

  async function load(probe) {
    mount(listBody, skeleton(1));
    try {
      const res = await ctx.get(`/api/agent_bots${probe ? '?probe=1' : ''}`);
      mount(listBody, table([
        { label: 'Agent', render: (b) => h('div', null, h('strong', { text: b.agent_name }), b.notes ? h('div', { class: 'xsmall muted', text: b.notes }) : null) },
        { label: 'Bot', render: (b) => { const u = b.username || b.bot_username; return u ? `@${String(u).replace(/^@/, '')}` : (b.first_name || b.bot_name || '—'); } },
        { label: 'Token', render: (b) => h('span', { class: 'mono', text: b.bot_token_preview || '—' }) },
        { label: 'Status', render: (b) => (b.is_live === undefined ? badge('UNPROBED', 'Not checked') : badge(b.is_live ? 'ONLINE' : 'OFFLINE', b.is_live ? 'Live' : 'Invalid')) },
        { label: 'Updated', render: (b) => fmtDate(b.last_deployed_at || b.updated_at || b.created_at || b.registered_at) },
        { label: 'Actions', cls: 'actions', render: (b) => h('div', { class: 'btn-group' },
          button('Test', () => test(b), { size: 'sm' }),
          button('Deploy', () => deploy(b), { size: 'sm' }),
          button('Remove', () => remove(b), { size: 'sm', variant: 'danger' })) },
      ], res.agent_bots || [], { caption: 'Agent bots', empty: emptyState('No agent bots yet', 'Register a bot token above.') }));
    } catch (err) { mount(listBody, errorState(err, () => load(probe))); }
  }

  async function test(b) {
    try {
      const res = await ctx.post('/api/agent_bots/test', { agent_name: b.agent_name });
      toast(res.success ? 'Test message sent to the authorised chat' : (res.error || 'Test failed'), res.success ? 'ok' : 'bad', 6000);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deploy(b) {
    const v = await formDialog({
      title: `Deploy ${b.agent_name} token`,
      description: 'Writes the bot token into the agent config (a backup is made first).',
      fields: [{ name: 'target_path', label: 'Config path (optional)', value: b.config_path || '', mono: true }],
      submitText: 'Deploy',
    });
    if (!v) return;
    try {
      const res = await ctx.post('/api/agent_bots/deploy', { agent_name: b.agent_name, target_path: v.target_path || undefined });
      toast(res.success ? `Deployed to ${res.target || 'config'}` : (res.error || 'Deploy failed'), res.success ? 'ok' : 'bad', 7000);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function remove(b) {
    const ok = await confirmDialog({ title: `Remove ${b.agent_name}?`, message: 'The bot token is removed from the vault. The bot itself is not deleted on Telegram.', confirmText: 'Remove', danger: true });
    if (!ok) return;
    try {
      await ctx.post('/api/agent_bots/remove', { agent_name: b.agent_name });
      toast('Agent bot removed', 'ok');
      await load(false);
    } catch (err) { toast(err.message, 'bad'); }
  }

  await load(false);
}
