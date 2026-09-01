// The Holidays page: one row per holiday, on or off, named how you like, with a picture.
//
// Not part of the generated settings form, because none of this is a scalar — a row is a
// toggle, a rename and a file upload at once, and a thumbnail is the whole point of the
// picture control. The generated form still lists the simple fields (country, horizon);
// this page owns the per-holiday list.
//
// State lives in `settings`, not in the snapshot rows: the rows say what holidays exist
// and which pictures they have, while whether one is on and what it is called comes from
// config. Rendering the snapshot's copy of that would make every edit flicker back to the
// old value until the republish landed.

import { html, useState, useEffect, useRef, useMemo } from './htm-preact.js';

const UI = { 'x-requested-with': 'scoreboard-ui' };

/** Pydantic hands back a list of per-field errors; say which field, in words. */
function reason(body) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).slice(1).join('.') || 'value'}: ${e.msg}`).join(' · ');
  return 'the scoreboard refused that change';
}

const ok = async (r) => {
  if (!r.ok) throw new Error(reason(await r.json().catch(() => null)));
  return r.status === 204 ? null : r.json();
};

const api = {
  get: (p) => fetch(p).then(ok),
  put: (p, body) => fetch(p, { method: 'PUT', headers: { ...UI, 'content-type': 'application/json' }, body: JSON.stringify(body) }).then(ok),
  del: (p) => fetch(p, { method: 'DELETE', headers: UI }).then(ok),
  upload: (p, file) => fetch(p, { method: 'POST', headers: UI, body: file }).then(ok),
};

const SETTINGS = '/api/holidays/settings';
const imageUrl = (slug, bump) => `/api/holidays/images/${slug}?v=${bump}`;

// A custom date is either MM-DD (every year) or YYYY-MM-DD (once). The date input always
// wants a full date, so a yearly one is shown against the current year.
const isYearly = (d) => (d || '').length === 5;
const toInput = (d) => (isYearly(d) ? `${new Date().getFullYear()}-${d}` : d || '');
const fromInput = (v, yearly) => (yearly ? (v || '').slice(5) : v);
// Local, not toISOString(): near midnight UTC that would offer yesterday's date.
const pad = (n) => String(n).padStart(2, '0');
const blankDate = () => { const d = new Date(); return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };

/** An override that changes nothing is dropped, so config.json only holds real edits. */
const isDefault = (o) => (o.enabled === undefined || o.enabled === true) && !o.display && !o.image;

// `slug` is where an upload goes; `shows` is where the picture on screen comes from. A row
// that borrows another's art has two different ones, and drawing the first would 404.
function Picture({ slug, shows, uploaded, bump, disabled, onPick, onClear, busy }) {
  const title = !slug ? 'Name it first, then you can give it a picture'
    : uploaded ? 'Your picture — click to replace'
    : shows ? 'The picture that ships with the scoreboard — click to replace'
    : 'No picture yet — click to add one';
  return html`<span class="pic">
    <button class=${'thumb' + (shows ? '' : ' empty')} title=${title} disabled=${disabled || busy}
            onclick=${() => onPick(slug)}>
      ${busy ? html`<span class="spin">…</span>`
        : shows ? html`<img src=${imageUrl(shows, bump)} alt="" />`
        : html`<span class="plus">+</span>`}
    </button>
    ${uploaded && html`<a class="rm" title="Remove your picture" onclick=${() => onClear(slug)}>✕</a>`}
  </span>`;
}

export function Holidays() {
  const [settings, setSettings] = useState(null);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [q, setQ] = useState('');
  const [bump, setBump] = useState(1);        // thumbnails are cached; nudge them after an upload
  const [busy, setBusy] = useState(null);     // slug currently uploading
  const picker = useRef(null);
  const pending = useRef(null);

  const loadRows = () => api.get('/api/snapshot')
    .then(s => setRows(s.data['holidays.available'] || null))
    .catch(() => setRows(null));
  const loadSettings = () => api.get(SETTINGS).then(setSettings).catch(e => setError(e.message));

  useEffect(() => { loadSettings(); loadRows(); }, []);

  // Saving replaces the whole section, which is what makes *removing* an override
  // possible — a PATCH could only ever add keys to it. See web/holidays.py.
  const save = (next) => {
    setSettings(next);                                    // optimistic: typing must not lag
    return api.put(SETTINGS, next)
      .then(saved => { setSettings(saved); setError(null); return loadRows(); })
      .catch(e => { setError(e.message); loadSettings(); });
  };

  const overrides = settings?.overrides || {};
  const editOverride = (name, patch) => {
    const next = { enabled: true, display: '', image: '', ...overrides[name], ...patch };
    const rest = { ...overrides };
    if (isDefault(next)) delete rest[name]; else rest[name] = next;
    save({ ...settings, overrides: rest });
  };
  const editCustom = (i, patch) =>
    save({ ...settings, custom: settings.custom.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  // One file input for the whole page rather than one per row; `pending` remembers which
  // row opened it.
  const pick = (slug) => { pending.current = slug; picker.current.value = ''; picker.current.click(); };
  const chosen = (ev) => {
    const file = ev.target.files && ev.target.files[0];
    const slug = pending.current;
    if (!file || !slug) return;
    setBusy(slug);
    api.upload(`/api/holidays/images/${slug}`, file)
      .then(() => { setError(null); setBump(b => b + 1); return loadRows(); })
      .catch(e => setError(e.message))
      .finally(() => setBusy(null));
  };
  const clearPicture = (slug) => api.del(`/api/holidays/images/${slug}`)
    .then(() => { setBump(b => b + 1); return loadRows(); })
    .catch(e => setError(e.message));

  const bySlug = useMemo(() => Object.fromEntries((rows || []).map(r => [r.name, r])), [rows]);
  const calendar = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rows || []).filter(r => !r.custom && (!needle || r.display.toLowerCase().includes(needle)
      || r.name.toLowerCase().includes(needle)));
  }, [rows, q]);

  if (!settings) return html`<p class="muted">Loading…</p>`;

  const picture = (row, slugFallback) => {
    const slug = row?.image_slug || slugFallback || '';
    return html`<${Picture} slug=${slug} shows=${row?.image_name || ''} uploaded=${!!row?.uploaded} bump=${bump}
      disabled=${!slug} busy=${busy === slug} onPick=${pick} onClear=${clearPicture} />`;
  };

  const hiddenCount = Object.values(overrides).filter(o => o.enabled === false).length;

  return html`
    <input type="file" accept="image/*" ref=${picker} onchange=${chosen} style="display:none" />

    <p class="crumb"><a href="#settings">← Settings</a></p>
    ${error && html`<div class="card error">${error}</div>`}

    <div class="card"><h2>Calendar</h2>
      <p class="muted small">Which country's holidays to start from, and how far ahead the board counts.</p>
      <div class="row">
        <label class="inline">Country
          <input type="text" size="3" maxlength="2" value=${settings.country}
            onchange=${e => save({ ...settings, country: e.target.value.toUpperCase() })} /></label>
        <label class="inline">State / province
          <input type="text" size="4" maxlength="5" placeholder="all" value=${settings.subdivision}
            onchange=${e => save({ ...settings, subdivision: e.target.value.toUpperCase() })} /></label>
        <label class="inline">Look ahead
          <input type="number" min="1" max="365" style="width:80px" value=${settings.horizon_days}
            onchange=${e => save({ ...settings, horizon_days: +e.target.value })} /> days</label>
      </div>
    </div>

    <div class="card holidays"><h2>Your own dates</h2>
      <p class="muted small">Birthdays, anniversaries, the home opener. Give two of them the same picture
        by uploading it to one and pointing the other at it.</p>
      <ul>
        ${settings.custom.map((c, i) => html`<li key=${i}>
          ${picture(bySlug[c.name], c.image)}
          <input type="checkbox" title="Show this one" checked=${c.enabled !== false}
            onchange=${e => editCustom(i, { enabled: e.target.checked })} />
          <input type="text" class="grow" placeholder="Name it" maxlength="40" value=${c.name}
            onchange=${e => editCustom(i, { name: e.target.value })} />
          <input type="date" value=${toInput(c.date)}
            onchange=${e => e.target.value && editCustom(i, { date: fromInput(e.target.value, isYearly(c.date)) })} />
          <label class="inline small muted" title="Repeat on this day every year">
            <input type="checkbox" checked=${isYearly(c.date)}
              onchange=${e => editCustom(i, { date: e.target.checked ? toInput(c.date).slice(5) : toInput(c.date) })} />
            yearly</label>
          <button class="danger" title="Delete" onclick=${() =>
            save({ ...settings, custom: settings.custom.filter((_, j) => j !== i) })}>✕</button>
        </li>`)}
      </ul>
      ${settings.custom.length === 0 && html`<p class="muted small">Nothing yet.</p>`}
      <button class="secondary" onclick=${() =>
        save({ ...settings, custom: [...settings.custom, { name: '', date: blankDate(), enabled: true, image: '' }] })}>
        + Add a date</button>
    </div>

    <div class="card holidays"><h2>From the calendar</h2>
      ${rows === null ? html`<p class="muted small">The holidays source has not published yet — give it a moment.</p>` : html`
        <p class="muted small">Untick one to hide it. Type over a name to show your own wording on the
          panel — the real name is kept underneath, so it keeps its picture.
          ${hiddenCount > 0 && ` ${hiddenCount} hidden.`}</p>
        <input type="search" placeholder=${`Filter ${calendar.length} holidays…`} value=${q}
          oninput=${e => setQ(e.target.value)} />
        <ul>
          ${calendar.map(r => {
            const o = overrides[r.name] || {};
            return html`<li key=${r.name}>
              ${picture(r)}
              <input type="checkbox" title="Show this one" checked=${o.enabled !== false}
                onchange=${e => editOverride(r.name, { enabled: e.target.checked })} />
              <input type="text" class="grow" maxlength="40" placeholder=${r.name} value=${o.display || ''}
                onchange=${e => editOverride(r.name, { display: e.target.value.trim() })} />
              ${o.display && html`<a class="revert" title=${`Call it "${r.name}" again`}
                onclick=${() => editOverride(r.name, { display: '' })}>↺</a>`}
            </li>`;
          })}
        </ul>
        ${calendar.length === 0 && html`<p class="muted small">Nothing matches.</p>`}`}
    </div>`;
}
