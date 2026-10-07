/**
 * Phase T1 — the task detail page's action bar.
 *
 * The claim worth testing is that the buttons come from the SERVER's
 * `capabilities` object and from nothing else. If this page ever grew its own
 * opinion about who may do what, the two would drift and the UI would start
 * offering actions the API refuses — which is exactly the failure the
 * capability flags exist to prevent.
 *
 * So the fixtures differ ONLY in `capabilities`, and the assertions are about
 * which buttons appear.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import TaskDetail from './TaskDetail';

vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../services/taskService', () => ({
  taskService: {
    getTask: vi.fn(),
    searchEmployees: vi.fn(),
    setChecklist: vi.fn(),
    addLink: vi.fn(),
    removeAttachment: vi.fn(),
    flagAttachment: vi.fn(),
    getDownloadLog: vi.fn(),
    editComment: vi.fn(),
    getTemplates: vi.fn(),
    applyTemplate: vi.fn(),
    saveAsTemplate: vi.fn(),
    accept: vi.fn(),
    approve: vi.fn(),
    requestRework: vi.fn(),
    submitForReview: vi.fn(),
    close: vi.fn(),
    block: vi.fn(),
    cancel: vi.fn(),
    updateProgress: vi.fn(),
    tickChecklistItem: vi.fn(),
    addComment: vi.fn(),
    uploadAttachments: vi.fn(),
    start: vi.fn(),
    unblock: vi.fn(),
    assign: vi.fn(),
    requestClarification: vi.fn(),
    createSubtask: vi.fn(),
    updateSubtask: vi.fn(),
    deleteSubtask: vi.fn(),
    completeSubtask: vi.fn(),
    reorderSubtasks: vi.fn(),
    downloadAttachment: vi.fn(),
  },
}));

import { taskService } from '../../services/taskService';
import { useAuth } from '../../hooks/useAuth';

const NO_CAPS = {
  can_view: true, can_edit: false, can_delete: false,
  can_manage_assignees: false, can_assign: false, can_accept: false,
  can_request_clarification: false, can_update_progress: false,
  can_submit_for_review: false, can_review: false, can_close: false,
  can_block: false, can_unblock: false, can_cancel: false,
  can_comment: false, can_upload: false, can_manage_checklist: false,
  can_tick_checklist: false,
};

const task = (overrides = {}) => ({
  id: 't1',
  task_number: 'NIFN-TSK-2083-0001',
  title: 'Prepare the quarterly return',
  description: 'Consolidated across all three cost centres.',
  status: 'under_review',
  status_label: 'Under Review',
  priority: 'high',
  priority_label: 'High',
  due_date: '2026-09-30',
  is_overdue: false,
  progress_percent: 100,
  checklist_percent: 50,
  department_name: 'Engineering',
  created_by_name: 'Hod Person',
  reviewer_name: 'Hod Person',
  assignee_names: ['Employee Person'],
  checklist_done: 1,
  checklist_total: 2,
  blocked_reason: '',
  clarification_note: '',
  assignees: [{ id: 'a1', user: { id: 'u1', full_name: 'Employee Person' },
    user_name: 'Employee Person', is_primary: true, has_accepted: true }],
  checklist: [
    { id: 'c1', text: 'Pull the ledger', is_done: true,
      done_by_name: 'Employee Person', done_at: '2026-09-01T09:00:00Z' },
    { id: 'c2', text: 'Reconcile', is_done: false, done_by_name: '', done_at: null },
  ],
  attachments: [],
  evidence: [{
    id: 'f1', kind: 'file', original_name: 'return.pdf', is_evidence: true,
    uploaded_by: ME, uploaded_by_name: 'Employee Person', size: 2048,
    download_count: 0, is_removed: false,
    uploaded_at: '2026-09-01T09:00:00Z',
    download_url: '/api/v1/tasks/t1/attachments/f1/download/',
  }],
  removed_attachments: [],
  checklist_groups: [],
  progress_is_auto: true,
  template_name: null,
  comments: [{
    id: 'm1', parent: null, author: { id: ME, full_name: 'Employee Person' },
    author_name: 'Employee Person', body: 'The ledger export is missing May.',
    created_at: '2026-09-01T09:00:00Z', edited_at: null, is_edited: false,
    mentions: [], replies: [],
  }],
  timeline: [{ id: 'e1', action: 'created', action_label: 'Created',
    actor_name: 'Hod Person', remarks: 'Task created.',
    created_at: '2026-09-01T08:00:00Z' }],
  created_at: '2026-09-01T08:00:00Z',
  closed_at: null,
  capabilities: NO_CAPS,
  ...overrides,
});

/* The page rewrites /tasks/<uuid> to /tasks/<reference> on first render
   (Phase TASK-DEEP-LINK-SHARING). Tests that interact after load need to know
   that rename has committed, so the current path is exposed beside the page. */
const PathProbe = () => <span data-testid="path">{useLocation().pathname}</span>;

