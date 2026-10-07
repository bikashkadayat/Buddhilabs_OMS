/**
 * Search sources and result shaping (Phase 204).
 *
 * The load-bearing guarantees:
 *   - local indexes respect the SAME capability gates the sidebar uses
 *   - old menu labels stay findable, which is what makes a trimmed nav safe
 *   - actions come from the work queue, never inferred from a search payload
 *   - a malformed row costs its own row, not the group
 */
import { describe, it, expect } from 'vitest';
import {
  GROUPS, GROUP_ORDER, MIN_QUERY, NAV_INDEX, QUICK_ACTIONS, REMOTE_SOURCES,
  searchLocal, normaliseResults, withQueueActions, groupResults, sourcesFor,
} from './globalSearch';

const bySourceKey = (k) => REMOTE_SOURCES.find((s) => s.key === k);

describe('capability gating', () => {
  it('hides "Apply for leave" from Admin, who has no self-service', () => {
    // roles.js deliberately excludes admin from applyLeave/createMemo.
    const asAdmin = searchLocal('leave', 'admin').map((r) => r.title);
    const asMaker = searchLocal('leave', 'maker').map((r) => r.title);
    expect(asMaker).toContain('Apply for leave');
    expect(asAdmin).not.toContain('Apply for leave');
  });

  it('hides "Create memo" from Admin but offers it to an Employee', () => {
    expect(searchLocal('create memo', 'maker').map((r) => r.title)).toContain('Create memo');
    expect(searchLocal('create memo', 'admin').map((r) => r.title)).not.toContain('Create memo');
  });

  it('hides User management from everyone but Admin', () => {
    expect(searchLocal('user management', 'admin').length).toBeGreaterThan(0);
    expect(searchLocal('user management', 'maker').length).toBe(0);
  });

  it('offers ungated destinations to every role', () => {
    for (const role of ['maker', 'checker', 'approver', 'admin']) {
      expect(searchLocal('work queue', role).length).toBeGreaterThan(0);
    }
  });
});

describe('old labels stay findable', () => {
  // The whole point of the alias index: a person who learned the old menu must
  // not be stranded when the sidebar is retitled in a later phase.
  it.each([
    ['draft for review memo', '/memos/draft-for-review'],
    ['my pending actions', '/memos/pending'],
    ['needs my action', '/minutes/needs-me'],
    ['my acknowledgements', '/circulars/my-acknowledgements'],
    ['assigned circular', '/circulars/assigned'],
  ])('“%s” still resolves to %s', (term, to) => {
    const hits = searchLocal(term, 'checker');
    expect(hits.map((h) => h.to)).toContain(to);
  });

  it('never shows the alias text itself as the label', () => {
    const hit = searchLocal('draft for review memo', 'maker')
      .find((h) => h.to === '/memos/draft-for-review');
    expect(hit.title).toBe('Memos sent for review');
  });

  it('finds a page by a plain-language word that is in no label', () => {
    expect(searchLocal('biometric', 'maker').map((h) => h.to)).toContain('/my-attendance');
    expect(searchLocal('gate pass', 'checker').map((h) => h.to)).toContain('/inventory/approvals');
  });
});

describe('index hygiene', () => {
  it('gives every entry a unique id and a destination', () => {
    const all = [...NAV_INDEX, ...QUICK_ACTIONS];
    const ids = all.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const e of all) {
      expect(e.label, `${e.id} needs a label`).toBeTruthy();
      expect(e.to, `${e.id} needs a route`).toMatch(/^\//);
    }
  });

  it('caps each local group so the palette stays a shortcut', () => {
    // "a" matches a great many entries; the cap must still hold.
    const nav = searchLocal('a', 'admin').filter((r) => r.group === GROUPS.NAV);
    expect(nav.length).toBeLessThanOrEqual(5);
  });
});

