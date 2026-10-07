/**
 * Queue normaliser guard (Phase 203).
 *
 * The normaliser is where six unrelated API shapes become one row shape, and it
 * runs on the busiest screen in the system. Its contract is:
 *
 *   - a valid row maps to a QueueItem with the fields the UI reads
 *   - an unfamiliar payload yields blanks, never a thrown TypeError
 *   - a single bad row is dropped, not allowed to empty the queue
 *   - EVERY envelope shape the six services return is unwrapped
 *
 * The last one matters most: the services genuinely differ. memoService returns
 * {items}, leaveService returns {data}, workforceService returns {results},
 * inventoryService returns a bare array. Getting one wrong silently shows an
 * empty queue to somebody who has work waiting.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  SOURCES, TYPES, KINDS, normaliseSource, summarise, applyFilter, sourceKey,
  fetchWorkQueue,
} from './workQueue';

const bySourceKey = (key) => SOURCES.find((s) => sourceKey(s) === key || s.type === key);

describe('envelope unwrapping', () => {
  const source = bySourceKey('memo');
  const row = { id: 1, subject: 'Budget', memo_number: 'M-1' };

  it('unwraps a bare array', () => {
    expect(normaliseSource(source, [row])).toHaveLength(1);
  });
  it('unwraps the DRF {results} envelope', () => {
    expect(normaliseSource(source, { results: [row] })).toHaveLength(1);
  });
  it('unwraps memoService\'s {items} envelope', () => {
    expect(normaliseSource(source, { items: [row] })).toHaveLength(1);
  });
  it('unwraps leaveService\'s {data} envelope', () => {
    expect(normaliseSource(source, { data: [row] })).toHaveLength(1);
  });
  it('returns nothing for null, undefined or a scalar', () => {
    expect(normaliseSource(source, null)).toEqual([]);
    expect(normaliseSource(source, undefined)).toEqual([]);
    expect(normaliseSource(source, 42)).toEqual([]);
  });
});

describe('memo mapping', () => {
  const source = bySourceKey('memo');
  // What the server now sends on every pending memo row: the viewer's own step.
  const APPROVER_STEP = {
    role_type: 'approver', role_label: 'Approver', action_label: 'Approve',
    requires_comment: true, min_comment_length: 10, decisions: ['proceed', 'reject'],
  };

  it('maps the fields the row renders', () => {
    const [item] = normaliseSource(source, [{
      id: 7, subject: 'HR Budget Approval', memo_number: 'MEMO-2083-14',
      created_by_name: 'Rita Sharma', submitted_at: '2026-08-25T04:00:00Z',
      my_step: APPROVER_STEP,
    }]);
    expect(item).toMatchObject({
      id: 'memo:7',
      sourceId: 7,
      type: TYPES.MEMO,
      kind: KINDS.APPROVAL,
      title: 'HR Budget Approval',
      requester: 'Rita Sharma',
      href: '/memos/7',
    });
    expect(item.subtitle).toContain('MEMO-2083-14');
    expect(item.requesterInitials).toBe('RS');
    expect(item.actions.map((a) => a.verb)).toEqual(['approve', 'reject']);
  });

  it('reads a nested requester name when the flat one is absent', () => {
    const [item] = normaliseSource(source, [
      { id: 8, subject: 'X', created_by: { full_name: 'Anita Gurung' } },
    ]);
    expect(item.requester).toBe('Anita Gurung');
    expect(item.requesterInitials).toBe('AG');
  });

  it('falls back rather than rendering "undefined" when the subject is missing', () => {
    const [item] = normaliseSource(source, [{ id: 9 }]);
    expect(item.title).toBe('Untitled memo');
    expect(item.requester).toBe('Unknown');
    expect(item.requesterInitials).toBe('UN');
  });

  it('renders an em dash rather than a blank avatar when there is no name at all', () => {
    const nameless = { ...source, map: (row) => ({ ...source.map(row), requester: '' }) };
    const [item] = normaliseSource(nameless, [{ id: 10, subject: 'X' }]);
    expect(item.requesterInitials).toBe('—');
  });
});

describe('leave mapping', () => {
  it('uses the flattened shape leaveService.mapLeave already produces', () => {
    const [item] = normaliseSource(bySourceKey('leave'), {
      data: [{
        id: 3, employee: 'Sunita Thapa', type: 'Annual Leave',
        start: '2026-09-05', end: '2026-09-09', days: 5, status: 'pending',
      }],
    });
    expect(item.id).toBe('leave:3');
    expect(item.title).toBe('Annual Leave — Sunita Thapa');
    expect(item.subtitle).toContain('5 days');
    expect(item.kind).toBe(KINDS.APPROVAL);
  });
});

describe('the escalation rule', () => {
  it('gives minutes no inline action — they need the record in front of them', () => {
    const [item] = normaliseSource(bySourceKey('minute'), [{ id: 1, subject: 'Board 2083-04' }]);
    expect(item.actions).toEqual([]);
  });

  it('gives asset requests no inline action — routing depends on the stage', () => {
    const [item] = normaliseSource(bySourceKey('asset-requests'), [{ id: 1, item_name: 'Laptop' }]);
    expect(item.actions).toEqual([]);
  });

  it('gives circulars a single acknowledge, never an approve/reject pair', () => {
    const [item] = normaliseSource(bySourceKey('circular'), [{ id: 1, subject: 'Notice' }]);
    expect(item.actions.map((a) => a.verb)).toEqual(['acknowledge']);
  });

  it('requires a remark before any rejection', () => {
    const [item] = normaliseSource(bySourceKey('memo'), [{ id: 1, subject: 'X',
      my_step: { role_type: 'reviewer', role_label: 'Reviewer', action_label: 'Review',
        requires_comment: true, min_comment_length: 10 } }]);
    const reject = item.actions.find((a) => a.verb === 'reject');
    expect(reject.needsRemark).toBe(true);
    expect(reject.minRemark).toBe(10);
  });
});

describe('resilience', () => {
  it('drops a row that throws instead of losing the whole source', () => {
    const exploding = {
      ...bySourceKey('memo'),
      map: (row) => {
        if (row.id === 2) throw new TypeError('bad row');
        return { id: `memo:${row.id}`, type: 'memo', kind: 'approval', title: 'ok', actions: [] };
      },
    };
    const out = normaliseSource(exploding, [{ id: 1 }, { id: 2 }, { id: 3 }]);
    expect(out.map((i) => i.id)).toEqual(['memo:1', 'memo:3']);
  });

  it('drops a mapped row with no id, which would break React keys', () => {
    const idless = { ...bySourceKey('memo'), map: () => ({ title: 'no id' }) };
    expect(normaliseSource(idless, [{ id: 1 }])).toEqual([]);
  });

  it('never throws on a completely unexpected row', () => {
    for (const source of SOURCES) {
      expect(() => normaliseSource(source, [{}, { id: null }, { id: 1 }])).not.toThrow();
    }
  });
});

describe('one source cannot take the queue down', () => {
  /** Every source stubbed, so the assertion does not depend on a live server. */
  const withStubs = (failing, mode) => SOURCES.map((source, i) => {
    const impl = i !== failing
      ? () => Promise.resolve({ results: [{ id: i + 1, subject: `row ${i}` }] })
      : (mode === 'sync'
        ? () => { throw new TypeError('thrown, not rejected'); }
        : () => Promise.reject(new Error('boom')));
    return vi.spyOn(source, 'fetch').mockImplementation(impl);
  });

  it('reports a source that REJECTS and keeps the other five', async () => {
    const spies = withStubs(0, 'async');
    const { items, sources } = await fetchWorkQueue();
    expect(sources[0]).toMatchObject({ ok: false, error: 'boom' });
    expect(sources.filter((s) => s.ok)).toHaveLength(SOURCES.length - 1);
    expect(items.length).toBe(SOURCES.length - 1);
    spies.forEach((s) => s.mockRestore());
  });

  it('survives a source that throws SYNCHRONOUSLY before returning a promise', async () => {
    // The failure the QA harness caught: a sync throw escapes Promise.allSettled
    // entirely, so the whole queue dies for what should cost one source's rows.
    const spies = withStubs(0, 'sync');
    const { items, sources } = await fetchWorkQueue();
    expect(sources[0].ok).toBe(false);
    expect(sources.filter((s) => s.ok)).toHaveLength(SOURCES.length - 1);
    expect(items.length).toBe(SOURCES.length - 1);
    spies.forEach((s) => s.mockRestore());
  });
});

