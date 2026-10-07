/**
 * Timeline grouping (Phase TASK-DETAIL-UI-POLISH).
 *
 * Kept out of the component so the folding — which is the whole point of the
 * redesigned timeline — can be tested as a function rather than as rendered
 * markup, and so the component file exports nothing but a component.
 */
/** "Today" / "Yesterday" / "25 Sep 2026" for a day bucket. */
export const dayLabel = (iso) => {
  const then = new Date(iso);
  const today = new Date();
  const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((midnight(today) - midnight(then)) / 86_400_000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return then.toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: '2-digit' });
};

export const timeOnly = (iso) => new Date(iso)
  .toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });

/**
 * [row] -> [{ key, label, entries }] grouped by day, with consecutive rows of
 * the same action folded into one entry.
 */
export const groupTimeline = (rows = []) => {
  const days = [];
  for (const row of rows) {
    const key = (row.created_at || '').slice(0, 10);
    let day = days[days.length - 1];
    if (!day || day.key !== key) {
      day = { key, label: dayLabel(row.created_at), entries: [] };
      days.push(day);
    }
    const last = day.entries[day.entries.length - 1];
    // Only CONSECUTIVE repeats fold. Two "Progress updated" with a comment
    // between them are two moments in the task's life, not one.
    if (last && last.action === row.action) {
      last.rows.push(row);
    } else {
      day.entries.push({ action: row.action, label: row.action_label, rows: [row] });
    }
  }
  return days;
};



/**
 * The VALUE an event carries, when it has one worth showing beside its name
 * (Phase TASK-PROGRESS-UX-HARDENING).
 *
 * A run of three "Progress updated" rows folds into one entry — which is the
 * point — but folded to just "Progress updated ×3" it hides the very thing
 * somebody opens the group to see. The percentages are already on the row, in
 * the audit metadata the engine writes; this reads them out.
 *
 * Returns null for every other kind of event, so nothing else changes
 * appearance. A row written before this metadata existed also returns null and
 * simply renders as it always did.
 */
export const eventValue = (row) => {
  if (row?.action !== 'progress_updated') return null;
  const meta = row.metadata || {};
  const value = meta.reported ?? meta.to;
  return Number.isFinite(value) ? `${value}%` : null;
};

/**
 * A folded run of progress updates, said as the journey it was: "25% → 75%".
 * Rows arrive newest-first (the timeline reads that way), so the span is read
 * from the back.
 */
export const entrySummary = (entry) => {
  if (!entry || entry.rows.length < 2) return null;
  const values = entry.rows.map(eventValue);
  if (values.some((v) => v === null)) return null;
  const first = values[values.length - 1];
  const last = values[0];
  return first === last ? first : `${first} → ${last}`;
};
