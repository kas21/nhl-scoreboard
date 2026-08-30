import { h, render, useState, useEffect } from './htm-preact.js';
import { html } from './htm-preact.js';
import { Wizard } from './wizard.js';
import { Settings } from './settings.js';

const api = {
  get: (p) => fetch(p).then(r => r.json()),
  patch: (p, body) => fetch(p, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { if (!r.ok) throw new Error(JSON.stringify((await r.json()).detail)); return r.json(); }),
  post: (p) => fetch(p, { method: 'POST' }).then(r => r.json()),
};

function Preview() {
  const [src, setSrc] = useState('/api/preview.png');
  const [led, setLed] = useState(() => { try { return localStorage.getItem('ledLook') !== '0'; } catch (e) { return true; } });
  const canvasRef = { current: null };
  useEffect(() => { try { localStorage.setItem('ledLook', led ? '1' : '0'); } catch (e) {} }, [led]);
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
  // LED look: decode the frame, redraw every pixel as a round LED with a gap and a soft glow.
  useEffect(() => {
    if (!led) return;
    const img = new Image();
    img.onload = () => {
      const c = document.getElementById('ledcanvas'); if (!c) return;
      const w = img.naturalWidth, h = img.naturalHeight;
      const cell = Math.max(2, Math.min(8, Math.floor(Math.min(640 / w, 320 / h))));
      c.width = w * cell; c.height = h * cell;
      const off = document.createElement('canvas'); off.width = w; off.height = h;
      const octx = off.getContext('2d'); octx.drawImage(img, 0, 0);
      const data = octx.getImageData(0, 0, w, h).data;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, c.width, c.height);
      const r = cell * 0.42;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4, R = data[i], G = data[i + 1], B = data[i + 2];
        if (R + G + B < 12) { ctx.fillStyle = '#0b0b0b'; ctx.beginPath(); ctx.arc(x * cell + cell / 2, y * cell + cell / 2, r * 0.8, 0, 6.283); ctx.fill(); continue; }
        ctx.fillStyle = `rgba(${R},${G},${B},0.35)`; ctx.beginPath(); ctx.arc(x * cell + cell / 2, y * cell + cell / 2, r * 1.35, 0, 6.283); ctx.fill();
        ctx.fillStyle = `rgb(${R},${G},${B})`; ctx.beginPath(); ctx.arc(x * cell + cell / 2, y * cell + cell / 2, r, 0, 6.283); ctx.fill();
      }
    };
    img.src = src;
  }, [src, led]);
  return html`<div class="preview">
    ${led ? html`<canvas id="ledcanvas" style="max-width:100%"></canvas>` : html`<img src=${src} alt="live preview" />`}
    <label class="ledtoggle"><input type="checkbox" checked=${led} onchange=${e => setLed(e.target.checked)} /> LED look</label>
  </div>`;
}

function Updater() {
  const [st, setSt] = useState(null);
  const refresh = () => api.get('/api/system/update').then(setSt).catch(() => {});
  useEffect(() => { refresh(); const id = setInterval(refresh, 3000); return () => clearInterval(id); }, []);
  if (!st) return null;
  if (!st.is_checkout) return html`<div class="card"><h2>Updates</h2><p class="muted">This install is not a git checkout, so it can't update itself. Reinstall with <code>scripts/install.sh</code> to enable.</p></div>`;
  const busy = st.updating || st.checking;
  return html`<div class="card"><h2>Updates</h2>
    <div class="row">
      <span>${st.available ? html`<b>Update available</b> — ${st.behind} new commit${st.behind === 1 ? '' : 's'}${st.latest_message ? html`: <i>${st.latest_message}</i>` : ''}` : html`<span class="ok">Up to date</span>`}
      <span class="muted"> · ${st.current || '?'}${st.checked_at ? ` · checked ${new Date(st.checked_at * 1000).toLocaleTimeString()}` : ''}</span></span>
      <button class="secondary" disabled=${busy} onclick=${() => api.post('/api/system/update/check').then(setSt)}>Check now</button>
      ${st.available && html`<button disabled=${busy} onclick=${() => confirm('Update and restart the scoreboard?') && api.post('/api/system/update').then(setSt)}>${st.updating ? 'Updating…' : 'Update & restart'}</button>`}
    </div>
    ${st.error && html`<p class="error">${st.error}</p>`}
    ${st.log && st.log.length > 0 && html`<pre>${st.log.join('\n')}</pre>`}
  </div>`;
}

