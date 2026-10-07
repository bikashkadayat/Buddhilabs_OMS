/**
 * Turn a DRF error body into one sentence the memo author can act on.
 *
 * WHY THIS EXISTS
 * ---------------
 * Both memo mutations used to report failures as
 * `e.response.data.detail || 'Could not submit the memo for approval.'`.
 * A field error has NO `detail` key, so every field failure collapsed into the
 * generic sentence. The real body was already precise:
 *
 *   {"sections":[{},{},{"title":["This field may not be blank."]}]}
 *
 * and the author was told none of it. A server log from one session showed 52
 * memos created against 44 section rejections — the same person resubmitting a
 * memo they could not see the fault in, orphaning a draft on every attempt.
 *
 * The index matters as much as the message: "a title is required" is not
 * actionable on a memo with five blocks. Positional errors are therefore
 * reported as "Section 3 title is required."
 */

// The server's phrasing for an omitted value, restated as an instruction.
const BLANK = /may not be blank|may not be null|is required|This field is required/i;

const firstString = (value) => {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = firstString(entry);
      if (found) return found;
    }
  }
  return '';
};

/** "Section 3 title is required." from the positional `sections` error array. */
const describeSections = (rows) => {
  if (!Array.isArray(rows)) return firstString(rows);
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i];
    if (!row || typeof row !== 'object' || Array.isArray(row)) {
      const flat = firstString(row);
      if (flat) return `Section ${i + 1}: ${flat}`;
      continue;
    }
    const [field, messages] = Object.entries(row)[0] || [];
    if (!field) continue;
    const message = firstString(messages);
    const label = field === 'body' ? 'description' : field;
    return BLANK.test(message)
      ? `Section ${i + 1} ${label} is required.`
      : `Section ${i + 1} ${label}: ${message}`;
  }
  return '';
};

const LABELS = {
  subject: 'Subject', to_line: 'To', memo_type: 'Memo type',
  reference_number: 'Reference Memo', files: 'Attachment', attachment: 'Attachment',
};

/**
 * @param {*} payload  `error.response.data`
 * @param {string} fallback  used only when the body carries nothing readable
 * @returns {string}
 */
export const describeMemoError = (payload, fallback = 'Could not save the memo.') => {
  if (!payload) return fallback;
  // A string body is the server's sentence -- unless it is an error PAGE.
  // A 502 from the proxy or a Django debug page arrives as HTML, and was shown
  // to people as a screenful of markup.
  if (typeof payload === 'string') {
    const text = payload.trim();
    if (!text || text.length > 300 || /<\s*(html|!doctype|body|head|div)\b/i.test(text)) {
      return fallback;
    }
    return text;
  }

  if (payload.sections) {
    const message = describeSections(payload.sections);
    if (message) return message;
  }
  // `detail` is the server's own sentence — it outranks a generic field dump,
  // but NOT the positional section message above, which is more specific.
  const detail = firstString(payload.detail);
  if (detail) return detail;

  for (const [field, value] of Object.entries(payload)) {
    if (field === 'sections' || field === 'detail') continue;
    const message = firstString(value);
    if (!message) continue;
    const label = LABELS[field];
    if (!label) return message;
    return BLANK.test(message) ? `${label} is required.` : `${label}: ${message}`;
  }
  return fallback;
};

export default describeMemoError;
