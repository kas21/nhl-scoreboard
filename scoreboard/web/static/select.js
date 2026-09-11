import { html, useMemo } from './htm-preact.js';

/**
 * A <select> whose <option> nodes are built once per distinct list and handed back
 * unchanged on every later render.
 *
 * Preact rewrites each option's value attribute whenever it diffs a fresh option vnode,
 * even when nothing about it changed. Chrome rebuilds an open dropdown menu the moment
 * the options under it are touched, and the click that follows lands on nothing. The
 * pages that poll (Simulator every second, Boards every 15 s) re-render their dropdowns
 * on every tick, so a menu left open for a moment would shut on you mid-pick. A vnode
 * seen before is skipped by the diff, so the options stay put until the list itself
 * changes; the select's own value is still written when it differs.
 *
 * `options`: [value, label] pairs, or plain values that serve as their own label.
 * Every other prop goes on the <select> as-is.
 */
export function Select({ options, ...rest }) {
  const pairs = (options || []).map(o => (Array.isArray(o) ? o : [o, o]));
  const key = JSON.stringify(pairs);
  const nodes = useMemo(() => pairs.map(([v, l]) => html`<option value=${v}>${l}</option>`), [key]);
  return html`<select ...${rest}>${nodes}</select>`;
}
