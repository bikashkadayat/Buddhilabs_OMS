import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * Phase S6.75: the launch readiness page.
 *
 * The assertion that matters most is that a high score does not read as
 * permission to launch. An operator who sees "92%" in large type and has to
 * hunt for the one critical failure below it will launch on a platform with
 * no purchasable plan, and every customer who registers that day will be
 * refused after clicking the link in their email.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import LaunchReadiness from './LaunchReadiness';

const check = (key, label, ready, severity, detail, hint = '') =>
  ({ key, label, ready, severity, detail, hint });

const READY = {
  ready: true, score: 100, verdict: 'Launch Ready', blocking: [],
  counts: { total: 3, ready: 3, critical_failing: 0, important_failing: 0,
            advisory_failing: 0 },
  mode: { tenancy: true, public_registration: true },
  checks: [
    check('database', 'Database', true, 'critical', 'postgresql reachable'),
    check('plans', 'Purchasable plans', true, 'critical', 'monthly, annual'),
    check('email', 'Email provider', true, 'critical', 'SMTP via smtp.x'),
  ],
};

const BLOCKED = {
  ready: false, score: 92, verdict: 'Not Launch Ready', blocking: ['plans'],
  counts: { total: 12, ready: 11, critical_failing: 1, important_failing: 0,
            advisory_failing: 0 },
  mode: { tenancy: true, public_registration: true },
  checks: [
    check('database', 'Database', true, 'critical', 'postgresql reachable'),
    check('plans', 'Purchasable plans', false, 'critical',
          'none: every provisioning attempt will be refused',
          'Seed at least one active, public plan with a current PlanPrice.'),
    check('export_retention', 'Export retention', false, 'advisory',
          '120 days'),
  ],
};

const EVENTS = {
  window_days: 30,
  panels: [
    { label: 'Tenant creation', key: 'tenant_created', source: 'audit',
      total: 4, by_day: {} },
    { label: 'Login succeeded', key: 'login_ok', source: 'counter',
      total: 120, by_day: {} },
    { label: 'Login failed', key: 'login_failed', source: 'counter',
      total: 6, by_day: {} },
  ],
  rates: { provisioning_success: 100.0, login_success: 95.2 },
  totals: { provisioning_attempts: 4, login_attempts: 126 },
};

const wrap = () => render(
  <MemoryRouter><LaunchReadiness /></MemoryRouter>,
);

const serve = (readiness, events = EVENTS) => {
  api.get.mockImplementation((url) => {
    if (url.includes('launch-readiness')) return Promise.resolve({ data: readiness });
    if (url.includes('events')) return Promise.resolve({ data: events });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
};

beforeEach(() => vi.clearAllMocks());

describe('the verdict', () => {
  it('leads with Launch Ready when nothing critical is failing', async () => {
    serve(READY);
    wrap();
    await waitFor(() => expect(
      screen.getByText('Launch Ready')).toBeInTheDocument());
    // The score appears twice in this fixture -- as the readiness score and
    // as a provisioning success rate -- so the assertion is about the one in
    // the verdict banner.
    expect(screen.getByLabelText('Readiness score'))
      .toHaveTextContent('100%');
  });

  it('says Not Launch Ready even on a high score', async () => {
    // 92% with one critical failure. The score is how much work is left; the
    // verdict is whether to open the doors, and they disagree here.
    serve(BLOCKED);
    wrap();
    await waitFor(() => expect(
      screen.getByText('Not Launch Ready')).toBeInTheDocument());
    expect(screen.getByText('92%')).toBeInTheDocument();
    expect(screen.getByText(/1 critical failing/)).toBeInTheDocument();
  });

  it('names the failing dependency and what to do about it', async () => {
    serve(BLOCKED);
    wrap();
    await waitFor(() => expect(
      screen.getByText(/every provisioning attempt will be refused/))
      .toBeInTheDocument());
    expect(screen.getByText(/Seed at least one active, public plan/))
      .toBeInTheDocument();
  });

  it('distinguishes a critical failure from an advisory one', async () => {
    // Both are "not ready". Only one of them stops a launch, and in a flat
    // list they look identical.
    serve(BLOCKED);
    wrap();
    await waitFor(() => expect(
      screen.getByText('Purchasable plans')).toBeInTheDocument());

    const plans = screen.getByText('Purchasable plans').closest('li');
    const retention = screen.getByText('Export retention').closest('li');
    expect(plans.className).toContain('is-bad');
    expect(retention.className).toContain('is-mute');
  });

  it('states the posture the checks were graded against', async () => {
    // A single-tenant deployment needs no purchasable plan, so the grades
    // only mean something alongside the mode.
    serve(READY);
    wrap();
    await waitFor(() => expect(
      screen.getByText(/public registration on/)).toBeInTheDocument());
  });

  it('re-runs the checks on request', async () => {
    serve(READY);
    wrap();
    await waitFor(() => expect(screen.getByText('Re-check')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Re-check'));
    await waitFor(() => expect(
      api.get.mock.calls.filter(([url]) => url.includes('launch-readiness'))
        .length).toBe(2));
  });

  it('says so when the report cannot be read', async () => {
    api.get.mockRejectedValue(new Error('boom'));
    wrap();
    await waitFor(() => expect(
      screen.getByText('Unavailable')).toBeInTheDocument());
  });
});

describe('the event dashboards', () => {
  it('shows every series with where the figure comes from', async () => {
    serve(READY);
    wrap();
    await waitFor(() => expect(
      screen.getByText('Tenant creation')).toBeInTheDocument());
    expect(screen.getByText('120')).toBeInTheDocument();
    // `audit` vs `counter`, so nobody has to read the module to know why
    // logins are measured differently from everything else.
    expect(screen.getAllByText('counter')).toHaveLength(2);
    expect(screen.getByText('audit')).toBeInTheDocument();
  });

  it('shows "no attempts" rather than a perfect score for an empty window',
    async () => {
      // 100% over zero attempts reads as a healthy signal where there is no
      // signal at all.
      serve(READY, {
        ...EVENTS,
        rates: { provisioning_success: null, login_success: null },
        totals: { provisioning_attempts: 0, login_attempts: 0 },
      });
      wrap();
      await waitFor(() => expect(
        screen.getAllByText('no attempts')).toHaveLength(2));
    });
});
