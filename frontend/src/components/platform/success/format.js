/** Shared bits for the Customer Success console. */
export const BAND_TONE = { healthy: 'good', watch: 'warn', at_risk: 'bad', onboarding: 'info' };
export const money = (minor, currency = 'NPR') => (minor == null ? '—'
  : `${currency} ${(minor / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
export const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '—');
export const TASK_KINDS = [
  ['follow_up', 'Follow up'], ['call', 'Call customer'], ['meeting', 'Schedule meeting'],
  ['training', 'Product training'], ['onboarding_review', 'Onboarding review'],
  ['domain_setup', 'Domain setup'], ['payment_verification', 'Payment verification'],
  ['outreach', 'Customer outreach'],
];
