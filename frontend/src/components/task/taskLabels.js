/**
 * Shared task vocabulary: status tones, priority tones, and date helpers.
 *
 * One file so the badge on a list, the badge on the detail page and the badge in
 * a tile can never disagree about what "Under Review" looks like. The tone names
 * are the ones `.min-status` already defines in index.css — reused rather than
 * duplicated with new colour values that would drift from them.
 *
 * The tones read from the product's five-hue status set (the --st-* tokens):
 * wait = purple (pending: a draft, something awaiting), info = blue (assigned,
 * accepted, in progress), warn = amber (under review), ok = green (completed,
 * closed), no = red (blocked). Home's chips and dots use the same hues.
 */

/** Server status value -> `.min-status` tone class. */
export const TASK_STATUS_TONES = {
  draft: 'wait',
  assigned: 'info',
  accepted: 'info',
  in_progress: 'info',
  under_review: 'warn',
  completed: 'ok',
  closed: 'ok',
  blocked: 'no',
  cancelled: 'muted',
};

/** The ladder, in order, for the progress rail on the detail page. */
/**
 * The five stages a task passes through (Phase TASK-SIMPLIFICATION):
 *
 *   Created -> In Progress -> Submitted -> Approved -> Completed
 *
 * The STORED statuses are unchanged - nine of them, and every historical row
 * keeps the one it was saved with. This is the reading of them, and the mapping
 * lives here so the ladder, the board and the card all say the same thing:
 *
 *   Created     draft, assigned, accepted   (written, not started)
 *   In Progress in_progress                 (being done; Blocked sits outside)
 *   Submitted   under_review                (with its reviewer)
 *   Approved    completed                   (the reviewer said yes)
 *   Completed   closed                      (verified and filed)
 *
 * Collapsing three stored statuses into "Created" is deliberate: the difference
 * between a draft and an assigned task is a detail of this system, not a state
 * of the work, and a person reading a task wants the second.
 */
export const TASK_LADDER = [
  { value: 'assigned', label: 'Created', covers: ['draft', 'assigned', 'accepted'] },
  { value: 'in_progress', label: 'In Progress', covers: ['in_progress'] },
  { value: 'under_review', label: 'Submitted', covers: ['under_review'] },
  { value: 'completed', label: 'Approved', covers: ['completed'] },
  { value: 'closed', label: 'Completed', covers: ['closed'] },
];

/** Stored status -> the stage it reads as. */
export const LADDER_STAGE = Object.fromEntries(
  TASK_LADDER.flatMap((stage) => (stage.covers || [stage.value])
    .map((status) => [status, stage.value])),
);

/** Blocked and Cancelled sit outside the ladder; the rail must not render them. */
export const OFF_LADDER = new Set(['blocked', 'cancelled']);

/**
 * The Status FILTER options.
 *
 * Same simplified stage names as the ladder, with ONE deliberate difference:
 * under_review reads as "Under Review" here, not the ladder's "Submitted". A
 * person filtering for the review queue looks for the word they see on every
 * card badge and on the Review board column — "Under Review" — so the filter
 * must offer that exact word. The stored value is unchanged (`under_review`),
 * so the query the server runs is identical; only the label differs.
 */
export const TASK_STATUSES = [
  { value: 'assigned', label: 'Created' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'under_review', label: 'Under Review' },
  { value: 'completed', label: 'Approved' },
  { value: 'closed', label: 'Completed' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'cancelled', label: 'Cancelled' },
];

export const TASK_PRIORITIES = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'urgent', label: 'Urgent' },
];

export const PRIORITY_TONES = {
  low: 'muted',
  medium: 'info',
  high: 'warn',
  urgent: 'no',
};

export const fmtDate = (iso) => (iso
  ? new Date(iso).toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: '2-digit' })
  : '—');

export const fmtDateTime = (iso) => (iso
  ? new Date(iso).toLocaleString(undefined,
    { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  : '—');

/**
 * "Due in 3 days" / "3 days overdue" / "Due today".
 *
 * Compared on calendar days rather than on elapsed hours: a task due today at
 * 09:00 read at 17:00 is due TODAY, not "9 hours overdue", and phrasing it the
 * second way is how a list starts shouting at people about nothing.
 */
export const dueLabel = (isoDate) => {
  if (!isoDate) return '—';
  const due = new Date(`${isoDate}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((due - today) / 86_400_000);
  if (days === 0) return 'Due today';
  if (days === 1) return 'Due tomorrow';
  if (days > 1) return `Due in ${days} days`;
  if (days === -1) return '1 day overdue';
  return `${Math.abs(days)} days overdue`;
};

export const statusTone = (status) => TASK_STATUS_TONES[status] || 'muted';
export const priorityTone = (priority) => PRIORITY_TONES[priority] || 'muted';
