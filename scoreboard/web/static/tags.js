import { html, useState } from './htm-preact.js';
import { Select } from './select.js';

// Immutable "take the item at `from`, put it at `to`".
const moved = (list, from, to) => {
  const l = [...list];
  l.splice(to, 0, l.splice(from, 1)[0]);
  return l;
};

/**
 * An ordered list of pills (favourite teams, conferences…) with a picker to add one.
 *
 * Order matters for these lists (favourites are "highest priority first"), so a pill can be
 * dragged into place instead of removed and re-added. Pointer events rather than HTML5
 * drag-and-drop, for the same reasons as the playlist rows: they fire for touch too, and a
 * release anywhere lands the pill. The list renders already reordered while dragging, so
 * you see the result before letting go; nothing is saved until the release.
 *
 * `options`: what the picker offers; `typed`: also accept a hand-typed code (a team the
 * list does not know yet); `numbered`: prefix each pill with its rank.
 */
export function Tags({ value, onChange, options, typed = false, numbered = false, addLabel = '+ add' }) {
  const list = value || [];
  const [drag, setDrag] = useState(null);
  const shown = drag ? moved(list, drag.from, drag.to) : list;

  const grab = (ev, from) => {
    if (ev.button || ev.target.closest('a')) return;     // left button / touch only; the ✕ is a click, not a grab
    ev.preventDefault();
    const box = ev.currentTarget.parentElement;
    let to = from;
    setDrag({ from, to });
    // Where the pointer would drop the pill, measured against the other pills as they sit
    // now (the pill being dragged is skipped, or it would fight with itself). Pills wrap,
    // so a pill is "after" the pointer if it starts on a lower row or, on the pointer's
    // row, if its centre is to the right.
    const slotAt = (x, y) => {
      const pills = [...box.querySelectorAll('.tag')].filter((_, i) => i !== to);
      const hit = pills.findIndex(el => { const r = el.getBoundingClientRect(); return y < r.top || (y < r.bottom && x < r.left + r.width / 2); });
      return hit === -1 ? pills.length : hit;
    };
    const onMove = (e) => {
      const at = slotAt(e.clientX, e.clientY);
      if (at !== to) { to = at; setDrag({ from, to }); }
    };
    const finish = (keep) => {
      removeEventListener('pointermove', onMove);
      removeEventListener('pointerup', onUp);
      removeEventListener('pointercancel', onCancel);
      removeEventListener('keydown', onKey);
      setDrag(null);
      if (keep && to !== from) onChange(moved(list, from, to));
    };
    const onUp = () => finish(true);
    const onCancel = () => finish(false);
    const onKey = (e) => { if (e.key === 'Escape') finish(false); };
    addEventListener('pointermove', onMove);
    addEventListener('pointerup', onUp);
    addEventListener('pointercancel', onCancel);
    addEventListener('keydown', onKey);
  };

  const add = (v) => { if (v && !list.includes(v)) onChange([...list, v]); };
  return html`<div class="tags sortable">
    ${shown.map((v, i) => html`<span class=${'tag' + (drag && drag.to === i ? ' dragging' : '')} title=${list.length > 1 ? 'Drag to reorder' : ''}
        onpointerdown=${ev => grab(ev, i)}>${numbered ? `${i + 1}. ` : ''}${v} <a onclick=${() => onChange(list.filter(x => x !== v))}>✕</a></span>`)}
    <${Select} options=${[['', addLabel], ...(options || []).filter(o => !list.includes(o))]}
      onchange=${e => { add(e.target.value); e.target.value = ''; }} />
    ${typed ? html`<input type="text" class="code" maxlength="4" placeholder="or type a code" title="A team code the list does not have yet, e.g. a new or relocated team"
      onchange=${e => { add(e.target.value.trim().toUpperCase()); e.target.value = ''; }} />` : ''}
  </div>`;
}
