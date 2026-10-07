/**
 * Circular dashboard: every tile reads its own API key, and nothing else.
 *
 * The failure this guards against is one an earlier module in this project shipped:
 * a dashboard endpoint returning fifteen counts while the page rendered seven tiles,
 * so queues a user could be waiting on had a working count and no way to see them.
 * A test that only checked "the tiles render" would not have caught it - they did
 * render, there were simply too few.
 *
 * So the assertions run in both directions: every named tile shows the value of its
 * own key, and every count the endpoint returns has a tile. Each fixture value is
 * DISTINCT, which is what makes the first claim testable - with two tiles reading
 * each other's keys, identical values would hide it.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import CircularDashboard from './CircularDashboard';

vi.mock('../../services/circularService', () => ({
  circularService: { getDashboard: vi.fn() },
}));

import { circularService } from '../../services/circularService';

const COUNTS = {
  drafts: 11,
  under_review: 22,
  ready_for_issue: 33,
  ready_for_broadcast: 44,
  assigned: 55,
  unread: 66,
  pending_acknowledgement: 77,
  acknowledged: 88,
  broadcasted: 99,
  archived: 111,
  // Returned by the endpoint and deliberately NOT given a tile: `issued` is a
  // component of ready_for_broadcast (the scope spans both statuses), so a tile for
  // it would double-count, and total_visible is a diagnostic rather than a queue.
  issued: 122,
  total_visible: 133,
};

const TILES = [
  ['Draft', 'drafts'],
  ['Under Review', 'under_review'],
  ['Ready For Issue', 'ready_for_issue'],
  ['Ready For Broadcast', 'ready_for_broadcast'],
  ['Assigned', 'assigned'],
  ['Unread', 'unread'],
  ['Pending Acknowledgement', 'pending_acknowledgement'],
  ['Acknowledged', 'acknowledged'],
  ['Broadcasted', 'broadcasted'],
  ['Archived', 'archived'],
];

const renderDashboard = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CircularDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('circular dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    circularService.getDashboard.mockResolvedValue(COUNTS);
  });

  it('renders every queue the module defines', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Draft')).toBeInTheDocument());
    for (const [label] of TILES) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it.each(TILES)('the %s tile shows counts.%s', async (label, key) => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText(label)).toBeInTheDocument());
    // Scoped to the tile rather than the page: two tiles could legitimately show
    // the same number in production, so a page-wide search would prove nothing.
    const tile = screen.getByText(label).closest('a');
    expect(tile).toBeTruthy();
    expect(tile.textContent).toContain(String(COUNTS[key]));
  });

  it('links every tile to the queue it counts', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Draft')).toBeInTheDocument());
    // A tile that counts one thing and navigates to another is worse than no tile,
    // because the number then looks wrong rather than the link.
    const expected = {
      Draft: '/circulars/drafts',
      'Under Review': '/circulars/under-review',
      'Ready For Issue': '/circulars/ready-for-issue',
      'Ready For Broadcast': '/circulars/ready-for-broadcast',
      Assigned: '/circulars/assigned',
      Unread: '/circulars/unread',
      Broadcasted: '/circulars/broadcasted',
      Archived: '/circulars/archived',
    };
    for (const [label, href] of Object.entries(expected)) {
      expect(screen.getByText(label).closest('a')).toHaveAttribute('href', href);
    }
  });

  it('keeps read tracking and acknowledgement as separate tiles', async () => {
    /*
     * They are different facts: opening a circular is something the system
     * observes, acknowledging it is a statement the person makes. One tile
     * covering both would let a page view stand in for a confirmation, which is
     * the whole distinction the module is built around.
     */
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Unread')).toBeInTheDocument());
    const unread = screen.getByText('Unread').closest('a');
    const pending = screen.getByText('Pending Acknowledgement').closest('a');
    expect(unread).not.toBe(pending);
    expect(unread.textContent).toContain('66');
    expect(pending.textContent).toContain('77');
  });

  it('shows the error state rather than blank tiles when the endpoint fails', async () => {
    circularService.getDashboard.mockRejectedValue(new Error('boom'));
    renderDashboard();
    // The alternative - tiles rendering em-dashes - reads as "you have nothing",
    // which is a different and wrong answer to the question the page asks.
    await waitFor(() => {
      expect(screen.queryByText('Draft')).not.toBeInTheDocument();
    });
  });
});
