/**
 * Phase T3 — the workspace views: board, calendar, workload, overdue, reports.
 *
 * Each page's claim is different, so each gets the assertions that matter to it:
 * the board must render the SERVER's columns rather than a copy of the mapping,
 * the calendar must never re-derive a task's kind, the workload page must render
 * whichever perspective it was handed, and the reports page must render five
 * different shapes through one table.
 */
import React from 'react';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: {
    getBoard: vi.fn(),
    getCalendar: vi.fn(),
    getWorkload: vi.fn(),
    getOverdue: vi.fn(),
    getReports: vi.fn(),
    getReport: vi.fn(),
    getFilterOptions: vi.fn(),
    downloadReport: vi.fn(),
  },
}));

import TaskBoard from '../../components/task/TaskBoard';
import TaskCalendar from './TaskCalendar';
import TaskWorkload from './TaskWorkload';
import TaskOverdue from './TaskOverdue';
import TaskReports from './TaskReports';
import { taskService } from '../../services/taskService';

const card = (id, extra = {}) => ({
  id,
  task_number: `NIFN-TSK-2083-000${id}`,
  title: `Task ${id}`,
  status: 'assigned',
  status_label: 'Assigned',
  priority: 'high',
  priority_label: 'High',
  due_date: '2026-09-30',
  is_overdue: false,
  overdue_days: 0,
  progress_percent: 40,
  checklist_done: 1,
  checklist_total: 4,
  assignee_names: ['Bikash Kadayat'],
  reviewer_name: 'Sanjaya Poudel',
  comment_count: 2,
  attachment_count: 1,
  evidence_count: 0,
  ...extra,
});

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
  taskService.getFilterOptions.mockResolvedValue({
    departments: [], assignees: [], reviewers: [],
  });
});

