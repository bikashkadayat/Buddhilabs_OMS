import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'bod' }) }));
vi.mock('../../services/leaveService', () => ({ leaveService: { getPendingApprovals: vi.fn() } }));
vi.mock('../../services/analyticsService', () => ({ analyticsService: { executive: vi.fn(), leave: vi.fn() } }));
vi.mock('../../services/attendanceService', () => ({ attendanceService: { dashboard: vi.fn() } }));
vi.mock('../../services/inventoryService', () => ({ assetLifecycle: { dashboard: vi.fn() } }));
vi.mock('../../services/memoService', () => ({ memoService: { getDashboard: vi.fn() } }));
vi.mock('../../services/taskService', () => ({ taskService: { getExecutive: vi.fn() } }));

import { leaveService } from '../../services/leaveService';
import { analyticsService } from '../../services/analyticsService';
import { attendanceService } from '../../services/attendanceService';
import { assetLifecycle } from '../../services/inventoryService';
import { memoService } from '../../services/memoService';
import { taskService } from '../../services/taskService';
import ExecutiveDashboard from './ExecutiveDashboard';

const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><ExecutiveDashboard /></MemoryRouter>
  </QueryClientProvider>,
);

beforeEach(() => {
  vi.clearAllMocks();
  leaveService.getPendingApprovals.mockResolvedValue({ data: [
    { id: 1, employee: 'Bikash Kadayat', type: 'Annual Leave', start: '2026-09-20', end: '2026-09-21' },
  ] });
  analyticsService.executive.mockResolvedValue({ scope: { departments: 4 },
    data: { kpis: { headcount: 13, compliance_pct: 92.5, leave_pct: 3.1, department_health_score: 80 } } });
  analyticsService.leave.mockResolvedValue({ data: {
    by_type: [{ code: 'ANNUAL', label: 'Annual Leave', days: 4, share_pct: 100 }],
    by_department: [{ department_id: 'd1', department: 'ICT Department', leave_days: 3, share_pct: 75 }] } });
  attendanceService.dashboard.mockResolvedValue({ present_now: 9, total_employees: 13,
    counts: { on_leave: 1, wfh: 2, late: 0 } });
  assetLifecycle.dashboard.mockResolvedValue({ counts: { total_assets: 8, assigned: 4,
    assets_with_no_owner: 4, pending_transfers: 0 } });
  memoService.getDashboard.mockResolvedValue({ total_visible: 10, pending_approval: 1, approved: 6, rejected: 0 });
  taskService.getExecutive.mockResolvedValue({
    totals: { total: 19, completed: 11, open: 8, overdue: 2, blocked: 0,
      review_backlog: 3, oldest_review_days: 5 },
    kpis: [{ key: 'completion_percent', label: 'Completion', unit: '%', value: 58 }],
    department_ranking: [
      { department: 'ICT Department', total: 9, completed: 5, overdue: 1,
        completion_percent: 56, position: 1, low_volume: false },
    ],
  });
});

describe('the Executive Dashboard', () => {
  it('shows the five task figures the Board governs by, from the server', async () => {
    mount();
    const tasks = within(await screen.findByTestId('exec-tasks'));
    expect(await tasks.findByText('Total tasks')).toBeInTheDocument();
    expect(tasks.getByText('19')).toBeInTheDocument();      // total
    expect(tasks.getByText('2')).toBeInTheDocument();       // overdue
    expect(tasks.getByText('3')).toBeInTheDocument();       // pending reviews
    expect(tasks.getByText('58%')).toBeInTheDocument();     // completion rate
    // Department progress: the server's own rows, not a total divided here.
    expect(tasks.getByText('ICT Department')).toBeInTheDocument();
    expect(tasks.getByText(/5 of 9 done/)).toBeInTheDocument();
  });

  it('keeps the task section when task analytics is the thing that fails', async () => {
    taskService.getExecutive.mockRejectedValue({ response: { status: 500 } });
    mount();
    const tasks = within(await screen.findByTestId('exec-tasks'));
    expect(await tasks.findByText(/Not available right now/)).toBeInTheDocument();
    // ...and the rest of the page still renders.
    expect(within(await screen.findByTestId('exec-memos')).getByText('10')).toBeInTheDocument();
  });

  it('shows what awaits the Board, and the organisation, in server figures', async () => {
    mount();
    const decisions = within(await screen.findByTestId('exec-decisions'));
    expect(await decisions.findByText('Awaiting the Board')).toBeInTheDocument();
    expect(decisions.getByText('Bikash Kadayat')).toBeInTheDocument();
    expect(within(await screen.findByTestId('exec-organisation')).getByText('13')).toBeInTheDocument();
    expect(within(await screen.findByTestId('exec-assets')).getByText('8')).toBeInTheDocument();
    expect(within(await screen.findByTestId('exec-memos')).getByText('10')).toBeInTheDocument();
  });

  it('is read-only by construction: links, and no buttons at all', async () => {
    mount();
    await screen.findByText('Bikash Kadayat');
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByText('Read-only')).toBeInTheDocument();
    // The decision is taken in the review queue, not here.
    expect(screen.getByRole('link', { name: 'Open the review queue' })).toHaveAttribute('href', '/leave/pending');
  });

  it('links all six reports the brief names', async () => {
    mount();
    const reports = within(screen.getByRole('navigation', { name: 'Reports' }));
    for (const name of ['Executive Dashboard analytics', 'Department Dashboard', 'Leave Analytics',
      'Attendance Analytics', 'Asset Analytics', 'Memo Analytics']) {
      expect(reports.getByRole('link', { name })).toBeInTheDocument();
    }
  });

  it('keeps the other sections when one fails', async () => {
    assetLifecycle.dashboard.mockRejectedValue(Object.assign(new Error('403'), { response: { status: 403 } }));
    mount();
    expect(await within(screen.getByTestId('exec-assets')).findByText(/Not available right now/)).toBeInTheDocument();
    expect(await within(screen.getByTestId('exec-memos')).findByText('10')).toBeInTheDocument();
  });
});