describe('every source is wired', () => {
  it('covers all six "waiting on me" lists with unique keys', () => {
    const keys = SOURCES.map(sourceKey);
    expect(new Set(keys).size).toBe(keys.length);
    expect(SOURCES.map((s) => s.type)).toEqual(
      expect.arrayContaining([
        TYPES.MEMO, TYPES.MINUTE, TYPES.CIRCULAR,
        TYPES.LEAVE, TYPES.ASSET, TYPES.ATTENDANCE,
      ]),
    );
  });

  it('gives every source a fetch, a map and a human label', () => {
    for (const s of SOURCES) {
      expect(typeof s.fetch).toBe('function');
      expect(typeof s.map).toBe('function');
      expect(s.label).toBeTruthy();
    }
  });
});

describe('summarise and applyFilter', () => {
  const items = [
    { id: '1', kind: KINDS.APPROVAL, dueAt: '2000-01-01T00:00:00Z' },
    { id: '2', kind: KINDS.APPROVAL, dueAt: null, createdAt: null },
    { id: '3', kind: KINDS.REVIEW, dueAt: null, createdAt: null },
    { id: '4', kind: KINDS.ACKNOWLEDGE, dueAt: null, createdAt: null },
    { id: '5', kind: KINDS.ACTION, dueAt: null, createdAt: null },
  ];

  it('counts by kind and by overdue', () => {
    expect(summarise(items)).toMatchObject({
      total: 5, overdue: 1, approval: 2, review: 1, acknowledge: 1, action: 1,
    });
  });

  it('filters by chip, and falls through to all for an unknown chip', () => {
    expect(applyFilter(items, 'approvals')).toHaveLength(2);
    expect(applyFilter(items, 'overdue')).toHaveLength(1);
    expect(applyFilter(items, 'nonsense')).toHaveLength(5);
  });
});


