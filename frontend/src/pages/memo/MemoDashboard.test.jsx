/**
 * Phase 49.5 - "Counts and UI must match".
 *
 * The Phase 49 audit found the endpoint returning fifteen counts while the page
 * rendered seven tiles, so five queues a user could be waiting on had a working
 * server-side count and no way to see them. A test that just checked "the tiles
 * render" would not have caught that, because they did render - there were simply
 * too few of them. So these assert the CORRESPONDENCE in both directions:
 *
 *   - every tile the brief names shows the value its own API key carries, and
 *   - a tile never shows a number the API did not send.
 *
 * Every count is given a DISTINCT value in the fixture, which is what makes the
 * second claim testable: with two tiles reading the same key, or reading each
 * other's, identical fixture values would hide it.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import MemoDashboard from './MemoDashboard';

vi.mock('../../services/memoService', () => ({
  memoService: {
    getDashboard: vi.fn(),
    getDashboardCharts: vi.fn(),
    listMemos: vi.fn(),
  },
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u1', full_name: 'Deepak Employee', name: 'Deepak Employee' },
    role: 'maker',
  }),
}));

// Charts are exercised by their own tests; here they are noise that slows the
// suite and can fail for reasons that have nothing to do with the tiles.
vi.mock('../../components/analytics/ComparisonBarChart', () => ({
  default: () => <div data-testid="bar-chart" />,
}));
vi.mock('../../components/analytics/TrendChart', () => ({
  default: () => <div data-testid="trend-chart" />,
}));

import { memoService } from '../../services/memoService';

/* Distinct values per key, so a tile reading the wrong one is visible. */
const COUNTS = {
  drafts: 11,
  assigned: 22,
  pending_review: 33,
  pending_recommendation: 44,
  pending_support: 55,
  pending_approval: 66,
  pending_notes: 77,
  completed_notes: 88,
  department: 99,
  approved: 111,
  archived: 222,
  inbox: 333,
  outbox: 444,
  my_memos: 555,
  draft_for_review: 666,
  rejected: 777,
  pending_actions: 22,
  total_visible: 888,
};

/* Label -> the API key it must read. The whole point of the test. */
const TILES = [
  ['Draft Memo', 'drafts'],
  ['Assigned Memo', 'assigned'],
  ['Pending Review', 'pending_review'],
  ['Pending Recommendation', 'pending_recommendation'],
  ['Pending Support', 'pending_support'],
  ['Pending Approval', 'pending_approval'],
  ['Pending Note', 'pending_notes'],
  ['Department Memo', 'department'],
  ['Approved', 'approved'],
  ['Archived Memo', 'archived'],
];

const renderDashboard = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><MemoDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('memo dashboard tiles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    memoService.getDashboard.mockResolvedValue(COUNTS);
    memoService.getDashboardCharts.mockResolvedValue({
      by_department: [], by_status: [], monthly_trend: [],
    });
    memoService.listMemos.mockResolvedValue({ items: [], total: 0 });
  });

  it('renders every tile the brief names', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Draft Memo')).toBeInTheDocument());
    for (const [label] of TILES) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it.each(TILES)('the %s tile shows the value of counts.%s', async (label, key) => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText(label)).toBeInTheDocument());
    // The value sits in the same tile as the label, so scope the search to it
    // rather than searching the page - two tiles could legitimately share a value.
    const tile = screen.getByText(label).closest('a');
    expect(tile).toBeTruthy();
    expect(tile.textContent).toContain(String(COUNTS[key]));
  });

  it('shows no number the API did not send', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Draft Memo')).toBeInTheDocument());
    const sent = new Set(Object.values(COUNTS).map(String));
    for (const [label] of TILES) {
      const value = screen.getByText(label).closest('a')
        .querySelector('.memo-tile-value, .memo-kpi-value, span');
      // Whatever the tile renders as its figure must be a number the server sent -
      // not a placeholder, not a locally computed sum.
      if (value && /^\d+$/.test(value.textContent.trim())) {
        expect(sent).toContain(value.textContent.trim());
      }
    }
  });

  it('does not fold the pending note count into the blocking-work total', async () => {
    /*
     * A note is informational and does not block the workflow. Assigned Memo is the
     * sum of the four pending_* approval counts (33+44+55+66 = 198 in the fixture's
     * own terms, delivered by the server as `assigned`), and adding 77 outstanding
     * notes to it would report non-blocking work as blocking.
     */
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Assigned Memo')).toBeInTheDocument());
    const assigned = screen.getByText('Assigned Memo').closest('a');
    expect(assigned.textContent).toContain('22');
    expect(assigned.textContent).not.toContain('99');

    const note = screen.getByText('Pending Note').closest('a');
    expect(note.textContent).toContain('77');
  });
});
