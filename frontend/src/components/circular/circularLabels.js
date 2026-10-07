/**
 * Presentation vocabulary for circulars.
 *
 * Tones only - every LABEL comes from the server's taxonomy endpoint, so adding a
 * status or a classification in the backend appears in the UI with no change here.
 * What cannot come from the server is which colour a status should read as, because
 * that is a design decision rather than data.
 */

// Which accent each status carries. `broadcasted` is the one deliberate choice
// worth explaining: it is 'ok' rather than 'info' because for a circular, reaching
// its audience IS the successful outcome - unlike a memo, where approval is.
export const CIRCULAR_STATUS_TONES = {
  draft: 'muted',
  under_review: 'info',
  ready_for_issue: 'warn',
  issued: 'info',
  ready_for_broadcast: 'warn',
  broadcasted: 'ok',
  archived: 'ok',
  rejected: 'no',
  cancelled: 'muted',
};

export const PRIORITY_TONES = {
  low: 'muted',
  normal: 'muted',
  high: 'warn',
  urgent: 'no',
};

// Acknowledgement states. `declined` is 'no' rather than 'muted' because a decline
// is a substantive response somebody has to read, not an absence of one.
export const ACK_TONES = {
  pending: 'warn',
  acknowledged: 'ok',
  declined: 'no',
};

export const READ_TONES = { read: 'ok', unread: 'muted' };
