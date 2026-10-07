import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * The inventory register (Phase D1).
 *
 * The polish is visual, so most of it belongs to the visual QA. What is pinned
 * here is the handful of things a restyle can quietly break and a screenshot
 * cannot catch:
 *
 *   - the counts come from the DASHBOARD, not from the rows on screen. The
 *     status filter runs on the server, so `items` holds one filter's rows;
 *     counting it would print "Available (3)" above three assigned assets.
 *   - "no assets at all" and "nothing at this filter" are different absences
 *     and must not offer the same way out.
 *   - a personal-scope user gets no summary rather than a crash - the endpoint
 *     returns a payload with no `counts` key for them.
 *   - every field in the panel has an accessible name. The markup this phase
 *     replaced put a bare <label> beside its input with no htmlFor, so none of
 *     them did.
 */

vi.mock('../../services/inventoryService', () => ({
  inventoryService: {
    items: vi.fn(), categories: vi.fn(), employees: vi.fn(),
    createItem: vi.fn(), updateItem: vi.fn(),
    assignItem: vi.fn(), handoverItem: vi.fn(), returnItem: vi.fn(),
  },
  // The dashboard hangs off assetLifecycle, not inventoryService. Mocked from
  // the right object so this file cannot pass against a call the real module
  // does not export.
  assetLifecycle: { dashboard: vi.fn(), report: vi.fn() },
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role }) }));

import { inventoryService, assetLifecycle } from '../../services/inventoryService';
import InventoryList from './InventoryList';

let role = 'admin';

const ORG_COUNTS = {
  total_assets: 42, available: 12, assigned: 20, maintenance: 3,
  retired: 5, disposed: 2, taken_out: 7, on_order: 4, awaiting_check_in: 1,
};

const asset = (over = {}) => ({
  id: 'i1', asset_code: 'NIF-INV-0001', name: 'Dell Latitude 5540',
  asset_type_display: 'IT / Computing', category_name: 'Laptops',
  status: 'available', status_display: 'Available',
  brand: '', model: '', serial_number: '', assigned_to_name: null, ...over,
});

/*
 * `roles` is part of the real /inventory/dashboard/ payload and is now what the
 * page reads to decide whether to offer Add / Edit / Assign - Phase
 * ASSET-TRANSFER-GOVERNANCE, after a Department Head was offered all three and
 * the server refused every one. Defaulted to a writer so the existing tests keep
 * testing what they were written for; the read-only case is asserted below.
 */
const WRITER = { roles: { can_manage_assets: true, can_browse_register: true } };
const READER = { roles: { can_manage_assets: false, can_browse_register: true } };

const mount = async ({ items = [asset()],
                       dashboard = { scope: 'organisation', counts: ORG_COUNTS, ...WRITER } } = {}) => {
  inventoryService.items.mockResolvedValue(items);
  inventoryService.categories.mockResolvedValue([{ id: 'c1', name: 'Laptops' }]);
  assetLifecycle.dashboard.mockResolvedValue(dashboard);
  // Department options for the Phase ASSET-TRANSFER-GOVERNANCE filter row.
  assetLifecycle.report.mockResolvedValue({ rows: [
    { department_id: 'd1', department: 'Engineering', total: 12 },
  ] });
  const view = render(<MemoryRouter><InventoryList /></MemoryRouter>);
  await waitFor(() => expect(inventoryService.items).toHaveBeenCalled());
  return view;
};

beforeEach(() => {
  vi.clearAllMocks();
  role = 'admin';
  inventoryService.employees.mockResolvedValue([]);
});

