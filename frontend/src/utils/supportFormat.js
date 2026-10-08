/** Shared bits for the Support Center pages. */
export const STATUS_TONE = {
  open: 'pf-chip-wait', in_progress: 'pf-chip-wait', waiting_customer: 'pf-chip-warn',
  resolved: 'pf-chip-ok', closed: 'pf-chip-mute',
};

export const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
}) : '');

/** The ten ticket types, keyed as the server stores them. */
export const TICKET_CATEGORIES = [
  ['bug', 'Bug', 'Something is broken.'],
  ['technical', 'Technical issue', 'Slow, errors, won’t load.'],
  ['attendance', 'Attendance', 'Check-in, devices, records.'],
  ['leave', 'Leave', 'Applying, approvals, balances.'],
  ['task', 'Tasks', 'Creating, reviewing, assigning.'],
  ['billing', 'Billing', 'Plans, payments, receipts.'],
  ['domain', 'Domain', 'Your own web address.'],
  ['login', 'Login', 'Can’t sign in, passwords.'],
  ['feature_request', 'Feature request', 'Something we should build.'],
  ['other', 'Other', 'Anything else.'],
];
