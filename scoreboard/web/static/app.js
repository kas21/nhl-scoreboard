import { h, render, useState, useEffect } from './htm-preact.js';
import { html } from './htm-preact.js';

const api = {
  get: (p) => fetch(p).then(r => r.json()),
  patch: (p, body) => fetch(p, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { if (!r.ok) throw new Error(JSON.stringify((await r.json()).detail)); return r.json(); }),
  post: (p) => fetch(p, { method: 'POST' }).then(r => r.json()),
};

function Preview() {
  const [src, setSrc] = useState('/api/preview.png');
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let ws, url;
    const connect = () => {
      ws = new WebSocket(`${proto}://${location.host}/ws/preview`);
      ws.binaryType = 'blob';
      ws.onmessage = (e) => { const next = URL.createObjectURL(e.data); setSrc(next); if (url) URL.revokeObjectURL(url); url = next; };
      ws.onclose = () => setTimeout(connect, 2000);
    };
    connect();
    return () => ws && ws.close();
  }, []);
  return html`<div class="preview"><img src=${src} alt="live preview" /></div>`;
}

function Dashboard({ config, save }) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    const tick = () => api.get('/api/status').then(setStatus).catch(() => {});
    tick(); const id = setInterval(tick, 3000); return () => clearInterval(id);
  }, []);
  return html`
    <div class="card"><h2>Live preview</h2><${Preview} /></div>
    <div class="card"><h2>Status</h2>
      ${status ? html`<div class="status">
        <div><span>State</span>${status.state}</div>
        <div><span>Board</span>${status.board}</div>
        <div><span>Brightness</span>${status.brightness}%</div>
        <div><span>Version</span>${status.version}</div>
      </div>` : html`<p class="muted">Loading…</p>`}
    </div>
    <div class="card"><h2>Brightness</h2>
      <input type="range" min="1" max="100" value=${config.brightness.day}
        onchange=${e => save({ brightness: { day: +e.target.value } })} />
      <span class="muted"> ${config.brightness.day}%</span>
    </div>`;
}

// ---- schema-driven forms ---------------------------------------------------

function resolve(schema, root) {
  if (schema && schema.$ref) return resolve(root.$defs[schema.$ref.split('/').pop()], root);
  if (schema && schema.anyOf) { const s = schema.anyOf.find(x => x.type !== 'null'); return { ...resolve(s, root), nullable: true, title: schema.title, description: schema.description }; }
  return schema;
}

function Field({ name, schema, value, onChange, root }) {
  const s = resolve(schema, root);
  const label = s.title || name;
  const common = { id: name };
  let input;
  if (s.enum) {
    input = html`<select ...${common} value=${value} onchange=${e => onChange(e.target.value)}>${s.enum.map(v => html`<option value=${v}>${v}</option>`)}</select>`;
  } else if (s.type === 'boolean') {
    input = html`<input type="checkbox" ...${common} checked=${!!value} onchange=${e => onChange(e.target.checked)} />`;
  } else if (s.type === 'integer' || s.type === 'number') {
    input = html`<input type="number" ...${common} value=${value ?? ''} min=${s.minimum} max=${s.maximum} step=${s.type === 'integer' ? 1 : 'any'}
      onchange=${e => onChange(e.target.value === '' ? null : +e.target.value)} />`;
  } else if (s.type === 'array' && s.items && resolve(s.items, root).enum) {
    const opts = resolve(s.items, root).enum; const sel = value || [];
    input = html`<div class="tags">
      ${sel.map((v, i) => html`<span class="tag">${v} <a onclick=${() => onChange(sel.filter((_, j) => j !== i))}>✕</a></span>`)}
      <select onchange=${e => { if (e.target.value) onChange([...sel, e.target.value]); e.target.value = ''; }}>
        <option value="">+ add</option>${opts.filter(o => !sel.includes(o)).map(o => html`<option value=${o}>${o}</option>`)}
      </select></div>`;
  } else if (s.type === 'array' && s.items && resolve(s.items, root).type === 'string') {
    input = html`<input type="text" ...${common} value=${(value || []).join(', ')} placeholder="comma separated"
      onchange=${e => onChange(e.target.value.split(',').map(x => x.trim()).filter(Boolean))} />`;
  } else if (s.type === 'array' && Array.isArray(value) && value.length === 3 && value.every(Number.isInteger)) {
    const hex = '#' + value.map(v => v.toString(16).padStart(2, '0')).join('');
    input = html`<input type="color" ...${common} value=${hex} onchange=${e => onChange([1, 3, 5].map(i => parseInt(e.target.value.substr(i, 2), 16)))} />`;
  } else if (s.type === 'string') {
    input = html`<input type="text" ...${common} value=${value ?? ''} onchange=${e => onChange(e.target.value)} />`;
  } else {
    return null;
  }
  return html`<div class="field"><label for=${name}>${label}</label>${input}${s.description && html`<small>${s.description}</small>`}</div>`;
}

function Section({ title, schema, value, onSave, root }) {
  const s = resolve(schema, root);
  if (!s || !s.properties) return null;
  return html`<details class="card" open>
    <summary>${title}</summary>
    ${Object.entries(s.properties).map(([k, sub]) => html`<${Field} key=${k} name=${k} schema=${sub} root=${root} value=${value?.[k]} onChange=${v => onSave({ [k]: v })} />`)}
  </details>`;
}

