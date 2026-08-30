// Schema-driven settings page.
//
// The whole form is generated from /api/schema, so a new pydantic field shows up
// here with no code change. What this module adds on top is navigation: ~140
// fields across ~30 sections is unusable as one flat list, so they are bucketed
// into categories, grouped by domain, collapsed, searchable, and filterable down
// to "only what I changed" or "hide the expert knobs".

import { html, useState, useEffect, useMemo } from './htm-preact.js';

const api = {
  post: (p) => fetch(p, { method: 'POST' }).then(r => r.json()),
};

// ---- schema helpers --------------------------------------------------------

/** Follow $ref / anyOf-with-null down to the schema that actually describes a value. */
export function resolve(schema, root) {
  if (schema && schema.$ref) return resolve(root.$defs[schema.$ref.split('/').pop()], root);
  if (schema && schema.anyOf) {
    const inner = resolve(schema.anyOf.find(x => x.type !== 'null'), root);
    return {
      ...inner, nullable: true,
      title: schema.title ?? inner.title,
      description: schema.description ?? inner.description,
      default: schema.default !== undefined ? schema.default : inner.default,
    };
  }
  return schema;
}

/** Which editor a resolved schema needs, or 'unsupported' if we have none. */
function kindOf(s, root) {
  if (!s) return 'unsupported';
  if (s.enum) return 'enum';
  if (s.type === 'boolean') return 'boolean';
  if (s.type === 'integer' || s.type === 'number') return 'number';
  if (s.type === 'string') return 'string';
  if (s.type === 'array') {
    const items = s.items ? resolve(s.items, root) : null;
    if (items && items.enum) return 'enum-list';
    if (items && items.type === 'string') return 'string-list';
    if (s.prefixItems && s.maxItems === 3) return 'color';   // RGB triple
    return 'unsupported';
  }
  if (s.type === 'object' && s.additionalProperties && resolve(s.additionalProperties, root).enum) return 'map';
  return 'unsupported';
}

function deepEqual(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every(k => deepEqual(a[k], b[k]));
}

/** Pydantic titles a model after its class unless the docstring says otherwise. */
function prettyTitle(title, fallback) {
  if (!title) return fallback;
  if (/\s/.test(title)) return title;                       // already a human phrase
  return title.replace(/Config$/, '').replace(/([a-z0-9])([A-Z])/g, '$1 $2');
}

const clone = (v) => (v === null || typeof v !== 'object' ? v : JSON.parse(JSON.stringify(v)));

// ---- page structure --------------------------------------------------------

// Top-level config keys the settings page never shows: edited elsewhere (playlists
// live on the Boards page, setup_complete in the wizard) or managed by the app.
const HIDDEN = new Set(['boards', 'sources', 'playlists', 'setup_complete', 'version']);

// Categories claim top-level keys. Anything unclaimed still appears, under "Other",
// so a newly added config section is never silently invisible.
const CATEGORIES = [
  { id: 'display', label: 'Display', keys: ['display'],
    note: 'Panel wiring and driver options. These take effect on the next driver restart — the Setup wizard has a button for it.' },
  { id: 'location', label: 'Location & time', keys: ['location'] },
  { id: 'brightness', label: 'Brightness', keys: ['brightness'] },
  { id: 'appearance', label: 'Appearance', keys: ['transition', 'ticker', 'logos', 'sports'] },
  { id: 'boards', label: 'Boards', plugin: 'boards' },
  { id: 'sources', label: 'Data sources', plugin: 'sources' },
  { id: 'system', label: 'System', keys: ['web', 'log_level'] },
];

/**
 * Flatten the schema into `[{ id, title, group, cat, path, fields[], save }]`.
 *
 * `group` is a heading within a category — plugin keys are bucketed by their
 * prefix ('nhl.goal' -> NHL), so boards read as three short lists instead of one
 * alphabetical list of twenty.
 */