// --------------------------------------------------------------------------- #
// Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE
//
// The queue sent {decision: "approve", remarks: ""} for every memo. The endpoint
// accepts only "proceed" or "reject", and every step needs a 10-character
// comment - two 400s, the first hiding the second. These pin what reaches the
// server, which the old mapping test never looked at.
// --------------------------------------------------------------------------- #
vi.mock('./memoService', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, memoService: { ...actual.memoService, actOnMemo: vi.fn().mockResolvedValue({}) } };
});

describe('memo actions send what the endpoint accepts', () => {
  const step = (role, label) => ({
    role_type: role, role_label: role[0].toUpperCase() + role.slice(1),
    action_label: label, requires_comment: true, min_comment_length: 10,
  });

  it.each([
    ['reviewer', 'Review', 'review', KINDS.REVIEW],
    ['recommender', 'Recommend', 'recommend', KINDS.REVIEW],
    ['supporter', 'Support', 'support', KINDS.REVIEW],
    ['approver', 'Approve', 'approve', KINDS.APPROVAL],
  ])('a %s step is labelled "%s", sends decision "proceed" and needs a comment',
    async (role, label, verb, kind) => {
      const { memoService } = await import('./memoService');
      memoService.actOnMemo.mockClear();
      const [item] = normaliseSource(bySourceKey('memo'), [{ id: 9, subject: 'S', my_step: step(role, label) }]);

      expect(item.kind).toBe(kind);
      const [proceed] = item.actions;
      expect(proceed.label).toBe(label);
      expect(proceed.verb).toBe(verb);
      expect(proceed.needsRemark).toBe(true);
      expect(proceed.minRemark).toBe(10);

      await proceed.run('Checked the budget figures.');
      expect(memoService.actOnMemo).toHaveBeenCalledWith(9,
        { decision: 'proceed', remarks: 'Checked the budget figures.' });
    });

  it('never sends "approve" as a decision', async () => {
    const { memoService } = await import('./memoService');
    memoService.actOnMemo.mockClear();
    const [item] = normaliseSource(bySourceKey('memo'), [{ id: 3, subject: 'S', my_step: step('approver', 'Approve') }]);
    for (const action of item.actions) await action.run('A long enough remark.');
    const decisions = memoService.actOnMemo.mock.calls.map(([, body]) => body.decision);
    expect(decisions).toEqual(['proceed', 'reject']);
  });

  it('offers no inline action when the memo is no longer waiting on the viewer', () => {
    const [item] = normaliseSource(bySourceKey('memo'), [{ id: 4, subject: 'S', my_step: null }]);
    expect(item.actions).toEqual([]);
  });
});

