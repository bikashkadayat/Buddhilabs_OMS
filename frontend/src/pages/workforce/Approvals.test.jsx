import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../hooks/useWorkforce', () => ({
  useWfhSummary: vi.fn(), useWfhRequests: vi.fn(),
  useSubmitWfh: vi.fn(), useApproveWfh: vi.fn(), useRejectWfh: vi.fn(), useCancelWfh: vi.fn(),
  useCompOffSummary: vi.fn(), useConfirmCompOff: vi.fn(), useRejectCompOff: vi.fn(),
  useMyWorkforce: vi.fn(), useConflicts: vi.fn(),
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));

import * as wf from '../../hooks/useWorkforce';
import { useAuth } from '../../hooks/useAuth';
import WFH from './WFH';
import CompOff from './CompOff';
import Conflicts from './Conflicts';

const mutation = () => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false });
const mutations = ['useSubmitWfh', 'useApproveWfh', 'useRejectWfh', 'useCancelWfh',
  'useConfirmCompOff', 'useRejectCompOff'];

const WFH_SUMMARY = {
  summary: { requests: { pending: 3, approved: 9, rejected: 0, cancelled: 0 },
    approved_day_rows: 9, worked_from_home_days: 7, conversion: 0.78 },
  pending_queue: [{ id: 'w1', employee: 'Ram Thapa', employee_id: 'e1',
    start_date: '2026-08-05', end_date: '2026-08-05', reason: 'Fibre cut', created_at: '' }],
  can_approve: false,
};

const wfhRow = {
  id: 'w1', user: 'e1', user_name: 'Ram Thapa', start_date: '2026-08-05',
  end_date: '2026-08-05', reason: 'Fibre cut', status: 'pending', reviewed_by_name: null,
};

const COMP = {
  summary: { earned: 12, used: 4, available: 8, pending: 3 },
  pending_queue: [{ id: 'p1', employee: 'Ram Thapa', employee_id: 'e1',
    days: 1, source_date: '2026-08-01', note: 'Auto: 8h worked' }],
  history: [{ id: 'h1', employee: 'Ram Thapa', entry_type: 'earn', source: 'attendance',
    status: 'confirmed', days: 1, source_date: '2026-08-01', created_at: '' }],
  trend: [{ month: '2026-08', confirmed: 1, pending: 0 }],
  can_confirm: false,
};

const setupWfh = (role, canApprove) => {
  useAuth.mockReturnValue({ role, user: { id: 'e1' } });
  wf.useWfhSummary.mockReturnValue({ data: { ...WFH_SUMMARY, can_approve: canApprove } });
  wf.useWfhRequests.mockReturnValue({ data: { results: [wfhRow], count: 1 } });
  mutations.forEach((m) => wf[m].mockReturnValue(mutation()));
  return render(<MemoryRouter><WFH /></MemoryRouter>);
};

const setupComp = (role, canConfirm) => {
  useAuth.mockReturnValue({ role, user: { id: 'e1' } });
  wf.useMyWorkforce.mockReturnValue({ data: { comp_off: COMP.summary } });
  wf.useCompOffSummary.mockReturnValue({ data: { ...COMP, can_confirm: canConfirm } });
  mutations.forEach((m) => wf[m].mockReturnValue(mutation()));
  return render(<MemoryRouter><CompOff /></MemoryRouter>);
};

