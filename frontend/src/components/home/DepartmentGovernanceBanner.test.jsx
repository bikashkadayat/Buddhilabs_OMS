/**
 * Governance on Home, after DASHBOARD-GOVERNANCE-CLEANUP.
 *
 * The claim that changed: this is NO LONGER AN ALERT. A missing department head
 * stopped blocking anybody's work in TASK-SIMPLIFICATION, so on the page that
 * exists to show what blocks the reader it is a pointer, not an emergency.
 * Rendering it as a critical error beside their actual work is what teaches
 * people to scroll past critical errors.
 *
 * What is still pinned: it appears only for the people who can act on it, it
 * carries the COUNT and a way in, and it says nothing when there is nothing to
 * say.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../services/leaveService', () => ({
  leaveService: { departmentGovernance: vi.fn() },
}));

import { leaveService } from '../../services/leaveService';
import { useAuth } from '../../hooks/useAuth';
import DepartmentGovernanceBanner from './DepartmentGovernanceBanner';

const GAP = {
  total: 4, active: 4, missing_head: 4,
  missing_head_names: ['Administrative Department', 'Human Resource Department',
    'ICT Department', 'Operational Department'],
  status: 'critical',
};
const CLEAN = { total: 4, active: 4, missing_head: 0, missing_head_names: [], status: 'ok' };

const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><DepartmentGovernanceBanner /></MemoryRouter>
  </QueryClientProvider>,
);

beforeEach(() => {
  vi.clearAllMocks();
  useAuth.mockReturnValue({ role: 'admin' });
  leaveService.departmentGovernance.mockResolvedValue(GAP);
});

describe('governance on the home dashboard', () => {
  it('is a count and a link, not a critical banner', async () => {
    mount();
    expect(await screen.findByText('Governance Issues (4)')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /view details/i })).toBeInTheDocument();
    // Not an alert: it does not block the person reading it.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('does not list the departments on Home any more', async () => {
    // The detail moved to the admin dashboard, System Health and the ownership
    // report. Home carries the number and the way in.
    mount();
    await screen.findByText('Governance Issues (4)');
    for (const name of GAP.missing_head_names) {
      expect(screen.queryByText(new RegExp(name))).toBeNull();
    }
    expect(screen.queryByText(/refused/i)).toBeNull();
  });

  it('points an Admin at the page where it is fixed', async () => {
    mount();
    expect(await screen.findByRole('link', { name: /view details/i }))
      .toHaveAttribute('href', '/admin/leaves/departments');
  });

  it('points HR at the health board, which is what they can reach', async () => {
    useAuth.mockReturnValue({ role: 'approver' });
    mount();
    expect(await screen.findByRole('link', { name: /view details/i }))
      .toHaveAttribute('href', '/monitoring');
  });

  it('says nothing when every department has a head', async () => {
    leaveService.departmentGovernance.mockResolvedValue(CLEAN);
    mount();
    await waitFor(() => expect(leaveService.departmentGovernance).toHaveBeenCalled());
    expect(screen.queryByText(/Governance Issues/)).toBeNull();
  });

  it.each(['maker', 'checker', 'bod'])(
    'renders nothing for %s, who cannot act on it', async (role) => {
      useAuth.mockReturnValue({ role });
      const { container } = mount();
      await waitFor(() => expect(container.firstChild).toBeNull());
      expect(leaveService.departmentGovernance).not.toHaveBeenCalled();
    });
});
