/**
 * The "Latest updates" model: day grouping, initials, truncation and the
 * collapsing of repeated rows. Pure functions, kept out of the component so
 * they can be tested without rendering and so the component file exports
 * only a component (React Fast Refresh).
 */
const DAY_MS = 86_400_000;

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** "Today" / "Yesterday" / "Monday, 28 Sep" — the group heading for a row. */
export const dayLabel = (iso, now = new Date()) => {
  if (!iso) return 'Earlier';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Earlier';
  const diff = Math.round((startOfDay(now) - startOfDay(d)) / DAY_MS);
  if (diff <= 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' });
};

/**
 * Words a notification can open with that are not a person's name, so "Task
 * assigned" never becomes a "T" avatar.
 */
const NON_NAMES = /^(Task|Memo|Minute|Circular|Leave|Asset|Your|You|New|The|A|An|Reminder|Subtask|Attendance|Appraisal|Draft|Request|Punch|Please|Welcome)$/i;

/**
 * Initials from the leading person's name in a notification ("Bibek Demo
 * completed …" → "BD"). Null when neither text opens with a name, so the row
 * falls back to a bell rather than guessing.
 */
export const initialsFrom = (...texts) => {
  for (const text of texts) {
    const m = String(text || '').trim()
      .match(/^([A-Z][\p{L}'.-]*(?:\s+[A-Z][\p{L}'.-]*){0,2})\s+[a-z]/u);
    if (!m) continue;
    const words = m[1].split(/\s+/);
    if (NON_NAMES.test(words[0])) continue;
    const first = words[0][0];
    const last = words.length > 1 ? words[words.length - 1][0] : '';
    return (first + last).toUpperCase();
  }
  return null;
};

export const truncate = (text, max = 80) => {
  const s = String(text || '').trim();
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1).trimEnd()}…`;
};

/**
 * Collapse consecutive rows about the same thing into one (shown as "×N").
 * Same category AND same object_id when the payload carries one; otherwise
 * same category and same title.
 */
export const collapseRows = (rows = []) => {
  const out = [];
  for (const n of rows) {
    const prev = out[out.length - 1];
    const same = prev && prev.category === n.category && (
      (n.object_id != null && prev.object_id != null)
        ? String(prev.object_id) === String(n.object_id)
        : prev.title === n.title
    );
    if (same) { prev.count += 1; continue; }
    out.push({ ...n, count: 1 });
  }
  return out;
};

/** Collapsed rows, grouped by day in order of first appearance. */
export const groupByDay = (rows = [], now = new Date()) => {
  const groups = [];
  for (const row of rows) {
    const label = dayLabel(row.created_at, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push(row);
    else groups.push({ label, rows: [row] });
  }
  return groups;
};
