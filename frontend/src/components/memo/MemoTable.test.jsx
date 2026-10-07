import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import MemoTable from './MemoTable';

const row = (overrides = {}) => ({
  id: 'm1', memo_number: 'NIFN-GEN-2026-0001', title: 'Server procurement',
  subject: 'Procurement approval', memo_type: 'general',
  status: 'under_review', created_at: '2026-08-01T10:00:00Z',
  created_by: { id: 'u1', full_name: 'Asha Rai' },
  department_label: 'Finance',
  pending_with: { id: 'u2', name: 'Bikash Thapa', role_label: 'Supporter' },
  ageing: {
    pending_since: '2026-08-10T10:00:00Z', pending_days: 2,
    sla_days: 4, due_days: 2, state: 'on_track',
  },
  ...overrides,
});

const renderTable = (items, columns) => render(
  <MemoryRouter><MemoTable items={items} columns={columns} /></MemoryRouter>,
);

const INBOX_COLUMNS = ['department', 'author', 'status', 'pendingSince', 'dueDays',
  'action'];

describe('MemoTable', () => {
  it('renders the inbox column set', () => {
    renderTable([row()], INBOX_COLUMNS);
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent.trim());
    ['Memo No', 'Subject', 'Department', 'Created By', 'Status',
      'Pending Since', 'Due Days', 'Action'].forEach((label) => {
      expect(headers).toContain(label);
    });
  });

  it('renders only the requested columns', () => {
    renderTable([row()], ['type', 'created']);
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent.trim());
    expect(headers).toContain('Type');
    expect(headers).not.toContain('Due Days');
    expect(headers).not.toContain('Department');
  });

  it('shows who a memo is currently sitting with, and in what capacity', () => {
    renderTable([row()], ['pending']);
    expect(screen.getByText('Bikash Thapa')).toBeInTheDocument();
    expect(screen.getByText('Supporter')).toBeInTheDocument();
  });

  it('states the due days in words as well as colour', () => {
    renderTable([row()], INBOX_COLUMNS);
    // The number is in the cell text, so urgency never depends on colour alone.
    expect(screen.getByText('2 days left')).toBeInTheDocument();
    expect(screen.getByText('2 days ago')).toBeInTheDocument();
  });

  it.each([
    ['on_track', 2, 'memo-sla-ok', '2 days left'],
    ['due_soon', 0, 'memo-sla-warn', 'Due today'],
    ['overdue', -3, 'memo-sla-bad', '3 days overdue'],
  ])('colour-codes a %s row', (state, dueDays, className, text) => {
    const { container } = renderTable(
      [row({ ageing: { ...row().ageing, due_days: dueDays, state } })],
      INBOX_COLUMNS,
    );
    expect(container.querySelector('tbody tr').className).toContain(className);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('does not colour-code lists that have no due-days column', () => {
    const { container } = renderTable([row()], ['type', 'status', 'created']);
    const rowClass = container.querySelector('tbody tr').className;
    expect(rowClass).not.toContain('memo-sla-');
  });

  it('copes with a memo that has no ageing data', () => {
    renderTable([row({ ageing: null, pending_with: null })], INBOX_COLUMNS);
    // Two dashes: pending-since and due-days. No crash, no "NaN days".
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  /**
   * Phase 26: the archive must answer "who approved this" from the list itself.
   * `approved_by` is derived server-side from the completed Approver step - the
   * memo table has no way to work it out, so a missing value must read as a dash
   * rather than as blank.
   */
  it('renders the approver in the archive column set', () => {
    renderTable(
      [row({ status: 'archived', approved_by: 'Rajesh HR',
        approved_at: '2026-08-12T12:00:00Z', archived_at: '2026-08-12T12:05:00Z' })],
      ['type', 'author', 'department', 'approvedBy', 'approved', 'archived'],
    );
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent.trim());
    expect(headers).toContain('Approved By');
    expect(screen.getByText('Rajesh HR')).toBeInTheDocument();
  });

  it('shows a dash when a listed memo has no approver yet', () => {
    renderTable([row({ approved_by: null })], ['approvedBy']);
    expect(screen.getByRole('cell', { name: '—' })).toBeInTheDocument();
  });
});
