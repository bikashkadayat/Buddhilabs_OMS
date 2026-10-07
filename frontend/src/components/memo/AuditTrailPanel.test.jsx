import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const ENTRIES = [
  {
    id: 'a1', action: 'Submit', transition: 'sent_for_review', actor: 'Asha Rai',
    actor_id: 'u1', at: '2026-08-12T08:30:00Z', ip_address: '203.0.113.7',
    user_agent: 'Mozilla/5.0', remarks: '', metadata: {},
  },
  {
    id: 'a2', action: 'Update', transition: 'reviewed', actor: 'Bikash Thapa',
    actor_id: 'u2', at: '2026-08-12T09:30:00Z', ip_address: '203.0.113.9',
    user_agent: 'Mozilla/5.0', remarks: 'Figures reconcile.', metadata: {},
  },
];

vi.mock('../../services/memoService', () => ({
  memoService: { getAuditTrail: vi.fn() },
}));

import { memoService } from '../../services/memoService';
import AuditTrailPanel from './AuditTrailPanel';

const renderPanel = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><AuditTrailPanel memoId="m1" /></QueryClientProvider>,
  );
};

const openPanel = () => fireEvent.click(screen.getByRole('button', { name: /Audit Trail/ }));

describe('AuditTrailPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('does not fetch until it is opened', () => {
    memoService.getAuditTrail.mockResolvedValue(ENTRIES);
    renderPanel();
    // Reading a memo should not cost a request for a panel nobody expanded.
    expect(memoService.getAuditTrail).not.toHaveBeenCalled();
  });

  it('shows user, action, IP and remarks once opened', async () => {
    memoService.getAuditTrail.mockResolvedValue(ENTRIES);
    renderPanel();
    openPanel();

    expect(await screen.findByText('Bikash Thapa')).toBeInTheDocument();
    expect(screen.getByText('203.0.113.9')).toBeInTheDocument();
    expect(screen.getByText('Figures reconcile.')).toBeInTheDocument();
    // The transition is shown under the action, humanised.
    expect(screen.getByText('sent for review')).toBeInTheDocument();
  });

  it('removes itself entirely when the caller is not entitled to the trail', async () => {
    // The endpoint 403s for anyone below HR. Showing a locked section would only
    // advertise data the viewer cannot have.
    memoService.getAuditTrail.mockRejectedValue({ response: { status: 403 } });
    const { container } = renderPanel();
    openPanel();
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('says so when there is nothing recorded', async () => {
    memoService.getAuditTrail.mockResolvedValue([]);
    renderPanel();
    openPanel();
    expect(await screen.findByText('No audit entries recorded.')).toBeInTheDocument();
  });

  it('collapses again without refetching', async () => {
    memoService.getAuditTrail.mockResolvedValue(ENTRIES);
    renderPanel();
    openPanel();
    await screen.findByText('Bikash Thapa');

    fireEvent.click(screen.getByRole('button', { name: /Audit Trail/ }));
    expect(screen.queryByText('Bikash Thapa')).not.toBeInTheDocument();
    expect(memoService.getAuditTrail).toHaveBeenCalledTimes(1);
  });
});
