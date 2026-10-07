import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import SummaryCards from './SummaryCards';
import { buildWorkSummary } from './workSummary';

/**
 * The action summary (Phase HOME-REDESIGN).
 *
 * One component at every width, four cards always, zeros shown. The parity
 * test that used to live here — desktop tiles against mobile cards — has no
 * second renderer left to compare, which was the point of removing it.
 */

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

const counts = { total: 7, overdue: 1, review: 3, approval: 2 };
const dashboard = { due_today: 0, completed: 3 };

describe('the four figures', () => {
  it('are built in reading order, each linked to the list that produced it', () => {
    const cards = buildWorkSummary({ counts, dashboard });
    expect(cards.map((c) => [c.label, c.n, c.to])).toEqual([
      ['Needs attention', 7, '/queue'],
      ['Pending reviews', 5, '/queue'],
      ['Due today', 0, '/tasks/due-today'],
      ['Completed', 3, '/tasks/completed'],
    ]);
  });

  it('counts reviews AND approvals as pending reviews', () => {
    const [, reviews] = buildWorkSummary({ counts: { review: 2, approval: 4 } });
    expect(reviews.n).toBe(6);
  });

  it('reads zero, not a crash, with no counts or dashboard at all', () => {
    expect(buildWorkSummary().map((c) => c.n)).toEqual([0, 0, 0, 0]);
  });
});

describe('the cards', () => {
  it('render all four as links, zeros included', () => {
    const { container } = wrap(<SummaryCards counts={counts} dashboard={dashboard} />);
    const links = container.querySelectorAll('a.hm-sum-card');
    expect(links).toHaveLength(4);
    expect(links[2]).toHaveAttribute('href', '/tasks/due-today');
    expect(within(links[2]).getByText('0')).toBeInTheDocument();
    expect(screen.getByText('Due today')).toBeInTheDocument();
    // No "all caught up" collapse any more: the frame is stable.
    expect(screen.queryByText(/all caught up/)).toBeNull();
  });

  it('still shows four cards when everything is zero', () => {
    const { container } = wrap(<SummaryCards counts={{}} dashboard={null} />);
    expect(container.querySelectorAll('a.hm-sum-card')).toHaveLength(4);
    expect(container.querySelectorAll('.hm-sum-n')).toHaveLength(4);
    for (const n of container.querySelectorAll('.hm-sum-n')) expect(n).toHaveTextContent('0');
  });

  it('fills the "Needs attention" card and no other', () => {
    const { container } = wrap(<SummaryCards counts={counts} dashboard={dashboard} />);
    const primary = container.querySelectorAll('.hm-sum-card.is-primary');
    expect(primary).toHaveLength(1);
    expect(primary[0]).toHaveTextContent('Needs attention');
  });

  it('shows dashes rather than zeros while loading', () => {
    const { container } = wrap(<SummaryCards counts={{}} loading />);
    for (const n of container.querySelectorAll('.hm-sum-n')) expect(n).toHaveTextContent('—');
  });
});
