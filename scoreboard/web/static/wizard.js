import { html, useState, useEffect } from './htm-preact.js';

const api = {
  get: (p) => fetch(p).then(r => r.json()),
  post: (p, body) => fetch(p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(async r => { if (!r.ok) throw new Error(JSON.stringify((await r.json()).detail)); return r.json(); }),
};

// Panel presets: what a friend can pick from a list instead of knowing driver flags.
const PANELS = [
  { id: '128x64', label: '128 x 64 (one panel)', display: { width: 128, height: 64, chain: 1, parallel: 1 } },
  { id: '2x64x64', label: '128 x 64 (two 64x64 panels chained)', display: { width: 128, height: 64, chain: 2, parallel: 1 } },
  { id: '64x32', label: '64 x 32 (one panel)', display: { width: 64, height: 32, chain: 1, parallel: 1 } },
  { id: '2x64x32', label: '128 x 32 (two 64x32 panels chained)', display: { width: 128, height: 32, chain: 2, parallel: 1 } },
  { id: '64x64', label: '64 x 64 (one panel)', display: { width: 64, height: 64, chain: 1, parallel: 1 } },
];
const BOARDS = [
  { id: 'adafruit-hat-pwm', label: 'Adafruit RGB Matrix HAT / Bonnet (with the PWM jumper mod)' },
  { id: 'adafruit-hat', label: 'Adafruit RGB Matrix HAT / Bonnet (no mod)' },
  { id: 'regular', label: 'Direct wiring / other HAT ("regular")' },
];
const ORDERS = ['RGB', 'RBG', 'GRB', 'GBR', 'BRG', 'BGR'];
const NHL = ['ANA','BOS','BUF','CAR','CBJ','CGY','CHI','COL','DAL','DET','EDM','FLA','LAK','MIN','MTL','NJD','NSH','NYI','NYR','OTT','PHI','PIT','SEA','SJS','STL','TBL','TOR','UTA','VAN','VGK','WPG','WSH'];

function Step({ n, title, children }) {
  return html`<div class="card"><h2><span class="muted">Step ${n} · </span>${title}</h2>${children}</div>`;
}

export function Wizard({ config, save, Preview, onDone }) {
  const [step, setStep] = useState(0);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const d = config.display;
  const preset = PANELS.find(p => p.display.width === d.width && p.display.height === d.height && p.display.chain === d.chain) || PANELS[0];

  // keep the test pattern on screen while the hardware steps are open
  useEffect(() => {
    if (step <= 1) { api.post('/api/override', { board: 'test_pattern', seconds: 120 }).catch(() => {}); const id = setInterval(() => api.post('/api/override', { board: 'test_pattern', seconds: 120 }).catch(() => {}), 60000); return () => { clearInterval(id); api.post('/api/override', { board: null }).catch(() => {}); }; }
  }, [step]);

  const saveDisplay = (patch) => { setNeedsRestart(true); return save({ display: patch }); };
  const restart = async () => {
    setBusy(true); setMsg('Restarting the display driver… the panel will go dark for a few seconds.');
    await api.post('/api/system/restart').catch(() => {});
    const t0 = Date.now();
    const poll = async () => { try { await api.get('/api/status'); if (Date.now() - t0 > 3000) { setBusy(false); setNeedsRestart(false); setMsg(''); return; } } catch (e) {} setTimeout(poll, 1000); };
    setTimeout(poll, 3000);
  };

  const steps = [
    html`<${Step} n=${1} title="Your panel">
      <p class="muted">Pick the closest match. The panel shows a test pattern: a yellow border, "TOP LEFT" in the top-left corner and red / green / blue / white bars.</p>
      <div class="field"><label>Panel size</label>
        <select value=${preset.id} onchange=${e => saveDisplay(PANELS.find(p => p.id === e.target.value).display)}>${PANELS.map(p => html`<option value=${p.id}>${p.label}</option>`)}</select></div>
      <div class="field"><label>Driver board</label>
        <select value=${d.gpio_mapping} onchange=${e => saveDisplay({ gpio_mapping: e.target.value })}>${BOARDS.map(b => html`<option value=${b.id}>${b.label}</option>`)}</select></div>
      ${needsRestart && html`<div class="row"><button disabled=${busy} onclick=${restart}>Apply to the panel (restart driver)</button><span class="muted">${msg}</span></div>`}
    <//>`,
    html`<${Step} n=${2} title="Colours and orientation">
      <p class="muted">Look at the physical panel, not the preview. The bars should read red, green, blue, white from left to right, and "TOP LEFT" should be at the top-left.</p>
      <div class="field"><label>Colour order</label>
        <select value=${d.rgb_sequence} onchange=${e => saveDisplay({ rgb_sequence: e.target.value })}>${ORDERS.map(o => html`<option value=${o}>${o}</option>`)}</select>
        <small>If the bars are in the wrong order, choose the order you actually see (e.g. you see red, blue, green → pick RBG).</small></div>
      <div class="field"><label>Orientation</label>
        <select value=${d.pixel_mapper} onchange=${e => saveDisplay({ pixel_mapper: e.target.value })}>
          <option value="">Normal</option><option value="Rotate:180">Upside down (rotate 180)</option><option value="Rotate:90">Rotate 90</option><option value="Rotate:270">Rotate 270</option><option value="Mirror:H">Mirrored</option></select></div>
      <div class="field"><label>Flicker fix</label>
        <select value=${d.slowdown_gpio} onchange=${e => saveDisplay({ slowdown_gpio: +e.target.value })}>
          <option value="1">1 (Pi 3 / Zero 2)</option><option value="2">2 (Pi 4, default)</option><option value="3">3</option><option value="4">4 (Pi 5)</option></select>
        <small>Raise this if the panel flickers or shows ghosting.</small></div>
      ${needsRestart && html`<div class="row"><button disabled=${busy} onclick=${restart}>Apply to the panel (restart driver)</button><span class="muted">${msg}</span></div>`}
    <//>`,
    html`<${Step} n=${3} title="Your team">
      <p class="muted">The first team is the one the board follows. Add more to fall back to when your first team isn't playing.</p>
      <${Favourites} value=${(config.sources.nhl || {}).favorites || []} onChange=${v => save({ sources: { nhl: { favorites: v } } })} />
    <//>`,
    html`<${Step} n=${4} title="Where you are">
      <p class="muted">Used for game times and, if you enable it, sunset dimming.</p>
      <div class="field"><label>Timezone</label><input type="text" value=${config.location.timezone} onchange=${e => save({ location: { timezone: e.target.value } })} />
        <small>Detected: ${Intl.DateTimeFormat().resolvedOptions().timeZone} <a onclick=${() => save({ location: { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } })} style="cursor:pointer;color:var(--accent)">use this</a></small></div>
      <${LocationPicker} location=${config.location} save=${save} />
    <//>`,
    html`<${Step} n=${5} title="Name this scoreboard">
      <p class="muted">You'll reach it at <b>name.local:8080</b> from any device on your Wi-Fi.</p>
      <${Hostname} />
    <//>`,
    html`<${Step} n=${6} title="All set">
      <p>The board will now show your team's games, the score ticker, standings and the clock. Everything can be changed later under Settings and Boards.</p>
      <button onclick=${async () => { await save({ setup_complete: true }); onDone(); }}>Finish</button>
    <//>`,
  ];

  return html`
    <div class="card"><h2>Live preview</h2><${Preview} /></div>
    ${steps[step]}
    <div class="row" style="margin-top:8px">
      <button class="secondary" disabled=${step === 0} onclick=${() => setStep(step - 1)}>Back</button>
      ${step < steps.length - 1 && html`<button disabled=${needsRestart && step <= 1} onclick=${() => setStep(step + 1)}>Next</button>`}
      ${needsRestart && step <= 1 && html`<span class="muted">Apply your panel changes before continuing.</span>`}
    </div>`;
}

function Favourites({ value, onChange }) {
  return html`<div class="tags">
    ${value.map((v, i) => html`<span class="tag">${i + 1}. ${v} <a onclick=${() => onChange(value.filter((_, j) => j !== i))}>✕</a></span>`)}
    <select onchange=${e => { if (e.target.value) onChange([...value, e.target.value]); e.target.value = ''; }}>
      <option value="">+ add team</option>${NHL.filter(t => !value.includes(t)).map(t => html`<option value=${t}>${t}</option>`)}
    </select></div>`;
}

function Hostname() {
  const [name, setName] = useState('');
  const [current, setCurrent] = useState('');
  const [msg, setMsg] = useState('');
  useEffect(() => { api.get('/api/system').then(s => { setCurrent(s.hostname); setName(s.hostname); }); }, []);
  const apply = () => api.post('/api/system/hostname', { hostname: name }).then(r => { setCurrent(r.hostname); setMsg(r.changed ? `Saved — reach it at ${r.hostname}.local:8080 after the next reboot.` : 'Not available on this system; keep using the current address.'); }).catch(e => setMsg(e.message));
  return html`<div class="field"><label>Name</label><input type="text" value=${name} onchange=${e => setName(e.target.value.toLowerCase())} />
    <small>Current: ${current}.local ${msg}</small></div>
    <button class="secondary" onclick=${apply} disabled=${!name || name === current}>Save name</button>`;
}


function LocationPicker({ location, save }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  const [msg, setMsg] = useState('');
  const search = () => api.get('/api/geocode?q=' + encodeURIComponent(q)).then(r => { setResults(r); setMsg(r.length ? '' : 'No matches — try a bigger town or add the country.'); }).catch(e => setMsg('Lookup failed: ' + e.message));
  const pick = (r) => { save({ location: { latitude: r.latitude, longitude: r.longitude, timezone: r.timezone || location.timezone } }); setResults([]); setMsg(`Saved ${r.name}${r.region ? ', ' + r.region : ''} (${r.latitude}, ${r.longitude})`); };
  const secure = window.isSecureContext && navigator.geolocation;
  return html`
    <div class="field"><label>Find your town</label>
      <div class="row"><input type="text" value=${q} placeholder="e.g. Toronto, or a postcode" oninput=${e => setQ(e.target.value)} onkeydown=${e => e.key === 'Enter' && search()} />
        <button class="secondary" onclick=${search} disabled=${q.length < 2}>Search</button></div>
      ${results.length > 0 && html`<div class="tags" style="margin-top:6px">${results.map(r => html`<span class="tag" style="cursor:pointer" onclick=${() => pick(r)}>${r.name}${r.region ? ', ' + r.region : ''} ${r.country}</span>`)}</div>`}
      <small>Used for weather, flights and sunset dimming. ${msg}</small></div>
    <div class="field"><label>Or enter coordinates</label>
      <div class="row">
        <input type="number" step="0.001" placeholder="latitude" value=${location.latitude ?? ''} onchange=${e => save({ location: { latitude: e.target.value === '' ? null : +e.target.value } })} style="max-width:130px" />
        <input type="number" step="0.001" placeholder="longitude" value=${location.longitude ?? ''} onchange=${e => save({ location: { longitude: e.target.value === '' ? null : +e.target.value } })} style="max-width:130px" />
        ${secure && html`<button class="secondary" onclick=${() => navigator.geolocation.getCurrentPosition(p => pick({ name: 'your location', latitude: +p.coords.latitude.toFixed(3), longitude: +p.coords.longitude.toFixed(3) }), () => setMsg('Location permission denied.'))}>Use my location</button>`}
      </div>
      <small>${location.latitude != null ? `Saved: ${location.latitude}, ${location.longitude}` : 'Not set yet.'}${secure ? '' : ' (Browser location needs HTTPS, so it is hidden here.)'}</small></div>`;
}
