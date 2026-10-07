import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../../hooks/useWorkforce', () => ({ useMyWorkforce: vi.fn() }));
vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));

import { useMyWorkforce } from '../../hooks/useWorkforce';
import { useAuth } from '../../hooks/useAuth';
import Portal from './Portal';

const PAYLOAD = {
  date: '2026-08-04',
  employee: { id: 'e1', name: 'Ram Thapa', employee_id: 'NIFN-EMP-2020-0001', department: 'Engineering' },
  today: {
    status: 'late', check_in: '2026-08-04T06:45:00Z', check_out: null,
    working_hours: '0.00', regular_hours: '0.00', overtime_hours: '0.00',
    late_minutes: 45, is_wfh: false, comp_off_eligible: false, source: 'browser',
    can_check_in: false, can_check_out: true,
  },
  shift: { name: 'General Shift', start_time: '10:00', end_time: '18:00' },
  policy: { name: 'NIF Attendance Policy', office_start: '10:00', late_after: '11:45', half_day_after: '13:00' },
  month_to_date: { working_hours: '25.26', regular_hours: '25.26', overtime_hours: '2.50', late_days: 2, late_minutes: 60 },
  comp_off: { earned: 2, used: 1, available: 1, pending: 0.5 },
  leave_balances: [{ leave_type: 'annual', total_allocated: 12, used_so_far: 3, remaining: 9 }],
  wfh: { approved_today: true, counts: { pending: 1, approved: 2, rejected: 0, cancelled: 0 } },
  recent_attendance: [
    { date: '2026-08-04', status: 'late', check_in: '2026-08-04T06:45:00Z', check_out: null,
      working_hours: '0.00', overtime_hours: '0.00', late_minutes: 45, is_wfh: false, source: 'browser' },
  ],
  corrections: { open: 1, applied: 3, rejected: 0 },
};

const renderPage = (role = 'maker') => {
  useAuth.mockReturnValue({ role, user: { id: 'e1' } });
  return render(
    <MemoryRouter initialEntries={['/workforce']}>
      <Routes>
        <Route path="/workforce" element={<Portal />} />
        <Route path="/workforce/hr" element={<div>HR command center</div>} />
      </Routes>
    </MemoryRouter>,
  );
};

describe('Employee portal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useMyWorkforce.mockReturnValue({ isLoading: true });
    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state with retry', () => {
    const refetch = vi.fn();
    useMyWorkforce.mockReturnValue({ isError: true, error: new Error('boom'), refetch });
    renderPage();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it("renders today's status, hours and overtime", () => {
    useMyWorkforce.mockReturnValue({ data: PAYLOAD });
    renderPage();
    expect(screen.getAllByText('Late').length).toBeGreaterThan(0);
    expect(screen.getByText('Ram Thapa · Engineering · 2026-08-04')).toBeInTheDocument();
    expect(screen.getByText('45 min')).toBeInTheDocument();
    expect(screen.getByText('2.50')).toBeInTheDocument(); // MTD overtime
  });

  it('surfaces the rules that decided the status', () => {
    useMyWorkforce.mockReturnValue({ data: PAYLOAD });
    renderPage();
    expect(screen.getByText('11:45')).toBeInTheDocument();
    expect(screen.getByText('13:00')).toBeInTheDocument();
    expect(screen.getByText('General Shift')).toBeInTheDocument();
  });

  it('shows comp-off, leave balance and WFH state', () => {
    useMyWorkforce.mockReturnValue({ data: PAYLOAD });
    renderPage();
    expect(screen.getByText('You have approved work-from-home today.')).toBeInTheDocument();
    expect(screen.getByText('annual')).toBeInTheDocument();
    expect(screen.getByText('Not spendable until confirmed')).toBeInTheDocument();
  });

  it('offers only the action that is currently possible', () => {
    useMyWorkforce.mockReturnValue({ data: PAYLOAD });
    renderPage();
    // by role, not by text: "Check in"/"Check out" also appear as field labels
    expect(screen.getByRole('button', { name: /check out/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /check in/i })).not.toBeInTheDocument();
  });

  it('lists recent attendance', () => {
    useMyWorkforce.mockReturnValue({ data: PAYLOAD });
    renderPage();
    expect(screen.getByText('Recent attendance')).toBeInTheDocument();
  });

  it('redirects an admin to the command center', () => {
    useMyWorkforce.mockReturnValue({ data: PAYLOAD });
    renderPage('admin');
    expect(screen.getByText('HR command center')).toBeInTheDocument();
  });
});
