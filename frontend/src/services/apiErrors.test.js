import { describe, expect, it } from 'vitest';
import { describeApiError } from './apiErrors';

const http = (status, data) => ({ message: `Request failed with status code ${status}`, response: { status, data } });

describe('describeApiError', () => {
  it('shows the server sentence for the exact 400s the memo queue hit', () => {
    // Captured in the browser, verbatim (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE).
    expect(describeApiError(http(400, { decision: ['"approve" is not a valid choice.'] })))
      .toBe('"approve" is not a valid choice.');
    expect(describeApiError(http(400, {
      remarks: 'A comment of at least 10 characters is required for the Reviewer step.' })))
      .toBe('A comment of at least 10 characters is required for the Reviewer step.');
  });

  it('never returns axios\'s generic text for an HTTP error', () => {
    for (const status of [400, 403, 404, 409, 500]) {
      expect(describeApiError(http(status, {}))).not.toMatch(/status code/);
      expect(describeApiError(http(status, null))).not.toMatch(/status code/);
    }
  });

  it('prefers detail and non_field_errors when present', () => {
    expect(describeApiError(http(403, { detail: 'You are not on this memo.' }))).toBe('You are not on this memo.');
    expect(describeApiError(http(400, { non_field_errors: ['Already actioned.'] }))).toBe('Already actioned.');
  });

  it('explains a request that never reached the server', () => {
    expect(describeApiError({ message: 'Network Error', request: {} })).toMatch(/can’t reach the server/);
  });

  it('never shows an HTML error page as a message', () => {
    const page = '<!DOCTYPE html><html><body><h1>502 Bad Gateway</h1></body></html>';
    expect(describeApiError(http(502, page))).not.toMatch(/</);
  });

  it('asks people to wait when they are rate limited', () => {
    expect(describeApiError(http(429, {}))).toMatch(/wait a minute/);
  });
});
