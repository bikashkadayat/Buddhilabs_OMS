/**
 * Label maps and colour pairs for the memo module.
 *
 * These live outside badges.jsx because a module that exports both components
 * and plain values breaks React Fast Refresh (and the project's lint gate
 * enforces that split). Keeping them here also means non-badge callers — the
 * filter dropdowns, the matrix editor's role <select> — can read the labels
 * without importing components they do not render.
 *
 * Each entry is [background, foreground]; every badge pairs its colour with a
 * text label so status is never carried by colour alone.
 */

// Memo workflow statuses, in progression order. Grey while unstarted, blue once
// moving, amber while a decision is outstanding, green at approval, slate filed.
export const STATUS_COLORS = {
  draft: ['#e5e7eb', '#374151'],
  draft_for_review: ['#e0e7ff', '#3730a3'],
  under_review: ['#fef3c7', '#92400e'],
  recommended: ['#cffafe', '#155e75'],
  supported: ['#ccfbf1', '#115e59'],
  approved: ['#d1fae5', '#065f46'],
  archived: ['#e2e8f0', '#334155'],
  rejected: ['#fee2e2', '#991b1b'],
  cancelled: ['#d1d5db', '#1f2937'],
};

export const STATUS_LABELS = {
  draft: 'Draft',
  draft_for_review: 'Draft For Review',
  under_review: 'Under Review',
  recommended: 'Recommended',
  supported: 'Supported',
  approved: 'Approved',
  archived: 'Archived',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
};

/**
 * Statuses offered in the list filters, in workflow order.
 *
 * `cancelled` is included at the end: a withdrawn memo is a real terminal state a
 * user may need to find, even though it sits outside the approval ladder. The
 * legacy `submitted` status is gone - migration 0010 converted every memo that
 * carried it to `draft_for_review`.
 */
export const FILTERABLE_STATUSES = [
  'draft', 'draft_for_review', 'under_review', 'recommended',
  'supported', 'approved', 'archived', 'rejected', 'cancelled',
];

/**
 * SLA state for an inbox row -> the colour it reads in. Green/amber/red, always
 * alongside the "Due Days" number, so the state is never carried by colour alone.
 * The state itself is computed server-side (memos.workflow.step_ageing).
 */
export const AGEING_TONES = {
  on_track: { tone: 'ok', label: 'On track' },
  due_soon: { tone: 'warn', label: 'Due today' },
  overdue: { tone: 'bad', label: 'Overdue' },
};

export const dueDaysLabel = (ageing) => {
  if (!ageing || ageing.due_days === null || ageing.due_days === undefined) return '—';
  const { due_days: due } = ageing;
  if (due > 0) return `${due} day${due === 1 ? '' : 's'} left`;
  if (due === 0) return 'Due today';
  return `${Math.abs(due)} day${Math.abs(due) === 1 ? '' : 's'} overdue`;
};