function buildSections(schema, config, boardTitles, sports) {
  const sections = [];

  const makeFields = (objSchema, root, value, path) =>
    Object.entries(objSchema.properties || {}).map(([name, sub]) => {
      const s = resolve(sub, root);
      const dflt = s.default;
      const v = value?.[name] !== undefined ? value[name] : dflt;
      return {
        name, root, schema: s, path: path ? `${path}.${name}` : name,
        kind: kindOf(s, root),
        label: s.title || name,
        desc: s.description || '',
        value: v,
        dflt,
        advanced: !!s.advanced,
        changed: dflt !== undefined && !deepEqual(v, dflt),
      };
    });

  const push = (sec) => { if (sec.fields.length) sections.push(sec); };

  // Fixed top-level sections, in category order.
  const claimed = new Set();
  for (const cat of CATEGORIES) {
    if (!cat.keys) continue;
    const loose = [];                                   // scalars with no object of their own
    for (const key of cat.keys) {
      if (!(key in schema.properties)) continue;
      claimed.add(key);
      const r = resolve(schema.properties[key], schema);
      if (r.properties) {
        push({ id: key, title: prettyTitle(r.title, key), cat: cat.id, path: key,
               fields: makeFields(r, schema, config[key], key),
               save: (patch) => ({ [key]: patch }) });
      } else {
        loose.push(...makeFields({ properties: { [key]: schema.properties[key] } }, schema, config, ''));
      }
    }
    if (loose.length) {
      push({ id: `${cat.id}:general`, title: 'General', cat: cat.id, path: '',
             fields: loose, save: (patch) => patch });
    }
  }

  // Plugin sections (boards, sources), grouped by domain.
  for (const cat of CATEGORIES) {
    if (!cat.plugin) continue;
    const props = schema.properties[cat.plugin]?.properties || {};
    claimed.add(cat.plugin);
    for (const [key, sub] of Object.entries(props)) {
      const domain = key.includes('.') ? key.split('.')[0] : null;
      const group = sports.includes(domain ?? key) ? (domain ?? key).toUpperCase()
                  : domain ? 'Extras'
                  : cat.plugin === 'sources' ? 'Extras' : 'General';
      push({
        id: `${cat.plugin}.${key}`,
        title: boardTitles[key] || prettyTitle(sub.title, key),
        group, cat: cat.id, path: `${cat.plugin}.${key}`,
        // each plugin model is its own self-contained schema, so it is its own root
        fields: makeFields(sub, sub, config[cat.plugin]?.[key] || {}, key),
        save: (patch) => ({ [cat.plugin]: { [key]: patch } }),
      });
    }
  }

  // Anything a category forgot still gets a home.
  const leftover = Object.keys(schema.properties).filter(k => !HIDDEN.has(k) && !claimed.has(k));
  for (const key of leftover) {
    const r = resolve(schema.properties[key], schema);
    if (r.properties) {
      push({ id: key, title: prettyTitle(r.title, key), cat: 'other', path: key,
             fields: makeFields(r, schema, config[key], key), save: (patch) => ({ [key]: patch }) });
    } else {
      push({ id: `other:${key}`, title: prettyTitle(r.title, key), cat: 'other', path: '',
             fields: makeFields({ properties: { [key]: schema.properties[key] } }, schema, config, ''),
             save: (patch) => patch });
    }
  }
  return sections;
}

// ---- field editor ----------------------------------------------------------

function Editor({ field, onChange }) {
  const { schema: s, root, kind, value, path } = field;
  const id = path.replace(/\./g, '-');
  switch (kind) {
    case 'enum':
      return html`<select id=${id} value=${value} onchange=${e => onChange(e.target.value)}>
        ${s.enum.map(v => html`<option value=${v}>${v}</option>`)}</select>`;
    case 'boolean':
      return html`<input type="checkbox" id=${id} checked=${!!value} onchange=${e => onChange(e.target.checked)} />`;
    case 'number':
      return html`<input type="number" id=${id} value=${value ?? ''} min=${s.minimum} max=${s.maximum}
        step=${s.type === 'integer' ? 1 : 'any'}
        onchange=${e => onChange(e.target.value === '' ? null : +e.target.value)} />`;
    case 'string':
      return html`<input type="text" id=${id} value=${value ?? ''} onchange=${e => onChange(e.target.value)} />`;
    case 'color': {
      const rgb = Array.isArray(value) && value.length === 3 ? value : [0, 0, 0];
      const hex = '#' + rgb.map(v => Number(v).toString(16).padStart(2, '0')).join('');
      return html`<input type="color" id=${id} value=${hex}
        onchange=${e => onChange([1, 3, 5].map(i => parseInt(e.target.value.substr(i, 2), 16)))} />`;
    }
    case 'enum-list': {
      const opts = resolve(s.items, root).enum;
      const sel = value || [];
      return html`<div class="tags">
        ${sel.map((v, i) => html`<span class="tag">${v} <a onclick=${() => onChange(sel.filter((_, j) => j !== i))}>✕</a></span>`)}
        <select onchange=${e => { if (e.target.value) onChange([...sel, e.target.value]); e.target.value = ''; }}>
          <option value="">+ add</option>${opts.filter(o => !sel.includes(o)).map(o => html`<option value=${o}>${o}</option>`)}
        </select></div>`;
    }
    case 'string-list':
      return html`<input type="text" id=${id} value=${(value || []).join(', ')} placeholder="comma separated"
        onchange=${e => onChange(e.target.value.split(',').map(x => x.trim()).filter(Boolean))} />`;
    case 'map': {
      const opts = resolve(s.additionalProperties, root).enum;
      const map = value || {};
      const entries = Object.entries(map);
      const rename = (from, to) => onChange(Object.fromEntries(entries.map(([k, v]) => [k === from ? to : k, v])));
      return html`<div class="map">
        ${entries.map(([k, v]) => html`<div class="map-row">
          <input type="text" value=${k} onchange=${e => e.target.value.trim() && rename(k, e.target.value.trim())} />
          <select value=${v} onchange=${e => onChange({ ...map, [k]: e.target.value })}>
            ${opts.map(o => html`<option value=${o}>${o}</option>`)}
          </select>
          <a class="rm" onclick=${() => { const n = { ...map }; delete n[k]; onChange(n); }}>✕</a>
        </div>`)}
        <div class="map-row">
          <input type="text" placeholder="+ add key, e.g. nhl:WSH"
            onchange=${e => { const k = e.target.value.trim(); if (k && !(k in map)) { onChange({ ...map, [k]: opts[0] }); e.target.value = ''; } }} />
        </div></div>`;
    }
    default:
      // No editor for this shape (e.g. a list of objects). Say so rather than
      // dropping the setting silently, so it can still be found and explained.
      return html`<span class="muted">Not editable here — edit <code>config.json</code>.</span>`;
  }
}

