/**
 * The four supervisor feedback prompts, stored in ONE text field.
 *
 * Same technique as the self-assessment, and the same reason: this phase
 * changes no models, and `supervisor_comments` is a TextField that already
 * exists. The prompts compose into markdown headings, so the stored value stays
 * readable everywhere it already appears — the employee's record, the PDF, the
 * audit trail — rather than becoming JSON that prints as punctuation.
 *
 * WHY PROMPTS AT ALL
 * ------------------
 * It was one empty textarea. An empty box asks "say something about this
 * person's year" and reliably produces three lines about the last month. Four
 * specific questions produce the thing the employee can actually act on.
 *
 * "How We Can Help" is the one that changes the character of the review. The
 * other three describe the person; that one commits the organisation to
 * something, which is what turns a verdict into a conversation.
 *
 * NOTHING IS EVER LOST
 * --------------------
 * Only the four exact headings are structural — any other `##` line is body
 * text. Anything written before this form existed, or through the API, is kept
 * in `preamble` and written back out ahead of the sections, and shown in the
 * form as an editable block. A form over somebody's written review must never
 * eat what is already there.
 */
export const FEEDBACK_SECTIONS = [
  { key: 'strengths', label: 'Strengths',
    help: 'What they do well, with something specific.' },
  { key: 'improve', label: 'Areas To Improve',
    help: 'What would make the biggest difference.' },
  { key: 'overall', label: 'Overall Feedback',
    help: 'Your account of their year.' },
  { key: 'help', label: 'How We Can Help',
    help: 'What you and the organisation will do to support this.' },
];

const BY_HEADING = new Map(
  FEEDBACK_SECTIONS.map((s) => [s.label.toLowerCase(), s.key]));

const HEADING = /^##\s+(.+?)\s*$/;

/** Split stored supervisor comments into their sections. Always every key. */
export const parseFeedback = (text) => {
  const out = { preamble: '' };
  for (const section of FEEDBACK_SECTIONS) out[section.key] = '';

  const buffers = { preamble: [] };
  for (const section of FEEDBACK_SECTIONS) buffers[section.key] = [];

  let current = 'preamble';
  for (const line of String(text || '').split('\n')) {
    const match = line.match(HEADING);
    const key = match && BY_HEADING.get(match[1].toLowerCase());
    if (key) { current = key; continue; }
    buffers[current].push(line);
  }
  for (const key of Object.keys(buffers)) out[key] = buffers[key].join('\n').trim();
  return out;
};

/**
 * Compose back into one document. Empty sections are omitted rather than
 * written as a bare heading — an employee reading "## Strengths" with nothing
 * under it cannot tell whether their supervisor found none or simply had not
 * written it yet.
 */
export const composeFeedback = (values = {}) => {
  const parts = [];
  const preamble = (values.preamble || '').trim();
  if (preamble) parts.push(preamble);
  for (const section of FEEDBACK_SECTIONS) {
    const body = (values[section.key] || '').trim();
    if (body) parts.push(`## ${section.label}\n${body}`);
  }
  return parts.join('\n\n');
};
