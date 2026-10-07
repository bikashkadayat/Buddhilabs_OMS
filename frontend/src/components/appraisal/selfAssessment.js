/**
 * The five self-assessment sections, stored in ONE text field.
 *
 * WHY NOT FIVE COLUMNS
 * --------------------
 * `Appraisal.self_assessment` is a single TextField and this phase does not
 * change the backend. Five columns would also be five migrations of somebody's
 * permanent record for what is, in the end, a writing prompt: the sections exist
 * so a person facing an empty box has somewhere to start, not because the
 * organisation needs to query "challenges" separately from "lessons learned".
 *
 * So the sections are a PRESENTATION of one document. They compose into
 * markdown headings, which means the stored value stays readable everywhere it
 * already appears — the record page, the PDF export, the audit log — instead of
 * turning into JSON that renders as punctuation in a printed appraisal.
 *
 * NOTHING IS EVER LOST
 * --------------------
 * Two rules make the round trip safe:
 *
 *  1. Only the five EXACT headings are structural. Any other `##` line is body
 *     text, so somebody writing markdown inside their answer does not shatter
 *     the document.
 *
 *  2. Text before the first recognised heading — a self-assessment written
 *     before this form existed, or edited elsewhere — is kept in `preamble` and
 *     written back out ahead of the sections. It is shown in the form as an
 *     editable block rather than silently dropped, because the one thing a form
 *     over somebody's own words must never do is eat them.
 */
export const SECTIONS = [
  { key: 'achievements', label: 'Achievements',
    help: 'What you delivered this cycle, in your own words.' },
  { key: 'challenges', label: 'Challenges',
    help: 'What got in the way, including things outside your control.' },
  { key: 'support', label: 'Support Needed',
    help: 'What would help you do this better.' },
  { key: 'future', label: 'Future Goals',
    help: 'Where you want to take your work next.' },
];

/**
 * Headings that are no longer offered but must still PARSE (Phase APM-UX).
 *
 * "Lessons Learned" and "Comments" were dropped: five prompts is a form, four
 * is a conversation, and "Support Needed" is the better question — it asks what
 * the organisation owes the person rather than what they got wrong.
 *
 * They stay in the parser because self-assessments written before this change
 * contain them, and a heading the parser stops recognising becomes body text
 * appended to whatever came before it. Recognised here, each keeps its own
 * block; the form renders any retired section that HAS content, read-only, so
 * nothing anybody wrote disappears from a record they may be asked about.
 */
export const RETIRED_SECTIONS = [
  { key: 'lessons', label: 'Lessons Learned' },
  { key: 'comments', label: 'Comments' },
];

/** Every section the parser understands, current and retired. */
export const ALL_SECTIONS = [...SECTIONS, ...RETIRED_SECTIONS];

/** Heading text -> section key. The only five lines treated as structure. */
const BY_HEADING = new Map(
  ALL_SECTIONS.map((s) => [s.label.toLowerCase(), s.key]));

const HEADING = /^##\s+(.+?)\s*$/;

/**
 * Split a stored self-assessment into its sections.
 *
 * Always returns every key, so a caller never has to guard for a missing one.
 * `preamble` holds anything written before the first recognised heading.
 */
export const parseSelfAssessment = (text) => {
  const out = { preamble: '' };
  for (const section of ALL_SECTIONS) out[section.key] = '';

  const lines = String(text || '').split('\n');
  let current = 'preamble';
  const buffers = { preamble: [] };
  for (const section of ALL_SECTIONS) buffers[section.key] = [];

  for (const line of lines) {
    const match = line.match(HEADING);
    const key = match && BY_HEADING.get(match[1].toLowerCase());
    if (key) {
      current = key;
      continue;
    }
    buffers[current].push(line);
  }

  for (const key of Object.keys(buffers)) {
    out[key] = buffers[key].join('\n').trim();
  }
  return out;
};

/**
 * Compose the sections back into one document.
 *
 * Empty sections are omitted rather than written as a bare heading with nothing
 * under it — a reviewer opening an appraisal should not have to work out
 * whether "## Challenges" with no text means "none" or "not written yet".
 * Returns '' when everything is empty, so an untouched form saves nothing.
 */
export const composeSelfAssessment = (values = {}) => {
  const parts = [];
  const preamble = (values.preamble || '').trim();
  if (preamble) parts.push(preamble);
  // Composed over ALL sections, retired ones included: a self-assessment that
  // already had "Lessons Learned" keeps it when the author edits something
  // else. Dropping it on save would delete somebody's words as a side effect of
  // a UI change they never asked for.
  for (const section of ALL_SECTIONS) {
    const body = (values[section.key] || '').trim();
    if (body) parts.push(`## ${section.label}\n${body}`);
  }
  return parts.join('\n\n');
};

/** Has anything been written at all? Drives the "nothing yet" empty state. */
export const isEmptySelfAssessment = (values = {}) =>
  composeSelfAssessment(values) === '';
