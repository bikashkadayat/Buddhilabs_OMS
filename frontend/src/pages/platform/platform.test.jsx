import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import RequirePlatform from '../../components/common/RequirePlatform';
import { money, bytes, statusLabel, statusTone } from '../../services/platformService';

vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(), patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

const authState = {
  isAuthenticated: true, isPlatformStaff: true, loading: false,
  user: { email: 'ops@platform.test' }, role: null, logout: vi.fn(),
};
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => authState }));

import api from '../../services/api';
import PlatformDashboard from './Dashboard';
import PlatformOrganizations from './Organizations';

const DASHBOARD = {
  organizations_total: 3, organizations_active: 1, organizations_trial: 1,
  organizations_grace: 1, organizations_suspended: 0,
  organizations_cancelled: 0, organizations_provisioning: 0,
  organizations_by_status: { active: 1, trial: 1, grace: 1 },
  organizations_by_subscription_status: { active: 1, trial: 1, grace: 1 },
  users_total: 42, storage_bytes: 5242880,
  subscriptions_active: 2, subscriptions_trial: 1,
  revenue_forecast: {
    currency: 'NPR', monthly_minor: 199800, annual_minor: 2397600,
    counted: 2, unpriced: 0,
  },
  payments_pending_verification: 1,
  plan_usage: { monthly: 2, annual: 1 },
  system_health: {
    status: 'ok', database: 'up', cache: 'up',
    payments_pending_verification: 1, organizations_stuck_provisioning: [],
    subscription_mirror_drift: {}, problems: [],
  },
};

const wrap = (ui, path = '/platform') => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/platform" element={ui} />
      <Route path="/platform/organizations" element={ui} />
      <Route path="/" element={<div>tenant workspace</div>} />
      <Route path="/login" element={<div>sign in</div>} />
    </Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  authState.isAuthenticated = true;
  authState.isPlatformStaff = true;
  authState.loading = false;
});

describe('formatting helpers', () => {
  it('renders minor units without ever doing float arithmetic on them', () => {
    // 199800 paisa is NPR 1,998 — not 199800, and not 1998.00000000001.
    expect(money(199800)).toContain('1,998');
    expect(money(null)).toBe('—');
  });

  it('shows storage in the largest readable unit', () => {
    expect(bytes(0)).toBe('0 B');
    expect(bytes(5242880)).toBe('5.0 MB');
  });

  it('labels every organization status, including the ones added this phase', () => {
    expect(statusLabel('grace')).toBe('Grace period');
    expect(statusLabel('provisioning')).toBe('Provisioning');
    // Grace is a warning, not a success: the tenant is working normally and
    // somebody needs to phone them before it ends.
    expect(statusTone('grace')).toBe('is-warn');
    expect(statusTone('active')).toBe('is-good');
  });
});

describe('RequirePlatform', () => {
  it('sends a tenant user to their own workspace, not to /unauthorized', () => {
    // They are not forbidden from the product; they are in the wrong part of it.
    authState.isPlatformStaff = false;
    wrap(<RequirePlatform><div>console</div></RequirePlatform>);
    expect(screen.getByText('tenant workspace')).toBeInTheDocument();
  });

  it('sends an anonymous visitor to sign in', () => {
    authState.isAuthenticated = false;
    wrap(<RequirePlatform><div>console</div></RequirePlatform>);
    expect(screen.getByText('sign in')).toBeInTheDocument();
  });

  it('lets a platform operator through', () => {
    wrap(<RequirePlatform><div>console</div></RequirePlatform>);
    expect(screen.getByText('console')).toBeInTheDocument();
  });
});

