/**
 * Phase APM-03b — the self-assessment round trip.
 *
 * These matter more than they look. The value being parsed is somebody's own
 * account of their year, written once, read in a meeting about them. A parser
 * that drops a section on an unexpected input loses work nobody can reconstruct,
 * so the properties pinned here are all about NOT LOSING TEXT.
 */
import { describe, it, expect } from 'vitest';
import {
  ALL_SECTIONS, RETIRED_SECTIONS, SECTIONS, composeSelfAssessment,
  isEmptySelfAssessment, parseSelfAssessment,
} from './selfAssessment';

const FULL = {
  preamble: '',
  achievements: 'Closed the year-end return on time.',
  challenges: 'The archive migration slipped twice.',
  support: 'An earlier data extract from Finance.',
  future: 'Own the quarterly close end to end.',
  lessons: '',
  comments: '',
};

describe('the round trip', () => {
  it('composes and parses back to exactly what was written', () => {
    expect(parseSelfAssessment(composeSelfAssessment(FULL))).toEqual(FULL);
  });

  it('keeps the sections in a fixed order however they were filled in', () => {
    const text = composeSelfAssessment(FULL);
    const order = SECTIONS.map((s) => text.indexOf(`## ${s.label}`));
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it('offers the four questions the specification names', () => {
    expect(SECTIONS.map((s) => s.label)).toEqual([
      'Achievements', 'Challenges', 'Support Needed', 'Future Goals']);
  });

  it('omits an empty section rather than writing a bare heading', () => {
    // "## Challenges" with nothing under it leaves a reviewer unable to tell
    // "none" from "not written yet".
    const text = composeSelfAssessment({ ...FULL, challenges: '' });
    expect(text).not.toContain('## Challenges');
    expect(text).toContain('## Achievements');
  });

  it('saves nothing at all for an untouched form', () => {
    expect(composeSelfAssessment({})).toBe('');
    expect(isEmptySelfAssessment({})).toBe(true);
    expect(isEmptySelfAssessment(FULL)).toBe(false);
  });
});

describe('text written before this form existed', () => {
  it('keeps it, rather than dropping it on the floor', () => {
    // A self-assessment written against the plain textarea, or edited through
    // the API. It has no headings and must survive being opened here.
    const legacy = 'I delivered both objectives and helped with the audit.';
    const parsed = parseSelfAssessment(legacy);
    expect(parsed.preamble).toBe(legacy);
    expect(parsed.achievements).toBe('');
  });

  it('writes it back out ahead of the sections', () => {
    const parsed = parseSelfAssessment('Some earlier words.');
    const text = composeSelfAssessment({ ...parsed, achievements: 'New.' });
    expect(text.indexOf('Some earlier words.'))
      .toBeLessThan(text.indexOf('## Achievements'));
    expect(parseSelfAssessment(text).preamble).toBe('Some earlier words.');
  });
});

describe('markdown a person might actually type', () => {
  it('treats an unrecognised heading as body text, not structure', () => {
    // Somebody writing "## Q3" inside their achievements must not shatter the
    // document into sections nobody asked for.
    const parsed = parseSelfAssessment(
      '## Achievements\n## Q3\nShipped the migration.');
    expect(parsed.achievements).toBe('## Q3\nShipped the migration.');
    expect(parsed.challenges).toBe('');
  });

  it('matches a heading regardless of its capitalisation', () => {
    const parsed = parseSelfAssessment('## lessons learned\nAsk earlier.');
    expect(parsed.lessons).toBe('Ask earlier.');
  });

  it('keeps blank lines and paragraphs inside a section', () => {
    const body = 'First paragraph.\n\nSecond paragraph.';
    const parsed = parseSelfAssessment(`## Achievements\n${body}`);
    expect(parsed.achievements).toBe(body);
  });

  it('never loses a word, whatever the input', () => {
    // The property that matters: every non-heading line survives somewhere.
    const messy = 'Preamble line.\n## Achievements\nDid a thing.\n'
      + '## Not A Section\nStill mine.\n## Comments\nThank you.';
    const parsed = parseSelfAssessment(messy);
    const recovered = composeSelfAssessment(parsed);
    for (const line of ['Preamble line.', 'Did a thing.', '## Not A Section',
      'Still mine.', 'Thank you.']) {
      expect(recovered, `lost: ${line}`).toContain(line);
    }
  });

  it('returns every key for an empty input, so no caller has to guard', () => {
    const parsed = parseSelfAssessment(null);
    expect(Object.keys(parsed).sort())
      .toEqual(['preamble', ...ALL_SECTIONS.map((s) => s.key)].sort());
  });
});


describe('sections this form no longer offers', () => {
  it('still parses a heading that was retired', () => {
    // A self-assessment written before the form dropped these headings must not
    // have them silently swallowed into whatever section came before.
    const parsed = parseSelfAssessment(
      '## Achievements\nShipped it.\n\n## Lessons Learned\nAsk earlier.');
    expect(parsed.achievements).toBe('Shipped it.');
    expect(parsed.lessons).toBe('Ask earlier.');
  });

  it('keeps retired content when the author edits something else', () => {
    // Dropping it on save would delete somebody's words as a side effect of a
    // UI change they never asked for.
    const parsed = parseSelfAssessment('## Comments\nThank you all.');
    const saved = composeSelfAssessment({
      ...parsed, achievements: 'Something new.' });
    expect(saved).toContain('## Comments');
    expect(saved).toContain('Thank you all.');
    expect(parseSelfAssessment(saved).comments).toBe('Thank you all.');
  });

  it('names exactly the two that were retired', () => {
    expect(RETIRED_SECTIONS.map((s) => s.label))
      .toEqual(['Lessons Learned', 'Comments']);
  });
});
