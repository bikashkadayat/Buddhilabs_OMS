import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import ApprovalMatrixTable from './ApprovalMatrixTable';

const STEPS = [
  {
    id: 's1', sequence: 1, role_type: 'reviewer', status: 'completed',
    designation: 'Senior Officer', department_label: 'Finance',
    acted_at: '2026-08-12T09:30:00Z', remarks: 'Figures reconcile.',
    assignee: { id: 'e1', full_name: 'Asha Rai' },
  },
  {
    id: 's2', sequence: 2, role_type: 'supporter', status: 'active',
    designation: 'Manager', department_label: 'Finance',
    acted_at: null, remarks: '',
    assignee: { id: 'e2', full_name: 'Bikash Thapa' },
  },
  {
    id: 's3', sequence: 3, role_type: 'approver', status: 'pending',
    designation: 'Director', department_label: 'Finance',
    acted_at: null, remarks: '',
    assignee: { id: 'e3', full_name: 'Chandra Shah' },
  },
];

const renderTable = (props = {}) => render(
  <MemoryRouter><ApprovalMatrixTable steps={STEPS} {...props} /></MemoryRouter>,
);

const headers = () => screen.getAllByRole('columnheader').map((h) => h.textContent.trim());

describe('ApprovalMatrixTable', () => {
  it('renders the Phase 12 column set', () => {
    renderTable();
    const labels = headers();
    ['S.N.', 'Employee', 'Designation', 'Department', 'Role',
      'Current Status', 'Action Date', 'Remarks'].forEach((label) => {
      expect(labels).toContain(label);
    });
  });

  it('shows sequence, designation, department and remarks per row', () => {
    renderTable();
    expect(screen.getByText('Asha Rai')).toBeInTheDocument();
    expect(screen.getByText('Senior Officer')).toBeInTheDocument();
    expect(screen.getByText('Figures reconcile.')).toBeInTheDocument();
    expect(screen.getAllByText('Finance')).toHaveLength(3);
  });

  it('highlights the step awaiting action and labels it Current', () => {
    const { container } = renderTable();
    const rows = container.querySelectorAll('tbody tr');
    expect(rows[0].className).not.toContain('memo-row-active');
    expect(rows[1].className).toContain('memo-row-active');
    expect(rows[2].className).not.toContain('memo-row-active');
    expect(screen.getByText('Current')).toBeInTheDocument();
  });

  it('marks a completed step with a tick and the active one with a clock', () => {
    const { container } = renderTable();
    // The mark carries the outcome as a shape, so the column reads in greyscale.
    expect(container.querySelectorAll('.memo-step-mark.is-done')).toHaveLength(1);
    expect(container.querySelectorAll('.memo-step-mark.is-active')).toHaveLength(1);
    expect(container.querySelectorAll('.memo-step-mark.is-pending')).toHaveLength(1);
  });

  it('marks a rejected step distinctly', () => {
    render(
      <MemoryRouter>
        <ApprovalMatrixTable steps={[{ ...STEPS[0], status: 'rejected' }]} />
      </MemoryRouter>,
    );
    expect(document.querySelectorAll('.memo-step-mark.is-rejected')).toHaveLength(1);
  });

  it('tags the viewer’s own row', () => {
    renderTable({ currentUserId: 'e2' });
    expect(screen.getByText('You')).toBeInTheDocument();
  });

  it('reports how far through the chain the memo is', () => {
    renderTable();
    expect(screen.getByText('1 of 3 complete')).toBeInTheDocument();
  });

  it('explains itself when there is no workflow', () => {
    render(<MemoryRouter><ApprovalMatrixTable steps={[]} /></MemoryRouter>);
    expect(screen.getByText(/No approval workflow has been set/)).toBeInTheDocument();
  });
});