const STATUS_LABEL = { ok: 'OK', starting: 'Starting', degraded: 'Degraded', offline: 'Offline', crashed: 'Crashed', stopped: 'Stopped' };
const fmtAgo = (s) => s == null ? '—' : s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${(s / 3600).toFixed(1)}h ago`;
const fmtIn = (s) => s == null ? '—' : s <= 0 ? 'now' : s < 60 ? `in ${Math.round(s)}s` : `in ${Math.round(s / 60)}m`;
const fmtMs = (ms) => ms == null ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;

function useSources(intervalMs = 3000) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    const tick = () => api.get('/api/sources').then(setRows).catch(() => {});
    tick(); const id = setInterval(tick, intervalMs); return () => clearInterval(id);
  }, []);
  return rows;
}

function SourceDot({ status }) {
  return html`<span class=${'dot ' + status} title=${STATUS_LABEL[status] || status}></span>`;
}

function SourcesSummary() {
  const rows = useSources();
  if (!rows) return html`<p class="muted">Loading…</p>`;
  if (!rows.length) return html`<p class="muted">No data sources loaded.</p>`;
  return html`<div class="sources-summary">
    ${rows.map(r => html`<a href="#diagnostics" class="source-chip" title=${r.last_error || ''}>
      <${SourceDot} status=${r.status} />
      <b>${r.key}</b>
      <span class="muted">${r.status === 'ok' ? fmtAgo(r.last_ok_ago) : STATUS_LABEL[r.status] || r.status}</span>
    </a>`)}
  </div>`;
}

function SourcesTable() {
  const rows = useSources();
  const [open, setOpen] = useState(null);
  if (!rows) return html`<p class="muted">Loading…</p>`;
  if (!rows.length) return html`<p class="muted">No data sources loaded.</p>`;
  return html`<div class="tablewrap"><table class="sources">
    <thead><tr><th></th><th>Source</th><th>Last OK</th><th>Next poll</th><th>Fetches</th><th>Errors</th><th>Latency</th><th>Publishes</th><th>Restarts</th></tr></thead>
    <tbody>${rows.map(r => html`
      <tr class=${open === r.key ? 'open' : ''} onclick=${() => setOpen(open === r.key ? null : r.key)}>
        <td><${SourceDot} status=${r.status} /></td>
        <td><b>${r.key}</b><div class="muted small">${STATUS_LABEL[r.status] || r.status}${r.uptime != null ? ` · up ${fmtAgo(r.uptime).replace(' ago', '')}` : ''}</div></td>
        <td>${fmtAgo(r.last_ok_ago)}</td>
        <td>${fmtIn(r.next_poll_in)}</td>
        <td>${r.fetches}</td>
        <td class=${r.error_streak ? 'error' : ''}>${r.failures}${r.error_streak ? ` (${r.error_streak} in a row)` : ''}</td>
        <td>${fmtMs(r.last_latency_ms)}</td>
        <td>${r.publishes}<div class="muted small">${fmtAgo(r.last_publish_ago)}</div></td>
        <td>${r.restarts}</td>
      </tr>
      ${open === r.key ? html`<tr class="detail"><td colspan="9">
        <div><span class="muted">Publishes to:</span> ${r.keys.length ? r.keys.join(', ') : '—'}</div>
        <div><span class="muted">Last request:</span> ${r.last_url || '—'}${r.last_fetch_ago != null ? html` <span class="muted">(${fmtAgo(r.last_fetch_ago)})</span>` : ''}</div>
        <div><span class="muted">Last error:</span> ${r.last_error ? html`<span class="error">${r.last_error}</span> <span class="muted">(${fmtAgo(r.last_error_ago)})</span>` : '—'}</div>
      </td></tr>` : ''}`)}
    </tbody></table></div>`;
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
    <div class="card"><h2>Data sources</h2><${SourcesSummary} /></div>
    <${Updater} />
    <div class="card"><h2>Brightness</h2>
      <input type="range" min="1" max="100" value=${config.brightness.day}
        onchange=${e => save({ brightness: { day: +e.target.value } })} />
      <span class="muted"> ${config.brightness.day}%</span>
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
  return html`
    <div class="card"><h2>Data sources</h2><p class="muted small">Background fetchers and their cadence. Click a row for details.</p><${SourcesTable} /></div>
    <div class="card"><h2>Logs</h2><pre>${lines.join('\n')}</pre></div>`;
}

function UpdateBadge() {
  const [st, setSt] = useState(null);
  useEffect(() => { const t = () => api.get('/api/system/update').then(setSt).catch(() => {}); t(); const id = setInterval(t, 60000); return () => clearInterval(id); }, []);
  if (!st) return null;
  if (st.updating) return html`<a href="#dashboard" class="badge busy" title="Updating…">Updating…</a>`;
  if (st.available) return html`<a href="#dashboard" class="badge" title=${st.latest_message || ''}>Update available</a>`;
  if (st.error && st.is_checkout) return html`<a href="#dashboard" class="badge warn" title=${st.error}>Update check failed</a>`;
  return null;
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
  const pages = { dashboard: 'Dashboard', playlists: 'Boards', settings: 'Settings', diagnostics: 'Diagnostics', setup: 'Setup' };
  const showWizard = config && (!config.setup_complete || page === 'setup');
  return html`
    <header><h1>Scoreboard</h1><${UpdateBadge} /><nav>${Object.entries(pages).map(([k, v]) => html`<a href=${'#' + k} class=${page === k ? 'active' : ''}>${v}</a>`)}</nav></header>
    <main>
      ${error && html`<div class="card error">${error}</div>`}
      ${!config ? html`<p class="muted">Loading…</p>`
        : showWizard ? html`<${Wizard} config=${config} save=${save} Preview=${Preview} onDone=${() => { location.hash = '#dashboard'; }} />`
        : page === 'settings' ? html`<${Settings} config=${config} schema=${schema} boards=${boards} save=${save} />`
        : page === 'playlists' ? html`<${Playlists} config=${config} boards=${boards} save=${save} />`
        : page === 'diagnostics' ? html`<${Diagnostics} />`
        : html`<${Dashboard} config=${config} save=${save} />`}
    </main>`;
}

render(h(App), document.getElementById('app'));