// --------------------------------------------------------------------------- #
// Phase MEMO-QUEUE-UX-HARDENING - role, current step, next step, and a check on open
// --------------------------------------------------------------------------- #
describe('memo step context and the open-time check', () => {
  const row = (over = {}) => ({
    id: 21, subject: 'S',
    my_step: {
      id: 'step-1', role_type: 'reviewer', role_label: 'Reviewer', action_label: 'Review',
      requires_comment: true, min_comment_length: 10, position: 1, total_steps: 4,
      next_step: { role_label: 'Recommender', assignee_name: 'Sanjaya Poudel' },
      is_final_step: false, author_name: 'Prashanta Acharya',
    },
    ...over,
  });

  it('summarises the step on the item and hands the dialog the full context', () => {
    const [item] = normaliseSource(bySourceKey('memo'), [row()]);
    expect(item.stepLine).toBe('Reviewer · Step 1 of 4 · Next: Recommender');
    expect(item.actions[0].workflow).toEqual({
      roleLabel: 'Reviewer', position: 1, total: 4,
      next: { roleLabel: 'Recommender', name: 'Sanjaya Poudel' },
      isFinalStep: false, authorName: 'Prashanta Acharya',
    });
  });

  it('calls the last step a final approval', () => {
    const [item] = normaliseSource(bySourceKey('memo'), [row({ my_step: {
      ...row().my_step, role_type: 'approver', role_label: 'Approver', action_label: 'Approve',
      position: 4, next_step: null, is_final_step: true } })]);
    expect(item.stepLine).toBe('Approver · Step 4 of 4 · Final approval');
  });

  it.each([
    ['still waiting on the viewer', { is_read_only: false, my_step: { id: 'step-1', is_my_turn: true } },
      { ok: true }],
    ['withdrawn', { is_read_only: true, status: 'cancelled', status_label: 'Cancelled' },
      { ok: false, reason: 'This memo is cancelled and can no longer be actioned.' }],
    ['moved on to someone else', { is_read_only: false, my_step: { id: 'step-1', is_my_turn: false },
      pending_with: { name: 'Sanjaya Poudel', role_label: 'Recommender' } },
      { ok: false, reason: 'This memo has moved on — it is now with Sanjaya Poudel (Recommender).' }],
  ])('the check on open reports a memo that is %s', async (_label, detail, verdict) => {
    const { memoService } = await import('./memoService');
    memoService.getMemo = vi.fn().mockResolvedValue(detail);
    const [item] = normaliseSource(bySourceKey('memo'), [row()]);
    await expect(item.actions[0].precheck()).resolves.toEqual(verdict);
    expect(memoService.getMemo).toHaveBeenCalledWith(21);
  });
});
