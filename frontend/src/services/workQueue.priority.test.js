/**
 * Queue priority guard (Phase 203).
 *
 * The sort is the queue's whole promise: "the thing that most needs you is at
 * the top". These tests pin the four buckets, the tie-break, and the one rule
 * that is a governance decision rather than a technical one - that record type
 * never influences order.
 */
import { describe, it, expect } from 'vitest';
import {
  priority, compareItems, isOverdue, waitingLabel,
} from './workQueue';

const NOW = new Date('2026-08-27T10:00:00Z').getTime();
const HOUR = 3_600_000;
const DAY = 86_400_000;

const item = (over = {}) => ({
  id: 'x:1', type: 'memo', kind: 'approval',
  createdAt: new Date(NOW - HOUR).toISOString(), dueAt: null, ...over,
});

describe('priority', () => {
  it('puts a passed due date in bucket 0', () => {
    expect(priority(item({ dueAt: new Date(NOW - DAY).toISOString() }), NOW)).toBe(0);
  });

  it('puts a due date inside 24h in bucket 1', () => {
    expect(priority(item({ dueAt: new Date(NOW + 6 * HOUR).toISOString() }), NOW)).toBe(1);
  });

  it('puts an undated item older than 48h in bucket 2', () => {
    expect(priority(item({ createdAt: new Date(NOW - 3 * DAY).toISOString() }), NOW)).toBe(2);
  });

  it('puts everything else in bucket 3', () => {
    expect(priority(item(), NOW)).toBe(3);
  });

  it('treats an unparseable date as no date rather than throwing', () => {
    expect(() => priority(item({ dueAt: 'not-a-date' }), NOW)).not.toThrow();
    expect(priority(item({ dueAt: 'not-a-date' }), NOW)).toBe(3);
  });

  it('survives a null item', () => {
    expect(() => priority(null, NOW)).not.toThrow();
  });
});

describe('compareItems', () => {
  it('sorts overdue before everything else', () => {
    const overdue = item({ id: 'a', dueAt: new Date(NOW - DAY).toISOString() });
    const normal = item({ id: 'b' });
    expect([normal, overdue].sort((x, y) => compareItems(x, y, NOW))[0].id).toBe('a');
  });

  it('sorts oldest first within a bucket', () => {
    const older = item({ id: 'old', createdAt: new Date(NOW - 5 * HOUR).toISOString() });
    const newer = item({ id: 'new', createdAt: new Date(NOW - HOUR).toISOString() });
    expect([newer, older].sort((x, y) => compareItems(x, y, NOW))[0].id).toBe('old');
  });

  it('never orders by record type — a memo does not outrank a leave request', () => {
    const memo = item({ id: 'memo:2', type: 'memo', createdAt: new Date(NOW - HOUR).toISOString() });
    const leave = item({ id: 'leave:1', type: 'leave', createdAt: new Date(NOW - 2 * HOUR).toISOString() });
    // The leave request is older, so it wins despite the memo sorting first
    // alphabetically and despite memos being "more official".
    expect([memo, leave].sort((x, y) => compareItems(x, y, NOW))[0].id).toBe('leave:1');
  });

  it('is stable: equal items fall back to id so the order never jitters', () => {
    const a = item({ id: 'a', createdAt: null });
    const b = item({ id: 'b', createdAt: null });
    expect(compareItems(a, b, NOW)).toBeLessThan(0);
    expect(compareItems(b, a, NOW)).toBeGreaterThan(0);
  });
});

describe('isOverdue / waitingLabel', () => {
  it('agrees with bucket 0', () => {
    expect(isOverdue(item({ dueAt: new Date(NOW - DAY).toISOString() }), NOW)).toBe(true);
    expect(isOverdue(item(), NOW)).toBe(false);
  });

  it('labels an overdue item in days', () => {
    expect(waitingLabel(item({ dueAt: new Date(NOW - 2 * DAY).toISOString() }), NOW))
      .toBe('overdue 2 days');
  });

  it('labels an imminent deadline as due today', () => {
    expect(waitingLabel(item({ dueAt: new Date(NOW + 2 * HOUR).toISOString() }), NOW))
      .toBe('due today');
  });

  it('falls back to elapsed time when there is no due date', () => {
    expect(waitingLabel(item({ createdAt: new Date(NOW - 4 * HOUR).toISOString() }), NOW))
      .toBe('4 hours');
  });

  it('returns an em dash rather than NaN when there is nothing to measure', () => {
    expect(waitingLabel({ id: 'x' }, NOW)).toBe('—');
  });
});
