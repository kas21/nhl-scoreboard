// Dashboard info cards: what's on (games for the next few days) and what's around
// (planes, holidays, weather). Everything comes from /api/dashboard, which is the snapshot
// trimmed to these fields; the cards only format it.
//
// The games window is whatever each sport's `show_games_within_days` setting says — the
// sources publish exactly that many days as `<sport>.schedule` — so the dashboard and the
// panel agree on how far ahead "upcoming" looks.

import { html, useState, useEffect } from './htm-preact.js';

const POLL_MS = 5000;

export function useDashboard() {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const tick = () => fetch('/api/dashboard').then(r => r.json()).then(d => { setData(d); setFailed(false); }).catch(() => setFailed(true));
    tick(); const id = setInterval(tick, POLL_MS); return () => clearInterval(id);
  }, []);
  return [data, failed];
}

// -- date / time helpers (dates are YYYY-MM-DD in the scoreboard's timezone) --------------

const parseDay = (s) => { const [y, m, d] = (s || '').split('-').map(Number); return Number.isFinite(d) ? Date.UTC(y, m - 1, d) : null; };
const dayDiff = (day, today) => { const a = parseDay(day), b = parseDay(today); return a == null || b == null ? null : Math.round((a - b) / 86400000); };
const WEEKDAY = new Intl.DateTimeFormat([], { weekday: 'short', timeZone: 'UTC' });
const MONTHDAY = new Intl.DateTimeFormat([], { month: 'short', day: 'numeric', timeZone: 'UTC' });

export function dayLabel(day, today) {
  const diff = dayDiff(day, today);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  const t = parseDay(day);
  return t == null ? day : `${WEEKDAY.format(t)} ${MONTHDAY.format(t)}`;
}

const shortDay = (day) => { const t = parseDay(day); return t == null ? day : `${WEEKDAY.format(t)} ${MONTHDAY.format(t)}`; };
const localTime = (iso) => { const t = iso ? new Date(iso) : null; return t && !isNaN(t) ? t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''; };

// -- games ---------------------------------------------------------------------------------

function gameStatus(g) {
  if (g.phase === 'postgame') return g.outcome || 'Final';
  if (g.phase === 'live' || g.phase === 'intermission') return [g.period, g.phase === 'intermission' ? 'INT' : g.clock].filter(Boolean).join(' ');
  return localTime(g.start_time_utc) || 'TBD';
}

function GameRow({ g }) {
  const started = g.phase !== 'pregame';
  return html`<tr class=${(g.favorite ? 'fav ' : '') + (g.main ? 'main ' : '') + g.phase}>
    <td class="team">${g.away.abbrev}${started ? html` <b>${g.away.score}</b>` : ''}</td>
    <td class="at">@</td>
    <td class="team">${g.home.abbrev}${started ? html` <b>${g.home.score}</b>` : ''}</td>
    <td class="state">${gameStatus(g)}${g.main ? html` <span class="chip" title="The game the panel is following">on panel</span>` : ''}</td>
  </tr>`;
}

export function recordText(r) {
  if (!r || r.wins == null) return '';
  const parts = [r.wins, r.losses];
  if (r.otl != null) parts.push(r.otl);
  if (r.ties) parts.push(r.ties);
  const extras = [];
  if (r.points != null) extras.push(`${r.points} pts`);
  if (r.games_back != null && r.games_back !== 0) extras.push(`${r.games_back} GB`);
  if (r.division_rank && r.division) extras.push(`${ordinal(r.division_rank)} in ${r.division}`);
  return [parts.join('-'), ...extras].join(' · ');
}

const ordinal = (n) => `${n}${['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 20) ? 0 : Math.min(n % 10, 4) % 4] || 'th'}`;

function nextGameText(ng, today) {
  if (!ng) return '';
  const when = dayLabel(ng.date, today);
  const time = localTime(ng.start_time_utc);
  return `${ng.home ? 'vs' : '@'} ${ng.opponent} · ${when}${time ? ' ' + time : ''}`;
}

function seasonNote(season) {
  if (!season || season.phase === 'regular' || season.phase === 'playoffs') return '';
  const in_ = (n, what) => (n != null && n >= 0 ? `${what} in ${n} day${n === 1 ? '' : 's'}` : '');
  const note = in_(season.days_to_preseason, 'preseason') || in_(season.days_to_regular, 'regular season') || in_(season.days_to_next, 'next game');
  return `${season.phase === 'preseason' ? 'Preseason' : 'Off-season'}${note ? ' · ' + note : ''}`;
}

