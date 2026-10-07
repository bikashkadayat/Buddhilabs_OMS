/**
 * Phase T1 — the task menus.
 *
 * The claim these pages make is that a menu is A ROUTE PLUS A SERVER-SIDE SCOPE
 * NAME, never a second list implementation with its own filtering. So what is
 * worth testing is not that rows render — TaskTable does that — but that each
 * menu asks the server for the scope it advertises, and that no two menus ask
 * for the same one.
 *
 * The scope strings here are the contract with `tasks.views._apply_scope`. If
 * one is renamed on either side without the other, this fails and names it.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: {
    getTasks: vi.fn(),
    getBoard: vi.fn(),
    getFilterOptions: vi.fn(),
  },
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));

import * as pages from './scopePages';
import { taskService } from '../../services/taskService';
import { useAuth } from '../../hooks/useAuth';

/**
 * Menu component -> the scope it must request, its heading, and which endpoint
 * it opens on.
 *
 * Phase T3 gave Team Tasks a board by default — a manager opening it wants to
 * see where work is, not a list to scroll. The two views ask different
 * endpoints with the SAME params, so the row records which one to expect.
 */
const MENUS = [
  ['NeedsMyAction', 'needs_me', 'Waiting for Me', 'list'],
  ['MyTasks', 'mine', 'My Tasks', 'list'],
  ['AssignedByMe', 'assigned_by_me', 'Assigned By Me', 'list'],
  // Phase TASK-GOVERNANCE-HARDENING renamed it: the page a Department Head
  // opens is their department's board, and the rail item still says Team tasks.
  ['TeamTasks', 'team', 'Department Board', 'board'],
  ['DueToday', 'due_today', 'Due Today', 'list'],
  ['OverdueTasks', 'overdue', 'Overdue', 'list'],
  ['CompletedTasks', 'completed', 'Completed', 'list'],
  ['AllTasks', 'all', 'All Tasks', 'list'],
  ['DraftTasks', 'drafts', 'Draft Tasks', 'list'],
  ['PendingReview', 'pending_review', 'Pending Review', 'list'],
];

const renderMenu = (name) => {
  const Page = pages[name];
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Page /></MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('task menus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ role: 'checker', user: { id: 'u1' } });
    taskService.getTasks.mockResolvedValue({ results: [], count: 0 });
    taskService.getBoard.mockResolvedValue({ columns: [], total: 0 });
    taskService.getFilterOptions.mockResolvedValue({
      departments: [], assignees: [], reviewers: [],
    });
  });

  it.each(MENUS)('%s asks the server for scope "%s"',
    async (name, scope, _title, endpoint) => {
      const call = endpoint === 'board' ? taskService.getBoard : taskService.getTasks;
      renderMenu(name);
      await waitFor(() => expect(call).toHaveBeenCalled());
      expect(call.mock.calls[0][0]).toMatchObject({ scope });
    });

  it.each(MENUS)('%s opens on its %s view without fetching the other',
    async (name, _scope, _title, endpoint) => {
      /* Only the visible view is fetched. Loading both would double every
         list page's cost for a view nobody has asked for yet. */
      const idle = endpoint === 'board' ? taskService.getTasks : taskService.getBoard;
      const used = endpoint === 'board' ? taskService.getBoard : taskService.getTasks;
      renderMenu(name);
      await waitFor(() => expect(used).toHaveBeenCalled());
      expect(idle).not.toHaveBeenCalled();
    });

  it.each(MENUS)('%s is titled "%s"', async (name, _scope, title) => {
    renderMenu(name);
    expect(await screen.findByRole('heading', { name: title, level: 1 }))
      .toBeInTheDocument();
  });

  it('gives every menu a distinct scope, so no two are the same list twice', () => {
    const scopes = MENUS.map(([, scope]) => scope);
    expect(new Set(scopes).size).toBe(scopes.length);
  });

  it('covers every menu the module structure names', () => {
    /* Dashboard · My Tasks · Assigned By Me · Team Tasks · Due Today ·
       Overdue · Completed — the Dashboard is its own page, the other six are
       here. */
    const named = MENUS.map(([name]) => name);
    for (const menu of ['MyTasks', 'AssignedByMe', 'TeamTasks', 'DueToday',
      'OverdueTasks', 'CompletedTasks']) {
      expect(named).toContain(menu);
      expect(pages[menu]).toBeTypeOf('function');
    }
  });

  it('offers Create Task to a department head', async () => {
    useAuth.mockReturnValue({ role: 'checker', user: { id: 'u1' } });
    renderMenu('MyTasks');
    expect(await screen.findByRole('button', { name: /create task/i }))
      .toBeInTheDocument();
  });

  it('offers Create Task to an employee too, since everybody raises tasks',
    async () => {
      /* Phase TASK-MANAGEMENT-ASANA-MODEL. A separate test rather than a second
         render in the one above: the first tree stays mounted and re-renders
         when its query settles, so it would pick up the changed mock and report
         the second role's answer. */
      useAuth.mockReturnValue({ role: 'maker', user: { id: 'u2' } });
      renderMenu('MyTasks');
      await waitFor(() => expect(taskService.getTasks).toHaveBeenCalled());
      expect(screen.getByRole('button', { name: /create task/i })).toBeTruthy();
    });
});