describe('page header', () => {
  it('names the page and offers the primary action to a manager', async () => {
    await mount();
    expect(screen.getByRole('heading', { level: 1, name: 'Inventory Register' })).toBeInTheDocument();
    expect(screen.getByText('Manage organisational assets and equipment')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add asset/ })).toBeInTheDocument();
  });

  it('keeps a way back up the hierarchy', async () => {
    await mount();
    expect(screen.getByRole('link', { name: 'Assets' })).toHaveAttribute('href', '/assets');
  });

  it('hides Add asset from somebody the server says may not write', async () => {
    // Was `role = 'maker'`. The role is no longer what decides: an inventory
    // officer IS a maker and may write, and a Department Head is senior and may
    // not. The server's answer is the one that counts, so that is what is mocked.
    role = 'maker';
    await mount({ dashboard: { scope: 'organisation', counts: ORG_COUNTS, ...READER } });
    expect(screen.queryByRole('button', { name: /Add asset/ })).toBeNull();
  });
});

describe('summary', () => {
  it('reports the whole register, not the filtered page', async () => {
    // One row on screen; forty-two in the register. Deriving from `items` would
    // print 1 here.
    await mount({ items: [asset()] });
    const summary = await screen.findByRole('list', { name: '' }).catch(() => null);
    expect(summary || document.querySelector('.inv-summary')).toBeTruthy();
    const cards = document.querySelectorAll('.inv-sum');
    expect(cards).toHaveLength(5);
    expect(within(cards[0]).getByText('42')).toBeInTheDocument();
    expect(within(cards[1]).getByText('12')).toBeInTheDocument();
    expect(within(cards[3]).getByText('3')).toBeInTheDocument();
  });

  it('filters the register when a card is pressed', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /12\s*Available/ }));
    await waitFor(() =>
      expect(inventoryService.items).toHaveBeenLastCalledWith({ status: 'available' }));
  });

  it('shows nothing at all in personal scope rather than crashing', async () => {
    role = 'maker';
    await mount({ dashboard: { scope: 'self', assigned_items: [], ...WRITER } });
    expect(document.querySelector('.inv-summary')).toBeNull();
    // The register itself still renders.
    expect(screen.getByText('Dell Latitude 5540')).toBeInTheDocument();
  });

  it('survives a dashboard that fails, since the register does not depend on it', async () => {
    inventoryService.items.mockResolvedValue([asset()]);
    inventoryService.categories.mockResolvedValue([]);
    assetLifecycle.dashboard.mockRejectedValue(new Error('403'));
    render(<MemoryRouter><InventoryList /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Dell Latitude 5540')).toBeInTheDocument());
    expect(document.querySelector('.inv-summary')).toBeNull();
  });
});