describe('WFH page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useAuth.mockReturnValue({ role: 'maker', user: { id: 'e1' } });
    wf.useWfhSummary.mockReturnValue({ isLoading: true });
    wf.useWfhRequests.mockReturnValue({ isLoading: true });
    mutations.forEach((m) => wf[m].mockReturnValue(mutation()));
    render(<MemoryRouter><WFH /></MemoryRouter>);
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
  });

  it('shows statistics to a manager', () => {
    setupWfh('checker', false);
    expect(screen.getByText('Conversion')).toBeInTheDocument();
    expect(screen.getByText('78%')).toBeInTheDocument();
  });

  it('does NOT offer approve to a department head', () => {
    setupWfh('checker', false);
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.getByText(/approved by HR/i)).toBeInTheDocument();
  });

  it('offers approve to HR', () => {
    setupWfh('approver', true);
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
  });

  it('hides statistics from an ordinary employee', () => {
    setupWfh('maker', false);
    expect(screen.queryByText('Conversion')).not.toBeInTheDocument();
  });

  it('hides the request button from an admin', () => {
    setupWfh('admin', true);
    expect(screen.queryByRole('button', { name: /request wfh/i })).not.toBeInTheDocument();
  });
});

describe('Comp-off page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a personal balance to everyone', () => {
    setupComp('maker', false);
    expect(screen.getByText('My balance')).toBeInTheDocument();
    expect(screen.getByText('Not spendable yet')).toBeInTheDocument();
  });

  it('hides the team queue from an ordinary employee', () => {
    setupComp('maker', false);
    expect(screen.queryByText('Team balance')).not.toBeInTheDocument();
  });

  it('shows the queue to a manager but no confirm button', () => {
    setupComp('checker', false);
    expect(screen.getByText('Pending with HR')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.getByText('Awaiting HR')).toBeInTheDocument();
  });

  it('offers confirm and reject to HR', () => {
    setupComp('approver', true);
    expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
  });

  it('renders history and the trend', () => {
    setupComp('approver', true);
    expect(screen.getByText('Earned per month')).toBeInTheDocument();
    expect(screen.getByText('History')).toBeInTheDocument();
  });
});

describe('Conflicts page', () => {
  beforeEach(() => vi.clearAllMocks());

  const data = {
    from: '2026-05-07', to: '2026-08-04',
    summary: { total: 1, employees_affected: 1, leave_days_double_counted: 1 },
    conflicts: [{
      employee_id: 'e1', employee_name: 'Ram Thapa', employee_code: 'NIFN-EMP-2020-0001',
      department: 'Engineering', date: '2026-07-20', leave_id: 'l1', leave_type: 'annual',
      day_portion: 'full', attendance_status: 'present', check_in: '2026-07-20T04:15:00Z',
      check_out: null, working_hours: '8.00', attendance_source: 'biometric',
      impact: 'Recorded as worked and deducted from the leave balance for the same day.',
    }],
  };

  it('shows a loading skeleton', () => {
    wf.useConflicts.mockReturnValue({ isLoading: true });
    render(<MemoryRouter><Conflicts /></MemoryRouter>);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state', () => {
    wf.useConflicts.mockReturnValue({ isError: true, error: new Error('x'), refetch: vi.fn() });
    render(<MemoryRouter><Conflicts /></MemoryRouter>);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('lists conflicts with the attendance that caused them', () => {
    wf.useConflicts.mockReturnValue({ data });
    render(<MemoryRouter><Conflicts /></MemoryRouter>);
    expect(screen.getByText('Ram Thapa')).toBeInTheDocument();
    expect(screen.getByText('2026-07-20')).toBeInTheDocument();
    expect(screen.getByText('biometric')).toBeInTheDocument();
  });

  it('states plainly that it never refunds leave', () => {
    wf.useConflicts.mockReturnValue({ data });
    render(<MemoryRouter><Conflicts /></MemoryRouter>);
    expect(screen.getByText(/detects only/i)).toBeInTheDocument();
  });

  it('shows an empty state when leave and attendance agree', () => {
    wf.useConflicts.mockReturnValue({
      data: { ...data, summary: { total: 0, employees_affected: 0, leave_days_double_counted: 0 }, conflicts: [] },
    });
    render(<MemoryRouter><Conflicts /></MemoryRouter>);
    expect(screen.getByText(/No conflicts in this period/i)).toBeInTheDocument();
  });
});