const renderPage = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/tasks/t1']}>
        <Routes>
          <Route path="/tasks/:id" element={<><TaskDetail /><PathProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const settled = () => waitFor(() =>
  expect(screen.getByText('Task Information')).toBeInTheDocument());

/* Settled AND renamed: the observer is on the canonical key, so a mutation's
   refresh is one fetch, not one per key. */
const renamed = (reference = 'NIFN-TSK-2083-0001') => waitFor(() =>
  expect(screen.getByTestId('path')).toHaveTextContent(`/tasks/${reference}`));

const button = (name) => screen.queryByRole('button', { name });

const ME = 'u1';

describe('task detail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ user: { id: ME }, role: 'maker' });
    taskService.searchEmployees.mockResolvedValue([]);
    taskService.getTemplates.mockResolvedValue([]);
  });

  it('renders every section the specification names', async () => {
    taskService.getTask.mockResolvedValue(task());
    renderPage();
    await settled();
    for (const heading of ['Task Information', 'Progress', 'Checklist',
      'Comments', 'Attachments', 'Evidence', 'Activity Timeline']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    }
  });

  it('offers no action at all when the server grants none', async () => {
    taskService.getTask.mockResolvedValue(task());
    renderPage();
    await settled();
    for (const name of [/accept task/i, /approve/i, /need rework/i,
      /verify & close/i, /submit for review/i, /cancel task/i]) {
      expect(button(name)).toBeNull();
    }
  });

  it('offers Approve and Need Rework only with can_review', async () => {
    taskService.getTask.mockResolvedValue(
      task({ capabilities: { ...NO_CAPS, can_review: true } }));
    renderPage();
    await settled();
    expect(button(/^approve$/i)).toBeInTheDocument();
    expect(button(/need rework/i)).toBeInTheDocument();
    expect(button(/verify & close/i)).toBeNull();
  });

  it('offers Verify & Close only with can_close', async () => {
    taskService.getTask.mockResolvedValue(
      task({ status: 'completed', status_label: 'Completed',
        capabilities: { ...NO_CAPS, can_close: true } }));
    renderPage();
    await settled();
    expect(button(/verify & close/i)).toBeInTheDocument();
    expect(button(/^approve$/i)).toBeNull();
  });

  it('will not send a rework request until a real reason is given', async () => {
    /* The server requires ten characters; the button says so rather than
       letting the user discover it as a 400. */
    taskService.getTask.mockResolvedValue(
      task({ capabilities: { ...NO_CAPS, can_review: true } }));
    renderPage();
    await settled();

    fireEvent.click(button(/need rework/i));
    const submit = await screen.findByRole('button',
      { name: /give a reason \(10\+ characters\)/i });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/need rework reason/i),
      { target: { value: 'Page two cites stale figures.' } });
    const ready = screen.getByRole('button', { name: /^need rework$/i });
    expect(ready).not.toBeDisabled();
    fireEvent.click(ready);
    await waitFor(() => expect(taskService.requestRework)
      .toHaveBeenCalledWith('t1', 'Page two cites stale figures.'));
  });

  it('shows the workflow ladder, and replaces it when a task is off-ladder',
    async () => {
      taskService.getTask.mockResolvedValue(task());
      const { unmount } = renderPage();
      await settled();
      expect(screen.getByLabelText('Workflow progress')).toBeInTheDocument();
      unmount();

      taskService.getTask.mockResolvedValue(
        task({ status: 'blocked', status_label: 'Blocked',
          blocked_reason: "Waiting on the auditor's figures." }));
      renderPage();
      await settled();
      expect(screen.queryByLabelText('Workflow progress')).toBeNull();
      expect(screen.getByText(/waiting on the auditor's figures/i))
        .toBeInTheDocument();
    });

  it('reports both the progress number and the checklist number', async () => {
    /* They answer different questions and may legitimately differ, so neither
       is silently preferred over the other. */
    taskService.getTask.mockResolvedValue(task({ progress_percent: 80 }));
    renderPage();
    await settled();
    expect(screen.getByText('80%')).toBeInTheDocument();
    expect(screen.getByText(/checklist 50%/)).toBeInTheDocument();
  });

  it('disables the checklist when the server has not granted a tick', async () => {
    taskService.getTask.mockResolvedValue(task());
    renderPage();
    await settled();
    expect(screen.getByLabelText(/pull the ledger/i, { selector: 'input' })
      || screen.getAllByRole('checkbox')[0]).toBeDisabled();
  });

  it('ticks a checklist item through the server when it may', async () => {
    taskService.getTask.mockResolvedValue(
      task({ capabilities: { ...NO_CAPS, can_tick_checklist: true } }));
    taskService.tickChecklistItem.mockResolvedValue({});
    renderPage();
    await settled();
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    await waitFor(() => expect(taskService.tickChecklistItem)
      .toHaveBeenCalledWith('t1', 'c2', true));
  });

  it('reports a refused transition instead of failing silently', async () => {
    taskService.getTask.mockResolvedValue(
      task({ capabilities: { ...NO_CAPS, can_review: true } }));
    taskService.approve.mockRejectedValue({
      response: { data: { workflow: 'Only a task under review can be approved.' } },
    });
    renderPage();
    await settled();
    fireEvent.click(button(/^approve$/i));
    await waitFor(() => expect(screen.getByRole('alert').textContent)
      .toContain('Only a task under review can be approved.'));
  });

  it('renders an HTML description through the sanitiser', async () => {
    /* Phase TASK-SUBTASKS: a description written by the rich editor is HTML.
       It is rendered as markup — but only through the same allowlist the
       server applies, so a script tag never reaches the page. */
    taskService.getTask.mockResolvedValue(task({
      description_format: 'html',
      description: '<p>Consolidated across <strong>all three</strong> centres.</p>'
        + '<script>window.__pwned = 1</script>',
    }));
    const { container } = renderPage();
    await settled();
    const bold = screen.getByText('all three');
    expect(bold.tagName).toBe('STRONG');
    expect(container.querySelector('script')).toBeNull();
    expect(window.__pwned).toBeUndefined();
  });

  it('keeps a text description as text', async () => {
    taskService.getTask.mockResolvedValue(task({
      description_format: 'text', description: 'Line one <b>not bold</b>',
    }));
    renderPage();
    await settled();
    expect(screen.getByText('Line one <b>not bold</b>').tagName).toBe('P');
  });

  it('shows the subtask tally and derives progress from it', async () => {
    taskService.getTask.mockResolvedValue(task({
      subtasks: [
        { id: 's1', title: 'Pull the ledger', assignee: ME, assignee_name: 'Employee Person',
          is_done: true, done_by_name: 'Employee Person', done_at: '2026-09-01T09:00:00Z',
          can_complete: true, comment_count: 0, evidence_count: 0 },
        { id: 's2', title: 'Reconcile', assignee: ME, assignee_name: 'Employee Person',
          is_done: false, can_complete: true, comment_count: 0, evidence_count: 0 },
      ],
      subtask_done: 1, subtask_total: 2, subtask_percent: 50,
      progress_percent: 50, status: 'in_progress', status_label: 'In Progress',
      capabilities: { ...NO_CAPS, can_update_progress: true },
    }));
    renderPage();
    await settled();
    expect(screen.getByRole('heading', { name: /subtasks · 1 \/ 2 completed/i }))
      .toBeInTheDocument();
    expect(screen.getByText('1 / 2 completed · 50%')).toBeInTheDocument();
    expect(screen.getByText('Progress is derived from subtasks.')).toBeInTheDocument();
    // No manual control while subtasks exist: the server would refuse it.
    expect(screen.queryByRole('radiogroup', { name: 'Set progress' })).toBeNull();
    // The assignee cannot submit yet, and is told why.
    expect(screen.getByRole('button', { name: /submit for review/i })).toBeDisabled();
    expect(screen.getAllByText('1 subtask still open').length).toBeGreaterThan(0);
  });

  it('ticks a subtask through the server and refreshes the task', async () => {
    taskService.getTask.mockResolvedValue(task({
      subtasks: [{ id: 's2', title: 'Reconcile', assignee: ME, assignee_name: 'Employee Person',
        is_done: false, can_complete: true, comment_count: 0, evidence_count: 0 }],
      subtask_done: 0, subtask_total: 1, subtask_percent: 0,
    }));
    taskService.completeSubtask.mockResolvedValue({});
    renderPage();
    await settled();
    await renamed();
    // One fetch so far: the rename hands its key the task already on screen.
    expect(taskService.getTask).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText('Mark Reconcile done'));
    await waitFor(() => expect(taskService.completeSubtask)
      .toHaveBeenCalledWith('t1', 's2', true));
    // ...and exactly one more: the invalidation refetches the task on screen.
    await waitFor(() => expect(taskService.getTask).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 150));
    expect(taskService.getTask).toHaveBeenCalledTimes(2);
  });

  it('offers evidence as a fetch, not a link to the authenticated endpoint',
    async () => {
      /*
       * Inverted (Phase CRITICAL-TASK-EVIDENCE-DOWNLOAD-BUG). It asserted an
       * <a href> to /api/v1/tasks/t1/attachments/f1/download/ — an
       * authenticated view — and passed while every click in production
       * returned "Authentication credentials were not provided."
       *
       * A link is the wrong shape for a file the server will only hand over
       * with a token, so the assertion is now on the shape that works.
       */
      taskService.getTask.mockResolvedValue(task());
      renderPage();
      await settled();
      expect(screen.queryByRole('link', { name: 'return.pdf' })).toBeNull();
      expect(screen.getByRole('button', { name: 'return.pdf' })).toBeInTheDocument();
    });
});
