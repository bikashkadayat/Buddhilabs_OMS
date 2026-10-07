/**
 * Minute label helpers and tone maps.
 *
 * A plain module, not a component file: a component module that also exports helpers
 * trips `react-refresh/only-export-components`, which is an eslint ERROR in this
 * project and therefore CI-breaking. The memo module learned that twice.
 *
 * Nothing here hardcodes the TAXONOMY (the minute types) — that arrives from
 * /minutes/taxonomy/ because it lives in the database. What is here is presentation
 * for the state machine, which is code either way.
 */

/** The manual's four states (E-minute-manual pp. 6-10). */
export const MINUTE_STATUS_TONES = {
  draft: 'muted',
  draft_for_review: 'info',
  pending_acknowledgement: 'wait',
  archived: 'ok',
};

/**
 * Attendance is the grouping the manual's form uses: Members Present, Members Absent,
 * Invitee Members (p.4). Only the present are asked to acknowledge.
 */
export const ATTENDANCE_TONES = {
  present: 'ok',
  absent: 'no',
  invitee: 'info',
};

export const ATTENDANCE_LABELS = {
  present: 'Members Present',
  absent: 'Members Absent',
  invitee: 'Invitee Members',
};

export const ACK_TONES = {
  acknowledged: 'ok',
  pending: 'wait',
  not_required: 'muted',
};

/**
 * Percent of members present who have acknowledged, for the progress bar. Derived from
 * the server's summary rather than recomputed, so the bar and the tally cannot
 * disagree.
 */
export const ackPercent = (summary) => (summary?.total ? summary.percent : 0);

/** How long a minute has been sitting where it is, in words. */
export const sinceLabel = (iso) => {
  if (!iso) return '—';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return 'Today';
  return `${days} day${days === 1 ? '' : 's'}`;
};
