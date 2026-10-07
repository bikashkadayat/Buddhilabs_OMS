import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Home (Phase HOME-REDESIGN).
 *
 * What is pinned: the greeting sentence in its three shapes, the urgency
 * lines under it, the order of the sections at every width, and that the
 * summary cards show zeros rather than hiding.
 */

const queue = {
  allItems: [], counts: { total: 0, overdue: 0, review: 0, approval: 0 },
  sources: [], isLoading: false, act: vi.fn(), retrySources: vi.fn(),
};
vi.mock('../hooks/useWorkQueue', () => ({ useWorkQueue: () => queue }));
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u1', full_name: 'Bikash Demo' }, role: 'checker', loading: false }),
}));

const dashboard = { current: {} };
vi.mock('../services/taskService', () => ({
  taskService: { getDashboard: () => Promise.resolve(dashboard.current) },
}));
vi.mock('../services/notificationService', () => ({
  notificationService: { list: () => Promise.resolve([]) },
}));
vi.mock('../services/circularService', () => ({
  circularService: {
    getDashboard: () => Promise.resolve({}),
    getCirculars: () => Promise.resolve([]),
  },
}));
vi.mock('../services/memoService', () => ({
  memoService: { getDashboard: () => Promise.resolve({}) },
}));
vi.mock('../services/minuteService', () => ({
  minuteService: { getDashboard: () => Promise.resolve({}) },
}));
vi.mock('../services/workforceService', () => ({
  workforceService: { team: () => Promise.resolve({ counts: { present: 3 }, absent_employees: [] }) },
}));
vi.mock('../services/attendanceService', () => ({
  attendanceService: { today: () => Promise.resolve({}) },
}));
vi.mock('../services/leaveService', () => ({
  leaveService: {
    getBalances: () => Promise.resolve({ data: [] }),
    departmentGovernance: () => Promise.resolve({ status: 'ok', missing_head: 0 }),
  },
}));

import Home from './Home';

const mount = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Home /></MemoryRouter>
    </QueryClientProvider>,
  );
};

const item = (extra = {}) => ({
  id: `task:${Math.random()}`, type: 'task', kind: 'action', title: 'A task',
  subtitle: 'NIFN-TSK-1', href: '/tasks/1', actions: [], createdAt: '2026-09-20T09:00:00Z',
  dueAt: null, ...extra,
});

beforeEach(() => {
  queue.allItems = [];
  queue.counts = { total: 0, overdue: 0, review: 0, approval: 0 };
  queue.isLoading = false;
  dashboard.current = { my_tasks: 3, completed: 3, to_accept: 0, due_today: 0, overdue: 0 };
});

describe('the greeting', () => {
  it('names the person and ends with a full stop', () => {
    mount();
    expect(screen.getByRole('heading', { level: 1 }))
      .toHaveTextContent(/^Good (morning|afternoon|evening), Bikash\.$/);
  });

  it('counts the items in the plural', () => {
    queue.counts = { total: 7, overdue: 1, review: 3, approval: 2 };
    mount();
    expect(screen.getByText(/that need attention today\./).textContent)
      .toBe('You have 7 items that need attention today.');
  });

  it('counts one item in the singular', () => {
    queue.counts = { total: 1, overdue: 0, review: 0, approval: 0 };
    mount();
    expect(screen.getByText(/needs attention today\./).textContent)
      .toBe('You have 1 item that needs attention today.');
  });

  it('says so when nothing is waiting', () => {
    mount();
    expect(screen.getByText('Nothing needs your attention today.')).toBeInTheDocument();
    expect(document.querySelector('.hm-urgent')).toBeNull();
  });

  it('says it is still checking while the queue loads', () => {
    queue.isLoading = true;
    mount();
    expect(screen.getByText('Checking what needs your attention…')).toBeInTheDocument();
  });

  it('carries a date line built from local parts', () => {
    mount();
    const d = new Date();
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    expect(document.querySelector('.ah-date')).toHaveTextContent(iso);
    expect(document.querySelector('.ah-date')).toHaveTextContent(
      d.toLocaleDateString(undefined, { weekday: 'long' }),
    );
  });
});

