import { describe, it, expect } from 'vitest';
import { describeMemoError } from './memoErrors';

describe('describeMemoError', () => {
  it('names the section and the field for a blank title', () => {
    // The exact body captured from the live server when "+ Add more" left a
    // blank heading — the failure that was being reported as
    // "Could not submit the memo for approval."
    const payload = { sections: [{}, {}, { title: ['This field may not be blank.'] }] };
    expect(describeMemoError(payload)).toBe('Section 3 title is required.');
  });

  it('passes through a non-blank section message verbatim', () => {
    const payload = { sections: [{ title: ['Ensure this field has no more than 150 characters.'] }] };
    expect(describeMemoError(payload))
      .toBe('Section 1 title: Ensure this field has no more than 150 characters.');
  });

  it('prefers the positional section error over a generic detail', () => {
    const payload = { detail: 'Invalid input.', sections: [{ title: ['This field may not be blank.'] }] };
    expect(describeMemoError(payload)).toBe('Section 1 title is required.');
  });

  it('reports an attachment rejection', () => {
    expect(describeMemoError({ files: ['File is larger than 10 MB.'] }))
      .toBe('Attachment: File is larger than 10 MB.');
  });

  it('labels a named header field', () => {
    expect(describeMemoError({ subject: ['This field may not be blank.'] }))
      .toBe('Subject is required.');
  });

  it('uses detail when there is no field error', () => {
    expect(describeMemoError({ detail: 'This memo is locked.' })).toBe('This memo is locked.');
  });

  it('falls back only when the body says nothing', () => {
    expect(describeMemoError({}, 'Could not save the memo.')).toBe('Could not save the memo.');
    expect(describeMemoError(null, 'Could not save the memo.')).toBe('Could not save the memo.');
  });
});
