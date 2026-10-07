/**
 * Phase TASK-SUBTASKS — the subtask panel.
 *
 * What is worth pinning: the tally and bar are built from the server's
 * numbers; a row can only be ticked when ITS `can_complete` says so; every
 * owner action calls back rather than writing anything itself; delete asks
 * inline rather than through window.confirm; and a deep link opens and lights
 * the row it names.
 */
import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: { downloadAttachment: vi.fn(), searchEmployees: vi.fn() },
}));

import SubtaskPanel from './SubtaskPanel';

const row = (overrides = {}) => ({
  id: 's1', title: 'Pull the ledger', description: '', position: 0,
  assignee: 'u1', assignee_name: 'Employee Person', due_date: null,
  is_done: false, done_by_name: '', done_at: null, is_overdue: false,
  comment_count: 0, evidence_count: 0, can_complete: false, can_edit: false,
  created_at: '2026-09-01T08:00:00Z',
  ...overrides,
});

const task = (overrides = {}) => ({
  id: 't1',
  subtasks: [
    row(),
    row({ id: 's2', title: 'Reconcile', is_done: true, done_by_name: 'Employee Person',
      done_at: '2026-09-02T08:00:00Z', can_complete: true, comment_count: 1 }),
    row({ id: 's3', title: 'File the return', assignee: null, assignee_name: '',
      due_date: '2026-08-01', is_overdue: true }),
  ],
  subtask_done: 1, subtask_total: 3, subtask_percent: 33,
  comments: [
    { id: 'c1', parent: null, subtask: 's2', author: { id: 'u1' }, author_name: 'Employee Person',
      body: 'Figures reconciled against May.', created_at: '2026-09-02T08:00:00Z',
      mentions: [], replies: [] },
    { id: 'c2', parent: null, subtask: null, author: { id: 'u1' }, author_name: 'Employee Person',
      body: 'A task-level comment.', created_at: '2026-09-02T08:00:00Z',
      mentions: [], replies: [] },
  ],
  evidence: [
    { id: 'f1', kind: 'file', subtask: 's2', original_name: 'reconciliation.xlsx',
      uploaded_by_name: 'Employee Person', uploaded_at: '2026-09-02T08:00:00Z' },
  ],
  ...overrides,
});

const handlers = () => ({
  onToggle: vi.fn(), onCreate: vi.fn(), onUpdate: vi.fn(), onDelete: vi.fn(),
  onReorder: vi.fn(), onComment: vi.fn(), onEditComment: vi.fn(), onUpload: vi.fn(),
});

const renderPanel = (props = {}, { path = '/tasks/t1' } = {}) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const h = handlers();
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <SubtaskPanel task={task()} assignees={[{ id: 'u1', name: 'Employee Person' }]}
          currentUserId="u1" {...h} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, h };
};

