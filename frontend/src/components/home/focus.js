/**
 * The "My focus today" model: how queue items are ordered, named and timed on
 * Home. Pure functions, so the component stays layout and the rules are
 * testable without rendering.
 */
import { KINDS, TYPES, isOverdue } from '../../services/workQueue';

const DAY_MS = 86_400_000;

const dueMs = (item) => {
  if (!item?.dueAt) return null;
  const t = new Date(item.dueAt).getTime();
  return Number.isNaN(t) ? null : t;
};

/**
 * Overdue first (the latest first), then due soonest, then the rest in the
 * order the queue already gave them (oldest first — see compareItems).
 */
export const focusOrder = (items = [], now = Date.now()) => items
  .map((item, index) => ({ item, index }))
  .sort((a, b) => {
    const da = dueMs(a.item);
    const db = dueMs(b.item);
    const oa = isOverdue(a.item, now);
    const ob = isOverdue(b.item, now);
    if (oa !== ob) return oa ? -1 : 1;
    if (da !== null && db !== null && da !== db) return da - db;
    if ((da === null) !== (db === null)) return da === null ? 1 : -1;
    return a.index - b.index;
  })
  .map(({ item }) => item);

const ACTION_VERB = {
  accept: 'Accept',
  acknowledge: 'Acknowledge',
  approve: 'Approve',
  review: 'Review',
  recommend: 'Review',
  support: 'Review',
};

/**
 * The one verb a row carries, from the item's inline action when it has one
 * and from what the item IS when it does not. The same word labels the chip
 * and the button, so a row never says "Review" above a button saying "Open".
 */
export const focusVerb = (item) => {
  const action = item?.actions?.find((a) => a.variant === 'primary') || item?.actions?.[0];
  if (action && ACTION_VERB[action.verb]) return ACTION_VERB[action.verb];
  if (item?.kind === KINDS.REVIEW) return 'Review';
  if (item?.kind === KINDS.APPROVAL) return 'Approve';
  if (item?.kind === KINDS.ACKNOWLEDGE) return 'Acknowledge';
  if (item?.type === TYPES.MINUTE) return 'Sign off';
  // A task that is the person's own work and already accepted: nothing to
  // decide, something to do.
  if (item?.type === TYPES.TASK) return 'Start';
  return 'Open';
};

/** The primary inline action for the verb button, if the item has one. */
export const focusAction = (item) => (
  item?.actions?.find((a) => a.variant === 'primary') || item?.actions?.[0] || null
);

/**
 * "Overdue 2 days" / "Due today" / "6 days left", from the due date; with no
 * due date, how long the item has been waiting.
 */
export const timeLeft = (item, now = Date.now()) => {
  const due = dueMs(item);
  if (due !== null) {
    if (due < now) {
      const days = Math.max(1, Math.round((now - due) / DAY_MS));
      return { label: `Overdue ${days} day${days === 1 ? '' : 's'}`, late: true };
    }
    const days = Math.round((due - now) / DAY_MS);
    if (days < 1) return { label: 'Due today', late: false };
    return { label: `${days} day${days === 1 ? '' : 's'} left`, late: false };
  }
  const created = item?.createdAt ? new Date(item.createdAt).getTime() : null;
  if (created === null || Number.isNaN(created)) return { label: '', late: false };
  const days = Math.round(Math.max(0, now - created) / DAY_MS);
  if (days < 1) return { label: 'Waiting since today', late: false };
  return { label: `Waiting ${days} day${days === 1 ? '' : 's'}`, late: false };
};

/**
 * Chip tone by verb, from the product's five-hue status set (the --st-*
 * tokens): a review, approval, acknowledgement or sign-off is a decision
 * about somebody else's work, so amber; accepting is pending, so purple;
 * starting your own work is blue; a plain "Open" is neutral. An overdue
 * row's chip is red whatever the verb — lateness outranks kind, which is
 * also how the row is ordered.
 */
export const verbTone = (verb, late = false) => (late ? 'red' : ({
  Review: 'amber',
  Approve: 'amber',
  Acknowledge: 'amber',
  'Sign off': 'amber',
  Accept: 'purple',
  Start: 'blue',
  Open: 'neutral',
}[verb] || 'neutral'));
