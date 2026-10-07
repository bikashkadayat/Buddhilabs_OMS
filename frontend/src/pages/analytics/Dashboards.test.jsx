import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// The department page now asks who answers for each department (Phase
// DEPARTMENT-GOVERNANCE-HARDENING), so it needs a query client. The call is
// stubbed to "no gaps" here; the banner has its own tests.
vi.mock('../../services/leaveService', () => ({
  leaveService: { departmentGovernance: vi.fn().mockResolvedValue({
    total: 2, active: 2, missing_head: 0, missing_head_names: [], status: 'ok',
  }) },
}));

vi.mock('../../hooks/useAnalytics', () => ({
  useExecutiveAnalytics: vi.fn(),
  useDepartmentAnalytics: vi.fn(),
  useAnalyticsMeta: vi.fn(() => ({ data: META })),
  useAnalyticsExport: vi.fn(() => ({ mutate: vi.fn(), isPending: false, isError: false })),
}));
// recharts needs layout jsdom cannot provide; the chart contract is tested on
// the server payload and the theme has its own unit test.
vi.mock('../../components/analytics/TrendChart', () => ({
  default: () => <div data-testid="trend-chart" />,
}));
vi.mock('../../components/analytics/ComparisonBarChart', () => ({
  default: () => <div data-testid="bar-chart" />,
}));

import {
  useDepartmentAnalytics, useExecutiveAnalytics,
} from '../../hooks/useAnalytics';
import Executive from './Executive';
import Departments from './Departments';

const META = {
  departments: [{ id: 'd1', name: 'Engineering', headcount: 12 }],
  definitions: { compliance_pct: 'Expected working days minus unexplained absences.' },
  min_department_sample: 3,
  scope: { level: 'organization' },
};

const ENVELOPE = {
  window: { from: '2026-07-01', to: '2026-07-31', granularity: 'day', truncated: false },
  scope: { level: 'organization', department: null, headcount: 37, warnings: [] },
  generated_at: '2026-08-05T10:14:03+05:45',
  cached: false,
};

const EXECUTIVE = {
  ...ENVELOPE,
  data: {
    kpis: {
      headcount: 37, present_pct: 91.2, late_pct: 4.1, absent_pct: 3.2,
      wfh_pct: 6.8, leave_pct: 5.4, compliance_pct: 96.8, overtime_hours: 312,
      avg_working_hours: 7.9, comp_off_earned: 41, comp_off_used: 27,
      comp_off_pending: 9, department_health_score: 88.4,
    },
    attendance: { expected_days: 740, attended_days: 700 },
    trend: [{ period: '2026-07', label: 'Jul 26', present_pct: 91.2, absent_pct: 3.2,
              wfh_pct: 6.8, leave_pct: 5.4, compliance_pct: 96.8, overtime_hours: 312 }],
    departments: [
      { department_id: 'd1', department: 'Engineering', headcount: 12, rank: 1,
        compliance_pct: 98.1, present_pct: 94.0, late_pct: 2.0, absent_pct: 1.9,
        overtime_hours: 120, health_score: 94.2, small_sample: false },
      { department_id: 'd3', department: 'Legal', headcount: 2, rank: null,
        rank_excluded_reason: 'small_sample', compliance_pct: 90.0, present_pct: 90.0,
        late_pct: 0, absent_pct: 10, overtime_hours: 0, health_score: null,
        small_sample: true },
    ],
    health_weights: { compliance: 0.5, punctuality: 0.2, presence: 0.2, overtime_load: 0.1 },
    comp_off: { earned: 41, used: 27, pending: 9, available: 14 },
  },
};

const DEPARTMENTS_AS_MANAGER = {
  ...ENVELOPE,
  scope: { level: 'department', department: 'Engineering', headcount: 12, warnings: [] },
  data: {
    departments: [
      { department_id: 'd1', department: 'Engineering', headcount: 12, rank: 2,
        compliance_pct: 94.0, present_pct: 92.0, late_pct: 3.0, absent_pct: 4.0,
        overtime_hours: 40, health_score: 90.1, small_sample: false },
      { department_id: null, department: 'Department 1', redacted: true, rank: 1,
        headcount: null, compliance_pct: null, health_score: null },
    ],
    org_average: { compliance_pct: 95.5, present_pct: 92.8, late_pct: 3.4,
                   overtime_per_capita: 4.1, health_score: 91.0 },
    own_department: { department_id: 'd1', department: 'Engineering', rank: 2,
                      compliance_pct: 94.0 },
    percentile: 50.0,
    department_count: 3,
    trends: { series: [{ department: 'Engineering', points: [
      { period: '2026-07', label: 'Jul 26', compliance_pct: 94.0, expected_days: 240 }] }],
      folded_departments: 0, max_series: 8 },
    health_weights: { compliance: 0.5, punctuality: 0.2, presence: 0.2, overtime_load: 0.1 },
    min_sample: 3,
  },
};

