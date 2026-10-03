import { h, mount, card, pageHead, table, badge, button, field, formValues, busy, toast, notice, confirmDialog, errorState, skeleton, emptyState, kv, yesNo, fmtDate, relTime, fmtBytes } from '../ui.js';

export async function render(root, ctx) {
  const statusBody = h('div', null, skeleton(1));
  const listBody = h('div', null, skeleton(1));

  mount(root,
    pageHead('Vault & backups', 'Every change is written atomically, mirrored, and snapshotted. Restore any known backup or re-run auto-recall if keys go missing.',
      [button('Refresh', () => loadAll(), { iconName: 'refresh', size: 'sm' })]),
    h('div', { class: 'grid grid-2' },
      card({ title: 'Vault health', body: statusBody }),
      actionsCard()),
    card({ title: 'Backups & snapshots', sub: 'Newest first. Restoring takes a pre-restore backup automatically.', body: listBody }));

  function actionsCard() {
    const backupForm = h('form', { class: 'form', novalidate: true },
      field({ name: 'label', label: 'Backup label (optional)', placeholder: 'before-rotation' }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Create backup')));
    backupForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      await busy(backupForm.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/vault/backup', formValues(backupForm));
          toast(res.success ? `Backup saved: ${res.filename}` : 'Backup failed', res.success ? 'ok' : 'bad');
          backupForm.reset();
          await loadAll();
        } catch (err) { toast(err.message, 'bad'); }
      });
    });

    const recallForm = h('form', { class: 'form', novalidate: true },
      field({ name: 'sync_remote', label: 'Also sync keys from the Venice account (needs admin key)', type: 'checkbox', value: true }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn' }, 'Run auto-recall')));
    recallForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      await busy(recallForm.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/vault/recall', formValues(recallForm));
          const bits = [];
          if (res.recovered_admin_key) bits.push('admin key recovered');
          if (res.recovered_inference_key) bits.push('inference key recovered');
          if (res.remote_keys_synced) bits.push(`${res.remote_keys_synced} remote keys synced`);
          toast(bits.length ? `Recall: ${bits.join(', ')}` : 'Recall complete — nothing missing', 'ok', 6000);
          await loadAll();
        } catch (err) { toast(err.message, 'bad'); }
      });
    });

    return card({
      title: 'Actions',
      body: h('div', { class: 'stack' },
        h('div', { class: 'stack-sm' }, h('h3', { text: 'Manual backup' }), backupForm),
        h('hr'),
        h('div', { class: 'stack-sm' },
          h('h3', { text: 'Auto-recall' }),
          h('p', { class: 'small muted', text: 'Scans backup mirrors, agent configs, .env files and environment variables for missing keys.' }),
          recallForm)),
    });
  }

  async function loadStatus() {
    try {
      const s = await ctx.get('/api/vault/status');
      mount(statusBody, kv([
        ['Vault file', h('span', { class: 'mono small', text: s.vault_path })],
        ['Size', fmtBytes(s.vault_size_bytes)],
        ['Local mirror', yesNo(s.backup_mirror_exists, 'Present', 'Missing')],
        ['Profile mirror', yesNo(s.user_profile_mirror_exists, 'Present', 'Missing')],
        ['Snapshots', String(s.total_snapshots || 0)],
        ['Last backup', s.last_backup_at ? `${relTime(s.last_backup_at)} (${fmtDate(s.last_backup_at)})` : '—'],
        ['Admin key', yesNo(s.has_admin_key, 'Stored', 'Not set')],
        ['Inference key', yesNo(s.has_inference_key, 'Stored', 'Not set')],
        ['Keys / sub-keys / allocations', `${s.keys_count || 0} / ${s.subkeys_count || 0} / ${s.allocations_count || 0}`],
      ]));
    } catch (err) { mount(statusBody, errorState(err, loadStatus)); }
  }

  async function loadBackups() {
    try {
      const res = await ctx.get('/api/vault/backups');
      mount(listBody, table([
        { label: 'File', render: (b) => h('span', { class: 'mono small', text: b.filename }) },
        { label: 'Type', render: (b) => badge(b.type === 'snapshot' ? 'INFO' : 'OK', b.type.replace(/_/g, ' ')) },
        { label: 'Modified', render: (b) => fmtDate(b.modified_at) },
        { label: 'Size', render: (b) => fmtBytes(b.size_bytes) },
        { label: 'Contents', render: (b) => `${b.keys_count} keys · ${b.subkeys_count} sub-keys${b.has_inference_key ? ' · inference key' : ''}` },
        { label: 'Actions', cls: 'actions', render: (b) => button('Restore', () => restore(b), { size: 'sm', variant: 'danger' }) },
      ], res.backups || [], { caption: 'Backups', empty: emptyState('No backups yet', 'Create one with the button above.') }));
    } catch (err) { mount(listBody, errorState(err, loadBackups)); }
  }

  async function restore(b) {
    const ok = await confirmDialog({
      title: 'Restore this backup?',
      message: `The live vault is replaced with ${b.filename} (${b.keys_count} keys). A pre-restore backup is taken first, so this can be undone.`,
      confirmText: 'Restore', danger: true,
    });
    if (!ok) return;
    try {
      const res = await ctx.post('/api/vault/restore', { filename: b.filename });
      toast(res.success ? `Restored ${res.keys_count} keys` : (res.error || 'Restore failed'), res.success ? 'ok' : 'bad', 6000);
      await loadAll();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function loadAll() { await Promise.all([loadStatus(), loadBackups()]); }
  await loadAll();
}
