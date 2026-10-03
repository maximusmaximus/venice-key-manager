import { h, mount, card, pageHead, button, busy, toast, notice, errorState, skeleton, emptyState, json, copyButton, append } from '../ui.js';

/** Build form controls from a JSON-schema "properties" object. */
function schemaForm(tool) {
  const schema = tool.inputSchema || {};
  const props = schema.properties || {};
  const required = new Set(schema.required || []);
  const controls = [];
  const readers = [];

  for (const [name, spec] of Object.entries(props)) {
    const id = `mcp-${tool.name}-${name}`;
    const type = Array.isArray(spec.type) ? spec.type[0] : spec.type;
    const label = h('label', { class: 'label', for: id, text: `${name}${required.has(name) ? ' *' : ''}` });
    const help = spec.description ? h('p', { class: 'help', text: spec.description }) : null;
    let control;
    let read;

    if (spec.enum) {
      control = h('select', { class: 'select', id, required: required.has(name) },
        required.has(name) ? null : h('option', { value: '', text: '— not set —' }),
        spec.enum.map((v) => h('option', { value: String(v), text: String(v) })));
      if (spec.default !== undefined) control.value = String(spec.default);
      read = () => (control.value === '' ? undefined : control.value);
    } else if (type === 'boolean') {
      control = h('input', { type: 'checkbox', id, checked: spec.default === true });
      read = () => control.checked;
      controls.push(h('div', { class: 'field' }, h('label', { class: 'check' }, control, ` ${name}`), help));
      readers.push([name, read]);
      continue;
    } else if (type === 'number' || type === 'integer') {
      control = h('input', { class: 'input', type: 'number', id, step: type === 'integer' ? 1 : 'any', value: spec.default, required: required.has(name), inputmode: 'decimal' });
      read = () => (control.value === '' ? undefined : Number(control.value));
    } else if (type === 'object' || type === 'array') {
      control = h('textarea', { class: 'textarea mono', id, rows: 4, placeholder: type === 'array' ? '[]' : '{}', required: required.has(name), spellcheck: 'false' });
      read = () => {
        if (!control.value.trim()) return undefined;
        try { return JSON.parse(control.value); } catch (e) { throw new Error(`"${name}" must be valid JSON`); }
      };
    } else {
      const secret = /key|token|code|secret|password/i.test(name);
      control = h('input', { class: `input ${secret ? 'mono' : ''}`, type: secret ? 'password' : 'text', id, value: spec.default, required: required.has(name), autocomplete: 'off', spellcheck: 'false' });
      read = () => (control.value.trim() === '' ? undefined : control.value.trim());
    }
    controls.push(h('div', { class: 'field' }, label, control, help));
    readers.push([name, read]);
  }

  const collect = () => {
    const args = {};
    for (const [name, read] of readers) {
      const v = read();
      if (v !== undefined) args[name] = v;
    }
    return args;
  };
  return { controls, collect };
}

export async function render(root, ctx) {
  const listHost = h('div', null, skeleton(2));
  const detailHost = h('div', null, emptyState('Pick a tool', 'Choose an MCP tool on the left to see its inputs and run it against this machine.'));
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search tools', 'aria-label': 'Search tools' });
  let tools = [];
  let selected = null;

  const origin = location.origin;
  const rpcExample = `curl -X POST ${origin}/api/mcp/rpc \\\n  -H "Content-Type: application/json" \\\n  -H "X-Pairing-Code: <pairing code>" \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`;

  mount(root,
    pageHead('MCP console', 'Every MCP tool exposed to agents, runnable from the browser with forms generated from each tool\'s input schema.'),
    h('div', { class: 'split' },
      card({ title: 'Tools', body: h('div', { class: 'stack-sm' }, search, listHost) }),
      h('div', { class: 'stack' }, detailHost,
        card({
          title: 'Connect an agent',
          sub: 'Agents can use these tools over stdio (local) or HTTP JSON-RPC (remote, pairing code required).',
          body: h('div', { class: 'stack-sm' },
            h('div', { class: 'field-head' }, h('span', { class: 'label', text: 'Stdio (MCP client config)' }), copyButton('python run.py --mcp', 'Copy')),
            h('code', { class: 'code-box', text: 'python run.py --mcp' }),
            h('div', { class: 'field-head' }, h('span', { class: 'label', text: 'HTTP JSON-RPC' }), copyButton(rpcExample, 'Copy')),
            h('pre', { class: 'code-box', text: rpcExample })),
        }))));

  search.addEventListener('input', renderList);

  function renderList() {
    const q = search.value.trim().toLowerCase();
    const rows = q ? tools.filter((t) => `${t.name} ${t.description}`.toLowerCase().includes(q)) : tools;
    if (!rows.length) { mount(listHost, emptyState('No tools match', '')); return; }
    mount(listHost, h('div', { class: 'tool-list stack-sm' }, rows.map((t) =>
      h('button', {
        type: 'button', class: 'tool-item', 'aria-pressed': String(selected === t.name),
        onclick: () => select(t),
      }, h('span', { class: 'name', text: t.name }), h('span', { class: 'desc', text: t.description || '' })))));
  }

  function select(tool) {
    selected = tool.name;
    renderList();
    const { controls, collect } = schemaForm(tool);
    const out = h('div', { role: 'status', 'aria-live': 'polite' });
    const form = h('form', { class: 'form', novalidate: true },
      controls.length ? controls : h('p', { class: 'muted small', text: 'This tool takes no arguments.' }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Run tool')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      let args;
      try { args = collect(); } catch (err) { mount(out, notice(err.message, 'warn')); return; }
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const res = await ctx.post('/api/mcp/call', { name: tool.name, arguments: args }, { timeout: 120000 });
          const r = res.result || {};
          const failed = r.success === false || (r.error && r.success !== true);
          mount(out, h('div', { class: 'stack-sm' },
            notice(failed ? (r.error || 'Tool reported an error') : 'Tool completed', failed ? 'warn' : 'ok'),
            h('div', { class: 'row' }, copyButton(JSON.stringify(r, null, 2), 'Copy result')),
            json(r)));
          if (/balance|infer|key/.test(tool.name)) ctx.refreshBalance();
        } catch (err) { mount(out, notice(err.message, 'bad')); }
      });
    });
    mount(detailHost, card({
      title: tool.name,
      sub: tool.description,
      body: h('div', { class: 'stack' }, form, out),
    }));
    if (window.matchMedia('(max-width: 1023.98px)').matches) detailHost.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  try {
    const res = await ctx.get('/api/mcp/tools');
    tools = res.tools || [];
    renderList();
    const pre = ctx.params.get('tool');
    if (pre) {
      const t = tools.find((x) => x.name === pre);
      if (t) select(t);
    }
  } catch (err) {
    mount(listHost, errorState(err, () => render(root, ctx)));
  }
}