const withQuery = (ui) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>
);
const renderExecutive = () => render(withQuery(<Executive />));
const renderDepartments = () => render(withQuery(<Departments />));

describe('Executive dashboard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useExecutiveAnalytics.mockReturnValue({ isLoading: true });
    renderExecutive();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state with retry', () => {
    useExecutiveAnalytics.mockReturnValue({
      isError: true, error: new Error('boom'), refetch: vi.fn() });
    renderExecutive();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('renders every headline KPI', () => {
    useExecutiveAnalytics.mockReturnValue({ data: EXECUTIVE, refetch: vi.fn() });
    renderExecutive();
    ['Headcount', 'Attendance compliance', 'Present', 'Absent', 'Late',
      'Work from home', 'On leave', 'Overtime hours', 'Comp days earned',
      'Department health'].forEach((label) => {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    });
    expect(screen.getByText('96.8%')).toBeInTheDocument();
  });

  it('stamps when the figures were generated', () => {
    useExecutiveAnalytics.mockReturnValue({ data: EXECUTIVE, refetch: vi.fn() });
    renderExecutive();
    expect(screen.getByText(/as of/i)).toBeInTheDocument();
  });

  it('lists a small department but does not rank or score it', () => {
    useExecutiveAnalytics.mockReturnValue({ data: EXECUTIVE, refetch: vi.fn() });
    renderExecutive();
    const legal = screen.getByRole('rowheader', { name: /Legal/ }).closest('tr');
    expect(within(legal).getByText('too small to rank')).toBeInTheDocument();
    // Rank cell is an em dash, and the health pill has no number.
    expect(within(legal).getAllByText('—').length).toBeGreaterThan(0);
  });

  it('publishes the health-score weights beside the score', () => {
    useExecutiveAnalytics.mockReturnValue({ data: EXECUTIVE, refetch: vi.fn() });
    renderExecutive();
    expect(screen.getByText(/compliance 50%/i)).toBeInTheDocument();
  });

  it('never renders an employee name or id', () => {
    useExecutiveAnalytics.mockReturnValue({ data: EXECUTIVE, refetch: vi.fn() });
    const { container } = renderExecutive();
    expect(container.textContent).not.toMatch(/employee_id|NIFN-EMP/i);
  });
});

describe('Department analytics', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a department head their own row in full', () => {
    useDepartmentAnalytics.mockReturnValue({
      data: DEPARTMENTS_AS_MANAGER, refetch: vi.fn() });
    renderDepartments();
    const own = screen.getByRole('rowheader', { name: /Engineering/ }).closest('tr');
    expect(within(own).getByText('94.0%')).toBeInTheDocument();
  });

  it('hides other departments behind a rank', () => {
    useDepartmentAnalytics.mockReturnValue({
      data: DEPARTMENTS_AS_MANAGER, refetch: vi.fn() });
    renderDepartments();
    expect(screen.getAllByText(/outside your scope/i).length).toBeGreaterThan(0);
    expect(screen.queryByText('Operations')).not.toBeInTheDocument();
  });

  it('still gives them the org average and their percentile', () => {
    useDepartmentAnalytics.mockReturnValue({
      data: DEPARTMENTS_AS_MANAGER, refetch: vi.fn() });
    renderDepartments();
    expect(screen.getAllByText('Org compliance').length).toBeGreaterThan(0);
    expect(screen.getByText('Your percentile')).toBeInTheDocument();
  });

  it('explains the ranking rule on the page', () => {
    useDepartmentAnalytics.mockReturnValue({
      data: DEPARTMENTS_AS_MANAGER, refetch: vi.fn() });
    renderDepartments();
    expect(screen.getByText(/Individual employees are never scored/i))
      .toBeInTheDocument();
  });
});

describe('Chart data tables', () => {
  beforeEach(() => vi.clearAllMocks());

  it('offers a table view of every chart, as the palette relief requires', async () => {
    useExecutiveAnalytics.mockReturnValue({ data: EXECUTIVE, refetch: vi.fn() });
    renderExecutive();
    const toggles = screen.getAllByRole('button', { name: /view data/i });
    expect(toggles.length).toBeGreaterThan(0);

    await userEvent.click(toggles[0]);
    expect(screen.getByRole('button', { name: /hide data/i })).toBeInTheDocument();
  });
});
