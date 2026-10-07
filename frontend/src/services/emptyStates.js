/**
 * The empty-state vocabulary (Phase UI-PRODUCTION-V1).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * An audit found 219 distinct ways of saying nothing is here. Many are
 * legitimately different situations — "no assets assigned" is not "no goals
 * set". The problem is the ones that are the SAME situation in different words,
 * written months apart by whoever was in the file:
 *
 *   "Nothing is waiting on you"            (work queue, My Day)
 *   "Nothing is waiting on you right now." (team appraisals)
 *   "Nothing is waiting on your action."   (memo dashboard, memo scopes)
 *   "Nothing needs your action right now." (minute scopes, task scopes)
 *
 * Four sentences, one fact. A person meets several of them in a single session
 * and the product sounds like four products.
 *
 * So the shared situations live here and nowhere else. A module with a genuinely
 * specific empty state still writes its own sentence — this is a vocabulary, not
 * a straitjacket.
 *
 * RULES OF THE VOCABULARY
 *   - State the fact. Do not apologise, and do not congratulate.
 *   - No "right now" or "at the moment". Every empty state is about now; saying
 *     so adds length and no meaning.
 *   - "Nothing" for work that could have been waiting; "No" for things that are
 *     simply absent.
 */

export const EMPTY = {
  /** No item of any kind is waiting on this person. */
  nothingWaiting: 'Nothing is waiting on you.',
  /** Nothing awaits this person's review specifically. */
  nothingToReview: 'Nothing is waiting for your review.',
  /** Nothing awaits a review or a signature — the circular workflow's pair. */
  nothingToReviewOrSign: 'Nothing is waiting for your review or signature.',
  /** Nothing has passed its date. */
  nothingOverdue: 'Nothing is overdue.',
  /** The person is between appraisal cycles. */
  noAppraisalOpen: 'You have no appraisal open.',
  /** Nothing unfinished has been saved. */
  noDrafts: 'No drafts saved.',
  /** No tasks are assigned to this person. */
  noTasksAssigned: 'No tasks assigned to you.',
  /** Nothing awaits this person's approval. */
  noPendingApprovals: 'Nothing is waiting for your approval.',
  /** No equipment is signed out to this person. */
  noAssetsAssigned: 'No assets are assigned to you.',
  /** The page exists but this person may not see it. */
  noPermission: 'You do not have permission to view this page.',
};

export default EMPTY;
