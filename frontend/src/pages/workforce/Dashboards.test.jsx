import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../hooks/useWorkforce', () => ({
  useTeamDashboard: vi.fn(),
  useHRDashboard: vi.fn(),
}));
vi.mock('../../hooks/useAttendanceStream', () => ({
  useAttendanceStream: () => ({ connected: false, lastEvent: null }),
}));
vi.mock('../../hooks/useAutoRefresh', () => ({ useAutoRefresh: vi.fn() }));
// recharts needs layout it cannot get in jsdom; the chart has its own test.
vi.mock('../../components/workforce/AttendanceTrendChart', () => ({
  default: () => <div data-testid="trend-chart" />,
}));

import { useHRDashboard, useTeamDashboard } from '../../hooks/useWorkforce';
import TeamDashboard from './TeamDashboard';
import CommandCenter from './CommandCenter';

const COUNTS = {
  present: 16, late: 3, half_day: 1, absent: 2, on_leave: 1,
  holiday: 0, wfh: 4, not_applicable: 2,
};

const TEAM = {
  date: '2026-08-04', is_holiday: false, holiday_name: null, team_size: 29,
  counts: COUNTS, present_now: 24,
  queues: { corrections_manager_stage: 2, corrections_hr_stage: 1, wfh_pending: 3 },
  month_to_date: { working: '404.16', regular: '400.00', overtime: '4.16', late_days: '32', late_minutes: '160' },
  trends: [{ date: '2026-08-04', present: 16, late: 3, wfh: 4, absent: 2 }],
  department_summary: [
    { department_id: 'd1', department: 'Engineering', counts: COUNTS, present_now: 24, headcount: 29 },
  ],
  conflicts: { total: 2, employees_affected: 2, leave_days_double_counted: 2 },
  absent_employees: [{ id: 'e9', name: 'Sita Gurung', employee_id: 'NIFN-EMP-2021-0009' }],
};

const HR = {
  ...TEAM, headcount: 37, comp_off_eligible_today: 5,
  devices: {
    online: [{ id: 'dev1', name: 'Main Gate', label: '192.168.77.201', unmapped_count: 0, pending_punches: 0 }],
    offline: [{ id: 'dev2', name: 'Warehouse', label: 'warehouse', unmapped_count: 3, pending_punches: 12 }],
    pending_mapping: 3,
  },
  queues: { ...TEAM.queues, comp_off_pending: 4, leave_pending_hr: 2 },
  comp_off: { earned: 12, used: 4, available: 8, pending: 3 },
  wfh: { requests: { pending: 3, approved: 9, rejected: 1, cancelled: 0 }, approved_day_rows: 9, worked_from_home_days: 7, conversion: 0.78 },
  department_breakdown: TEAM.department_summary,
  recent_punches: [],
};

const renderTeam = () => render(<MemoryRouter><TeamDashboard /></MemoryRouter>);
const renderHR = () => render(<MemoryRouter><CommandCenter /></MemoryRouter>);

describe('Manager dashboard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useTeamDashboard.mockReturnValue({ isLoading: true });
    renderTeam();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state with retry', () => {
    useTeamDashboard.mockReturnValue({ isError: true, error: new Error('x'), refetch: vi.fn() });
    renderTeam();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('renders every status count', () => {
    useTeamDashboard.mockReturnValue({ data: TEAM, refetch: vi.fn() });
    renderTeam();
    // getAllByText: these labels also appear as department-table headers.
    ['Present', 'Late', 'Half day', 'Work from home', 'On leave', 'Absent']
      .forEach((label) => expect(screen.getAllByText(label).length).toBeGreaterThan(0));
    expect(screen.getAllByText('24').length).toBeGreaterThan(0); // present now
  });

  it('shows the pending queues', () => {
    useTeamDashboard.mockReturnValue({ data: TEAM, refetch: vi.fn() });
    renderTeam();
    expect(screen.getByText('Corrections to review')).toBeInTheDocument();
    expect(screen.getByText('WFH requests pending')).toBeInTheDocument();
  });

  it('names absent employees rather than only counting them', () => {
    useTeamDashboard.mockReturnValue({ data: TEAM, refetch: vi.fn() });
    renderTeam();
    expect(screen.getByText('Sita Gurung')).toBeInTheDocument();
  });

  it('renders the department summary and the trend chart', () => {
    useTeamDashboard.mockReturnValue({ data: TEAM, refetch: vi.fn() });
    renderTeam();
    expect(screen.getByText('Engineering')).toBeInTheDocument();
    expect(screen.getByTestId('trend-chart')).toBeInTheDocument();
  });
});

describe('HR command center', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useHRDashboard.mockReturnValue({ isLoading: true });
    renderHR();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state with retry', () => {
    useHRDashboard.mockReturnValue({ isError: true, error: new Error('x'), refetch: vi.fn() });
    renderHR();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('splits devices into online and offline, and flags pending mapping', () => {
    useHRDashboard.mockReturnValue({ data: HR, refetch: vi.fn() });
    renderHR();
    expect(screen.getByText('Online (1)')).toBeInTheDocument();
    expect(screen.getByText('Offline (1)')).toBeInTheDocument();
    expect(screen.getByText('3 pending mapping')).toBeInTheDocument();
    expect(screen.getByText('3 unmapped')).toBeInTheDocument();
  });

  it('shows every approval queue', () => {
    useHRDashboard.mockReturnValue({ data: HR, refetch: vi.fn() });
    renderHR();
    ['Corrections with HR', 'Corrections with dept heads', 'WFH pending',
      'Comp days to confirm', 'Leave awaiting HR']
      .forEach((label) => expect(screen.getByText(label)).toBeInTheDocument());
  });

  it('shows comp-off eligibility for today', () => {
    useHRDashboard.mockReturnValue({ data: HR, refetch: vi.fn() });
    renderHR();
    expect(screen.getByText('Comp-off eligible')).toBeInTheDocument();
    expect(screen.getByText('Worked a Saturday or holiday')).toBeInTheDocument();
  });

  it('renders the department breakdown', () => {
    useHRDashboard.mockReturnValue({ data: HR, refetch: vi.fn() });
    renderHR();
    expect(screen.getByText('Department breakdown')).toBeInTheDocument();
    expect(screen.getByText('Engineering')).toBeInTheDocument();
  });

  it('survives a payload with an unknown status', () => {
    // The Phase 8 regression class: a hardcoded map turned a new status into a
    // crash. The tile row is built from the API's counts object.
    const withNew = { ...HR, counts: { ...COUNTS, sabbatical: 2 } };
    useHRDashboard.mockReturnValue({ data: withNew, refetch: vi.fn() });
    expect(() => renderHR()).not.toThrow();
    expect(screen.getByText('Attendance today')).toBeInTheDocument();
  });
});
