import { describe, it, expect } from 'vitest';
import {
  collapseRows, dayLabel, groupByDay, initialsFrom, truncate,
} from './feed';

const NOW = new Date(2026, 9, 1, 15, 0, 0); // 1 Oct 2026, local

describe('dayLabel', () => {
  it('says Today, Yesterday, then the weekday and date', () => {
    expect(dayLabel(new Date(2026, 9, 1, 9).toISOString(), NOW)).toBe('Today');
    expect(dayLabel(new Date(2026, 8, 30, 23).toISOString(), NOW)).toBe('Yesterday');
    expect(dayLabel(new Date(2026, 8, 28, 10).toISOString(), NOW)).toMatch(/Monday/);
    expect(dayLabel(null, NOW)).toBe('Earlier');
  });
});

describe('initialsFrom', () => {
  it('reads a leading name from the title or the body', () => {
    expect(initialsFrom('Subtask completed', 'Bibek Demo completed "x" on Website')).toBe('BD');
    expect(initialsFrom('Prashanta accepted Shared deployment')).toBe('P');
    expect(initialsFrom('Balkishor Chaudhary approved Website Deployment')).toBe('BC');
  });

  it('refuses words that are not names', () => {
    expect(initialsFrom('Task assigned to you', 'Your task is due')).toBeNull();
    expect(initialsFrom('New circular published')).toBeNull();
    expect(initialsFrom('', null)).toBeNull();
  });
});

describe('truncate', () => {
  it('cuts long text at about 80 characters with an ellipsis', () => {
    const long = 'x'.repeat(120);
    expect(truncate(long)).toHaveLength(80);
    expect(truncate(long).endsWith('…')).toBe(true);
    expect(truncate('short')).toBe('short');
  });
});

describe('collapseRows', () => {
  const n = (id, category, title, extra = {}) => ({ id, category, title, ...extra });

  it('folds consecutive rows with the same category and object_id', () => {
    const rows = collapseRows([
      n(1, 'task_subtask_completed', 'Subtask completed', { object_id: 9 }),
      n(2, 'task_subtask_completed', 'Subtask completed', { object_id: 9 }),
      n(3, 'task_subtask_completed', 'Subtask completed', { object_id: 10 }),
      n(4, 'task_comment', 'New comment', { object_id: 10 }),
    ]);
    expect(rows.map((r) => [r.id, r.count])).toEqual([[1, 2], [3, 1], [4, 1]]);
  });

  it('falls back to category + title when there is no object_id', () => {
    const rows = collapseRows([
      n(1, 'task_comment', 'New comment on Shared deployment'),
      n(2, 'task_comment', 'New comment on Shared deployment'),
      n(3, 'task_comment', 'New comment on Website'),
      n(4, 'task_comment', 'New comment on Website'),
      n(5, 'task_comment', 'New comment on Shared deployment'),
    ]);
    expect(rows.map((r) => [r.id, r.count])).toEqual([[1, 2], [3, 2], [5, 1]]);
  });
});

describe('groupByDay', () => {
  it('groups in order of first appearance', () => {
    const today = new Date(2026, 9, 1, 9).toISOString();
    const yday = new Date(2026, 8, 30, 9).toISOString();
    const groups = groupByDay([
      { id: 1, created_at: today }, { id: 2, created_at: today }, { id: 3, created_at: yday },
    ], NOW);
    expect(groups.map((g) => [g.label, g.rows.length])).toEqual([['Today', 2], ['Yesterday', 1]]);
  });
});
