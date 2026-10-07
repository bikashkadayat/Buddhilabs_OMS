import { describe, it, expect } from 'vitest';
import {
  focusOrder, focusVerb, timeLeft, verbTone,
} from './focus';

const NOW = new Date('2026-10-01T12:00:00Z').getTime();
const item = (extra = {}) => ({ id: Math.random(), type: 'task', kind: 'action', actions: [], ...extra });

describe('focusOrder', () => {
  it('puts the latest overdue first, then the soonest due, then the rest as given', () => {
    const a = item({ id: 'a', dueAt: '2026-10-05' });
    const b = item({ id: 'b', dueAt: '2026-09-20' });   // 11 days late
    const c = item({ id: 'c', dueAt: '2026-09-29' });   // 2 days late
    const d = item({ id: 'd' });                        // no due date
    const e = item({ id: 'e', dueAt: '2026-10-02' });
    expect(focusOrder([d, a, c, e, b], NOW).map((i) => i.id)).toEqual(['b', 'c', 'e', 'a', 'd']);
  });
});

describe('focusVerb', () => {
  it('uses the inline action when there is one', () => {
    expect(focusVerb(item({ actions: [{ verb: 'accept', variant: 'primary' }] }))).toBe('Accept');
    expect(focusVerb(item({ actions: [{ verb: 'approve', variant: 'primary' }, { verb: 'reject' }] }))).toBe('Approve');
    expect(focusVerb(item({ actions: [{ verb: 'acknowledge' }] }))).toBe('Acknowledge');
    expect(focusVerb(item({ actions: [{ verb: 'recommend' }] }))).toBe('Review');
  });

  it('names what the item is when there is no inline action', () => {
    expect(focusVerb(item({ kind: 'review' }))).toBe('Review');
    expect(focusVerb(item({ type: 'minute' }))).toBe('Sign off');
    expect(focusVerb(item({ type: 'task' }))).toBe('Start');
    expect(focusVerb(item({ type: 'asset', kind: 'approval' }))).toBe('Approve');
    expect(focusVerb(item({ type: 'other', kind: 'other' }))).toBe('Open');
  });
});

describe('timeLeft', () => {
  it('counts days overdue, due today and days left', () => {
    expect(timeLeft(item({ dueAt: '2026-09-29T12:00:00Z' }), NOW)).toEqual({ label: 'Overdue 2 days', late: true });
    expect(timeLeft(item({ dueAt: '2026-10-01T18:00:00Z' }), NOW)).toEqual({ label: 'Due today', late: false });
    expect(timeLeft(item({ dueAt: '2026-10-07T12:00:00Z' }), NOW)).toEqual({ label: '6 days left', late: false });
    expect(timeLeft(item({ dueAt: '2026-10-02T12:00:00Z' }), NOW).label).toBe('1 day left');
  });

  it('falls back to how long the item has waited', () => {
    expect(timeLeft(item({ createdAt: '2026-09-28T12:00:00Z' }), NOW).label).toBe('Waiting 3 days');
    expect(timeLeft(item(), NOW).label).toBe('');
  });
});

describe('verbTone', () => {
  it('maps every verb onto the five-hue status set', () => {
    // Reviews and decisions amber, pending purple, your own work blue.
    for (const v of ['Review', 'Approve', 'Acknowledge', 'Sign off']) expect(verbTone(v)).toBe('amber');
    expect(verbTone('Accept')).toBe('purple');
    expect(verbTone('Start')).toBe('blue');
    expect(verbTone('Open')).toBe('neutral');
    expect(verbTone('Anything else')).toBe('neutral');
  });

  it('turns red when the row is overdue, whatever the verb', () => {
    expect(verbTone('Review', true)).toBe('red');
    expect(verbTone('Start', true)).toBe('red');
  });
});
