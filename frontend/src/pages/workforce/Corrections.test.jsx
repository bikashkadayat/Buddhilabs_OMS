import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../hooks/useWorkforce', () => ({
  useCorrections: vi.fn(),
  useSubmitCorrection: vi.fn(),
  useApproveCorrection: vi.fn(),
  useRejectCorrection: vi.fn(),
  useCancelCorrection: vi.fn(),
  useRevertCorrection: vi.fn(),
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));

import {
  useApproveCorrection, useCancelCorrection, useCorrections,
  useRejectCorrection, useRevertCorrection, useSubmitCorrection,
} from '../../hooks/useWorkforce';
import { useAuth } from '../../hooks/useAuth';
import Corrections from './Corrections';

const row = (over = {}) => ({
  id: 'c1', employee: 'e1', employee_name: 'Ram Thapa', department: 'Engineering',
  attendance_date: '2026-08-01',
  previous_check_in: null, previous_check_out: null,
  requested_check_in: '2026-08-01T04:15:00Z', requested_check_out: '2026-08-01T12:15:00Z',
  requested_status: '', reason: 'Device missed my punch', status: 'pending',
  has_attachment: false, ...over,
});

const mutation = () => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false });

const setup = (role, rows = [row()], user = { id: 'e1' }) => {
  useAuth.mockReturnValue({ role, user });
  useCorrections.mockReturnValue({ data: { results: rows, count: rows.length } });
  useSubmitCorrection.mockReturnValue(mutation());
  useApproveCorrection.mockReturnValue(mutation());
  useRejectCorrection.mockReturnValue(mutation());
  useCancelCorrection.mockReturnValue(mutation());
  useRevertCorrection.mockReturnValue(mutation());
  return render(<MemoryRouter><Corrections /></MemoryRouter>);
};

describe('Corrections', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a loading skeleton', () => {
    useAuth.mockReturnValue({ role: 'maker', user: { id: 'e1' } });
    useCorrections.mockReturnValue({ isLoading: true });
    [useSubmitCorrection, useApproveCorrection, useRejectCorrection,
      useCancelCorrection, useRevertCorrection].forEach((h) => h.mockReturnValue(mutation()));
    render(<MemoryRouter><Corrections /></MemoryRouter>);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error state with retry', () => {
    const refetch = vi.fn();
    useAuth.mockReturnValue({ role: 'maker', user: { id: 'e1' } });
    useCorrections.mockReturnValue({ isError: true, error: new Error('nope'), refetch });
    [useSubmitCorrection, useApproveCorrection, useRejectCorrection,
      useCancelCorrection, useRevertCorrection].forEach((h) => h.mockReturnValue(mutation()));
    render(<MemoryRouter><Corrections /></MemoryRouter>);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('lists requests with their stage', () => {
    setup('maker');
    expect(screen.getByText('Ram Thapa')).toBeInTheDocument();
    expect(screen.getByText('Pending — Dept Head')).toBeInTheDocument();
  });

  // --- permissions --------------------------------------------------------
  it('lets an employee cancel only their own pending request', () => {
    setup('maker');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
  });

  it('does not let an employee cancel someone else’s request', () => {
    setup('maker', [row({ employee: 'other' })]);
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('lets a department head approve the first stage', () => {
    setup('checker', [row()], { id: 'head' });
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
  });

  it('does not let a department head act on the HR stage', () => {
    setup('checker', [row({ status: 'manager_approved' })], { id: 'head' });
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
  });

  it('lets HR finalise the HR stage', () => {
    setup('approver', [row({ status: 'manager_approved' })], { id: 'hr' });
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
  });

  it('offers revert to HR on an applied correction', () => {
    setup('approver', [row({ status: 'hr_approved' })], { id: 'hr' });
    expect(screen.getByRole('button', { name: /revert/i })).toBeInTheDocument();
  });

  // Separate test, not a second render in the one above: two renders share the
  // same document and the first component's buttons would still be found.
  it('does not offer revert to a department head', () => {
    setup('checker', [row({ status: 'hr_approved' })], { id: 'head' });
    expect(screen.queryByRole('button', { name: /revert/i })).not.toBeInTheDocument();
  });

  it('hides the submit button from admins', () => {
    setup('admin', [row()], { id: 'a1' });
    expect(screen.queryByRole('button', { name: /new request/i })).not.toBeInTheDocument();
  });

  it('offers the submit button to an employee', () => {
    setup('maker');
    expect(screen.getByRole('button', { name: /new request/i })).toBeInTheDocument();
  });

  // --- actions ------------------------------------------------------------
  it('calls approve with the request id', async () => {
    const approve = mutation();
    useAuth.mockReturnValue({ role: 'approver', user: { id: 'hr' } });
    useCorrections.mockReturnValue({ data: { results: [row()], count: 1 } });
    useApproveCorrection.mockReturnValue(approve);
    [useSubmitCorrection, useRejectCorrection, useCancelCorrection,
      useRevertCorrection].forEach((h) => h.mockReturnValue(mutation()));

    render(<MemoryRouter><Corrections /></MemoryRouter>);
    await userEvent.click(screen.getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(approve.mutateAsync).toHaveBeenCalledWith({ id: 'c1', remarks: '' }));
  });

  it('asks for a reason before rejecting', async () => {
    const reject = mutation();
    vi.spyOn(window, 'prompt').mockReturnValue('Not supported by the log');
    useAuth.mockReturnValue({ role: 'approver', user: { id: 'hr' } });
    useCorrections.mockReturnValue({ data: { results: [row()], count: 1 } });
    useRejectCorrection.mockReturnValue(reject);
    [useSubmitCorrection, useApproveCorrection, useCancelCorrection,
      useRevertCorrection].forEach((h) => h.mockReturnValue(mutation()));

    render(<MemoryRouter><Corrections /></MemoryRouter>);
    await userEvent.click(screen.getByRole('button', { name: /reject/i }));
    await waitFor(() => expect(reject.mutateAsync).toHaveBeenCalledWith({
      id: 'c1', reason: 'Not supported by the log',
    }));
  });

  it('opens the submit modal', async () => {
    setup('maker');
    await userEvent.click(screen.getByRole('button', { name: /new request/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
