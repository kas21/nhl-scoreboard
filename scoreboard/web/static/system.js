// Restarting the scoreboard from the browser, and knowing when it is back.
//
// A restart (display driver options, an update) exits the process and systemd starts it
// again. "Back" means the *new* process is answering: the old one keeps serving for a
// moment while it shuts down, so any 200 after a short delay is not proof. Every process
// has its own boot_id on /api/status; we wait for it to change.
import { html, useState } from './htm-preact.js';

const UI = { 'x-requested-with': 'scoreboard-ui' };
const WAIT_MS = 90000;

export const bootId = () => fetch('/api/status').then(r => r.ok ? r.json() : Promise.reject(r.status)).then(s => s.boot_id);

/** Ask for a restart and resolve true once a new process answers (false if it never does). */
export async function restartAndWait(setMsg) {
  const before = await bootId().catch(() => null);
  await fetch('/api/system/restart', { method: 'POST', headers: UI }).catch(() => {});
  if (setMsg) setMsg('Restarting… the panel goes dark for a few seconds.');
  const t0 = Date.now();
  while (Date.now() - t0 < WAIT_MS) {
    await new Promise(r => setTimeout(r, 1000));
    try { const id = await bootId(); if (id && id !== before) return true; } catch (e) { /* still down */ }
  }
  return false;
}

/** Wait for a restart somebody else started (an update) and resolve when a new process is up. */
export async function waitForRestart(before) {
  const t0 = Date.now();
  while (Date.now() - t0 < WAIT_MS) {
    await new Promise(r => setTimeout(r, 1000));
    try { const id = await bootId(); if (id && id !== before) return true; } catch (e) { /* still down */ }
  }
  return false;
}

export function RestartButton({ label = 'Restart', onDone }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const go = async () => {
    setBusy(true);
    const ok = await restartAndWait(setMsg);
    setMsg(ok ? 'Back up.' : 'Still not back; reload the page in a moment.');
    setBusy(false);
    if (ok && onDone) onDone();
  };
  return html`<div class="row"><button disabled=${busy} onclick=${go}>${label}</button>${msg && html`<span class="muted">${msg}</span>`}</div>`;
}
