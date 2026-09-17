// The rotation view: the current playlist as one lap, drawn as a bar whose slices are
// proportional to each board's length, with what the board is made of above (7 games,
// 4 aircraft) and its name and length below. The active slice fills as the board runs.
//
// Everything comes from /api/rotation, which is the director's own view: the same entry
// filter the frame loop uses, so a board the panel passes over is listed as skipped here
// with the reason rather than drawn as if it played. "Auto" lengths are what the board
// says it needs right now and move with the data, hence the ≈.

import { html, useState, useEffect } from './htm-preact.js';

const POLL_MS = 3000;
const TICK_MS = 500;
const OPEN_WEIGHT = 30;      // a board with no length of its own gets this much of the bar
// No slice is narrower than this, whatever its length: a 15 s clock beside a 3 minute ticker
// stays legible instead of 25 px wide. Labels wrap onto a second line rather than truncate.
const MIN_PX = 60;

const fmtSecs = (s) => s == null ? '' : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const plural = (n, unit) => `${n} ${n === 1 || unit === 'aircraft' ? unit : unit + 's'}`;
const family = (key) => {
  const head = key.split('.')[0];
  return ['nhl', 'nfl', 'ncaaf', 'mlb', 'ncaah', 'ahl', 'weather', 'flights', 'holidays', 'season'].includes(head) ? head : 'other';
};

export function useRotation() {
  const [data, setData] = useState(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const tick = () => fetch('/api/rotation').then(r => r.json()).then(d => setData({ ...d, at: Date.now() })).catch(() => {});
    tick();
    const poll = setInterval(tick, POLL_MS);
    const clock = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, []);
  if (!data) return null;
  // Advance the playhead between polls so it moves rather than jumping every few seconds.
  const drift = (now - data.at) / 1000;
  return { ...data, entries: data.entries.map(e => e.active && e.elapsed != null ? { ...e, elapsed: e.elapsed + drift } : e) };
}

const weight = (e) => e.seconds == null ? OPEN_WEIGHT : Math.max(e.seconds, 1);

// The lap is a grid, one column per slice and one row per label line, so the bars line up
// and a name that wraps in one slice does not push its neighbours' lengths out of line.
const columns = (playing) => playing.map(e => `minmax(${MIN_PX}px, ${weight(e)}fr)`).join(' ');

function Slice({ e }) {
  const open = e.seconds == null;
  const progress = e.active && !open && e.seconds > 0 ? Math.min(e.elapsed / e.seconds, 1) : 0;
  const count = e.count != null && e.unit ? plural(e.count, e.unit) : '';
  const each = e.pace_unit ? `${fmtSecs(e.duration != null ? e.duration : e.count ? e.seconds / e.count : null)} each` : '';
  const length = open ? 'until state changes' : `${e.auto || e.pace_unit ? '≈' : ''}${fmtSecs(e.seconds)}`;
  const title = `${e.title}${count ? ` · ${count}` : ''} · ${length}${each ? ` · ${each}` : ''}${e.auto ? ' (auto)' : ''}${e.active ? ` · ${fmtSecs(e.elapsed)} in` : ''}`;
  return html`<div class=${['seg', 'fam-' + family(e.board), e.active ? 'active' : '', open ? 'open' : ''].join(' ').trim()} title=${title}>
    <div class="count">${count || ' '}</div>
    <div class="bar"><div class="fill" style=${`width:${(progress * 100).toFixed(1)}%`}></div></div>
    <div class="name">${e.title}</div>
    <div class="len">${e.active ? `${fmtSecs(e.elapsed)} / ` : ''}${length}${each && !e.active ? ` · ${each}` : ''}</div>
  </div>`;
}

export function Rotation({ compact }) {
  const rot = useRotation();
  if (!rot) return html`<div class="card rotation"><h2>Rotation</h2><p class="muted">Loading…</p></div>`;
  // A board that finishes at once (a countdown with nothing to count, an alerts board with
  // nothing in force) is in the director's list but never really on screen: show it with
  // the skipped ones so the bar only holds slices that take time.
  const entries = rot.entries.map(e => e.skipped == null && e.seconds != null && e.seconds < 0.5 ? { ...e, skipped: 'nothing to show' } : e);
  const playing = entries.filter(e => e.skipped == null);
  const skipped = entries.filter(e => e.skipped != null);
  const active = playing.find(e => e.active);
  const i = playing.indexOf(active);
  const next = active && playing.length > 1 ? playing[(i + 1) % playing.length] : null;
  const remaining = active && active.seconds != null ? Math.max(active.seconds - active.elapsed, 0) : null;
  const lap = playing.length && playing.every(e => e.seconds != null) ? playing.reduce((s, e) => s + e.seconds, 0) : null;
  const estimated = playing.some(e => e.auto || e.pace_unit);

  const summary = [rot.state, `${playing.length} board${playing.length === 1 ? '' : 's'}`,
    lap != null ? `lap ${estimated ? '≈ ' : ''}${fmtSecs(lap)}` : playing.length ? 'no fixed lap' : ''].filter(Boolean).join(' · ');
  const nextText = rot.event ? `${rot.event.title} is playing (${fmtSecs(rot.event.elapsed)}), the rotation resumes after it`
    : rot.override ? `previewing ${rot.override} from the UI`
    : next ? `next: ${next.title}${remaining != null ? ` in ${fmtSecs(remaining)}` : ''}` : '';

  return html`<div class=${'card rotation' + (compact ? ' compact' : '')}>
    <div class="rot-head"><h2>Rotation</h2><span class="muted">${summary}</span>${nextText ? html`<span class="muted next">${nextText}</span>` : ''}</div>
    ${playing.length ? html`<div class="lap" style=${`grid-template-columns:${columns(playing)}`}>${playing.map(e => html`<${Slice} e=${e} />`)}</div>`
      : html`<p class="muted">${rot.state === 'boot' ? 'Starting up.' : rot.state === 'error' ? 'No data and no connection: the panel shows the clock.'
        : `Nothing in the ${rot.state} playlist can play right now, so the panel shows the ${rot.board || 'clock'}.`}</p>`}
    ${skipped.length ? html`<div class="skipped muted small">Skipped: ${skipped.map((e, k) => html`${k ? ' · ' : ''}<b>${e.title}</b> (${e.skipped})`)}</div>` : ''}
  </div>`;
}
