import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import EligibleAssetSelect from './EligibleAssetSelect';

/**
 * Phase 70.19-F/G.
 *
 * The defect these guard against is not "the wrong asset was listed" — it is that
 * four different situations all rendered as one empty dropdown, so the UI could not
 * tell the user whether to wait, retry, or go and ask for an asset. Each test below
 * pins one of those situations to a message a person can act on.
 */
const assets = [
  { id: 'a1', asset_code: 'LT-00045', name: 'Dell Latitude 7440', category_name: 'Laptop',
    current_holder: 'Bikash Kadayat', is_mine: true },
  { id: 'a2', asset_code: 'PR-00012', name: 'HP LaserJet Pro', category_name: 'Printer',
    current_holder: null, is_mine: false },
];

const base = { assets: [], value: '', onChange: vi.fn(), loading: false, error: null,
  emptyReason: null, onRetry: vi.fn(), disabled: false };

describe('EligibleAssetSelect', () => {
  it('shows a loading state rather than an empty control', () => {
    render(<EligibleAssetSelect {...base} loading />);
    expect(screen.getByText(/loading assets/i)).toBeInTheDocument();
  });

  it('shows the server-supplied reason when nothing is eligible', () => {
    render(<EligibleAssetSelect {...base}
      emptyReason="You currently have no take-out eligible assets. Contact your Inventory Officer." />);
    expect(screen.getByText(/no eligible assets found/i)).toBeInTheDocument();
    expect(screen.getByText(/contact your inventory officer/i)).toBeInTheDocument();
  });

  it('falls back to a usable message when the server sends no reason', () => {
    render(<EligibleAssetSelect {...base} />);
    expect(screen.getByText(/no eligible assets found/i)).toBeInTheDocument();
    expect(screen.getByText(/contact your inventory officer/i)).toBeInTheDocument();
  });

  it('surfaces a load failure instead of silently showing nothing', () => {
    render(<EligibleAssetSelect {...base} error="Request failed with status code 500" />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/error loading assets/i)).toBeInTheDocument();
    expect(screen.getByText(/request failed with status code 500/i)).toBeInTheDocument();
  });

  it('offers a retry that calls back', () => {
    const onRetry = vi.fn();
    render(<EligibleAssetSelect {...base} error="boom" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('renders code, name, category and holder for each asset', () => {
    render(<EligibleAssetSelect {...base} assets={assets} />);
    expect(screen.getByText('LT-00045')).toBeInTheDocument();
    expect(screen.getByText('Dell Latitude 7440')).toBeInTheDocument();
    expect(screen.getByText(/Laptop · Assigned to you/)).toBeInTheDocument();
    expect(screen.getByText(/Printer · In stock/)).toBeInTheDocument();
  });

  it('reports the selection by asset id', () => {
    const onChange = vi.fn();
    render(<EligibleAssetSelect {...base} assets={assets} onChange={onChange} />);
    fireEvent.click(screen.getByText('Dell Latitude 7440'));
    expect(onChange).toHaveBeenCalledWith('a1');
  });

  it('marks the selected asset for assistive tech', () => {
    render(<EligibleAssetSelect {...base} assets={assets} value="a2" />);
    const options = screen.getAllByRole('option');
    expect(options.find(o => o.getAttribute('aria-selected') === 'true'))
      .toHaveTextContent('HP LaserJet Pro');
  });

  it('never renders an empty option list as a bare control', () => {
    const { container } = render(<EligibleAssetSelect {...base} />);
    // The old bug: a <select> whose only child was the placeholder.
    expect(container.querySelector('select')).toBeNull();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('filters on search once the list is long enough to need it', () => {
    const many = Array.from({ length: 6 }, (_, i) => ({
      id: `x${i}`, asset_code: `AS-0000${i}`, name: i === 3 ? 'Epson Projector' : `Laptop ${i}`,
      category_name: i === 3 ? 'Projector' : 'Laptop', current_holder: null, is_mine: false,
    }));
    render(<EligibleAssetSelect {...base} assets={many} />);
    fireEvent.change(screen.getByLabelText(/search eligible assets/i),
      { target: { value: 'Epson' } });
    expect(screen.getByText('Epson Projector')).toBeInTheDocument();
    expect(screen.queryByText('Laptop 0')).toBeNull();
  });

  it('explains a search that matches nothing', () => {
    const many = Array.from({ length: 6 }, (_, i) => ({
      id: `x${i}`, asset_code: `AS-0000${i}`, name: `Laptop ${i}`,
      category_name: 'Laptop', current_holder: null, is_mine: false,
    }));
    render(<EligibleAssetSelect {...base} assets={many} />);
    fireEvent.change(screen.getByLabelText(/search eligible assets/i),
      { target: { value: 'zzzz' } });
    expect(screen.getByText(/no assets match/i)).toBeInTheDocument();
    expect(screen.getByText(/clear the search to see all 6/i)).toBeInTheDocument();
  });
});
