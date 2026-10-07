import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/inventoryService', () => ({
  inventoryService: { items: vi.fn() },
  assetLifecycle: { visibilityOptions: vi.fn(), report: vi.fn() },
}));

import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import AssetVisibility from './AssetVisibility';

const OPTIONS = {
  statuses: [{ value: 'assigned', label: 'Assigned' }, { value: 'available', label: 'Available' }],
  conditions: [{ value: 'good', label: 'Good' }, { value: 'damaged', label: 'Damaged' }],
  asset_types: [{ value: 'it', label: 'IT / Computing' }],
  departments: [{ value: 'd-ict', label: 'ICT Department' }, { value: 'd-ops', label: 'Operations' }],
  owners: [{ value: 'u1', label: 'Bikash Kadayat' }],
  read_only: true,
};
const ASSETS = [
  { id: 'a1', asset_code: 'NIF-INV-0001', name: 'Dell Latitude 5420', category_name: 'Laptop',
    department_name: 'ICT Department', current_holder: 'Bikash Kadayat',
    condition_display: 'Good', status_display: 'Assigned' },
  { id: 'a2', asset_code: 'NIF-INV-0002', name: 'Spare monitor', category_name: 'Monitor',
    department_name: 'Operations', current_holder: null,
    condition_display: 'Good', status_display: 'Available' },
];

const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><AssetVisibility /></MemoryRouter>
  </QueryClientProvider>,
);

beforeEach(() => {
  vi.clearAllMocks();
  assetLifecycle.visibilityOptions.mockResolvedValue(OPTIONS);
  assetLifecycle.report.mockResolvedValue({ rows: [
    { department: 'ICT Department', assets_owned: 1, assigned: 1, unassigned: 0,
      in_maintenance: 0, held_by_staff: 1 },
    { department: 'Operations', assets_owned: 1, assigned: 0, unassigned: 1,
      in_maintenance: 0, held_by_staff: 0 },
  ] });
  inventoryService.items.mockResolvedValue(ASSETS);
});

describe('asset visibility', () => {
  it('shows every department and every owner at once', async () => {
    mount();
    const departments = await screen.findByTestId('visibility-departments');
    expect(within(departments).getByText('ICT Department')).toBeInTheDocument();
    expect(within(departments).getByText('Operations')).toBeInTheDocument();

    const assets = await screen.findByTestId('visibility-assets');
    expect(within(assets).getByText('Bikash Kadayat')).toBeInTheDocument();
    expect(within(assets).getByText('Unassigned')).toBeInTheDocument();
  });

  it('offers the five filters the brief names', async () => {
    mount();
    await screen.findByTestId('visibility-assets');
    for (const label of ['Department', 'Owner', 'Asset type', 'Condition', 'Status']) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    // "Nobody" is a first-class owner answer.
    expect(screen.getByRole('option', { name: /Nobody/ })).toBeInTheDocument();
  });

  it('sends every filter to the server rather than narrowing in the browser', async () => {
    mount();
    await screen.findByTestId('visibility-assets');
    fireEvent.change(screen.getByLabelText('Department'), { target: { value: 'd-ops' } });
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'unassigned' } });
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'available' } });
    await waitFor(() => expect(inventoryService.items).toHaveBeenLastCalledWith({
      department: 'd-ops', owner: 'unassigned', status: 'available' }));
  });

  it('is read-only by construction: no write control of any kind', async () => {
    mount();
    await screen.findByTestId('visibility-assets');
    // Said twice on purpose: in the page's description and as a badge.
    expect(screen.getAllByText('Read-only').length).toBeGreaterThanOrEqual(1);
    const buttons = screen.queryAllByRole('button').map((b) => b.textContent);
    expect(buttons.filter((t) => /add|edit|assign|return|transfer|delete|approve/i.test(t)))
      .toEqual([]);
  });

  it('explains a refusal instead of showing an empty register', async () => {
    assetLifecycle.visibilityOptions.mockRejectedValue({ response: { status: 403,
      data: { detail: 'Asset Visibility is for Department Heads, HR, administrators and the store.' } } });
    mount();
    expect(await screen.findByText(/Asset Visibility is for Department Heads/)).toBeInTheDocument();
    expect(inventoryService.items).not.toHaveBeenCalled();
  });
});
