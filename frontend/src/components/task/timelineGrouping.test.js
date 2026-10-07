import { describe, expect, it } from 'vitest';

import { groupTimeline } from './timelineGrouping';

const row = (id, action, at, actor = 'A') => ({
  id, action, action_label: action, actor_name: actor, created_at: at,
});

describe('timeline grouping', () => {
  it('folds a consecutive run of the same event into one entry', () => {
    const [day] = groupTimeline([
      row(1, 'accepted', '2026-09-25T09:00:00Z', 'Bibek'),
      row(2, 'accepted', '2026-09-25T09:05:00Z', 'Prashanta'),
      row(3, 'accepted', '2026-09-25T09:09:00Z', 'Sanjaya'),
    ]);
    expect(day.entries).toHaveLength(1);
    expect(day.entries[0].rows).toHaveLength(3);
  });

  it('keeps non-consecutive repeats apart', () => {
    // Two progress reports with a comment between them are two moments in the
    // task's life, not one thing that happened three times.
    const [day] = groupTimeline([
      row(1, 'progress_updated', '2026-09-25T09:00:00Z'),
      row(2, 'commented', '2026-09-25T09:30:00Z'),
      row(3, 'progress_updated', '2026-09-25T10:00:00Z'),
    ]);
    expect(day.entries.map((e) => e.action)).toEqual(
      ['progress_updated', 'commented', 'progress_updated']);
  });

  it('never folds across a day boundary', () => {
    const days = groupTimeline([
      row(1, 'accepted', '2026-09-24T09:00:00Z'),
      row(2, 'accepted', '2026-09-25T09:00:00Z'),
    ]);
    expect(days).toHaveLength(2);
    expect(days.every((d) => d.entries[0].rows.length === 1)).toBe(true);
  });

  it('loses nothing: every row comes back out', () => {
    const rows = [
      row(1, 'created', '2026-09-24T09:00:00Z'),
      row(2, 'accepted', '2026-09-25T09:00:00Z'),
      row(3, 'accepted', '2026-09-25T09:01:00Z'),
      row(4, 'commented', '2026-09-25T11:00:00Z'),
    ];
    const ids = groupTimeline(rows)
      .flatMap((d) => d.entries).flatMap((e) => e.rows).map((r) => r.id);
    expect(ids.sort()).toEqual([1, 2, 3, 4]);
  });

  it('survives an empty timeline', () => {
    expect(groupTimeline([])).toEqual([]);
    expect(groupTimeline()).toEqual([]);
  });
});

describe('progress values in the timeline', () => {
  const progress = (id, at, reported) => ({
    id, action: 'progress_updated', action_label: 'Progress updated',
    actor_name: 'Bibek', created_at: at, metadata: { from: 0, to: reported, reported },
  });

  it('reads the percentage off the audit metadata', async () => {
    const { eventValue } = await import('./timelineGrouping');
    expect(eventValue(progress(1, '2026-09-25T09:00:00Z', 50))).toBe('50%');
  });

  it('shows nothing for events that carry no figure', async () => {
    const { eventValue } = await import('./timelineGrouping');
    expect(eventValue({ action: 'commented', metadata: {} })).toBeNull();
    // A row written before this metadata existed must not render "undefined%".
    expect(eventValue({ action: 'progress_updated', metadata: {} })).toBeNull();
    expect(eventValue(undefined)).toBeNull();
  });

  it('summarises a folded run as the journey it was', async () => {
    const { entrySummary, groupTimeline } = await import('./timelineGrouping');
    // Newest first, as the timeline supplies them.
    const [day] = groupTimeline([
      progress(3, '2026-09-25T11:00:00Z', 75),
      progress(2, '2026-09-25T10:00:00Z', 50),
      progress(1, '2026-09-25T09:00:00Z', 25),
    ]);
    expect(day.entries).toHaveLength(1);
    expect(entrySummary(day.entries[0])).toBe('25% → 75%');
  });

  it('does not summarise a single event, or a mixed run', async () => {
    const { entrySummary } = await import('./timelineGrouping');
    expect(entrySummary({ rows: [progress(1, '2026-09-25T09:00:00Z', 25)] })).toBeNull();
    expect(entrySummary({ rows: [progress(1, '2026-09-25T09:00:00Z', 25),
      { action: 'commented', metadata: {} }] })).toBeNull();
  });
});
