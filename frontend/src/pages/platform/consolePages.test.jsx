import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

/**
 * The four console pages the brief names that were previously only reachable
 * as a figure on the dashboard or a panel on one organization's page:
 * Subscriptions, Plans, Tenant usage, Platform health.
 *
 * WHAT EACH TEST IS ACTUALLY GUARDING is called out in its own comment. The
 * ones worth stating up front: a usage table built the obvious way shows every
 * customer as empty under row-level security, and a health page that polls
 * writes to the database on a timer. Both are asserted here rather than
 * described in a comment nobody re-reads.
 */
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
import PlatformSubscriptions from './Subscriptions';
import PlatformPlans from './Plans';
import PlatformUsage from './Usage';
import PlatformHealth from './Health';

const ORGS = [
  {
    id: '1', name: 'Nepal Internet Foundation', slug: 'nif', status: 'active',
    status_display: 'Active', subscription_status: 'active', plan_code: 'annual',
    subscription_expiry: '2027-01-01', seat_count: 30, seats_included: 25,
    storage_bytes: 1048576, is_admitted: true, email: 'a@nif.test',
  },
  {
    id: '2', name: 'ABC School', slug: 'abcschool', status: 'suspended',
    status_display: 'Suspended', subscription_status: 'active',
    plan_code: 'monthly', subscription_expiry: '2026-10-10', seat_count: 12,
    seats_included: null, storage_bytes: 0, is_admitted: false,
    email: 'a@abc.test',
  },
];

const PLANS = [
  {
    id: 'p1', code: 'monthly', name: 'Monthly', interval_months: 1,
    trial_days: 14, grace_days: 7, billing_mode: 'prepaid',
    included_seats: 25, is_public: true, is_active: true,
    price: { amount_minor: 199800, currency: 'NPR' },
  },
  {
    id: 'p2', code: 'legacy', name: 'Legacy', interval_months: 12,
    trial_days: 0, grace_days: 0, billing_mode: 'prepaid',
    included_seats: null, is_public: false, is_active: false, price: null,
  },
];

const HEALTH = {
  status: 'ok', database: 'up', cache: 'up',
  payments_pending_verification: 0, organizations_stuck_provisioning: [],
  subscription_mirror_drift: {}, problems: [],
};

const wrap = (ui) => render(
  <MemoryRouter initialEntries={['/platform/x']}>
    <Routes>
      <Route path="/platform/x" element={ui} />
      <Route path="/platform/organizations/:slug" element={<div>org page</div>} />
    </Routes>
  </MemoryRouter>,
);

beforeEach(() => { vi.clearAllMocks(); });

describe('subscriptions page', () => {
  it('puts what expires soonest at the top', async () => {
    // The only question on this page has a deadline attached. Sorting by name
    // would bury the customer who loses access first.
    api.get.mockResolvedValue({ data: ORGS });
    wrap(<PlatformSubscriptions />);
    await waitFor(() => expect(screen.getByText('ABC School')).toBeInTheDocument());

    const names = screen.getAllByRole('row').slice(1)
      .map((row) => row.cells[0].textContent);
    expect(names).toEqual(['ABC School', 'Nepal Internet Foundation']);
  });

  it('separates what was paid for from whether anybody can get in', async () => {
    // ABC School is paid up and locked out. One column cannot say that.
    api.get.mockResolvedValue({ data: ORGS });
    wrap(<PlatformSubscriptions />);
    await waitFor(() => expect(screen.getByText('ABC School')).toBeInTheDocument());

    const row = screen.getByText('ABC School').closest('tr');
    expect(row).toHaveTextContent('Active');      // subscription
    expect(row).toHaveTextContent('Suspended');   // access
    expect(row).toHaveTextContent('No');          // can sign in
  });

  it('says so when the list cannot be loaded', async () => {
    api.get.mockRejectedValue(new Error('boom'));
    wrap(<PlatformSubscriptions />);
    await waitFor(() => expect(
      screen.getByText('Could not load subscriptions')).toBeInTheDocument());
  });
});

describe('plans page', () => {
  it('distinguishes a plan with no price from a free one', async () => {
    // `price: null` means no active PlanPrice row for today. Rendering that as
    // a blank or as 0 would read as free, and the plan picker refuses to sell
    // it — so the table has to say something different.
    api.get.mockResolvedValue({ data: PLANS });
    wrap(<PlatformPlans />);
    await waitFor(() => expect(screen.getByText('Legacy')).toBeInTheDocument());

    expect(screen.getByText('not priced')).toBeInTheDocument();
    expect(screen.getByText(/1,998/)).toBeInTheDocument();
  });

  it('names the billing interval instead of printing a number of months', async () => {
    api.get.mockResolvedValue({ data: PLANS });
    wrap(<PlatformPlans />);
    await waitFor(() => expect(screen.getByText('Legacy')).toBeInTheDocument());
    // "12" tells an operator nothing; "Annual" is what that interval is called.
    expect(screen.getByText('Annual')).toBeInTheDocument();
    // And a 1-month plan reads "Monthly" in the interval column as well as
    // being named Monthly, which is why these queries do not search for it.
    expect(screen.getAllByText('Monthly')).toHaveLength(2);
  });

  it('shows an unlimited seat allowance as unlimited, not as blank', async () => {
    api.get.mockResolvedValue({ data: PLANS });
    wrap(<PlatformPlans />);
    await waitFor(() => expect(screen.getByText('Legacy')).toBeInTheDocument());
    expect(screen.getByText('unlimited')).toBeInTheDocument();
  });

  it('marks a retired plan rather than hiding it', async () => {
    // It is still attached to live subscriptions; hiding it makes those
    // unexplainable.
    api.get.mockResolvedValue({ data: PLANS });
    wrap(<PlatformPlans />);
    await waitFor(() => expect(screen.getByText('Legacy')).toBeInTheDocument());
    expect(screen.getByText('Retired')).toBeInTheDocument();
  });
});

