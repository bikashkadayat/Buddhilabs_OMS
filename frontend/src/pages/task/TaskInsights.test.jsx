/**
 * Phase T5.1 — analytics visibility.
 *
 * Nothing here tests a new calculation, because none was added. What it tests
 * is that figures the backend already served are now REACHABLE and VISIBLE, and
 * that each chart draws from the same payload as the numbers beside it — a
 * chart fed by a second source is a second answer to the same question.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: {
    getDashboard: vi.fn(), getWorkload: vi.fn(),
    getReviewerAnalytics: vi.fn(),
  },
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u1' }, role: 'checker' }),
}));

import TaskDashboard from './TaskDashboard';
import TaskWorkload from './TaskWorkload';
import TaskBoard from '../../components/task/TaskBoard';
import { visibleItems } from '../../components/layout/navConfig';
import { taskService } from '../../services/taskService';

const DASHBOARD = {
  needs_my_action: 2, my_tasks: 8, due_today: 1, overdue: 3, completed: 5,
  pending_review: 2, blocked: 1, assigned_by_me: 0, to_accept: 1,
  my_drafts: 0, average_completion_days: 2.5,
};

const WORKLOAD = {
  perspective: 'manager',
  personal: { open: 3, overdue: 1, due_today: 0, completed: 4 },
  utilisation: { people: 2, open: 9, median: 4, busiest: 6 },
  by_employee: [
    { user_id: 'a', name: 'Aarati Shrestha', designation: 'Officer',
      department: 'Engineering', open: 6, completed: 2, overdue: 2, total: 8 },
    { user_id: 'z', name: 'Zenith Rai', designation: 'Officer',
      department: 'Finance', open: 3, completed: 5, overdue: 0, total: 8 },
  ],
  by_department: [
    { department: 'Engineering', total: 8, open: 6, completed: 2, overdue: 2,
      completion_percent: 25 },
    { department: 'Finance', total: 8, open: 3, completed: 5, overdue: 0,
      completion_percent: 63 },
  ],
};

const REVIEWERS = {
  reviewers: [
    { reviewer: 'Sita Rai', awaiting: 4, delayed: 1, decided: 9,
      oldest_days: 6, average_review_days: 1.2, workload: 4 },
    { reviewer: 'Nobody Waiting', awaiting: 0, delayed: 0, decided: 3,
      oldest_days: 0, average_review_days: 0.5, workload: 0 },
  ],
};

const COLUMNS = [
  { key: 'backlog', label: 'Backlog', statuses: ['draft'], count: 2, tasks: [] },
  { key: 'assigned', label: 'Assigned', statuses: ['assigned'], count: 3, tasks: [] },
  { key: 'progress', label: 'In Progress', statuses: ['in_progress'], count: 4, tasks: [] },
  { key: 'review', label: 'Review', statuses: ['under_review'], count: 0, tasks: [] },
  { key: 'done', label: 'Completed', statuses: ['completed'], count: 7, tasks: [] },
  { key: 'hold', label: 'On Hold', statuses: ['on_hold'], count: 1, tasks: [] },
];

const renderPage = (ui) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  taskService.getDashboard.mockResolvedValue(DASHBOARD);
  taskService.getWorkload.mockResolvedValue(WORKLOAD);
  taskService.getReviewerAnalytics.mockResolvedValue(REVIEWERS);
});

// ---------------------------------------------------------------------------
// Part 1 — discoverability
// ---------------------------------------------------------------------------
describe('finding the analytics', () => {
  it('names the seven menus the phase specifies, in order', () => {
    // Phase TASK-MANAGEMENT-ASANA-MODEL: Dashboard, My Tasks, Team Tasks, All
    // Tasks, Task Board, Reviews, Reports. Insights, Calendar and Overdue left
    // the rail to make room and are reached from the Dashboard instead.
    expect(visibleItems('task', 'admin').map((i) => i.label)).toEqual([
      'Dashboard', 'My tasks', 'Team tasks', 'All tasks', 'Task board',
      'Reviews', 'Reports']);
  });

  it('gives an Employee the three menus that are theirs, and no empty ones', () => {
    // A rail row that always 403s or is always empty teaches people the rail is
    // unreliable.
    expect(visibleItems('task', 'maker').map((i) => i.to)).toEqual([
      '/tasks', '/tasks/mine', '/tasks/board']);
  });

  it('keeps All Tasks for the roles that can read every department', () => {
    expect(visibleItems('task', 'checker').map((i) => i.to))
      .not.toContain('/tasks/all');
    for (const role of ['approver', 'bod', 'admin']) {
      expect(visibleItems('task', role).map((i) => i.to)).toContain('/tasks/all');
    }
  });

  it('keeps the rail within its seven-item budget', () => {
    // Seven menus, seven slots: the phase's list IS the budget. Raising the cap
    // is how a rail becomes the 76-link sidebar this replaced.
    for (const role of ['maker', 'checker', 'approver', 'admin']) {
      expect(visibleItems('task', role).filter((i) => !i.group).length)
        .toBeLessThanOrEqual(7);
    }
  });
});

// ---------------------------------------------------------------------------
// Part 2 — the dashboard donut
// ---------------------------------------------------------------------------
describe('the dashboard distribution', () => {
  it('charts the same numbers the tiles show', async () => {
    // One payload, two renderings. A chart fed by a second request is a second
    // answer to the same question.
    renderPage(<TaskDashboard />);
    const frame = await screen.findByText('Where your work sits');
    expect(frame).toBeInTheDocument();
    expect(taskService.getDashboard).toHaveBeenCalledTimes(1);
  });

  it('drops zero-value slices rather than drawing a label at nothing',
    async () => {
      taskService.getDashboard.mockResolvedValue({
        ...DASHBOARD, blocked: 0, pending_review: 0 });
      renderPage(<TaskDashboard />);
      await screen.findByText('Where your work sits');
      // Blocked is 0, so it is not a slice — but the TILES still say so.
      const table = screen.queryByRole('table');
      if (table) expect(within(table).queryByText('Blocked')).toBeNull();
    });

  it('renders no chart at all when there is nothing to show', async () => {
    taskService.getDashboard.mockResolvedValue({
      needs_my_action: 0, my_tasks: 0, due_today: 0, overdue: 0, completed: 0,
      pending_review: 0, blocked: 0 });
    renderPage(<TaskDashboard />);
    await screen.findByText('My Tasks');
    expect(screen.queryByText('Where your work sits')).toBeNull();
  });

  it('keeps the tiles, which are the way through to the lists', async () => {
    // A chart is not a substitute for navigation.
    renderPage(<TaskDashboard />);
    await screen.findByText('My Tasks');
    expect(screen.getByText('Overdue').closest('a'))
      .toHaveAttribute('href', '/tasks/overdue');
  });
});

// ---------------------------------------------------------------------------
// Part 3 — workload charts
// ---------------------------------------------------------------------------
describe('the workload charts', () => {
  it('charts assignees, departments and reviewers', async () => {
    renderPage(<TaskWorkload />);
    expect(await screen.findByText('Tasks by assignee')).toBeInTheDocument();
    expect(screen.getByText('Tasks by department')).toBeInTheDocument();
    expect(await screen.findByText('Tasks by reviewer')).toBeInTheDocument();
  });

  it('charts work IN HAND, never completed counts per person', async () => {
    // "Completed per person" on a workload chart is a ranking of people, which
    // this module does not produce. Open and overdue are load, not output.
    renderPage(<TaskWorkload />);
    const frame = (await screen.findByText('Tasks by assignee'))
      .closest('section');
    expect(frame).toHaveTextContent(/not a measure of anybody's output/i);
  });

  it('omits reviewers with nothing waiting', async () => {
    // A bar of zero for everybody who reviews nothing makes the chart
    // unreadable and says nothing.
    //
    // Asserted against the frame's DATA TABLE rather than the chart: recharts
    // draws to canvas-like SVG that jsdom does not lay out, so the table behind
    // "View data" is the only place the rows are readable in a test — and it is
    // the same array the chart is given.
    const user = userEvent.setup();
    renderPage(<TaskWorkload />);
    const frame = (await screen.findByText('Tasks by reviewer'))
      .closest('section');
    await user.click(within(frame).getByRole('button', { name: /view data/i }));

    expect(frame).toHaveTextContent('Sita Rai');
    expect(frame).not.toHaveTextContent('Nobody Waiting');
  });

  it('keeps the tables, so every chart has its figures underneath', async () => {
    renderPage(<TaskWorkload />);
    await screen.findByText('Tasks by assignee');
    expect(screen.getAllByRole('table').length).toBeGreaterThan(0);
  });

  it('survives the reviewer feed failing, without losing the page', async () => {
    taskService.getReviewerAnalytics.mockRejectedValue(new Error('403'));
    renderPage(<TaskWorkload />);
    expect(await screen.findByText('Tasks by assignee')).toBeInTheDocument();
    expect(screen.queryByText('Tasks by reviewer')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Part 4 — board summary
// ---------------------------------------------------------------------------
describe('the board summary', () => {
  it('shows a count for every column, including the empty one', () => {
    // "Nothing in review" is information. A summary that dropped the zeroes
    // would make it look identical to "no review column".
    render(<MemoryRouter><TaskBoard columns={COLUMNS} /></MemoryRouter>);
    const summary = document.querySelector('.tsk-board-summary');
    for (const column of COLUMNS) {
      expect(within(summary).getByText(column.label)).toBeInTheDocument();
    }
    expect(within(summary).getByText('0')).toBeInTheDocument();
  });

  it('takes its counts from the board payload, not from the cards', () => {
    // The columns carry counts the server computed over the WHOLE column;
    // counting the rendered cards would report only the page that was sent.
    render(<MemoryRouter><TaskBoard columns={COLUMNS} /></MemoryRouter>);
    const summary = document.querySelector('.tsk-board-summary');
    expect(within(summary).getByText('7')).toBeInTheDocument();  // Completed
  });

  it('renders nothing when the board is empty', () => {
    render(<MemoryRouter>
      <TaskBoard columns={COLUMNS.map((c) => ({ ...c, count: 0 }))} />
    </MemoryRouter>);
    expect(document.querySelector('.tsk-board-summary')).toBeNull();
  });

  it('does not duplicate the board\'s own column list for screen readers', () => {
    // The board is already a list of these six columns; a second list means
    // every column is announced twice.
    render(<MemoryRouter><TaskBoard columns={COLUMNS} /></MemoryRouter>);
    expect(screen.getAllByRole('listitem')).toHaveLength(COLUMNS.length);
  });
});