function Field({ field, onChange }) {
  const id = field.path.replace(/\./g, '-');
  return html`<div class=${'field' + (field.changed ? ' changed' : '')}>
    <label for=${id}>${field.label}${field.advanced && html`<span class="pill">adv</span>`}</label>
    <div class="field-input">
      <${Editor} field=${field} onChange=${onChange} />
      ${field.changed && html`<a class="revert" title=${`Reset to default (${JSON.stringify(field.dflt)})`}
        onclick=${() => onChange(clone(field.dflt))}>↺</a>`}
    </div>
    ${field.desc && html`<small>${field.desc}</small>`}
  </div>`;
}

// ---- page ------------------------------------------------------------------

const pref = {
  get: (k, fallback) => { try { const v = localStorage.getItem('settings.' + k); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set: (k, v) => { try { localStorage.setItem('settings.' + k, JSON.stringify(v)); } catch (e) {} },
};

/** Sections stay open by default in small categories; Boards/Sources start collapsed. */
const AUTO_OPEN_UP_TO = 3;

export function Settings({ config, schema, boards, save }) {
  const [q, setQ] = useState('');
  const [cat, setCat] = useState(() => pref.get('cat', 'display'));
  const [advanced, setAdvanced] = useState(() => pref.get('advanced', false));
  const [changedOnly, setChangedOnly] = useState(() => pref.get('changedOnly', false));
  const [toggled, setToggled] = useState({});          // explicit user open/close, by section id

  useEffect(() => { pref.set('cat', cat); }, [cat]);
  useEffect(() => { pref.set('advanced', advanced); }, [advanced]);
  useEffect(() => { pref.set('changedOnly', changedOnly); }, [changedOnly]);

  const boardTitles = useMemo(() => Object.fromEntries((boards || []).map(b => [b.key, b.title])), [boards]);
  const sports = useMemo(() => config?.sports?.priority || [], [config]);
  const sections = useMemo(
    () => (schema ? buildSections(schema, config, boardTitles, sports) : []),
    [schema, config, boardTitles, sports]);

  const needle = q.trim().toLowerCase();
  const matchesField = (f) => !needle
    || f.label.toLowerCase().includes(needle)
    || f.desc.toLowerCase().includes(needle)
    || f.path.toLowerCase().includes(needle);

  // A section survives with the fields that pass every active filter. A search hit
  // on the section's own title keeps all of its fields.
  const visible = sections.map(sec => {
    const titleHit = needle && (sec.title.toLowerCase().includes(needle) || sec.path.toLowerCase().includes(needle));
    const fields = sec.fields.filter(f =>
      (advanced || !f.advanced) && (!changedOnly || f.changed) && (titleHit || matchesField(f)));
    return { ...sec, fields, titleHit, hiddenAdvanced: advanced ? 0 : sec.fields.filter(f => f.advanced).length };
  }).filter(sec => sec.fields.length);

  const searching = !!needle;
  const shown = searching ? visible : visible.filter(s => s.cat === cat);
  const perCat = Object.fromEntries(CATEGORIES.concat([{ id: 'other', label: 'Other' }])
    .map(c => [c.id, visible.filter(s => s.cat === c.id).length]));
  const totalChanged = sections.reduce((n, s) => n + s.fields.filter(f => f.changed).length, 0);
  const activeCat = CATEGORIES.find(c => c.id === cat);

  // Ticking "Changed" (or "Advanced" off) can empty the category you are standing
  // in; move to one that still has something rather than showing a blank page.
  const firstNonEmpty = CATEGORIES.find(c => perCat[c.id] > 0)?.id;
  const catCount = perCat[cat] || 0;
  useEffect(() => {
    if (!searching && catCount === 0 && firstNonEmpty) setCat(firstNonEmpty);
  }, [searching, catCount, firstNonEmpty]);

  if (!schema) return html`<p class="muted">Loading…</p>`;

  const isOpen = (sec, count) => toggled[sec.id] !== undefined ? toggled[sec.id]
    : searching || count <= AUTO_OPEN_UP_TO;
  const bare = !searching && shown.length === 1 && !shown[0].group;   // no header worth drawing

  // Group headings within the current view, in sport-priority order then extras.
  const groupOrder = (g) => {
    const i = sports.map(s => s.toUpperCase()).indexOf(g);
    return i >= 0 ? i : g === 'Extras' ? 90 : g === 'General' ? 91 : 92;
  };
  const groups = [...new Set(shown.map(s => s.group || ''))].sort((a, b) => groupOrder(a) - groupOrder(b));

  return html`
    <div class="settings-bar">
      <div class="row">
        <input class="search" type="search" placeholder="Search all settings…" value=${q}
          oninput=${e => setQ(e.target.value)} />
        <label class="toggle"><input type="checkbox" checked=${changedOnly}
          onchange=${e => setChangedOnly(e.target.checked)} /> Changed${totalChanged ? ` (${totalChanged})` : ''}</label>
        <label class="toggle"><input type="checkbox" checked=${advanced}
          onchange=${e => setAdvanced(e.target.checked)} /> Advanced</label>
      </div>
      ${!searching && html`<nav class="subnav">
        ${CATEGORIES.concat(perCat.other ? [{ id: 'other', label: 'Other' }] : []).map(c => html`
          <a class=${(cat === c.id ? 'active' : '') + (perCat[c.id] ? '' : ' empty')}
             onclick=${() => setCat(c.id)}>${c.label}<span class="count">${perCat[c.id] || 0}</span></a>`)}
      </nav>`}
      ${searching && html`<p class="muted searchnote">
        ${`${shown.reduce((n, s) => n + s.fields.length, 0)} settings in ${shown.length} section${shown.length === 1 ? '' : 's'} \u00b7 `}
        <a onclick=${() => setQ('')}>clear search</a></p>`}
    </div>

    ${!searching && activeCat?.note && html`<p class="muted catnote">${activeCat.note}</p>`}

    ${shown.length === 0 && html`<div class="card"><p class="muted">
      Nothing matches${changedOnly ? ' — every setting in view is at its default.' : '.'}</p></div>`}

    <div class="sections">${groups.map(g => html`<div key=${g}>
      ${g && html`<h3 class="group">${g}</h3>`}
      ${shown.filter(s => (s.group || '') === g).map(sec => {
        const changed = sec.fields.filter(f => f.changed).length;
        const open = bare || isOpen(sec, shown.length);
        return html`<div class=${'card sect' + (open ? ' open' : '') + (bare ? ' bare' : '')} key=${sec.id}>
          ${!bare && html`<button class="sect-head" onclick=${() => setToggled({ ...toggled, [sec.id]: !open })}>
            <span class="caret">${open ? '▾' : '▸'}</span>
            <span class="sect-title">${sec.title}</span>
            <span class="muted sect-meta">${sec.fields.length} setting${sec.fields.length === 1 ? '' : 's'}</span>
            ${changed > 0 && html`<span class="chip">${changed} changed</span>`}
          </button>`}
          ${open && html`<div class="sect-body">
            <div class="fields">${sec.fields.map(f => html`<${Field} key=${f.path} field=${f}
              onChange=${v => save(sec.save({ [f.name]: v }))} />`)}</div>
            ${sec.hiddenAdvanced > 0 && html`<p class="muted hiddenadv">
              ${`${sec.hiddenAdvanced} advanced setting${sec.hiddenAdvanced === 1 ? '' : 's'} hidden \u00b7 `}
              <a onclick=${() => setAdvanced(true)}>show</a></p>`}
          </div>`}
        </div>`;
      })}
    </div>`)}</div>

    <div class="card row">
      <button class="danger" onclick=${() => confirm('Reset all settings to defaults?')
        && api.post('/api/config/reset').then(() => location.reload())}>Reset to defaults</button>
      <span class="muted">${totalChanged} setting${totalChanged === 1 ? '' : 's'} differ from the defaults.</span>
    </div>`;
}
