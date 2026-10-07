/**
 * Phase T1 — the task dashboard.
 *
 * The claim worth testing is not "tiles render" but that the page is FOUR
 * dashboards chosen by the SERVER. The specification names an Employee view, an
 * HR view, a Department Head view and an Admin view; the payload simply does not
 * carry `team_tasks` for an employee, so the page cannot show a tile the API
 * would not fill. All three shapes are asserted, in both directions.
 *
 * Every fixture count is DISTINCT, which is what makes "this tile reads its own
 * key" testable: with two tiles reading each other's keys, identical values
 * would hide it.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import TaskDashboard from './TaskDashboard';

vi.mock('../../services/taskService', () => ({
  taskService: { getDashboard: vi.fn() },
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));

import { taskService } from '../../services/taskService';
import { useAuth } from '../../hooks/useAuth';

const EMPLOYEE = {
  needs_my_action: 11,
  my_tasks: 22,
  due_today: 33,
  overdue: 44,
  completed: 55,
  assigned_by_me: 0,
  my_drafts: 0,
  blocked: 0,
  to_accept: 4,
  by_status: [{ value: 'assigned', label: 'Assigned', count: 7 }],
  by_priority: [{ value: 'high', label: 'High', count: 7 }],
};

const HEAD = {
  ...EMPLOYEE,
  assigned_by_me: 66,
  team_tasks: 77,
  team_completed: 88,
  team_overdue: 99,
  team_completion_percent: 41,
  pending_review: 12,
  pending_closure: 13,
};

const HR = {
  ...HEAD,
  org_open: 111,
  org_completed: 122,
  org_overdue: 133,
  org_total: 233,
  org_completion_percent: 52,
  by_department: [
    { label: 'Engineering', open: 4, total: 10 },
    { label: 'Finance', open: 1, total: 4 },
  ],
};

const EMPLOYEE_TILES = [
  ['Waiting for Me', 'needs_my_action', '/tasks/needs-me'],
  ['My Tasks', 'my_tasks', '/tasks/mine'],
  ['Due Today', 'due_today', '/tasks/due-today'],
  ['Overdue', 'overdue', '/tasks/overdue'],
  ['Completed', 'completed', '/tasks/completed'],
];

const tile = (label) => {
  const hit = screen.getAllByText(label)
    .map((node) => node.closest('a'))
    .find(Boolean);
  if (!hit) throw new Error(`No dashboard tile labelled "${label}"`);
  return hit;
};

const renderPage = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TaskDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
};

const settled = () => waitFor(() =>
  expect(screen.getByText('My Tasks')).toBeInTheDocument());

describe('task dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ role: 'maker', user: { id: 'u1' } });
  });

  describe('for an employee', () => {
    beforeEach(() => taskService.getDashboard.mockResolvedValue(EMPLOYEE));

    it.each(EMPLOYEE_TILES)('the %s tile shows counts.%s', async (label, key) => {
      renderPage();
      await settled();
      expect(tile(label).textContent).toContain(String(EMPLOYEE[key]));
    });

    it.each(EMPLOYEE_TILES)('the %s tile links to the list it counts',
      async (label, _key, href) => {
        renderPage();
        await settled();
        expect(tile(label)).toHaveAttribute('href', href);
      });

    it('offers a Create Task button: everybody raises their own work',
      async () => {
        // Phase TASK-MANAGEMENT-ASANA-MODEL. Which KIND an Employee may raise is
        // the server's answer, asked for on the form itself.
        renderPage();
        await settled();
        expect(screen.getByRole('button', { name: /create task/i })).toBeTruthy();
      });

    it('shows no team or organisation section', async () => {
      renderPage();
      await settled();
      expect(screen.queryByText('Team Tasks')).toBeNull();
      expect(screen.queryByText('Organisation Summary')).toBeNull();
    });
  });

  describe('for a department head', () => {
    beforeEach(() => {
      useAuth.mockReturnValue({ role: 'checker', user: { id: 'u2' } });
      taskService.getDashboard.mockResolvedValue(HEAD);
    });

    it('adds the team tiles the specification names', async () => {
      renderPage();
      await settled();
      expect(tile('Team Tasks').textContent).toContain('77');
      expect(tile('Completion').textContent).toContain('41%');
      expect(tile('Pending Review').textContent).toContain('12');
    });

    it('keeps the personal tiles as well', async () => {
      /* A department head is also an employee with tasks of their own. */
      renderPage();
      await settled();
      expect(tile('My Tasks').textContent).toContain('22');
    });

    it('still shows no organisation summary', async () => {
      renderPage();
      await settled();
      expect(screen.queryByText('Organisation Summary')).toBeNull();
    });

    it('offers Create Task', async () => {
      renderPage();
      await settled();
      expect(screen.getByRole('button', { name: /create task/i }))
        .toBeInTheDocument();
    });
  });

  describe('for HR', () => {
    beforeEach(() => {
      useAuth.mockReturnValue({ role: 'approver', user: { id: 'u3' } });
      taskService.getDashboard.mockResolvedValue(HR);
    });

    it('adds the organisation summary on top of everything else', async () => {
      renderPage();
      await settled();
      expect(screen.getByText('Organisation Summary')).toBeInTheDocument();
      expect(tile('Open Tasks').textContent).toContain('111');
      expect(tile('Completed Tasks').textContent).toContain('122');
      expect(tile('Overdue Tasks').textContent).toContain('133');
    });

    it('breaks the organisation down by department', async () => {
      renderPage();
      await settled();
      const table = screen.getByRole('table');
      expect(within(table).getByText('Engineering')).toBeInTheDocument();
      expect(within(table).getByText('Finance')).toBeInTheDocument();
      // Engineering: 6 of 10 done = 60%.
      expect(table.textContent).toContain('60%');
    });
  });

  it('surfaces a failure rather than rendering zeroes as if they were counts',
    async () => {
      useAuth.mockReturnValue({ role: 'maker', user: { id: 'u1' } });
      taskService.getDashboard.mockRejectedValue(new Error('Server unavailable'));
      renderPage();
      await waitFor(() =>
        expect(screen.getByRole('alert')).toBeInTheDocument());
      // A failure with no server answer reads as a sentence, never as the
      // raw exception text (which here would be "Server unavailable").
      expect(screen.getByRole('alert').textContent).toMatch(/couldn’t load this/);
    });
});