export const pendingSinceLabel = (ageing) => {
  if (!ageing?.pending_since) return '—';
  const days = ageing.pending_days ?? 0;
  if (days === 0) return 'Today';
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

export const memoStatusLabel = (status) => STATUS_LABELS[status] || status || '—';

// Approval-matrix role types (Phase 4).
export const ROLE_COLORS = {
  reviewer: ['#e0e7ff', '#3730a3'],
  recommender: ['#cffafe', '#155e75'],
  supporter: ['#ccfbf1', '#115e59'],
  approver: ['#d1fae5', '#065f46'],
};

export const ROLE_LABELS = {
  reviewer: 'Reviewer',
  recommender: 'Recommender',
  supporter: 'Supporter',
  approver: 'Approver',
};

/** Selectable role types, in the order the workflow runs them. */
export const ROLE_TYPES = ['reviewer', 'recommender', 'supporter', 'approver'];

export const roleTypeLabel = (role) => ROLE_LABELS[role] || role || '—';

/**
 * WHAT EACH ROLE DOES, in the words its button should use.
 *
 * ROLE_LABELS above names the ROLE; this names the ACTION, and they are not
 * interchangeable. The action button used to render the role label for
 * everyone except an approver, so a reviewer was offered a button that simply
 * said "Reviewer" beside one that said "Reject" — a noun and a verb, and
 * nothing on screen saying the noun was how you passed the memo on. Three of
 * the four roles hit that, and a memo sitting at one of them looks exactly
 * like a workflow that has stopped.
 *
 * `title` is the heading for the step, `instruction` says what to do before
 * pressing it, and `action` is the button. Keep `action` a verb.
 */
export const ROLE_ACTIONS = {
  reviewer: {
    title: 'Review required',
    instruction: 'Read the memo and open the attached files, then confirm you have reviewed it.',
    action: 'Mark as Reviewed',
    done: 'reviewed',
  },
  recommender: {
    title: 'Recommendation required',
    instruction: 'Check the memo and its attachments, then recommend it to the next step.',
    action: 'Recommend',
    done: 'recommended',
  },
  supporter: {
    title: 'Support required',
    instruction: 'Confirm you support this memo so it can go to the approver.',
    action: 'Support',
    done: 'supported',
  },
  approver: {
    title: 'Approval required',
    instruction: 'You are the final step. Approving issues the memo and archives it.',
    action: 'Approve',
    done: 'approved',
  },
};

/** The verb for a role's button. Falls back to a safe generic, never a noun. */
export const roleAction = (role) => ROLE_ACTIONS[role]?.action || 'Confirm';
export const roleGuidance = (role) => ROLE_ACTIONS[role] || {
  title: 'Action required',
  instruction: 'Review the memo and its attachments, then confirm.',
  action: 'Confirm',
  done: 'completed',
};

// Per-step status inside the matrix.
export const STEP_COLORS = {
  pending: ['#f1f5f9', '#475569'],
  active: ['#fef3c7', '#92400e'],
  completed: ['#d1fae5', '#065f46'],
  rejected: ['#fee2e2', '#991b1b'],
  skipped: ['#e5e7eb', '#6b7280'],
};

export const STEP_LABELS = {
  pending: 'Pending',
  active: 'Awaiting Action',
  completed: 'Completed',
  rejected: 'Rejected',
  skipped: 'Skipped',
};

export const PRIORITY_COLORS = {
  low: ['#f1f5f9', '#475569'],
  normal: ['#dbeafe', '#1e40af'],
  high: ['#ffedd5', '#9a3412'],
  urgent: ['#fee2e2', '#991b1b'],
};

/**
 * The manual's one Memo Type dropdown (E-memo-manual p.4): GENERAL, CONFIDENTIAL,
 * DRAFT.
 *
 * This replaced two vocabularies. `memo_type` used to carry a business category
 * (internal / external / financial / hr / general) and a separate `classification`
 * carried sensitivity; the manual has a single dropdown, so sensitivity moved into
 * the type and the business category was retired.
 *
 * CONFIDENTIAL is the one solid-filled badge in the module. That is deliberate —
 * it is the marking that should catch the eye before the reader has finished
 * scanning the row.
 */
export const TYPE_COLORS = {
  general: ['#f1f5f9', '#334155'],
  confidential: ['#7f1d1d', '#ffffff'],
  draft: ['#ffedd5', '#9a3412'],
};

export const TYPE_LABELS = {
  general: 'GENERAL',
  confidential: 'CONFIDENTIAL',
  draft: 'DRAFT',
};

export const MEMO_TYPES = [
  { code: 'general', label: 'GENERAL' },
  { code: 'confidential', label: 'CONFIDENTIAL' },
  { code: 'draft', label: 'DRAFT' },
];

export const memoTypeLabel = (value) => TYPE_LABELS[value] || value || '—';

/** Types that narrow who may read the memo. Mirrors the backend. */
export const RESTRICTED_TYPES = ['confidential'];

/**
 * The manual's "Select Unavailable type" dropdown, verbatim and in its order
 * (p.9). The stamp printed on an unavailable approver's block reads back this
 * label, so a wrong label is a wrong document.
 */
export const UNAVAILABILITY_REASONS = [
  { code: 'field_site_visit', label: 'Field/Site Visit' },
  { code: 'on_leave', label: 'On Leave' },
  { code: 'branch_visit', label: 'Branch Visit' },
  { code: 'on_training', label: 'On Training' },
  { code: 'on_conference_meeting', label: 'On Conference/Meeting' },
];

/* --- approval block layout (Phase 30) ------------------------------------- */
/**
 * The widest a certification row gets before it is worth wrapping, and the point
 * below which there is still room for the department line. Mirrors
 * MAX_BLOCKS_PER_ROW / DEPARTMENT_FITS_UP_TO in memos/workflow.py.
 */
export const MAX_BLOCKS_PER_ROW = 4;
export const DEPARTMENT_FITS_UP_TO = 3;

/**
 * Columns for `count` approval blocks — three steps across in one row, four across
 * in one row, five or more wrapped so no row is left holding a lone block.
 *
 * Mirrors workflow.signature_rows() on the server, which owns the same rule for the
 * PDF. Duplicated deliberately rather than served in the payload: the API returns a
 * flat list of signatures, and shipping a second pre-grouped copy of the same blocks
 * would mean two representations of one thing travelling together. The shapes are
 * asserted against the same expected values as the backend's tests.
 *
 * CSS grid fills rows greedily, so for up to nine blocks the visual rows match the
 * PDF exactly. At ten or more the two can differ by one block on the final row — a
 * chain that long is well past anything the approval matrix produces in practice.
 *
 * Lives here rather than in SignatureCards.jsx because a component module that also
 * exports a plain function breaks react-refresh, which is an eslint ERROR in this
 * project and therefore CI-breaking.
 */
export const columnsFor = (count) => {
  if (count <= MAX_BLOCKS_PER_ROW) return Math.max(count, 1);
  return Math.ceil(count / Math.ceil(count / MAX_BLOCKS_PER_ROW));
};
