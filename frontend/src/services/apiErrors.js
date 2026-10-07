import { describeMemoError } from './memoErrors';

/**
 * One readable sentence for a failed API call - never axios's own text
 * (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE).
 *
 * WHY THIS EXISTS
 * ---------------
 * The work queue reported failures as
 *
 *     error.response.data.detail || error.message
 *
 * A DRF field error has no `detail` key. The memo action endpoint answers a bad
 * request with exactly such a body -
 *
 *     {"remarks": "A comment of at least 10 characters is required for the Reviewer step."}
 *     {"decision": ["\"approve\" is not a valid choice."]}
 *
 * - so the expression fell through to `error.message`, which for any HTTP error
 * is axios's "Request failed with status code 400". The server had written a
 * precise, actionable sentence and the person was shown a status code.
 *
 * THE RULE: when the server answered, its body is the message. `error.message`
 * is used only when there was no response at all, and even then it is replaced
 * by a sentence a person can act on.
 *
 * @param {*} error     what the rejected promise carried
 * @param {string} fallback  used when the body carries nothing readable
 * @returns {string}
 */
export const describeApiError = (error, fallback = 'That didn’t go through. Please try again.') => {
  const response = error?.response;
  if (response) {
    // Reuses the memo describer: it already reads `detail`, `non_field_errors`
    // and the first field error in DRF's shapes, and returns the server's own
    // sentence rather than a field dump.
    const message = describeMemoError(response.data, '');
    if (message) return message;
    if (response.status === 403) return 'You don’t have access to do this.';
    if (response.status === 404) return 'This item no longer exists. It may have been deleted.';
    if (response.status === 429) return 'Too many attempts. Please wait a minute and try again.';
    if (response.status >= 500) return 'Something went wrong on our side. Please try again in a moment.';
    return fallback;
  }
  if (error?.request) return 'We can’t reach the server. Check your internet connection and try again.';
  return fallback;
};

export default describeApiError;