describe('platform dashboard', () => {
  it('shows every widget Part 1 names', async () => {
    api.get.mockResolvedValue({ data: DASHBOARD });
    wrap(<PlatformDashboard />);

    // The metric, not the "Organizations" tab of the recent lists below it.
    await waitFor(() => expect(
      screen.getByText('Organizations', { selector: '.pf-metric-label' })).toBeInTheDocument());
    expect(screen.getByText('42')).toBeInTheDocument();          // users
    expect(screen.getByText('5.0 MB')).toBeInTheDocument();      // storage
    expect(screen.getByText('In grace')).toBeInTheDocument();
    expect(screen.getByText('Collected this month')).toBeInTheDocument();
    expect(screen.getByText(/1,998/)).toBeInTheDocument();       // MRR
  });

  it('surfaces a degraded platform rather than burying it', async () => {
    api.get.mockResolvedValue({
      data: {
        ...DASHBOARD,
        system_health: {
          ...DASHBOARD.system_health, status: 'degraded',
          problems: ['2 organization(s) stuck provisioning'],
        },
      },
    });
    wrap(<PlatformDashboard />);
    await waitFor(() => expect(
      screen.getByText(/stuck provisioning/)).toBeInTheDocument());
  });

  it('says so when the dashboard cannot be loaded, and does not render zeros',
    async () => {
      // A dashboard that renders 0 organizations on a failed request is worse
      // than one that renders nothing: the figure looks like an answer.
      api.get.mockRejectedValue(new Error('boom'));
      wrap(<PlatformDashboard />);
      await waitFor(() => expect(
        screen.getByText('Dashboard unavailable')).toBeInTheDocument());
      expect(screen.queryByText('Users')).not.toBeInTheDocument();
    });
});

describe('organization list', () => {
  const ROWS = [
    {
      id: '1', name: 'Nepal Internet Foundation', slug: 'nif', status: 'active',
      subscription_status: 'active', plan_code: 'annual',
      subscription_expiry: '2027-01-01', seat_count: 30,
      storage_bytes: 1048576, is_admitted: true, email: 'a@nif.test',
    },
    {
      id: '2', name: 'ABC School', slug: 'abcschool', status: 'suspended',
      subscription_status: 'active', plan_code: 'monthly',
      subscription_expiry: '2026-12-01', seat_count: 12,
      storage_bytes: 0, is_admitted: false, email: 'a@abc.test',
    },
  ];

  it('shows the workspace status and the billing status separately', async () => {
    // They differ exactly when an operator has intervened — a paid-up tenant
    // suspended for abuse — and that is the case this screen is for.
    api.get.mockResolvedValue({ data: ROWS });
    wrap(<PlatformOrganizations />, '/platform/organizations');

    await waitFor(() => expect(screen.getByText('ABC School')).toBeInTheDocument());
    // Two columns, and they are not redundant: one says whether anybody can
    // get in, the other whether they have paid.
    expect(screen.getByRole('columnheader', { name: 'Workspace' }))
      .toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Billing' }))
      .toBeInTheDocument();
    // ABC School is fully paid up (billing: Active) and locked out anyway
    // (workspace: Suspended). "Suspended" also appears in the filter's option
    // list, so this reads the row rather than the document.
    const row = screen.getByText('ABC School').closest('tr');
    expect(row).toHaveTextContent('Suspended');
    expect(row).toHaveTextContent('Active');
  });

  it('offers a way out of a filter that matched nothing', async () => {
    api.get.mockResolvedValue({ data: [] });
    wrap(<PlatformOrganizations />, '/platform/organizations?status=suspended');
    await waitFor(() => expect(
      screen.getByText('No organization matches')).toBeInTheDocument());
    expect(screen.getByText('Clear filters')).toBeInTheDocument();
  });

  it('offers the create path when there is nothing at all', async () => {
    api.get.mockResolvedValue({ data: [] });
    wrap(<PlatformOrganizations />, '/platform/organizations');
    await waitFor(() => expect(
      screen.getByText('No organizations yet')).toBeInTheDocument());
  });

  it('opens the one-step provisioning dialog', async () => {
    api.get.mockResolvedValue({ data: ROWS });
    wrap(<PlatformOrganizations />, '/platform/organizations');
    await waitFor(() => expect(screen.getByText('ABC School')).toBeInTheDocument());

    fireEvent.click(screen.getAllByText('New organization')[0]);
    await waitFor(() => expect(
      screen.getByText(/built, set up and opened/)).toBeInTheDocument());
  });
});