describe('remote sources', () => {
  it('covers every kind of thing people look for', () => {
    // Phase T3 Part 9 added `task`, first: a task number is the thing somebody
    // most often looks up mid-work. The list stays pinned so a source cannot be
    // dropped, or silently reordered, without a decision here.
    expect(REMOTE_SOURCES.map((s) => s.key)).toEqual(
      ['task', 'memo', 'minute', 'circular', 'people', 'departments', 'leave',
        'attendance', 'assets']);
  });

  it('surfaces the five facts a task hit must carry', () => {
    const source = REMOTE_SOURCES.find((s) => s.key === 'task');
    const row = source.map({
      id: 't1',
      task_number: 'NIFN-TSK-2083-0001',
      title: 'Prepare the quarterly return',
      assignee_names: ['Bikash Kadayat'],
      priority_label: 'High',
      status_label: 'In Progress',
    });
    expect(row.title).toBe('Prepare the quarterly return');
    expect(row.subtitle).toContain('NIFN-TSK-2083-0001');
    expect(row.subtitle).toContain('Bikash Kadayat');
    expect(row.subtitle).toContain('High');
    expect(row.subtitle).toContain('In Progress');
    expect(row.to).toBe('/tasks/t1');
  });

  it('says Unassigned rather than leaving a gap in a task hit', () => {
    const source = REMOTE_SOURCES.find((s) => s.key === 'task');
    const row = source.map({ id: 't2', task_number: 'X', title: 'T',
      assignee_names: [] });
    expect(row.subtitle).toContain('Unassigned');
  });

  it('asks only the sources a role may query', () => {
    // An employee may not browse the register or a team's attendance; the
    // server would answer 403, which the palette would show as a failure.
    const keys = (role) => sourcesFor(role).map((src) => src.key);
    expect(keys('maker')).not.toContain('assets');
    expect(keys('maker')).not.toContain('attendance');
    expect(keys('maker')).toContain('leave');
    expect(keys('checker')).toEqual(expect.arrayContaining(['assets', 'attendance']));
  });

  it('opens a person only for those with a page to open', () => {
    const people = REMOTE_SOURCES.find((src) => src.key === 'people');
    expect(people.map({ id: 9, full_name: 'Bikash Dahal' }, 'admin').to).toBe('/admin/leaves/employees/9');
    expect(people.map({ id: 9, full_name: 'Bikash Dahal' }, 'maker').to).toBeNull();
  });

  it('maps a memo row to a titled, linked result', () => {
    const [r] = normaliseResults(bySourceKey('memo'), {
      results: [{ id: 7, subject: 'Budget approval', memo_number: 'M-1', status_label: 'Under review' }],
    });
    expect(r).toMatchObject({
      id: 'memo:7', group: GROUPS.MEMO, title: 'Budget approval', to: '/memos/7',
    });
    expect(r.subtitle).toContain('M-1');
  });

  it('maps a person with no link, because no person page exists', () => {
    const [r] = normaliseResults(bySourceKey('people'), [
      { id: 'u1', full_name: 'Sunita Thapa', designation: 'Officer', department: 'Finance' },
    ]);
    expect(r.title).toBe('Sunita Thapa');
    expect(r.to).toBeNull();
  });

  it('never exposes an email even if the server sent one', () => {
    const [r] = normaliseResults(bySourceKey('people'), [
      { id: 'u1', full_name: 'X', email: 'secret@nif.test' },
    ]);
    expect(JSON.stringify(r)).not.toContain('secret@nif.test');
  });

  it('drops a malformed row rather than the whole group', () => {
    const broken = { ...bySourceKey('memo'), map: (row) => {
      if (row.id === 2) throw new TypeError('bad');
      return { id: `memo:${row.id}`, group: GROUPS.MEMO, title: 't' };
    } };
    const out = normaliseResults(broken, { results: [{ id: 1 }, { id: 2 }, { id: 3 }] });
    expect(out.map((r) => r.id)).toEqual(['memo:1', 'memo:3']);
  });

  it('unwraps every envelope shape and survives nonsense', () => {
    const s = bySourceKey('memo');
    const row = { id: 1, subject: 'x' };
    expect(normaliseResults(s, [row])).toHaveLength(1);
    expect(normaliseResults(s, { results: [row] })).toHaveLength(1);
    expect(normaliseResults(s, { items: [row] })).toHaveLength(1);
    expect(normaliseResults(s, null)).toEqual([]);
    expect(normaliseResults(s, 42)).toEqual([]);
  });
});

describe('actions come from the work queue, never from search', () => {
  const result = { id: 'memo:7', group: GROUPS.MEMO, title: 'Budget', to: '/memos/7', queueId: 'memo:7' };

  it('offers Open only when the record is not actionable', () => {
    const [r] = withQueueActions([result], []);
    expect(r.actions.map((a) => a.label)).toEqual(['Open']);
  });

  it('adds the queue’s own actions when the record IS actionable', () => {
    const queued = {
      id: 'memo:7',
      actions: [{ label: 'Approve', verb: 'approve' }],
    };
    const [r] = withQueueActions([result], [queued]);
    expect(r.actions.map((a) => a.label)).toEqual(['Open', 'Approve']);
    expect(r.actions[1].item).toBe(queued);
  });

  it('never offers an action that needs a written remark', () => {
    // A palette row cannot collect a rejection reason, and firing one with an
    // empty remark would put an unexplained decision into the audit trail.
    const queued = {
      id: 'memo:7',
      actions: [
        { label: 'Approve', verb: 'approve' },
        { label: 'Reject', verb: 'reject', needsRemark: true },
      ],
    };
    const [r] = withQueueActions([result], [queued]);
    expect(r.actions.map((a) => a.label)).toEqual(['Open', 'Approve']);
    expect(r.actions.map((a) => a.label)).not.toContain('Reject');
  });

  it('ignores an item the user has already resolved', () => {
    const queued = { id: 'memo:7', resolved: { verb: 'approve' }, actions: [{ label: 'Approve' }] };
    const [r] = withQueueActions([result], [queued]);
    expect(r.actions.map((a) => a.label)).toEqual(['Open']);
  });

  it('gives a person no Open, because there is nowhere to open', () => {
    const person = { id: 'person:1', group: GROUPS.PEOPLE, title: 'X', to: null, queueId: null };
    const [r] = withQueueActions([person], []);
    expect(r.actions).toEqual([]);
  });
});

describe('grouping', () => {
  it('orders groups with the instant local ones first', () => {
    const rows = [
      { id: 'a', group: GROUPS.CIRCULAR }, { id: 'b', group: GROUPS.NAV },
      { id: 'c', group: GROUPS.MEMO }, { id: 'd', group: GROUPS.ACTION },
    ];
    expect(groupResults(rows).map((g) => g.group))
      .toEqual([GROUPS.ACTION, GROUPS.NAV, GROUPS.MEMO, GROUPS.CIRCULAR]);
  });

  it('omits empty groups entirely', () => {
    expect(groupResults([{ id: 'a', group: GROUPS.MEMO }]).map((g) => g.group))
      .toEqual([GROUPS.MEMO]);
  });

  it('declares an order for every group it can render', () => {
    for (const g of Object.values(GROUPS)) expect(GROUP_ORDER).toContain(g);
  });
});

describe('query gate', () => {
  it('matches the server’s own two-character directory gate', () => {
    expect(MIN_QUERY).toBe(2);
  });
});
