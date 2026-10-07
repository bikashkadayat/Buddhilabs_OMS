/**
 * How long a remark is, counted the way the SERVER counts it
 * (Phase MEMO-QUEUE-UX-HARDENING).
 *
 * The memo workflow checks `len(remarks.strip()) >= MIN_COMMENT_LENGTH` in Python,
 * and Python's `len` counts Unicode code points. JavaScript's `.length` counts
 * UTF-16 units, which differ for anything outside the Basic Multilingual Plane -
 * an emoji is ONE code point and TWO units. So "👍👍👍👍👍" read as 10 characters
 * in the dialog, enabled the send button, and was refused by the server as 5:
 * exactly the "backend is the first place you see the error" case this phase
 * exists to close.
 *
 * Spreading a string iterates by code point, which matches Python. Devanagari,
 * including its combining vowel signs, is in the BMP and counts the same either
 * way, so Nepali remarks are unaffected.
 */
export const remarkLength = (text = '') => [...String(text).trim()].length;

/**
 * The validation state of a remark against a minimum, in one place so the
 * dialog's counter, its message and its send button can never disagree.
 */
export const remarkState = (text, min) => {
  const length = remarkLength(text);
  const required = Math.max(1, min || 1);
  return {
    length,
    min: required,
    missing: Math.max(0, required - length),
    empty: length === 0,
    valid: length >= required,
  };
};