describe('the urgency lines', () => {
  it('lists overdue, reviews and tasks ready to start, most urgent first', async () => {
    queue.counts = { total: 7, overdue: 1, review: 3, approval: 2 };
    queue.allItems = [item({ dueAt: '2020-01-01' })];
    dashboard.current = { my_tasks: 3, completed: 3, to_accept: 1, due_today: 0, overdue: 1 };
    mount();
    await screen.findByText('1 task ready to start.');
    const list = screen.getByText('1 task is overdue.');
    const lines = Array.from(list.closest('ul').querySelectorAll('li')).map((li) => li.textContent);
    expect(lines).toEqual([
      '1 task is overdue.',
      '5 reviews are waiting for you.',
      '1 task ready to start.',
    ]);
    expect(list).toHaveClass('is-late');
  });

  it('pluralises each line and omits the zero ones', async () => {
    queue.counts = { total: 4, overdue: 2, review: 1, approval: 0 };
    queue.allItems = [item({ dueAt: '2020-01-01' }), item({ dueAt: '2020-01-02' })];
    dashboard.current = { my_tasks: 3, completed: 3, to_accept: 2, due_today: 0, overdue: 2 };
    mount();
    expect(await screen.findByText('2 tasks ready to start.')).toBeInTheDocument();
    expect(screen.getByText('2 tasks are overdue.')).toBeInTheDocument();
    expect(screen.getByText('1 review is waiting for you.')).toBeInTheDocument();
    expect(document.querySelectorAll('.hm-urgent li')).toHaveLength(3);
  });

  it('says "items" when the overdue rows are not all tasks', () => {
    queue.counts = { total: 2, overdue: 2, review: 0, approval: 0 };
    queue.allItems = [item({ dueAt: '2020-01-01' }), item({ type: 'memo', dueAt: '2020-01-02' })];
    mount();
    expect(screen.getByText('2 items are overdue.')).toBeInTheDocument();
  });
});

describe('the sections', () => {
  it('appear in the same order at every width', async () => {
    mount();
    await screen.findByRole('heading', { name: 'My tasks' });
    const names = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent.trim());
    // A department head sees their team first, under their own attendance.
    expect(names).toEqual([
      'Your team today', 'My focus today', 'Start something', 'My tasks',
      'Latest updates', 'Today at a glance',
    ]);
  });

  it('keeps the queue link, the empty focus state and the strips', async () => {
    mount();
    expect(screen.getByRole('link', { name: /Open my queue/ })).toHaveAttribute('href', '/queue');
    expect(screen.getByText('Nothing is waiting on you.')).toBeInTheDocument();
    expect(await screen.findByText('No unread notices')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /My attendance →/ })).toHaveAttribute('href', '/my-attendance');
  });

  it('renders the focus rows most urgent first, with one verb per row', () => {
    queue.counts = { total: 3, overdue: 1, review: 1, approval: 0 };
    queue.allItems = [
      item({ id: 'a', title: 'Later', dueAt: '2999-01-10' }),
      item({ id: 'b', title: 'Late', kind: 'review', dueAt: '2020-01-01' }),
      item({ id: 'c', title: 'Soon', dueAt: '2999-01-02', actions: [{ label: 'Accept', verb: 'accept', variant: 'primary', run: vi.fn() }] }),
    ];
    mount();
    const rows = document.querySelectorAll('.hm-focus-row');
    expect(Array.from(rows).map((r) => within(r).getByRole('link', { name: /Later|Late|Soon/ }).textContent))
      .toEqual(['Late', 'Soon', 'Later']);
    expect(within(rows[0]).getByText('Overdue', { exact: false })).toHaveClass('is-late');
    expect(within(rows[0]).getByRole('link', { name: 'Review' })).toHaveClass('is-primary');
    expect(within(rows[1]).getByRole('button', { name: 'Accept' })).not.toHaveClass('is-primary');
    expect(within(rows[2]).getByRole('link', { name: 'Start' })).toBeInTheDocument();
  });
});

describe('the summary cards', () => {
  it('show zeros rather than hiding', () => {
    mount();
    const cards = document.querySelectorAll('a.hm-sum-card');
    expect(cards).toHaveLength(4);
    expect(Array.from(cards).map((c) => c.querySelector('.hm-sum-n').textContent))
      .toEqual(['0', '0', '0', '0']);
    expect(screen.queryByText(/all caught up/)).toBeNull();
  });

  it('carry the queue and task figures once they resolve', async () => {
    queue.counts = { total: 7, overdue: 1, review: 3, approval: 2 };
    dashboard.current = { my_tasks: 3, completed: 3, to_accept: 1, due_today: 2, overdue: 1 };
    mount();
    const due = screen.getByText('Due today');
    await waitFor(() => expect(due.closest('a').querySelector('.hm-sum-n')).toHaveTextContent('2'));
    expect(screen.getByText('Needs attention').closest('a').querySelector('.hm-sum-n')).toHaveTextContent('7');
    expect(screen.getByText('Pending reviews').closest('a').querySelector('.hm-sum-n')).toHaveTextContent('5');
    expect(screen.getByText('Completed').closest('a').querySelector('.hm-sum-n')).toHaveTextContent('3');
  });
});