describe('tenant usage page', () => {
  it('reports seats against the plan allowance', async () => {
    api.get.mockResolvedValue({ data: ORGS });
    wrap(<PlatformUsage />);
    await waitFor(() => expect(
      screen.getByText('Nepal Internet Foundation')).toBeInTheDocument());

    const row = screen.getByText('Nepal Internet Foundation').closest('tr');
    expect(row).toHaveTextContent('30 of 25');
    // Reported, never enforced: this phase does not meter, and locking a
    // customer out of their own HR system over a seat count is not a remedy.
    expect(row).toHaveTextContent('over by 5');
  });

  it('shows a plan with no seat limit as unlimited', async () => {
    api.get.mockResolvedValue({ data: ORGS });
    wrap(<PlatformUsage />);
    await waitFor(() => expect(screen.getByText('ABC School')).toBeInTheDocument());
    expect(screen.getByText('ABC School').closest('tr'))
      .toHaveTextContent('12 of unlimited');
  });

  it('recomputes the counters on request, because a bulk import bypasses the signal',
    async () => {
      api.get.mockResolvedValue({ data: ORGS });
      api.post.mockResolvedValue({ data: { organizations: 2 } });
      wrap(<PlatformUsage />);
      await waitFor(() => expect(screen.getByText('ABC School')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Recompute counters'));
      await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        '/platform/counters/refresh/', {}));
    });
});

describe('platform health page', () => {
  it('reads the check once and does not poll', async () => {
    // The check walks every organization comparing mirror columns against the
    // subscription. A timer would issue that repeatedly for a page nobody is
    // looking at.
    vi.useFakeTimers();
    try {
      api.get.mockResolvedValue({ data: HEALTH });
      wrap(<PlatformHealth />);
      await vi.advanceTimersByTimeAsync(120000);
      expect(api.get).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('is where Launch readiness is reached from, now that it left the rail',
     async () => {
    // THE CONSOLE REDESIGN REMOVED Launch readiness from the sidebar, on the
    // grounds that it is a deploy-time pre-flight check consulted twice a
    // year. The route was left in place, so removing the rail entry made it
    // reachable only by typing the URL -- and both the redesign report and
    // the support runbook tell an operator to find it "Platform → Health".
    // This asserts that sentence is true.
    api.get.mockResolvedValue({ data: HEALTH });
    wrap(<PlatformHealth />);
    const link = await screen.findByRole('link', { name: /launch readiness/i });
    expect(link).toHaveAttribute('href', '/platform/launch');
  });

  it('states plainly that everything passed', async () => {
    api.get.mockResolvedValue({ data: HEALTH });
    wrap(<PlatformHealth />);
    await waitFor(() => expect(
      screen.getByText('All checks passed.')).toBeInTheDocument());
  });

  it('names the tenants stuck provisioning and links to each', async () => {
    // A half-built workspace means a customer is already waiting, and the
    // remedy is on that organization's page.
    api.get.mockResolvedValue({
      data: {
        ...HEALTH, status: 'degraded',
        organizations_stuck_provisioning: ['abcschool'],
        problems: ['1 organization(s) stuck provisioning'],
      },
    });
    wrap(<PlatformHealth />);
    await waitFor(() => expect(screen.getByRole(
      'heading', { name: 'Stuck provisioning' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('link', { name: 'abcschool' }));
    expect(screen.getByText('org page')).toBeInTheDocument();
  });

  it('offers to correct drifted mirrors, which previously nothing did',
    async () => {
      // Drift was detected, logged and displayed — and never repaired. The
      // mirror is what every request reads to decide whether a tenant is
      // admitted, so a detector with no remedy locks customers out.
      api.get.mockResolvedValue({
        data: {
          ...HEALTH, status: 'degraded',
          subscription_mirror_drift: { nif: "status 'active' != 'grace'" },
          problems: ['1 subscription mirror(s) drifted'],
        },
      });
      api.post.mockResolvedValue({ data: { repaired: { nif: 'status' } } });
      wrap(<PlatformHealth />);
      await waitFor(() => expect(screen.getByRole(
        'heading', { name: 'Mirrors drifted' })).toBeInTheDocument());
      expect(screen.getByText(/status 'active' != 'grace'/)).toBeInTheDocument();

      fireEvent.click(screen.getByText('Correct 1 mirror(s)'));
      await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        '/platform/mirrors/reconcile/', {}));
    });
});