function SportGames({ s, today }) {
  const teams = Object.entries(s.teams || {});
  const note = seasonNote(s.season);
  return html`<div class="sport">
    <h3>${s.title}${note ? html` <span class="muted small">${note}</span>` : ''}</h3>
    ${teams.length ? html`<div class="teams">${teams.map(([abbrev, t]) => html`<div class="team-line">
      <b>${abbrev}</b> <span class="muted">${recordText(t.record) || 'no record yet'}</span>
      ${t.next_game ? html`<span class="muted"> · next ${nextGameText(t.next_game, today)}</span>` : ''}
    </div>`)}</div>` : ''}
    ${s.days.length ? s.days.map(d => html`<div class="day">
      <div class="daylabel">${dayLabel(d.date, today)} <span class="muted small">${d.games.length} game${d.games.length === 1 ? '' : 's'}</span></div>
      <table class="games"><tbody>${d.games.map(g => html`<${GameRow} g=${g} />`)}</tbody></table>
    </div>`) : html`<p class="muted small">Nothing scheduled in the look-ahead window (Settings → ${s.title} → show games within days).</p>`}
  </div>`;
}

export function GamesCard() {
  const [data, failed] = useDashboard();
  return html`<div class="card"><h2>Games</h2>
    ${failed ? html`<p class="error">Could not reach the scoreboard.</p>`
      : !data ? html`<p class="muted">Loading…</p>`
      : !data.sports.length ? html`<p class="muted">No sports sources are enabled.</p>`
      : data.sports.map(s => html`<${SportGames} s=${s} today=${data.today} />`)}
  </div>`;
}

// -- around you: weather, planes, holidays -------------------------------------------------

const alt = (ft) => (ft == null ? 'ground' : `${ft.toLocaleString()} ft`);

function Flights({ rows }) {
  if (!rows.length) return html`<p class="muted small">No aircraft nearby right now.</p>`;
  return html`<table class="games flights"><tbody>${rows.map(a => html`<tr class=${a.overhead ? 'main' : ''}>
    <td><b>${a.ident || a.callsign || a.hex}</b>${a.overhead ? html` <span class="chip">overhead</span>` : ''}</td>
    <td class="muted">${a.airline || a.type_name || a.type || ''}</td>
    <td>${a.route || [a.origin, a.destination].filter(Boolean).join('→') || ''}</td>
    <td class="state">${a.on_ground ? 'on ground' : alt(a.altitude_ft)}${a.distance_km != null ? ` · ${a.distance_km} km ${a.bearing_compass || ''}` : ''}</td>
  </tr>`)}</tbody></table>`;
}

function Weather({ w }) {
  const c = w.current || {};
  const unit = (c.units && c.units.temp) ? `°${c.units.temp}` : '°';
  return html`<div class="weather">
    <div class="now"><b>${c.temp != null ? `${c.temp}${unit}` : '—'}</b> <span>${c.desc || c.short || ''}</span>
      ${c.feels != null ? html`<span class="muted small"> · feels ${c.feels}${unit}</span>` : ''}
      ${c.label && c.label !== 'WEATHER' ? html`<span class="muted small"> · ${c.label}</span>` : ''}</div>
    ${w.daily && w.daily.length ? html`<div class="forecast">${w.daily.map(d => html`<div>
      <span class="muted small">${shortDay(d.date).split(' ')[0]}</span><div>${d.hi != null ? d.hi : '—'}/${d.lo != null ? d.lo : '—'}</div><span class="muted small">${d.short || ''}</span>
    </div>`)}</div>` : ''}
  </div>`;
}

function Holidays({ rows, today }) {
  if (!rows.length) return html`<p class="muted small">No holidays coming up.</p>`;
  return html`<ul class="holidays">${rows.map(h => html`<li><b>${h.display || h.name}</b>
    <span class="muted">${shortDay(h.date)} · ${h.days === 0 ? 'today' : h.days === 1 ? 'tomorrow' : `in ${h.days} days`}</span></li>`)}</ul>`;
}

export function AroundCard() {
  const [data] = useDashboard();
  if (!data) return html`<div class="card"><h2>Around you</h2><p class="muted">Loading…</p></div>`;
  const sections = [];
  if (data.weather) sections.push(html`<div class="section"><h3>Weather</h3><${Weather} w=${data.weather} /></div>`);
  if (data.flights) sections.push(html`<div class="section"><h3>Planes nearby</h3><${Flights} rows=${data.flights} /></div>`);
  if (data.holidays) sections.push(html`<div class="section"><h3>Holidays</h3><${Holidays} rows=${data.holidays} today=${data.today} /></div>`);
  return html`<div class="card"><h2>Around you</h2>
    ${sections.length ? sections : html`<p class="muted">Turn on weather, flights or holidays in <a href="#settings">Settings</a> to see them here.</p>`}
  </div>`;
}
