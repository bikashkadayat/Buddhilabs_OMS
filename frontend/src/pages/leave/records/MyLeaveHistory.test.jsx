import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../hooks/useLeaveRecords', () => ({
  useMyHistory: vi.fn(),
}));

import { useMyHistory } from '../../../hooks/useLeaveRecords';
import MyLeaveHistory from './MyLeaveHistory';

const renderPage = () =>
  render(
    <MemoryRouter>
      <MyLeaveHistory />
    </MemoryRouter>,
  );

describe('MyLeaveHistory', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useMyHistory.mockReturnValue({ isLoading: true });
    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state with retry', () => {
    const refetch = vi.fn();
    useMyHistory.mockReturnValue({ isError: true, error: new Error('boom'), refetch });
    renderPage();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('renders balances and monthly rows on success', () => {
    useMyHistory.mockReturnValue({
      isLoading: false,
      isError: false,
      data: {
        user: { full_name: 'Jane', role: 'maker' },
        year: 2026,
        balances: [{
          leave_type_code: 'ANNUAL', leave_type_name: 'Annual Leave',
          entitled_days: '18.00', used_days: '5.00', pending_days: '0.00',
          available_days: '13.00', carried_forward_days: '0.00',
        }],
        monthly_summaries: [{
          year: 2026, month: 6, approved_days: '2.00', pending_days: '0.00',
          total_leave_days: '2.00', working_days: 22, attendance_percentage: '91.00',
          by_type: { ANNUAL: '2.0' },
        }],
        recent_leaves: [{ id: '1', leave_type: 'annual', start_date: '2026-06-08', end_date: '2026-06-09', status: 'approved' }],
      },
    });
    renderPage();
    // "Annual Leave" / "13" legitimately appear in both the card and the
    // quick-stats sidebar, so match all occurrences.
    expect(screen.getAllByText('Annual Leave').length).toBeGreaterThan(0);
    expect(screen.getByText('June')).toBeInTheDocument();
    expect(screen.getAllByText('13').length).toBeGreaterThan(0); // available days
  });

  it('shows the empty state when there is no data', () => {
    useMyHistory.mockReturnValue({
      isLoading: false, isError: false,
      data: { user: {}, year: 2026, balances: [], monthly_summaries: [], recent_leaves: [] },
    });
    renderPage();
    expect(screen.getByText(/No leaves recorded/i)).toBeInTheDocument();
  });
});
