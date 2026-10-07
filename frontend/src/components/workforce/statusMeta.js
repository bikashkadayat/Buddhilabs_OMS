/**
 * The single source of truth for how a status is labelled and coloured.
 *
 * Kept in its own module rather than beside the badge components so the file
 * that exports components exports *only* components — otherwise React Fast
 * Refresh cannot hot-reload it, which the lint rule enforces.
 *
 * Phase 8 shipped WORK_FROM_HOME and three separate colour maps had to be
 * hunted down. One map means the next status is a one-line change.
 */
export const STATUS_META = {
  present: { label: 'Present', tone: 'ok' },
  late: { label: 'Late', tone: 'warn' },
  half_day: { label: 'Half Day', tone: 'warn' },
  absent: { label: 'Absent', tone: 'bad' },
  on_leave: { label: 'On Leave', tone: 'info' },
  holiday: { label: 'Holiday', tone: 'muted' },
  wfh: { label: 'Work From Home', tone: 'accent' },
  not_applicable: { label: 'N/A', tone: 'muted' },
};

/** Workflow status for correction and WFH requests. */
export const REQUEST_STATUS_META = {
  pending: { label: 'Pending — Dept Head', tone: 'warn' },
  manager_approved: { label: 'Pending — HR', tone: 'info' },
  hr_approved: { label: 'Applied', tone: 'ok' },
  approved: { label: 'Approved', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
};

/** A status the UI has never seen must still read legibly, not render blank. */
export const humanise = (value) =>
  value ? String(value).replace(/_/g, ' ') : '—';

export const statusLabel = (status) => STATUS_META[status]?.label || humanise(status);
export const statusTone = (status) => STATUS_META[status]?.tone || 'muted';
export const requestLabel = (status) =>
  REQUEST_STATUS_META[status]?.label || humanise(status);
export const requestTone = (status) => REQUEST_STATUS_META[status]?.tone || 'muted';
