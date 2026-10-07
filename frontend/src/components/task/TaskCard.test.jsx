/**
 * Phase T2.8 — board cards.
 *
 * A card must carry exactly what the specification names, and — just as
 * importantly — must not carry a row of zeroes. The board itself is asserted to
 * be read-only: there is no drag handle, because a drag cannot ask a reviewer
 * why they are sending work back.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import TaskCard from './TaskCard';

const card = (extra = {}) => ({
  id: 't1',
  task_number: 'NIFN-TSK-2083-0001',
  title: 'Prepare the quarterly return',
  status: 'in_progress',
  status_label: 'In Progress',
  priority: 'high',
  priority_label: 'High',
  due_date: '2026-09-30',
  is_overdue: false,
  progress_percent: 40,
  checklist_done: 2,
  checklist_total: 5,
  assignee_names: ['Bikash Kadayat'],
  comment_count: 3,
  attachment_count: 2,
  evidence_count: 1,
  ...extra,
});

const renderCard = (task) => render(
  <MemoryRouter><TaskCard task={task} /></MemoryRouter>,
);

describe('task card', () => {
  it('carries everything the specification names', () => {
    renderCard(card());
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/tasks/t1');
    expect(link.textContent).toContain('Prepare the quarterly return');
    expect(link.textContent).toContain('Bikash Kadayat');
    expect(link.textContent).toContain('High');
    expect(link.textContent).toContain('40%');
    expect(link.textContent).toContain('3');      // comments
    expect(link.textContent).toContain('2');      // attachments
    expect(within(link).getByLabelText('40% complete')).toBeInTheDocument();
  });

  it('uses the board card classes, not the form card ones', () => {
    /* `.task-card` belongs to the create / edit form's section cards. Sharing
       the name gave those cards this one's hover lift and padding. */
    renderCard(card());
    const link = screen.getByRole('link');
    expect(link.className).toBe('task-board-card');
    expect(link.querySelector('.task-board-card-title')).toHaveTextContent(
      'Prepare the quarterly return');
    expect(link.querySelector('.task-card')).toBeNull();
    expect(link.querySelector('.task-card-title')).toBeNull();
  });

  it('shows the subtask tally when the task has subtasks', () => {
    renderCard(card({ subtask_done: 1, subtask_total: 4 }));
    expect(screen.getByText(/1\/4 subtasks/)).toBeInTheDocument();
  });

  it('renders no count at all when a count is zero', () => {
    /* A row of zeroes is noise, and the absence already says it. */
    renderCard(card({ comment_count: 0, attachment_count: 0, evidence_count: 0 }));
    const link = screen.getByRole('link');
    expect(within(link).queryByTitle(/comments/)).toBeNull();
    expect(within(link).queryByTitle(/attachments/)).toBeNull();
    expect(within(link).queryByTitle(/evidence/)).toBeNull();
  });

  it('says Unassigned rather than showing an empty space', () => {
    renderCard(card({ assignee_names: [] }));
    expect(screen.getByText('Unassigned')).toBeInTheDocument();
  });

  it('summarises three or more assignees rather than listing them', () => {
    renderCard(card({ assignee_names: ['A Person', 'B Person', 'C Person'] }));
    expect(screen.getByText('A Person +2')).toBeInTheDocument();
  });

  it('names both assignees when there are exactly two', () => {
    renderCard(card({ assignee_names: ['A Person', 'B Person'] }));
    expect(screen.getByText('A Person, B Person')).toBeInTheDocument();
  });

  it('marks an overdue card in words, not only in colour', () => {
    renderCard(card({ is_overdue: true, due_date: '2026-08-01' }));
    const due = screen.getByTitle(/overdue/i);
    expect(due.className).toContain('task-late');
  });

  it('shows the checklist tally beside the percentage', () => {
    renderCard(card());
    expect(screen.getByText(/2\/5 checklist/)).toBeInTheDocument();
  });
});

/*
 * The board itself is no longer tested here.
 *
 * In Phase T2 it derived its own columns from a flat list of tasks, so a
 * component test was the right place for that logic. Phase T3 moved the
 * status-to-column mapping to the server (tasks/board.py) and the board now
 * renders the columns it is handed — so what is worth testing is the mapping
 * (tasks/tests/test_board.py) and the rendering of a served payload
 * (pages/task/TaskWorkspace.test.jsx). Duplicating either here would pin a
 * client-side copy of a rule that no longer exists.
 */
