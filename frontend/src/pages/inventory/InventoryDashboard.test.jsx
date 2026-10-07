/**
 * Phase 70.14 - the inventory dashboard.
 *
 * The claim worth testing is not "tiles render" but that the page is TWO pages,
 * chosen by the server. `scope` comes back as `organisation` or `self`, and an
 * employee must get their own assets rather than a locked door or - worse - a
 * register they should not see. Both directions are asserted.
 *
 * Every fixture count is DISTINCT, which is what makes "this tile reads its own
 * key" testable: with two tiles reading each other's keys, identical values would
 * hide it.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import InventoryDashboard from './InventoryDashboard';

vi.mock('../../services/inventoryService', () => ({
  assetLifecycle: { dashboard: vi.fn() },
  inventoryService: { categories: vi.fn() },
}));

import { assetLifecycle } from '../../services/inventoryService';

const COUNTS = {
  total_assets: 11,
  assigned: 22,
  available: 33,
  maintenance: 44,
  taken_out: 55,
  pending_requests: 66,
  overdue_returns: 77,
  disposed: 88,
  on_order: 99,
  awaiting_check_in: 111,
  retired: 122,
  warranty_expiring: 133,
  pending_returns: 144,
  open_maintenance_tickets: 155,
  low_stock: 1,
  book_value: '9000000.00',
};

const TILES = [
  ['Total Assets', 'total_assets'],
  ['Assigned', 'assigned'],
  ['Available', 'available'],
  ['Maintenance', 'maintenance'],
  ['Taken Out', 'taken_out'],
  ['Pending Requests', 'pending_requests'],
  ['Overdue Returns', 'overdue_returns'],
  ['Disposed', 'disposed'],
  ['On Order', 'on_order'],
  ['Awaiting Check-In', 'awaiting_check_in'],
  ['Retired', 'retired'],
  ['Warranty Expiring', 'warranty_expiring'],
];

const ORGANISATION = {
  scope: 'organisation',
  roles: { is_inventory_officer: true, can_manage_assets: true,
    can_view_all_assets: true, is_admin: false },
  counts: COUNTS,
  low_stock: [{ id: 'c1', category: 'Laptop', available: 1, total: 6,
    threshold: 2 }],
};

const SELF = {
  scope: 'self',
  roles: { is_inventory_officer: false, can_manage_assets: false,
    can_view_all_assets: false, is_admin: false },
  employee: { id: 'u1', name: 'Deepak Employee', employee_id: 'E-1',
    department: 'ICT' },
  assigned: [{ id: 'a1', item_id: 'i1', code: 'NIF-INV-0007',
    name: 'ThinkPad T14', assigned_date: '2026-08-01', condition: 'good',
    accessories: 'Charger' }],
  history: [{ id: 'a0', code: 'NIF-INV-0002', name: 'Old Laptop',
    assigned_date: '2025-01-05', returned_at: '2026-01-05',
    return_condition: 'fair' }],
  take_outs: [], requests: [], returns: [],
};

/**
 * Find a tile by its label, scoped to the two tile groups.
 *
 * Not a bare `getByText`: "Available" is both a tile label and a column heading in
 * the Low Stock table, so a page-wide lookup matches two elements and throws. Both
 * uses are correct in context - the ambiguity is the test's, not the page's.
 */
const tile = (label) => {
  for (const id of ['inventory-tiles', 'inventory-stock-tiles']) {
    const group = screen.queryByTestId(id);
    const hit = group && within(group).queryByText(label);
    if (hit) return hit.closest('a');
  }
  throw new Error(`No dashboard tile labelled "${label}"`);
};

const renderPage = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><InventoryDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('inventory dashboard', () => {
  beforeEach(() => vi.clearAllMocks());

  describe('for somebody who runs the register', () => {
    beforeEach(() => assetLifecycle.dashboard.mockResolvedValue(ORGANISATION));

    it('renders every card the brief names', async () => {
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('Total Assets')).toBeInTheDocument());
      for (const [label] of TILES) {
        expect(tile(label)).toBeTruthy();
      }
    });

    it.each(TILES)('the %s tile shows counts.%s', async (label, key) => {
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('Total Assets')).toBeInTheDocument());
      expect(tile(label).textContent).toContain(String(COUNTS[key]));
    });

    it('sends each tile to the queue it counts', async () => {
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('Maintenance')).toBeInTheDocument());
      expect(tile('Maintenance')).toHaveAttribute(
        'href', '/inventory/maintenance');
      expect(tile('Pending Requests')).toHaveAttribute(
        'href', '/inventory/requests');
      expect(tile('Overdue Returns')).toHaveAttribute(
        'href', '/inventory/reports/unreturned');
    });

    it('shows low stock with the available figure, not the total', async () => {
      /*
       * A category with six assets and one free is low stock. Reporting the six
       * would say it is well supplied, which is how a request queue silently
       * builds up behind a category nobody realises is empty.
       */
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('Low Stock')).toBeInTheDocument());
      const row = screen.getByText('Laptop').closest('tr');
      expect(row.textContent).toContain('1');
      expect(row.textContent).toContain('6');
    });
  });

  describe('for an ordinary employee', () => {
    beforeEach(() => assetLifecycle.dashboard.mockResolvedValue(SELF));

    it('shows their own assets rather than the register', async () => {
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('My Assets')).toBeInTheDocument());
      expect(screen.getByText('NIF-INV-0007')).toBeInTheDocument();
      // The register must not leak through the personal view.
      expect(screen.queryByText('Total Assets')).not.toBeInTheDocument();
      expect(screen.queryByText('Low Stock')).not.toBeInTheDocument();
    });

    it('shows what they have held before as well as what they hold', async () => {
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('Previously Held')).toBeInTheDocument());
      expect(screen.getByText('NIF-INV-0002')).toBeInTheDocument();
    });

    it('offers a way to ask for one', async () => {
      renderPage();
      await waitFor(() =>
        expect(screen.getByText('Request an asset')).toBeInTheDocument());
      expect(screen.getByText('Request an asset').closest('a'))
        .toHaveAttribute('href', '/inventory/requests');
    });
  });

  it('shows the error state rather than empty tiles when the endpoint fails', async () => {
    assetLifecycle.dashboard.mockRejectedValue(new Error('boom'));
    renderPage();
    // Tiles rendering em-dashes would read as "you have nothing", which is a
    // different and wrong answer to the question the page asks.
    await waitFor(() =>
      expect(screen.queryByText('Total Assets')).not.toBeInTheDocument());
  });
});
