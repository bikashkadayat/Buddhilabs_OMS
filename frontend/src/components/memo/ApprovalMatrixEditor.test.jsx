import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const EMPLOYEES = [
  { id: 'e1', full_name: 'Asha Rai', designation: 'Senior Officer', department: 'Finance', role: 'maker', role_display: 'Employee' },
  { id: 'e2', full_name: 'Bikash Thapa', designation: 'Director', department: 'Finance', role: 'approver', role_display: 'HR' },
  { id: 'e3', full_name: 'Chandra Shah', designation: 'Manager', department: 'Admin', role: 'checker', role_display: 'Department Head' },
];

vi.mock('../../services/memoService', () => ({
  memoService: { searchEmployees: vi.fn(() => Promise.resolve(EMPLOYEES)) },
}));

import { memoService } from '../../services/memoService';
import ApprovalMatrixEditor from './ApprovalMatrixEditor';

/** Host that owns the rows, so reorder/remove are exercised through real state. */
const Host = ({ initial = [] }) => {
  const [rows, setRows] = useState(initial);
  return <ApprovalMatrixEditor rows={rows} onChange={setRows} />;
};

const renderEditor = (props = {}) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Host {...props} /></QueryClientProvider>);
};

const search = (term = 'a') =>
  fireEvent.change(screen.getByLabelText('Search employee'), { target: { value: term } });

const add = async (name) => {
  search('ah');
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(name) }));
};

/**
 * Employee names in row order. Read by column HEADER rather than by a fixed cell
 * index, so adding a column (the drag grip did exactly this) does not silently
 * shift every assertion onto the wrong field.
 */
const columnIndex = (label) => screen.getAllByRole('columnheader')
  .findIndex((header) => header.textContent.trim() === label);

const cellsUnder = (label) => {
  const index = columnIndex(label);
  return screen.getAllByRole('row').slice(1)
    .map((row) => row.querySelectorAll('td')[index]?.textContent)
    .filter((value) => value !== undefined);
};

const rowNames = () => cellsUnder('Employee').filter(Boolean);

describe('ApprovalMatrixEditor', () => {
  beforeEach(() => vi.clearAllMocks());

  it('starts empty and explains what is required', () => {
    renderEditor();
    expect(screen.getByText(/No approvers added yet/)).toBeInTheDocument();
    expect(screen.getByText(/final step must be an Approver/i)).toBeInTheDocument();
  });

  it('requires two characters before searching (roster-enumeration guard)', async () => {
    renderEditor();
    search('a');
    expect(await screen.findByText(/at least 2 characters/i)).toBeInTheDocument();
    expect(memoService.searchEmployees).not.toHaveBeenCalled();
  });

  it('adds any employee regardless of their role, with designation and department', async () => {
    renderEditor();
    await add('Asha Rai');
    // A plain employee (role: maker) is a legitimate matrix member — Phase 4
    // places no role restriction on who can hold which role type.
    expect(rowNames()).toEqual(['Asha Rai']);
    expect(screen.getByText('Senior Officer')).toBeInTheDocument();
    expect(screen.getByText('Finance')).toBeInTheDocument();
  });

  it('numbers rows by position and renumbers them after a reorder', async () => {
    renderEditor();
    await add('Asha Rai');
    await add('Bikash Thapa');
    expect(rowNames()).toEqual(['Asha Rai', 'Bikash Thapa']);

    fireEvent.click(screen.getByLabelText('Move Bikash Thapa up'));
    expect(rowNames()).toEqual(['Bikash Thapa', 'Asha Rai']);

    // Sequence is position, so the numbers follow the move with no renumbering.
    expect(cellsUnder('S.N.')).toEqual(['1', '2']);
  });

  it('disables the move buttons at the ends of the chain', async () => {
    renderEditor();
    await add('Asha Rai');
    await add('Bikash Thapa');
    expect(screen.getByLabelText('Move Asha Rai up')).toBeDisabled();
    expect(screen.getByLabelText('Move Bikash Thapa down')).toBeDisabled();
  });

  it('removes an employee from the chain', async () => {
    renderEditor();
    await add('Asha Rai');
    await add('Bikash Thapa');
    fireEvent.click(screen.getByLabelText('Remove Asha Rai'));
    expect(rowNames()).toEqual(['Bikash Thapa']);
  });

  it('changes a row role type', async () => {
    renderEditor();
    await add('Asha Rai');
    const select = screen.getByLabelText('Role type for Asha Rai');
    expect(select).toHaveValue('reviewer');
    fireEvent.change(select, { target: { value: 'supporter' } });
    expect(select).toHaveValue('supporter');
  });

  it('greys out an employee already in the chain instead of hiding them', async () => {
    renderEditor();
    await add('Asha Rai');
    search('ah');
    const option = await screen.findByRole('option', { name: /Asha Rai/ });
    // Hiding them would leave a user searching for someone they already added
    // with an unexplained empty result.
    expect(option).toBeDisabled();
    expect(screen.getByText('Already added')).toBeInTheDocument();
  });

  it('flags a chain that cannot reach approval', async () => {
    renderEditor();
    await add('Asha Rai');
    await add('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Asha Rai'), { target: { value: 'approver' } });
    // The chain now ends on a Reviewer, so it could never become Approved —
    // that is the more actionable problem to surface first.
    await waitFor(() => expect(
      screen.getByText(/final step must be an Approver/i),
    ).toBeInTheDocument());
  });

  it('flags an approver sitting before the end of the chain', async () => {
    renderEditor();
    await add('Asha Rai');
    await add('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Asha Rai'), { target: { value: 'approver' } });
    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'), { target: { value: 'approver' } });
    // Ends on an Approver, but an earlier step would approve with work still
    // outstanding behind it.
    await waitFor(() => expect(
      screen.getByText(/Approver can only be the last step/i),
    ).toBeInTheDocument());
  });

  it('clears the guidance once the chain ends in an approver', async () => {
    renderEditor();
    await add('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'), { target: { value: 'approver' } });
    await waitFor(() => expect(
      screen.queryByText(/final step must be an Approver/i),
    ).not.toBeInTheDocument());
  });

  it('renders read-only without editing controls', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ApprovalMatrixEditor
          readOnly
          onChange={() => {}}
          rows={[{ assignee_id: 'e1', full_name: 'Asha Rai', designation: 'Senior Officer', department: 'Finance', role_type: 'approver' }]}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByText('Asha Rai')).toBeInTheDocument();
    expect(screen.queryByLabelText('Search employee')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Remove Asha Rai')).not.toBeInTheDocument();
    expect(screen.getByText('Approver')).toBeInTheDocument();
  });
});
