import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../services/inventoryService', () => ({
  inventoryService: { item: vi.fn(), assignments: vi.fn() },
}));
import { inventoryService } from '../../services/inventoryService';
import InventoryItemDetail from './InventoryItemDetail';

const baseItem = {
  id: 'i1',
  name: 'Test Laptop',
  asset_code: 'NIF-INV-0001',
  asset_type_display: 'IT / Computing',
  category_name: 'Laptops',
  status_display: 'Assigned',
  condition_display: 'Good',
  specifications: {},
};

const renderWith = async (overrides) => {
  inventoryService.item.mockResolvedValue({ ...baseItem, ...overrides });
  inventoryService.assignments.mockResolvedValue([]);
  // Phase 70 mounted the asset-history panel on this page, and that panel uses
  // react-query. The provider is what the real app already supplies; without it
  // the page throws before any assertion below runs. The assertions themselves are
  // unchanged - only the setup caught up with what the page now depends on.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><InventoryItemDetail /></MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByText('Test Laptop')).toBeInTheDocument());
};

const receipt = () => screen.queryByRole('button', { name: /handover receipt/i });

describe('InventoryItemDetail — handover receipt gate', () => {
  beforeEach(() => vi.clearAllMocks());

  // Regression: the gate used to test for status 'approved', which is a
  // TakeOutRequest status an ITEM can never have — a permanently dead condition.
  it.each(['assigned', 'out'])('shows the receipt for a held item (%s)', async (status) => {
    await renderWith({ status, current_holder: 'Alice T' });
    expect(receipt()).toBeInTheDocument();
  });

  it('hides the receipt when nobody holds the item', async () => {
    await renderWith({ status: 'available', current_holder: null });
    expect(receipt()).not.toBeInTheDocument();
  });

  it('hides the receipt for a retired item with no holder', async () => {
    await renderWith({ status: 'retired', current_holder: null });
    expect(receipt()).not.toBeInTheDocument();
  });

  it('never renders on the impossible "approved" item status', async () => {
    // If this ever passes with a holder, the dead gate has been reintroduced.
    await renderWith({ status: 'approved', current_holder: 'Alice T' });
    expect(receipt()).not.toBeInTheDocument();
  });
});