// ---------------------------------------------------------------------------
// Board (Part 1)
// ---------------------------------------------------------------------------
describe('board', () => {
  const columns = [
    { key: 'backlog', label: 'Backlog', count: 0, tasks: [] },
    { key: 'assigned', label: 'Assigned', count: 2, tasks: [card('1'), card('2')] },
    { key: 'in_progress', label: 'In Progress', count: 0, tasks: [] },
    { key: 'review', label: 'Review', count: 0, tasks: [] },
    { key: 'completed', label: 'Completed', count: 0, tasks: [] },
    { key: 'on_hold', label: 'On Hold', count: 0, tasks: [] },
  ];

  it('renders the columns the server sent, in that order', () => {
    /* The mapping lives in tasks/board.py and is served with the payload. A
       second copy here is a second thing that can disagree, and a task claimed
       by neither is shown nowhere at all. */
    renderPage(<TaskBoard columns={columns} />);
    const rendered = screen.getAllByRole('listitem').map(
      (el) => el.getAttribute('aria-label').split(':')[0]);
    expect(rendered).toEqual(['Backlog', 'Assigned', 'In Progress', 'Review',
      'Completed', 'On Hold']);
  });

  it('keeps an empty column, because empty is information', () => {
    renderPage(<TaskBoard columns={columns} />);
    const review = screen.getByRole('listitem', { name: /Review: 0 tasks/ });
    expect(within(review).getByText('Nothing here.')).toBeInTheDocument();
  });

  it('puts every card the column carries into it', () => {
    renderPage(<TaskBoard columns={columns} />);
    const assigned = screen.getByRole('listitem', { name: /Assigned: 2 tasks/ });
    expect(within(assigned).getAllByRole('link')).toHaveLength(2);
  });

  it('shows the reviewer on a card, so "waiting on whom" is answerable', () => {
    renderPage(<TaskBoard columns={columns} />);
    expect(screen.getAllByText('Sanjaya Poudel').length).toBeGreaterThan(0);
  });

  it('is read-only: no card is draggable', () => {
    /* A drag cannot ask a reviewer why they are sending work back, and would
       move tasks into states the engine refuses. */
    renderPage(<TaskBoard columns={columns} />);
    for (const link of screen.getAllByRole('link')) {
      expect(link).not.toHaveAttribute('draggable', 'true');
    }
  });

  it('renders nothing at all when the server sends no columns', () => {
    renderPage(<TaskBoard columns={[]} />);
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Calendar (Part 3)
// ---------------------------------------------------------------------------
describe('calendar', () => {
  const payload = {
    view: 'month',
    anchor: '2026-09-15',
    start: '2026-09-01',
    end: '2026-09-30',
    counts: { due: 1, overdue: 1, completed: 1, upcoming: 0 },
    events: [
      { date: '2026-09-02', kind: 'overdue', task: card('1', { title: 'Late one' }) },
      { date: '2026-09-15', kind: 'due', task: card('2', { title: 'Due one' }) },
      { date: '2026-09-20', kind: 'completed', task: card('3', { title: 'Done one' }) },
    ],
  };

  beforeEach(() => taskService.getCalendar.mockResolvedValue(payload));

  it('offers Day, Week and Month', async () => {
    renderPage(<TaskCalendar />);
    await screen.findByText('2026-09-01 → 2026-09-30');
    for (const label of ['Day', 'Week', 'Month']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('asks the server for the window rather than computing one', async () => {
    renderPage(<TaskCalendar />);
    await waitFor(() => expect(taskService.getCalendar).toHaveBeenCalled());
    expect(taskService.getCalendar.mock.calls[0][0]).toMatchObject(
      { view: 'month' });

    fireEvent.click(screen.getByRole('button', { name: 'Week' }));
    await waitFor(() => expect(taskService.getCalendar).toHaveBeenCalledTimes(2));
    expect(taskService.getCalendar.mock.calls[1][0]).toMatchObject(
      { view: 'week' });
  });

  it('renders the window the server returned, not one it derived', async () => {
    renderPage(<TaskCalendar />);
    expect(await screen.findByText('2026-09-01 → 2026-09-30')).toBeInTheDocument();
  });

  it('carries the kind the server assigned, one per task', async () => {
    renderPage(<TaskCalendar />);
    const late = await screen.findByTitle(/Late one|NIFN-TSK-2083-0001/);
    expect(late.className).toContain('is-overdue');
    expect(screen.getByTitle(/NIFN-TSK-2083-0002/).className).toContain('is-due');
    expect(screen.getByTitle(/NIFN-TSK-2083-0003/).className)
      .toContain('is-completed');
  });

  it('labels every colour in words as well', async () => {
    /* This page gets printed, and colour alone is invisible to a colour-blind
       reader. */
    renderPage(<TaskCalendar />);
    await screen.findByText('2026-09-01 → 2026-09-30');
    for (const label of ['Overdue', 'Due soon', 'Completed', 'Active']) {
      expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
    }
  });

  it('links an event to its task', async () => {
    renderPage(<TaskCalendar />);
    const event = await screen.findByTitle(/NIFN-TSK-2083-0002/);
    expect(event).toHaveAttribute('href', '/tasks/2');
  });
});

// ---------------------------------------------------------------------------
// Workload (Part 4)
// ---------------------------------------------------------------------------
describe('workload', () => {
  const personal = {
    assigned: 7, open: 4, completed: 3, overdue: 1,
    completion_percent: 43, average_completion_days: 5.5,
  };

  it('shows an employee their own four numbers and nobody else\'s', async () => {
    taskService.getWorkload.mockResolvedValue({
      perspective: 'employee', personal,
    });
    renderPage(<TaskWorkload />);

    expect(await screen.findByText('My Workload')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('5.5')).toBeInTheDocument();
    expect(screen.queryByText('Team Workload')).toBeNull();
    expect(screen.queryByText('Department Workload')).toBeNull();
  });

  it('adds the team block for a department head, keeping the personal one',
    async () => {
      taskService.getWorkload.mockResolvedValue({
        perspective: 'department',
        personal,
        pending_review: 2,
        by_employee: [{
          user_id: 'u1', name: 'Bikash Kadayat', designation: 'Officer',
          department: 'Engineering', open: 6, overdue: 2, completed: 1,
          total: 7, completion_percent: 14, is_overloaded: true,
        }],
      });
      renderPage(<TaskWorkload />);

      expect(await screen.findByText('Team Workload')).toBeInTheDocument();
      expect(screen.getByText('My Workload')).toBeInTheDocument();
      expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument();
      expect(screen.getByText('Outlier load')).toBeInTheDocument();
    });

  it('adds departments and utilisation for HR', async () => {
    taskService.getWorkload.mockResolvedValue({
      perspective: 'organisation',
      personal,
      by_employee: [],
      by_department: [{
        department: 'Engineering', total: 10, open: 6, completed: 4,
        overdue: 1, completion_percent: 40,
      }],
      utilisation: {
        people: 4, open: 10, median_open: 2,
        busiest_quarter_share_percent: 60, overloaded: 1, unassigned_open: 3,
      },
    });
    renderPage(<TaskWorkload />);

    expect(await screen.findByText('Resource Utilisation')).toBeInTheDocument();
    expect(screen.getByText('Department Workload')).toBeInTheDocument();
    expect(screen.getByText('60%')).toBeInTheDocument();
    expect(screen.getByText('Open, nobody carrying')).toBeInTheDocument();
  });

  it('never presents concentration as capacity', async () => {
    /* Nothing here knows anybody's hours, so "83% utilised" would be invented. */
    taskService.getWorkload.mockResolvedValue({
      perspective: 'organisation',
      personal,
      by_employee: [],
      by_department: [],
      utilisation: {
        people: 4, open: 10, median_open: 2,
        busiest_quarter_share_percent: 60, overloaded: 1, unassigned_open: 0,
      },
    });
    renderPage(<TaskWorkload />);
    expect(await screen.findByText('Concentration, not capacity'))
      .toBeInTheDocument();
    expect(screen.queryByText(/utilised/i)).toBeNull();
  });

  it('reports a refusal rather than rendering an empty page', async () => {
    taskService.getWorkload.mockRejectedValue(new Error('Forbidden'));
    renderPage(<TaskWorkload />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Overdue centre (Part 5)
// ---------------------------------------------------------------------------
describe('overdue centre', () => {
  const row = (days, extra = {}) => ({
    task_id: `t${days}`,
    task_number: `NIFN-TSK-2083-00${days}`,
    title: `Late by ${days}`,
    assignee: 'Bikash Kadayat',
    department: 'Engineering',
    priority: 'High',
    reviewer: 'Sanjaya Poudel',
    due_date: '2026-08-01',
    overdue_days: days,
    ...extra,
  });

  it('carries every column the specification names', async () => {
    taskService.getOverdue.mockResolvedValue({
      rows: [row(12)], summary: { total: 1, worst_days: 12 },
    });
    renderPage(<TaskOverdue />);

    await screen.findByText('Late by 12');
    for (const heading of ['Days Overdue', 'Task No', 'Task', 'Assignee',
      'Department', 'Priority', 'Reviewer', 'Due']) {
      expect(screen.getByRole('columnheader', { name: heading }))
        .toBeInTheDocument();
    }
  });

  it('renders the server order, worst first', async () => {
    taskService.getOverdue.mockResolvedValue({
      rows: [row(30), row(9), row(2)],
      summary: { total: 3, worst_days: 30 },
    });
    renderPage(<TaskOverdue />);

    await screen.findByText('Late by 30');
    const titles = screen.getAllByText(/^Late by /).map((el) => el.textContent);
    expect(titles).toEqual(['Late by 30', 'Late by 9', 'Late by 2']);
  });

  it('states the day count in words, not only in colour', async () => {
    taskService.getOverdue.mockResolvedValue({
      rows: [row(1)], summary: { total: 1, worst_days: 1 },
    });
    renderPage(<TaskOverdue />);
    // Singular, because "1 days" is the kind of thing people notice.
    expect(await screen.findByText('1 day')).toBeInTheDocument();
  });

  it('says nothing is overdue rather than showing an empty table', async () => {
    taskService.getOverdue.mockResolvedValue({
      rows: [], summary: { total: 0, worst_days: 0 },
    });
    renderPage(<TaskOverdue />);
    expect(await screen.findByText(/Nothing is overdue/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Reports (Part 7)
// ---------------------------------------------------------------------------
describe('reports', () => {
  const catalogue = [
    { slug: 'completion', label: 'Task Completion Report', description: 'A' },
    { slug: 'status', label: 'Task Status Report', description: 'B' },
    { slug: 'department', label: 'Department Report', description: 'C' },
    { slug: 'workload', label: 'Workload Report', description: 'D' },
    { slug: 'overdue', label: 'Overdue Report', description: 'E' },
    { slug: 'reviewer', label: 'Reviewer Performance Report', description: 'F' },
  ];

  beforeEach(() => {
    taskService.getReports.mockResolvedValue(catalogue);
    taskService.getReport.mockResolvedValue({
      slug: 'completion',
      label: 'Task Completion Report',
      columns: [
        { key: 'employee', label: 'Employee' },
        { key: 'completed', label: 'Completed', numeric: true },
        { key: 'average_days', label: 'Avg Days', numeric: true },
      ],
      rows: [
        { employee: 'Bikash Kadayat', completed: 4, average_days: 3.5 },
        { employee: 'Sanjaya Poudel', completed: 0, average_days: null },
      ],
      summary: { total: 8, completion_percent: 50 },
    });
  });

  it('offers every report the catalogue carries', async () => {
    renderPage(<TaskReports />);
    for (const report of catalogue) {
      expect(await screen.findByRole('button', { name: report.label }))
        .toBeInTheDocument();
    }
  });

  it('renders any report through one table, from its own columns', async () => {
    /* The uniform envelope is what lets a sixth report appear server-side with
       no change here. */
    renderPage(<TaskReports />);
    await screen.findByRole('table');
    expect(screen.getByRole('columnheader', { name: 'Employee' }))
      .toBeInTheDocument();
    expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument();
  });

  it('renders a null cell as a dash, not as zero', async () => {
    /* null is "nothing completed yet"; 0.0 days would be read as "same day". */
    renderPage(<TaskReports />);
    await screen.findByRole('table');
    const row = screen.getByText('Sanjaya Poudel').closest('tr');
    expect(within(row).getByText('—')).toBeInTheDocument();
  });

  it('switches report when another is chosen', async () => {
    renderPage(<TaskReports />);
    fireEvent.click(await screen.findByRole('button',
      { name: 'Overdue Report' }));
    await waitFor(() => expect(taskService.getReport)
      .toHaveBeenLastCalledWith('overdue', {}));
  });

  /*
   * Rewritten in Phase FIX. These used to assert that a LINK existed with the
   * right href — which is precisely what the bug was: the link existed, pointed
   * at the right URL, and downloaded nothing, because a plain navigation
   * carries no Authorization header on a JWT-authenticated API.
   *
   * A test that checks the presence of a control rather than what it DOES will
   * pass against a control that does nothing. These now click the button and
   * assert the authenticated request was made.
   */
  it('downloads the selected report in both formats, with the screen\'s '
    + 'filters', async () => {
    const user = userEvent.setup();
    renderPage(<TaskReports />);

    await user.click(await screen.findByRole('button', { name: /CSV/ }));
    expect(taskService.downloadReport).toHaveBeenCalledWith(
      'completion', 'csv', expect.any(Object));

    await user.click(screen.getByRole('button', { name: /PDF/ }));
    expect(taskService.downloadReport).toHaveBeenCalledWith(
      'completion', 'pdf', expect.any(Object));
  });

  it('reports a refused export in place instead of navigating away', async () => {
    // The old behaviour navigated to the API and rendered its JSON, which is
    // why a permission error was reported as "blank page" rather than as a
    // permission error.
    const user = userEvent.setup();
    taskService.downloadReport.mockRejectedValue({
      response: { data: { detail: 'You may not export this report.' } },
    });
    renderPage(<TaskReports />);

    await user.click(await screen.findByRole('button', { name: /CSV/ }));
    expect(await screen.findByRole('alert'))
      .toHaveTextContent('You may not export this report.');
  });

  it('says so when a selection has nothing in it', async () => {
    taskService.getReport.mockResolvedValue({
      slug: 'overdue', label: 'Overdue Report',
      columns: [{ key: 'x', label: 'X' }], rows: [], summary: {},
    });
    renderPage(<TaskReports />);
    expect(await screen.findByText(/Nothing to report/)).toBeInTheDocument();
  });
});