describe('subtask panel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the tally and the derived bar from the server numbers', () => {
    renderPanel();
    expect(screen.getByText('1 / 3 completed')).toBeInTheDocument();
    expect(screen.getByLabelText('33% of subtasks complete')).toBeInTheDocument();
    expect(screen.getByText('33%')).toBeInTheDocument();
  });

  it('enables a tick only where the row itself allows it', () => {
    const { h } = renderPanel();
    expect(screen.getByLabelText('Mark Pull the ledger done')).toBeDisabled();
    const done = screen.getByLabelText('Mark Reconcile not done');
    expect(done).not.toBeDisabled();
    fireEvent.click(done);
    expect(h.onToggle).toHaveBeenCalledWith('s2', false);
  });

  it('marks an overdue date in words, not only in colour', () => {
    renderPanel();
    const due = screen.getByTitle('Overdue');
    expect(due.className).toContain('task-late');
    expect(due.textContent).toContain('(overdue)');
  });

  it('offers no owner tools without can_manage', () => {
    renderPanel({ canManage: false });
    expect(screen.queryByRole('button', { name: /add subtask/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /delete pull the ledger/i })).toBeNull();
  });

  it('adds a subtask through the callback, limited to the task assignees', () => {
    const { h } = renderPanel({ canManage: true });
    fireEvent.click(screen.getByRole('button', { name: /add subtask/i }));
    fireEvent.change(screen.getByLabelText('New subtask title'),
      { target: { value: 'Sign the cover note' } });
    const who = screen.getByLabelText('New subtask assignee');
    expect([...who.options].map((o) => o.textContent)).toEqual(['Unassigned', 'Employee Person']);
    fireEvent.change(who, { target: { value: 'u1' } });
    fireEvent.change(screen.getByLabelText('New subtask due date'),
      { target: { value: '2026-10-15' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add subtask' }));
    expect(h.onCreate).toHaveBeenCalledWith(
      { title: 'Sign the cover note', assignee: 'u1', due_date: '2026-10-15' });
  });

  it('confirms a delete inline, never through window.confirm', () => {
    const { h } = renderPanel({ canManage: true });
    const confirmSpy = vi.spyOn(window, 'confirm');
    fireEvent.click(screen.getByRole('button', { name: 'Delete Pull the ledger' }));
    expect(h.onDelete).not.toHaveBeenCalled();
    const box = screen.getByRole('group', { name: 'Delete Pull the ledger?' });
    fireEvent.click(within(box).getByRole('button', { name: 'Delete subtask' }));
    expect(h.onDelete).toHaveBeenCalledWith('s1');
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('reorders by sending the whole list in its new order', () => {
    const { h } = renderPanel({ canManage: true });
    expect(screen.getByRole('button', { name: 'Move Pull the ledger up' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Move Pull the ledger down' }));
    expect(h.onReorder).toHaveBeenCalledWith(['s2', 's1', 's3']);
    expect(screen.getByRole('button', { name: 'Move File the return down' })).toBeDisabled();
  });

  it('edits a row inline and sends only the subtask fields', () => {
    const { h } = renderPanel({ canManage: true });
    fireEvent.click(screen.getByRole('button', { name: 'Edit Reconcile' }));
    const title = screen.getByLabelText('Edit Reconcile title');
    expect(title).toHaveValue('Reconcile');
    fireEvent.change(title, { target: { value: 'Reconcile the ledger' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save subtask' }));
    expect(h.onUpdate).toHaveBeenCalledWith('s2',
      { title: 'Reconcile the ledger', assignee: 'u1', due_date: null });
  });

  it('opens a row to its own comments and evidence, and posts against it', () => {
    const { h } = renderPanel({ canComment: true });
    fireEvent.click(screen.getByRole('button', { name: /reconcile/i, expanded: false }));
    // Only the comment filed on THIS subtask, not the task-level one.
    expect(screen.getByText('Figures reconciled against May.')).toBeInTheDocument();
    expect(screen.queryByText('A task-level comment.')).toBeNull();
    expect(screen.getByRole('button', { name: 'reconciliation.xlsx' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Add a comment…'),
      { target: { value: 'Looks right to me.' } });
    fireEvent.click(screen.getByRole('button', { name: /post/i }));
    expect(h.onComment).toHaveBeenCalledWith('s2', 'Looks right to me.', { mentionIds: [] });
  });

  it('uploads evidence against the subtask it was opened from', () => {
    const { h } = renderPanel({ canUpload: true });
    fireEvent.click(screen.getByRole('button', { name: /pull the ledger/i, expanded: false }));
    const input = screen.getByLabelText('Upload evidence for Pull the ledger');
    const file = new File(['x'], 'ledger.csv', { type: 'text/csv' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(h.onUpload).toHaveBeenCalledWith('s1', [file]);
  });

  it('opens and lights the row a deep link names', () => {
    renderPanel({}, { path: '/tasks/t1#subtask-s3' });
    const li = document.getElementById('subtask-s3');
    expect(li.className).toContain('is-highlight');
    expect(within(li).getByRole('button', { name: /file the return/i }))
      .toHaveAttribute('aria-expanded', 'true');
    expect(document.getElementById('subtask-s1').className).not.toContain('is-highlight');
  });
});
