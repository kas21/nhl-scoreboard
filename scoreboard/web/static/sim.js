// The Simulator page: run a game (or any simulated feed) by hand and watch the panel react.
//
// Nothing here knows hockey. `/api/sim` lists every simulation the app loaded with the JSON
// schema of its start form, and for a running one a summary and the buttons that apply right
// now; this page draws forms from schemas and buttons from action specs. A new simulation —
// another sport, a weather alert, a plane overhead — shows up here with no change to this file.
//
// Polled once a second: a running clock is the thing you are looking at, and one small GET
// is cheaper than a socket for a page that is open for minutes at a time.

import { html, useState, useEffect, useRef } from './htm-preact.js';

const UI = { 'x-requested-with': 'scoreboard-ui' };
const POLL_MS = 1000;

function reason(body) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).slice(-1)[0] || 'value'}: ${e.msg}`).join(' · ');
  return 'the scoreboard refused that';
}

const ok = async (r) => { if (!r.ok) throw new Error(reason(await r.json().catch(() => null))); return r.json(); };
const api = {
  get: (p) => fetch(p).then(ok),
  post: (p, body) => fetch(p, { method: 'POST', headers: { ...UI, 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(ok),
};

// ---- the start form, from the options model's JSON schema ------------------------------

const resolve = (s, root) => (s && s.$ref ? root.$defs[s.$ref.split('/').pop()] : s) || {};
const label = (key, s) => s.title || key.replace(/_/g, ' ');

function OptionField({ name, schema, root, value, onChange }) {
  const s = resolve(schema, root);
  const id = `sim-opt-${name}`;
  let control;
  if (s.enum) {
    const labels = s.labels || {};
    control = html`<select id=${id} value=${value} onchange=${e => onChange(e.target.value)}>
      ${s.enum.map(v => html`<option value=${v}>${labels[v] || v}</option>`)}</select>`;
  } else if (s.type === 'boolean') {
    control = html`<input type="checkbox" id=${id} checked=${!!value} onchange=${e => onChange(e.target.checked)} />`;
  } else if (s.type === 'integer' || s.type === 'number') {
    control = html`<input type="number" id=${id} value=${value ?? ''} min=${s.minimum} max=${s.maximum} step=${s.type === 'integer' ? 1 : 'any'}
      onchange=${e => onChange(e.target.value === '' ? null : +e.target.value)} />`;
  } else {
    control = html`<input type="text" id=${id} value=${value ?? ''} onchange=${e => onChange(e.target.value)} />`;
  }
  return html`<div class="field">
    <label for=${id}>${label(name, s)}</label>
    <div class="field-input">${control}</div>
    ${s.description && html`<small>${s.description}</small>`}
  </div>`;
}

function StartForm({ sim, onStart, busy }) {
  const props = sim.schema.properties || {};
  // Remembered per simulation so a re-run after a stop starts from the last settings.
  const stored = () => { try { return JSON.parse(localStorage.getItem(`sim:${sim.key}`) || 'null'); } catch (e) { return null; } };
  const defaults = Object.fromEntries(Object.entries(props).map(([k, s]) => [k, resolve(s, sim.schema).default]));
  const [opts, setOpts] = useState(() => ({ ...defaults, ...(sim.options || stored() || {}) }));
  const set = (k, v) => setOpts(o => ({ ...o, [k]: v }));
  const submit = () => { try { localStorage.setItem(`sim:${sim.key}`, JSON.stringify(opts)); } catch (e) {} onStart(opts); };
  return html`<div class="sim-form">
    <div class="fields">${Object.entries(props).map(([k, s]) => html`
      <${OptionField} name=${k} schema=${s} root=${sim.schema} value=${opts[k]} onChange=${v => set(k, v)} />`)}</div>
    <div class="row" style="margin-top:12px">
      <button disabled=${busy} onclick=${submit}>${sim.running ? 'Restart with these settings' : 'Start'}</button>
      <span class="muted small">Takes over ${sim.claims.join(', ')} until stopped; the real feed comes back the moment you stop.</span>
    </div>
  </div>`;
}

// ---- a running simulation: summary, buttons, stop ---------------------------------------

function ParamInput({ p, value, onChange }) {
  if (p.kind === 'select') {
    return html`<select title=${p.label} value=${value} onchange=${e => onChange(e.target.value)}>
      ${p.options.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select>`;
  }
  if (p.kind === 'bool') return html`<label class="inline"><input type="checkbox" checked=${!!value} onchange=${e => onChange(e.target.checked)} /> ${p.label}</label>`;
  if (p.kind === 'number') return html`<input type="number" title=${p.label} value=${value ?? ''} onchange=${e => onChange(e.target.value === '' ? null : +e.target.value)} style="width:90px" />`;
  return html`<input type="text" title=${p.label} placeholder=${p.placeholder || p.label} value=${value ?? ''} onchange=${e => onChange(e.target.value)} />`;
}

function ActionRow({ action, onFire, busy }) {
  // Parameter values live here so typing a scorer's name survives the next poll; a text
  // field is cleared once the action fires, a select keeps its pick (the side you are on).
  const [params, setParams] = useState(() => Object.fromEntries(action.params.map(p => [p.name, p.default ?? ''])));
  const fire = () => onFire(action.name, params).then(() => {
    setParams(ps => Object.fromEntries(action.params.map(p => [p.name, p.kind === 'text' ? '' : ps[p.name]])));
  }).catch(() => {});
  return html`<div class="sim-action">
    ${action.params.map(p => html`<${ParamInput} p=${p} value=${params[p.name]} onChange=${v => setParams(ps => ({ ...ps, [p.name]: v }))} />`)}
    <button class=${action.primary ? '' : action.danger ? 'danger' : 'secondary'} disabled=${busy || !action.enabled}
      title=${action.hint || ''} onclick=${fire}>${action.label}</button>
  </div>`;
}

const fmtSince = (s) => s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${(s / 3600).toFixed(1)}h`;

function Running({ sim, onAction, onStop, onStart, busy }) {
  const groups = [];
  for (const a of sim.actions) {
    let g = groups.find(x => x.name === a.group);
    if (!g) { g = { name: a.group, actions: [] }; groups.push(g); }
    g.actions.push(a);
  }
  const [showForm, setShowForm] = useState(false);
  return html`<div class="sim-running">
    <div class="sim-head">
      <div class="sim-headline">${sim.state.headline}</div>
      <span class="muted small">running ${fmtSince(sim.since)}</span>
      <button class="danger" disabled=${busy} onclick=${onStop}>Stop</button>
    </div>
    <div class="status sim-status">
      ${sim.state.lines.map(([k, v]) => html`<div><span>${k}</span>${v}</div>`)}
    </div>
    ${groups.map(g => html`<div class="sim-group">
      ${g.name && html`<div class="group">${g.name}</div>`}
      <div class="sim-actions">${g.actions.map(a => html`<${ActionRow} key=${a.name} action=${a} onFire=${onAction} busy=${busy} />`)}</div>
    </div>`)}
    <details open=${showForm} ontoggle=${e => setShowForm(e.target.open)}>
      <summary class="muted small">Settings</summary>
      <${StartForm} sim=${sim} onStart=${onStart} busy=${busy} />
    </details>
  </div>`;
}

function SimCard({ sim, refresh, setError }) {
  const [busy, setBusy] = useState(false);
  const call = (p, body) => { setBusy(true); setError(null); return api.post(p, body).then(refresh).catch(e => { setError(e.message); throw e; }).finally(() => setBusy(false)); };
  const start = (opts) => call(`/api/sim/${sim.key}/start`, opts).catch(() => {});
  const stop = () => call(`/api/sim/${sim.key}/stop`).catch(() => {});
  const action = (name, params) => call(`/api/sim/${sim.key}/action`, { name, params });
  return html`<div class="card sim-card">
    <h2>${sim.title}${sim.running && html` <span class="chip live">running</span>`}</h2>
    <p class="muted small">${sim.description}</p>
    ${sim.running
      ? html`<${Running} sim=${sim} onAction=${action} onStop=${stop} onStart=${start} busy=${busy} />`
      : html`<${StartForm} sim=${sim} onStart=${start} busy=${busy} />`}
  </div>`;
}

export function Simulator({ Preview }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [failed, setFailed] = useState(false);
  const alive = useRef(true);
  const refresh = () => api.get('/api/sim').then(d => { if (alive.current) { setData(d); setFailed(false); } }).catch(() => setFailed(true));
  useEffect(() => { alive.current = true; refresh(); const id = setInterval(refresh, POLL_MS); return () => { alive.current = false; clearInterval(id); }; }, []);
  return html`
    <div class="card"><h2>Panel</h2><${Preview} /></div>
    ${error && html`<div class="card error">${error}</div>`}
    ${failed ? html`<div class="card"><p class="error">Could not reach the scoreboard.</p></div>`
      : !data ? html`<p class="muted">Loading…</p>`
      : !data.sims.length ? html`<div class="card"><p class="muted">No simulations are installed.</p></div>`
      : data.sims.map(s => html`<${SimCard} key=${s.key} sim=${s} refresh=${refresh} setError=${setError} />`)}
    <div class="card"><h2>What this does</h2>
      <p class="muted small">A simulation stands in for a data feed. While one runs, the panel follows it exactly as it would a
      real game — the state changes, the goal and penalty alerts interrupt the rotation, the ticker shows the game — and
      the real feed keeps polling underneath, out of sight. Stop it and the real data is back on the panel at once.
      Nothing is saved; a restart of the scoreboard ends every simulation.</p>
    </div>`;
}
