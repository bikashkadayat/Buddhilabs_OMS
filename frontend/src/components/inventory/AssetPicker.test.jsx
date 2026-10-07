import React, { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/inventoryService', () => ({
  inventoryService: { items: vi.fn() },
}));

import { inventoryService } from '../../services/inventoryService';
import AssetPicker from './AssetPicker';

const LAPTOP_A = {
  id: 'a1', asset_code: 'NIF-INV-0001', name: 'Dell Latitude 5420',
  serial_number: 'SN-5420-A', warranty_expiry: '2027-03-10',
  current_holder: 'Bikash Kadayat', current_holder_id: 'u1',
  department_name: 'ICT Department', category_name: 'Laptop',
  condition_display: 'Good', status: 'assigned', status_display: 'Assigned',
  purchase_date: '2024-03-10',
};
const LAPTOP_B = {
  ...LAPTOP_A, id: 'a2', asset_code: 'NIF-INV-0002',
  current_holder: 'Sanjaya Poudel', department_name: 'Operations',
  condition_display: 'Fair',
};
const IN_STORE = {
  ...LAPTOP_A, id: 'a3', asset_code: 'NIF-INV-0003', name: 'Spare monitor',
  current_holder: null, current_holder_id: null, status: 'available',
  status_display: 'Available',
};

const Harness = (props) => {
  const [value, setValue] = useState(null);
  return <AssetPicker value={value} onChange={setValue} {...props} />;
};

const renderPicker = (props = {}) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}><Harness {...props} /></QueryClientProvider>,
  );
};

beforeEach(() => {
  inventoryService.items.mockReset();
  inventoryService.items.mockResolvedValue([LAPTOP_A, LAPTOP_B, IN_STORE]);
});

describe('the smart asset selector', () => {
  it('shows what tells two identically-named assets apart', async () => {
    renderPicker();
    await userEvent.click(screen.getByRole('combobox'));

    const options = await screen.findAllByRole('option');
    const first = within(options[0]);
    // The whole point: same name, different code, owner, department and condition.
    expect(first.getByText('NIF-INV-0001')).toBeInTheDocument();
    expect(first.getByText('Bikash Kadayat')).toBeInTheDocument();
    expect(first.getByText('ICT Department')).toBeInTheDocument();
    expect(first.getByText('Good')).toBeInTheDocument();

    const second = within(options[1]);
    expect(second.getByText('Sanjaya Poudel')).toBeInTheDocument();
    expect(second.getByText('Operations')).toBeInTheDocument();
  });

  it('asks the SERVER to search, rather than filtering what it already has', async () => {
    renderPicker();
    await userEvent.click(screen.getByRole('combobox'));
    await screen.findAllByRole('option');

    await userEvent.type(screen.getByRole('combobox'), 'Bikash');

    // Filtering in the browser would only ever search the rows already fetched,
    // so an asset absent from them would read as "no matches" rather than "not
    // loaded" - and the two look identical to the person typing.
    await waitFor(() => expect(inventoryService.items).toHaveBeenCalledWith(
      expect.objectContaining({ search: 'Bikash' })));
  });

  it('passes the caller-supplied filter through to the server', async () => {
    renderPicker({ params: { status: 'assigned' } });
    await userEvent.click(screen.getByRole('combobox'));
    await waitFor(() => expect(inventoryService.items).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'assigned' })));
  });

  it('auto-populates all ten facts the brief names from the record it selected', async () => {
    renderPicker();
    await userEvent.click(screen.getByRole('combobox'));
    const options = await screen.findAllByRole('option');
    await userEvent.click(within(options[0]).getByRole('button'));

    const facts = within(screen.getByTestId('asset-info-card'));
    expect(facts.getByText('Bikash Kadayat')).toBeInTheDocument();
    expect(facts.getByText('ICT Department')).toBeInTheDocument();
    expect(facts.getByText('Laptop')).toBeInTheDocument();
    expect(facts.getByText('Good')).toBeInTheDocument();
    expect(facts.getByText('2024-03-10')).toBeInTheDocument();
    // Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD: the four the earlier summary
    // left out - and serial and warranty are the two that settle "is this the
    // laptop in front of me".
    expect(facts.getByText('NIF-INV-0001')).toBeInTheDocument();
    expect(facts.getByText('SN-5420-A')).toBeInTheDocument();
    expect(facts.getByText('2027-03-10')).toBeInTheDocument();
    expect(facts.getByText('Assigned')).toBeInTheDocument();
    expect(screen.getByTestId('asset-info-card').querySelectorAll('dt')).toHaveLength(10);
    // The list closes once something is chosen.
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
  });

  it('says an asset is in store rather than leaving the owner blank', async () => {
    renderPicker();
    await userEvent.click(screen.getByRole('combobox'));
    const options = await screen.findAllByRole('option');
    await userEvent.click(within(options[2]).getByRole('button'));

    expect(within(screen.getByTestId('asset-info-card'))
      .getByText('In store')).toBeInTheDocument();
    // And it warns, because there is no custody to transfer.
    expect(screen.getByRole('status')).toHaveTextContent(/no custody to transfer/i);
  });

  it('can be driven from the keyboard', async () => {
    renderPicker();
    const box = screen.getByRole('combobox');
    await userEvent.click(box);
    await screen.findAllByRole('option');

    await userEvent.keyboard('{ArrowDown}{Enter}');

    await waitFor(() => expect(screen.getByTestId('asset-info-card'))
      .toHaveTextContent('Sanjaya Poudel'));
  });

  it('lets the choice be changed without reloading the page', async () => {
    renderPicker();
    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(within((await screen.findAllByRole('option'))[0]).getByRole('button'));

    await userEvent.click(screen.getByRole('button', { name: /change/i }));

    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(screen.queryByTestId('asset-info-card')).not.toBeInTheDocument();
  });

  it('says so when nothing matches', async () => {
    inventoryService.items.mockResolvedValue([]);
    renderPicker({ emptyHint: 'No assigned asset matches.' });
    await userEvent.click(screen.getByRole('combobox'));

    expect(await screen.findByText('No assigned asset matches.')).toBeInTheDocument();
  });
});
