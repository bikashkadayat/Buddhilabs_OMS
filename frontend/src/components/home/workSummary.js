/**
 * The action-summary model behind Home's four summary cards.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The figures used to be rendered twice — a desktop tile row and a mobile card
 * grid — and the two lists drifted for a whole phase before anybody noticed.
 * The list lives here now, and ONE component (SummaryCards.jsx) renders it at
 * every width, so there is nothing left to drift.
 *
 * ZEROS ARE SHOWN. The previous zero-hiding rule (drop a card reading 0,
 * collapse to a single "all caught up" line) is gone: four cards in a fixed
 * row are a stable frame the eye learns, and a 0 in that frame is itself the
 * information. The cards never reflow as the counts change.
 *
 * Every number is a door: each card links to the list that produced it.
 *
 * TONES are the product's status hues (the --st-* tokens): the first card is
 * the one filled brand card; reviews are amber (a decision waits), due today
 * purple (pending the day), completed green.
 */

/**
 * @param {Object}  args
 * @param {Object}  args.counts     from useWorkQueue(): total, review, approval…
 * @param {Object}  args.dashboard  the /tasks/dashboard/ payload (may be absent)
 * @returns the four summary figures, in reading order.
 */
export const buildWorkSummary = ({ counts = {}, dashboard = null } = {}) => ([
  {
    key: 'attention',
    n: counts.total ?? 0,
    label: 'Needs attention',
    to: '/queue',
    tone: 'primary',
  },
  {
    key: 'reviews',
    // Reviews and approvals together: both are somebody else's work waiting
    // on this person's decision, and the queue's own chips split them only
    // because the filter needs to.
    n: (counts.review ?? 0) + (counts.approval ?? 0),
    label: 'Pending reviews',
    to: '/queue',
    tone: 'amber',
  },
  {
    key: 'due',
    n: dashboard?.due_today ?? 0,
    label: 'Due today',
    to: '/tasks/due-today',
    tone: 'purple',
  },
  {
    // "Completed", not "Completed this week": the dashboard's `completed` is
    // an all-time count and the endpoint carries no per-person weekly figure
    // (completion_trend is the organisation block, sent to HR and Admin only).
    // Labelling an all-time number "this week" would be a lie in a frame.
    key: 'completed',
    n: dashboard?.completed ?? 0,
    label: 'Completed',
    to: '/tasks/completed',
    tone: 'green',
  },
]);

export default buildWorkSummary;
