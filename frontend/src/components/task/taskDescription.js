/**
 * The task description as HTML (Phase TASK-SUBTASKS).
 *
 * Kept out of TaskForm.jsx so the page exports only its component (react-refresh
 * needs that to hot-reload it) and so the detail page's tests can use the same
 * conversion the form does.
 */

/** What a new task's description starts as: the questions a brief answers. */
export const STARTER_HTML = '<h2>Task Goal</h2><p></p><h2>Expected Deliverables</h2><p></p>'
  + '<h2>Success Criteria</h2><p></p><h2>Risks</h2><p></p><h2>Dependencies</h2><p></p>'
  + '<h2>Notes</h2><p></p>';

const escapeHtml = (text) => String(text)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

/**
 * A plain-text description, as paragraphs the editor can hold. Blank lines
 * separate paragraphs; a single newline inside one becomes a line break, so
 * the author's layout survives the conversion.
 */
export const textToHtml = (text) => {
  const value = String(text || '').replace(/\r\n?/g, '\n').trim();
  if (!value) return '';
  return value.split(/\n\s*\n/)
    .map((block) => `<p>${escapeHtml(block.trim()).replace(/\n/g, '<br>')}</p>`)
    .join('');
};

/** The text of some HTML, for comparing two copies and for an empty check. */
export const textOf = (html) => String(html || '')
  .replace(/<[^>]*>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();

/** An editor emptied by its author still reports `<p></p>`; that is empty. */
export const isBlankHtml = (html) => !/<(img|table|hr)\b/i.test(html || '')
  && textOf(html) === '';
