/**
 * Phase T4 — Work Queue integration, Home widgets and the review queue.
 *
 * The claims worth pinning: a task reaches the queue as ONE row per task with
 * the right situation on it, Home renders whichever role blocks the server sent
 * and stays silent when the source fails, and the review queue names the
 * bottleneck rather than only counting it.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../services/taskService', () => ({
  taskService: {
    getTasks: vi.fn(), getDashboard: vi.fn(), getReviewQueue: vi.fn(),
    accept: vi.fn(),
  },
}));

import { SOURCES, TYPES, KINDS, taskSituation, taskActions } from '../../services/workQueue';
import TaskWidgets from '../../components/home/TaskWidgets';
import TaskReviewQueue from './TaskReviewQueue';
import { taskService } from '../../services/taskService';
import { useAuth } from '../../hooks/useAuth';

const row = (extra = {}) => ({
  id: 't1',
  task_number: 'NIFN-TSK-2083-0001',
  title: 'Prepare the quarterly return',
  status: 'assigned',
  status_label: 'Assigned',
  priority_label: 'High',
  is_overdue: false,
  overdue_days: 0,
  progress_percent: 0,
  checklist_done: 1,
  checklist_total: 4,
  created_by_name: 'Sanjaya Poudel',
  created_at: '2026-09-01T09:00:00Z',
  due_date: '2026-09-30',
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
  useAuth.mockReturnValue({ user: { id: 'u1' }, role: 'maker' });
});

// ---------------------------------------------------------------------------
// Work Queue (Part 1)
// ---------------------------------------------------------------------------
describe('work queue integration', () => {
  const source = () => SOURCES.find((s) => s.type === TYPES.TASK);

  it('registers tasks as a queue source, first', () => {
    /* Tasks are the only source that is the person's OWN work rather than a
       decision about somebody else's, and the queue is read top-down. */
    expect(SOURCES[0].type).toBe(TYPES.TASK);
  });

  it('fetches one scope, not six', () => {
    /* The six named situations are all slices of `needs_me`, which the server
       already computes. Six requests would mean six chances to show the same
       task twice under different labels. */
    source().fetch();
    expect(taskService.getTasks).toHaveBeenCalledTimes(1);
    expect(taskService.getTasks).toHaveBeenCalledWith({ scope: 'needs_me' });
  });

  it('maps a task to a queue row that links to the task', () => {
    const item = source().map(row());
    expect(item.id).toBe('task:t1');
    expect(item.type).toBe(TYPES.TASK);
    expect(item.href).toBe('/tasks/t1');
    expect(item.title).toBe('Prepare the quarterly return');
    expect(item.subtitle).toContain('NIFN-TSK-2083-0001');
    expect(item.subtitle).toContain('1/4 checklist');
  });

  it.each([
    ['assigned', {}, 'awaiting your acceptance'],
    ['under review', { status: 'under_review' }, 'under review'],
    ['blocked', { status: 'blocked' }, 'blocked'],
    ['overdue', { is_overdue: true, overdue_days: 3 }, '3 day(s) overdue'],
    ['in progress', { status: 'in_progress', progress_percent: 40 }, 'in progress'],
  ])('names the %s situation', (_name, extra, expected) => {
    expect(taskSituation(row(extra))).toBe(expected);
  });

  it('lets the decisive situation win when a task is several at once', () => {
    /* Blocked AND overdue: the label should name what decides the next move. */
    expect(taskSituation(row({ status: 'blocked', is_overdue: true }))).toBe('blocked');
  });

  it('offers Accept inline, because it needs no typing', () => {
    const actions = taskActions(row());
    expect(actions).toHaveLength(1);
    expect(actions[0].label).toBe('Accept');
    actions[0].run();
    expect(taskService.accept).toHaveBeenCalledWith('t1');
  });

  it('offers no inline action for a review, which needs a written reason', () => {
    /* Returning work requires a remark the API enforces, and a queue row has
       nowhere to type one — so review opens the task instead. */
    expect(taskActions(row({ status: 'under_review' }))).toEqual([]);
  });

  it('files a review as a REVIEW and everything else as an ACTION', () => {
    expect(source().map(row({ status: 'under_review' })).kind).toBe(KINDS.REVIEW);
    expect(source().map(row()).kind).toBe(KINDS.ACTION);
  });
});

