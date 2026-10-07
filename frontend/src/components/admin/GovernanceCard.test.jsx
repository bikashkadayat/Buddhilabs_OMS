/**
 * The governance panel on the admin dashboard
 * (Phase DASHBOARD-GOVERNANCE-CLEANUP).
 *
 * This is where the detail Home dropped now lives. Two things worth pinning:
 * it NAMES the departments, and it renders when everything is fine too - a
 * panel that appears only on failure leaves a reader unable to tell "all good"
 * from "the check never ran".
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/leaveService', () => ({
  leaveService: { departmentGovernance: vi.fn() },
}));

import { leaveService } from '../../services/leaveService';
import GovernanceCard from './GovernanceCard';

const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><GovernanceCard /></MemoryRouter>
  </QueryClientProvider>,
);

beforeEach(() => vi.clearAllMocks());

describe('the admin governance panel', () => {
  it('names the departments with nobody answerable for them', async () => {
    leaveService.departmentGovernance.mockResolvedValue({
      total: 4, active: 4, missing_head: 2,
      missing_head_names: ['ICT Department', 'Operational Department'],
      status: 'critical',
    });
    mount();
    expect(await screen.findByText('ICT Department')).toBeInTheDocument();
    expect(screen.getByText('Operational Department')).toBeInTheDocument();
    expect(screen.getByText(/2 of 4 active departments/)).toBeInTheDocument();
  });

  it('confirms the healthy case rather than disappearing', async () => {
    leaveService.departmentGovernance.mockResolvedValue({
      total: 4, active: 4, missing_head: 0, missing_head_names: [], status: 'ok',
    });
    mount();
    expect(await screen.findByText(/All 4 active departments have a Department Head/))
      .toBeInTheDocument();
  });

  it('links to the three places the gap is acted on', async () => {
    leaveService.departmentGovernance.mockResolvedValue({
      total: 1, active: 1, missing_head: 0, missing_head_names: [], status: 'ok',
    });
    mount();
    await screen.findByText(/All 1 active departments/);
    expect(screen.getByRole('link', { name: 'Departments' }))
      .toHaveAttribute('href', '/admin/leaves/departments');
    expect(screen.getByRole('link', { name: 'System Health' }))
      .toHaveAttribute('href', '/monitoring');
    expect(screen.getByRole('link', { name: 'Ownership report' }))
      .toHaveAttribute('href', '/reports/build/department_ownership');
  });

  it('renders nothing when the check itself fails', async () => {
    leaveService.departmentGovernance.mockRejectedValue(new Error('nope'));
    const { container } = mount();
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});