describe('filter chips', () => {
  it('labels each chip with the register-wide count under its own name', async () => {
    await mount();
    // "Taken Out" reads taken_out, "On Order" reads on_order - the register and
    // the dashboard use different words for the same status.
    expect(screen.getByRole('button', { name: /Taken Out\s*7/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /On Order\s*4/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Awaiting Check-In\s*1/ })).toBeInTheDocument();
  });

  it('leaves a chip bare when the server publishes no count for it', async () => {
    await mount();
    const archived = screen.getByRole('button', { name: /^Archived$/ });
    expect(within(archived).queryByText(/\d/)).toBeNull();
  });

  it('marks the active chip with aria-pressed', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /Maintenance\s*3/ }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Maintenance\s*3/ })).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.getByRole('button', { name: /All\s*42/ })).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('empty states', () => {
  it('offers to add the first asset when the register itself is empty', async () => {
    await mount({ items: [] });
    expect(screen.getByText('You haven’t added any assets yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add first asset' })).toBeInTheDocument();
  });

  it('offers the way back instead when only the filter is empty', async () => {
    // Offering "Add first asset" to somebody who filtered to Retired would be
    // answering a question they did not ask.
    inventoryService.items.mockResolvedValue([]);
    inventoryService.categories.mockResolvedValue([]);
    assetLifecycle.dashboard.mockResolvedValue({ scope: 'organisation', counts: ORG_COUNTS, ...WRITER });
    render(<MemoryRouter initialEntries={['/inventory?status=retired']}><InventoryList /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Nothing at this status')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Add first asset' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show all assets' }));
    await waitFor(() => expect(inventoryService.items).toHaveBeenLastCalledWith({}));
  });

  it('does not offer to add anything to somebody who cannot', async () => {
    role = 'maker';
    await mount({ items: [], dashboard: { scope: 'self', ...READER } });
    expect(screen.getByText('You haven’t added any assets yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add first asset' })).toBeNull();
  });

  it('states the failure and offers the retry when loading breaks', async () => {
    inventoryService.items.mockRejectedValue({ response: { data: { detail: 'Server unavailable.' } } });
    inventoryService.categories.mockResolvedValue([]);
    assetLifecycle.dashboard.mockResolvedValue({ scope: 'self', ...WRITER });
    render(<MemoryRouter><InventoryList /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Server unavailable.')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('add / edit panel', () => {
  const open = async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /Add asset/ }));
    return screen.getByRole('dialog');
  };

  it('is a labelled dialog split into the four sections', async () => {
    const dialog = await open();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Add asset');
    for (const t of ['Basic information', 'Asset details', 'Documents', 'Specifications']) {
      expect(within(dialog).getByRole('heading', { name: t })).toBeInTheDocument();
    }
  });

  it('gives every control a real accessible name', async () => {
    const dialog = await open();
    for (const label of ['Name', 'Asset type', 'Category', 'Serial number', 'Condition',
      'Status', 'Purchase date', 'Vendor / supplier', 'Location', 'Warranty start',
      'Notes', 'Photo', 'Invoice / warranty card', 'Document label', 'Brand', 'CPU']) {
      expect(within(dialog).getByLabelText(new RegExp(`^${label}`))).toBeInTheDocument();
    }
  });

  it('marks the two required fields for assistive technology, not only in red', async () => {
    const dialog = await open();
    // The asterisk is aria-hidden, so the requirement has to reach a screen
    // reader some other way: the accessible name carries it, and so does the
    // control's own required attribute.
    expect(within(dialog).getByRole('textbox', { name: /Name.*required/ })).toBeRequired();
    expect(within(dialog).getByRole('combobox', { name: /Category.*required/ })).toBeRequired();
    // And nothing else claims to be.
    expect(within(dialog).getByRole('textbox', { name: /^Serial number/ })).not.toBeRequired();
  });

  it('hides the specifications section for a non-IT asset', async () => {
    const dialog = await open();
    fireEvent.change(within(dialog).getByLabelText(/^Asset type/), { target: { value: 'furniture' } });
    expect(within(screen.getByRole('dialog')).queryByRole('heading', { name: 'Specifications' })).toBeNull();
  });

  it('keeps Save disabled until name and category are both given', async () => {
    const dialog = await open();
    const save = within(dialog).getByRole('button', { name: /Save asset/ });
    expect(save).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'Chair' } });
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: /Save asset/ })).toBeDisabled();
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText(/^Category/), { target: { value: 'c1' } });
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: /Save asset/ })).toBeEnabled();
  });

  it('locks Status on an existing asset, so only the lifecycle moves it', async () => {
    await mount({ items: [asset({ status: 'assigned', status_display: 'Assigned' })] });
    fireEvent.click(screen.getAllByTitle(/edit/i)[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(/^Status/)).toBeDisabled();
  });
});


// --------------------------------------------------------------------------- #
// Phase ASSET-TRANSFER-GOVERNANCE
// --------------------------------------------------------------------------- #
describe('a reader who may not write', () => {
  it('is offered no way to add, edit or assign an asset', async () => {
    await mount({
      dashboard: {
        scope: 'organisation', counts: ORG_COUNTS,
        // A Department Head: sees the whole register, changes none of it.
        roles: { can_manage_assets: false, can_browse_register: true },
      },
    });

    expect(screen.queryByRole('button', { name: /Add asset/ })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: /Actions/ })).toBeNull();
    // The register itself still renders: read-only is not the same as locked out.
    expect(screen.getByText('NIF-INV-0001')).toBeInTheDocument();
  });
});