const HIDDEN = new Set(['boards', 'sources', 'playlists', 'setup_complete']);

function Settings({ config, schema, save }) {
  if (!schema) return html`<p class="muted">Loading…</p>`;
  return html`
    ${Object.entries(schema.properties).filter(([k]) => !HIDDEN.has(k)).map(([k, sub]) => {
      const r = resolve(sub, schema);
      if (!r.properties) return html`<div class="card"><${Field} name=${k} schema=${sub} root=${schema} value=${config[k]} onChange=${v => save({ [k]: v })} /></div>`;
      return html`<${Section} key=${k} title=${r.title || k} schema=${sub} root=${schema} value=${config[k]} onSave=${p => save({ [k]: p })} />`;
    })}
    <h2>Boards</h2>
    ${Object.entries(schema.properties.boards.properties).map(([k, sub]) => html`
      <${Section} key=${k} title=${sub.title || k} schema=${sub} root=${sub} value=${config.boards[k] || {}} onSave=${p => save({ boards: { [k]: p } })} />`)}
    <h2>Data sources</h2>
    ${Object.entries(schema.properties.sources.properties).map(([k, sub]) => html`
      <${Section} key=${k} title=${sub.title || k} schema=${sub} root=${sub} value=${config.sources[k] || {}} onSave=${p => save({ sources: { [k]: p } })} />`)}
    <div class="card row">
      <button class="danger" onclick=${() => confirm('Reset all settings to defaults?') && api.post('/api/config/reset').then(() => location.reload())}>Reset to defaults</button>
    </div>`;
}

function Playlists({ config, boards, save }) {
  const states = Object.keys(config.playlists);
  const update = (state, list) => save({ playlists: { [state]: list } });
  return states.map(state => {
    const list = config.playlists[state];
    const move = (i, d) => { const l = [...list]; const [x] = l.splice(i, 1); l.splice(i + d, 0, x); update(state, l); };
    return html`<div class="card playlist"><h2>${state}</h2><ul>
      ${list.map((e, i) => html`<li>
        <input type="checkbox" checked=${e.enabled} onchange=${ev => update(state, list.map((x, j) => j === i ? { ...x, enabled: ev.target.checked } : x))} />
        <select value=${e.board} onchange=${ev => update(state, list.map((x, j) => j === i ? { ...x, board: ev.target.value } : x))}>
          ${boards.map(b => html`<option value=${b.key}>${b.title}</option>`)}
        </select>
        <input type="number" min="1" placeholder="auto" value=${e.duration ?? ''} style="width:80px"
          onchange=${ev => update(state, list.map((x, j) => j === i ? { ...x, duration: ev.target.value === '' ? null : +ev.target.value } : x))} /> s
        <button class="secondary" disabled=${i === 0} onclick=${() => move(i, -1)}>↑</button>
        <button class="secondary" disabled=${i === list.length - 1} onclick=${() => move(i, 1)}>↓</button>
        <button class="danger" onclick=${() => update(state, list.filter((_, j) => j !== i))}>✕</button>
      </li>`)}
      </ul>
      <button class="secondary" onclick=${() => update(state, [...list, { board: boards[0].key, duration: 15, enabled: true }])}>+ Add board</button>
    </div>`;
  });
}

function Diagnostics() {
  const [lines, setLines] = useState([]);
  useEffect(() => { const t = () => api.get('/api/logs').then(setLines); t(); const id = setInterval(t, 3000); return () => clearInterval(id); }, []);
  return html`<div class="card"><h2>Logs</h2><pre>${lines.join('\n')}</pre></div>`;
}

function App() {
  const [page, setPage] = useState(location.hash.slice(1) || 'dashboard');
  const [config, setConfig] = useState(null);
  const [schema, setSchema] = useState(null);
  const [boards, setBoards] = useState([]);
  const [error, setError] = useState(null);
  useEffect(() => {
    api.get('/api/config').then(setConfig);
    api.get('/api/schema').then(setSchema);
    api.get('/api/boards').then(setBoards);
    const onHash = () => setPage(location.hash.slice(1) || 'dashboard');
    addEventListener('hashchange', onHash); return () => removeEventListener('hashchange', onHash);
  }, []);
  const save = (patch) => api.patch('/api/config', patch).then(c => { setConfig(c); setError(null); }).catch(e => setError(e.message));
  const pages = { dashboard: 'Dashboard', playlists: 'Boards', settings: 'Settings', diagnostics: 'Diagnostics' };
  return html`
    <header><h1>Scoreboard</h1><nav>${Object.entries(pages).map(([k, v]) => html`<a href=${'#' + k} class=${page === k ? 'active' : ''}>${v}</a>`)}</nav></header>
    <main>
      ${error && html`<div class="card error">${error}</div>`}
      ${!config ? html`<p class="muted">Loading…</p>`
        : page === 'settings' ? html`<${Settings} config=${config} schema=${schema} save=${save} />`
        : page === 'playlists' ? html`<${Playlists} config=${config} boards=${boards} save=${save} />`
        : page === 'diagnostics' ? html`<${Diagnostics} />`
        : html`<${Dashboard} config=${config} save=${save} />`}
    </main>`;
}

render(h(App), document.getElementById('app'));
