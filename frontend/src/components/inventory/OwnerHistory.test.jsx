import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/inventoryService', () => ({ assetLifecycle: { custody: vi.fn() } }));

import { assetLifecycle } from '../../services/inventoryService';
import OwnerHistory from './OwnerHistory';

const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><OwnerHistory itemId="i1" /></MemoryRouter>
  </QueryClientProvider>,
);

// The brief's own example, as the server writes it.
const TIMELINE = [
  { kind: 'assigned', label: 'Assigned to Bikash Kadayat', date: '2025-01-05', by: 'Store Officer',
    reference: '', from_owner: '', is_current: false },
  { kind: 'transferred', label: 'Transferred to Raj Kumar', date: '2025-06-01', by: 'HR',
    reference: 'TRF-2025-0004', from_owner: 'Bikash Kadayat', is_current: false },
  { kind: 'transferred', label: 'Transferred to Amar', date: '2026-02-11', by: 'HR',
    reference: 'TRF-2026-0002', from_owner: 'Raj Kumar', is_current: false },
  { kind: 'returned', label: 'Returned to Inventory', date: '2026-08-30', by: '',
    reference: '', from_owner: 'Amar', is_current: false },
];

// clearAllMocks, not custody.mockReset(): under vitest 2.1 a rejected call on a
// mock that had been mockReset() was re-raised as the test's own error, although
// the component handled the rejection - the refusal test failed with "403" while
// rendering exactly what it should.
beforeEach(() => vi.clearAllMocks());

describe('owner history', () => {
  it('reads as the chronological sentences the server wrote, in order', async () => {
    assetLifecycle.custody.mockResolvedValue({ owner_timeline: TIMELINE });
    mount();
    const steps = await screen.findAllByRole('listitem');
    expect(steps.map((li) => within(li).getByRole('strong', { hidden: true })?.textContent
      ?? li.querySelector('strong').textContent)).toEqual([
      'Assigned to Bikash Kadayat', 'Transferred to Raj Kumar',
      'Transferred to Amar', 'Returned to Inventory',
    ]);
  });

  it('names the approved request behind each transfer, and who it came from', async () => {
    assetLifecycle.custody.mockResolvedValue({ owner_timeline: TIMELINE });
    mount();
    expect(await screen.findByRole('link', { name: 'TRF-2025-0004' })).toBeInTheDocument();
    expect(screen.getByText(/from Bikash Kadayat/)).toBeInTheDocument();
  });

  it('marks who holds it now', async () => {
    assetLifecycle.custody.mockResolvedValue({ owner_timeline: [
      { ...TIMELINE[0], is_current: true },
    ] });
    mount();
    expect(await screen.findByText('Current owner')).toBeInTheDocument();
  });

  it('says so when the asset has never been assigned', async () => {
    assetLifecycle.custody.mockResolvedValue({ owner_timeline: [] });
    mount();
    expect(await screen.findByText(/never been assigned/)).toBeInTheDocument();
  });

  it('renders nothing for a reader the server refuses', async () => {
    assetLifecycle.custody.mockRejectedValue({ response: { status: 403 } });
    const { container } = mount();
    await new Promise((r) => setTimeout(r, 50));
    expect(container).toBeEmptyDOMElement();
  });
});