// ---------------------------------------------------------------------------
// Home widgets (Part 2)
// ---------------------------------------------------------------------------
describe('home task widgets', () => {
  const employee = {
    my_tasks: 6, due_today: 2, overdue: 1, completed: 9, pending_review: 0,
    to_accept: 2,
    by_status: [
      { value: 'assigned', label: 'Assigned', count: 2 },
      { value: 'under_review', label: 'Under Review', count: 3 },
    ],
    completion_trend: [{ week_starting: '2026-09-13', completed: 4 }],
  };

  const figure = (container, label) => Array.from(container.querySelectorAll('.hm-tstat'))
    .find((el) => el.textContent.includes(label));

  it('shows one card with the four personal figures', async () => {
    taskService.getDashboard.mockResolvedValue(employee);
    const { container } = renderPage(<TaskWidgets />);
    await screen.findByRole('heading', { name: 'My tasks' });

    expect(figure(container, 'Open')).toHaveTextContent('6');
    expect(figure(container, 'Pending acceptance')).toHaveTextContent('2');
    expect(figure(container, 'Waiting review')).toHaveTextContent('3');
    expect(figure(container, 'Overdue')).toHaveTextContent('1');
    // The three tiles and the "N completed · N open" strip are gone.
    expect(container.querySelector('.memo-tiles')).toBeNull();
    expect(container.querySelector('.hm-thin-tasks')).toBeNull();
    expect(screen.getByRole('link', { name: /Task workspace/ })).toHaveAttribute('href', '/tasks');
  });

  it('draws the completion ring over completed + open', async () => {
    // 9 completed of 9 + 6 = 60%. The denominator is stated on the ring so it
    // cannot be mistaken for the Insights page's rate over a different set.
    taskService.getDashboard.mockResolvedValue(employee);
    const { container } = renderPage(<TaskWidgets />);
    await screen.findByRole('heading', { name: 'My tasks' });
    const ring = container.querySelector('.hm-ring');
    expect(ring).toHaveTextContent('60%');
    expect(ring.style.getPropertyValue('--p')).toBe('60');
    expect(ring).toHaveAttribute('aria-label', '60% of my tasks completed');
  });

  it('reads "waiting review" from by_status and tolerates its absence', async () => {
    taskService.getDashboard.mockResolvedValue({ ...employee, by_status: undefined });
    const { container } = renderPage(<TaskWidgets />);
    await screen.findByRole('heading', { name: 'My tasks' });
    expect(figure(container, 'Waiting review')).toHaveTextContent('0');
  });

  it('carries no team or organisation block at all', async () => {
    // Phase DASHBOARD-V3. Team Completion, Organisation Completion and Overdue
    // Rate are department and organisation ANALYTICS, and Home is the action
    // centre. Sent the full HR payload, Home must show only the personal card.
    taskService.getDashboard.mockResolvedValue({
      ...employee, team_tasks: 12, team_completion_percent: 55, team_overdue: 3,
      pending_review: 4, org_total: 40, org_open: 22,
      org_completion_percent: 45, org_overdue_percent: 12,
    });
    renderPage(<TaskWidgets />);
    await screen.findByRole('heading', { name: 'My tasks' });

    for (const gone of ['Team Tasks', 'Team Completion', 'Team Late',
      'Organisation Open', 'Organisation Completion', 'Overdue Rate',
      'Task Reviews', 'Pending Reviews']) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it('renders nothing at all when the task dashboard will not load', async () => {
    /* Home aggregates half a dozen sources; one that fails must cost its own
       widget and nothing else. */
    taskService.getDashboard.mockRejectedValue(new Error('down'));
    const { container } = renderPage(<TaskWidgets />);
    await waitFor(() => expect(taskService.getDashboard).toHaveBeenCalled());
    // The loading skeleton is allowed; once the request has failed, nothing.
    await waitFor(() => expect(container.firstChild).toBeNull());
    expect(screen.queryByRole('heading', { name: 'My tasks' })).toBeNull();
  });

  it('collapses to one line for somebody with no tasks at all', async () => {
    taskService.getDashboard.mockResolvedValue({
      my_tasks: 0, due_today: 0, overdue: 0, completed: 0, pending_review: 0,
      assigned_by_me: 0, to_accept: 0, my_drafts: 0, blocked: 0, by_status: [],
    });
    const { container } = renderPage(<TaskWidgets />);
    expect(await screen.findByText(/No tasks assigned to you/i))
      .toBeInTheDocument();
    expect(container.querySelector('.hm-ring')).toBeNull();
  });

  it('shows the card as soon as there is anything to show', async () => {
    taskService.getDashboard.mockResolvedValue({
      ...employee, my_tasks: 0, due_today: 0, overdue: 0, to_accept: 0, completed: 2 });
    const { container } = renderPage(<TaskWidgets />);
    await screen.findByRole('heading', { name: 'My tasks' });
    expect(container.querySelector('.hm-ring')).toHaveTextContent('100%');
    expect(screen.queryByText(/No tasks assigned to you/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Review queue (Part 6)
// ---------------------------------------------------------------------------
describe('review queue', () => {
  const payload = {
    rows: [
      { task_id: 'a', task_number: 'NIFN-TSK-2083-0001', title: 'Old one',
        assignee: 'Bikash Kadayat', reviewer: 'Sanjaya Poudel',
        department: 'Engineering', priority: 'High', waiting_days: 21,
        due_date: '2026-12-30', is_overdue: false },
      { task_id: 'b', task_number: 'NIFN-TSK-2083-0002', title: 'New one',
        assignee: 'Bikash Kadayat', reviewer: 'Sanjaya Poudel',
        department: 'Engineering', priority: 'Low', waiting_days: 1,
        due_date: '2026-10-01', is_overdue: false },
    ],
    by_reviewer: [
      { reviewer: 'Sanjaya Poudel', waiting: 2, oldest_days: 21 },
    ],
    summary: { waiting: 2, oldest_days: 21, average_days: 11, reviewers: 1 },
  };

  it('names the bottleneck rather than only counting it', async () => {
    /* "Fourteen awaiting review" is a number; "eleven with one person, oldest
       three weeks" is something somebody can act on this afternoon. */
    taskService.getReviewQueue.mockResolvedValue(payload);
    renderPage(<TaskReviewQueue />);

    expect(await screen.findByText('Reviewer Backlog')).toBeInTheDocument();
    const table = screen.getAllByRole('table')[0];
    expect(within(table).getByText('Sanjaya Poudel')).toBeInTheDocument();
    expect(within(table).getByText('21 days')).toBeInTheDocument();
  });

  it('renders the server order, oldest first', async () => {
    taskService.getReviewQueue.mockResolvedValue(payload);
    renderPage(<TaskReviewQueue />);
    await screen.findByText('Old one');
    const titles = screen.getAllByText(/^(Old|New) one$/).map((el) => el.textContent);
    expect(titles).toEqual(['Old one', 'New one']);
  });

  it('states the wait in words as well as colour', async () => {
    taskService.getReviewQueue.mockResolvedValue({
      ...payload,
      rows: [{ ...payload.rows[0], waiting_days: 1 }],
      by_reviewer: [{ reviewer: 'Sanjaya Poudel', waiting: 1, oldest_days: 1 }],
    });
    renderPage(<TaskReviewQueue />);
    // Singular, because "1 days" is the kind of thing people notice.
    expect((await screen.findAllByText('1 day')).length).toBeGreaterThan(0);
  });

  it('says so when nothing is waiting rather than showing an empty table',
    async () => {
      taskService.getReviewQueue.mockResolvedValue({
        rows: [], by_reviewer: [],
        summary: { waiting: 0, oldest_days: 0, average_days: 0, reviewers: 0 },
      });
      renderPage(<TaskReviewQueue />);
      expect(await screen.findByText(/Nothing is waiting for review/))
        .toBeInTheDocument();
      expect(screen.queryByRole('table')).toBeNull();
    });
});
