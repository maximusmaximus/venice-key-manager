import { h, mount, card, pageHead, table, badge, button, field, formValues, busy, toast, notice, confirmDialog, infoDialog, errorState, skeleton, emptyState, json } from '../ui.js';

export async function render(root, ctx) {
  const listBody = h('div', null, skeleton(1));

  mount(root,
    pageHead('Fleet mesh', 'Peer Venice Key Manager nodes over Tailscale. Probe health, sync code and forward privileged calls with each node\'s pairing code.',
      [
        button('Probe health', () => load(true), { size: 'sm' }),
        button('Sync all', (e) => sync('all', e.currentTarget), { size: 'sm' }),
        button('Refresh', () => load(false), { iconName: 'refresh', size: 'sm' }),
      ]),
    card({ title: 'Nodes', body: listBody }),
    registerCard());

  function registerCard() {
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-grid' },
        field({ name: 'name', label: 'Node name', required: true, placeholder: 'mcmini' }),
        field({ name: 'base_url', label: 'Base URL', type: 'url', required: true, mono: true, placeholder: 'http://node.tailnet.ts.net:8844' }),
        field({ name: 'label', label: 'Label', placeholder: 'McMini (macOS)' }),
        field({ name: 'remote_pairing_code', label: 'Remote pairing code', type: 'password', mono: true, help: 'Stored in the vault; sent as X-Pairing-Code when forwarding. Never displayed again.' })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Register node')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          await ctx.post('/api/fleet/register_node', formValues(form));
          toast('Node registered', 'ok');
          form.reset();
          await load(false);
        } catch (err) { toast(err.message, 'bad', 7000); }
      });
    });
    return card({ title: 'Register a node', body: form });
  }

  async function load(probe) {
    mount(listBody, probe ? h('div', { class: 'row muted' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Probing nodes…') : skeleton(1));
    try {
      const res = await ctx.get(`/api/fleet/nodes${probe ? '?probe=1' : ''}`);
      mount(listBody, table([
        { label: 'Node', render: (n) => h('div', null, h('strong', { text: n.label || n.name }), h('div', { class: 'xsmall muted mono', text: n.name })) },
        { label: 'URL', render: (n) => h('span', { class: 'mono small', text: n.base_url || '—' }) },
        { label: 'Status', render: (n) => badge(n.status || 'UNPROBED') },
        { label: 'Latency', render: (n) => (n.latency_ms != null ? `${n.latency_ms} ms` : '—') },
        { label: 'Version', render: (n) => (n.version && n.version.commit) || '—' },
        { label: 'Auth', render: (n) => (n.is_self ? 'self' : (n.has_pairing_code ? badge('OK', 'Code stored') : badge('PENDING', 'No code'))) },
        { label: 'Actions', cls: 'actions', render: (n) => h('div', { class: 'btn-group' },
          button('Sync', (e) => sync(n.name, e.currentTarget), { size: 'sm' }),
          n.is_self ? null : button('Remove', () => remove(n), { size: 'sm', variant: 'danger' })) },
      ], res.nodes || [], { caption: 'Fleet nodes', empty: emptyState('No nodes', '') }));
    } catch (err) { mount(listBody, errorState(err, () => load(probe))); }
  }

  async function sync(node, btn) {
    const ok = await confirmDialog({
      title: node === 'all' ? 'Sync all nodes?' : `Sync ${node}?`,
      message: 'Runs git pull on the node(s). Services pick up new code on their next restart.',
      confirmText: 'Sync',
    });
    if (!ok) return;
    await busy(btn, async () => {
      try {
        const res = await ctx.post('/api/fleet/sync', { node });
        infoDialog({ title: 'Sync result', body: json(res) });
      } catch (err) { toast(err.message, 'bad'); }
    });
  }

  async function remove(n) {
    const ok = await confirmDialog({ title: `Remove ${n.name}?`, message: 'The node and its stored pairing code are removed from the vault.', confirmText: 'Remove', danger: true });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/fleet/remove_node', { name: n.name });
      toast(res.success ? 'Node removed' : 'Built-in nodes cannot be removed', res.success ? 'ok' : 'warn');
      await load(false);
    } catch (err) { toast(err.message, 'bad'); }
  }

  await load(false);
}
