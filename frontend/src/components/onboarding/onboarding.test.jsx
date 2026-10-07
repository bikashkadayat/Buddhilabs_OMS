import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * Phase S7 Parts 6, 7 and 8: the first-login wizard.
 *
 * What is worth testing here is not that a list renders. It is that the panel
 * keeps out of the way when it should, tells the truth about what is ready,
 * and never offers a tick on a step that would un-tick itself.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import OnboardingWizard from './OnboardingWizard';

const STATE = {
  applicable: true,
  show_wizard: true,
  dismissed_at: null,
  organization: {
    name: 'Sunrise School', slug: 'sunrise-school', status: 'trial',
    status_display: 'Trial', industry: 'education', country: 'NP',
    document_prefix: 'SUNRISESCH', created_at: '2026-10-05T09:00:00Z',
  },
  subscription: {
    status: 'trial', plan: 'monthly', trial_ends_on: '2026-10-19',
    period_ends_on: '2026-10-19', days_remaining: 14, is_trial: true,
  },
  ready: [
    { key: 'departments', label: 'Departments ready', ready: true },
    { key: 'leave', label: 'Leave management ready', ready: true },
    { key: 'attendance', label: 'Attendance ready', ready: true },
    { key: 'tasks', label: 'Task management ready', ready: true },
    { key: 'minutes', label: 'Minutes ready', ready: true },
    { key: 'inventory', label: 'Inventory ready', ready: true },
  ],
  health: { verdict: 'Tenant Ready', configuration_gaps: {} },
  checklist: [
    { key: 'logo', title: 'Upload your logo', detail: 'It appears on…',
      action: '/settings/branding', measured: false, done: false },
    { key: 'team', title: 'Invite your team members',
      detail: 'Everything else…', action: '/admin/users',
      measured: true, done: true },
    { key: 'leave_policy', title: 'Review your leave policy',
      detail: 'Annual…', action: '/admin/leaves/leave-types',
      measured: false, done: false },
    { key: 'attendance', title: 'Review your attendance rules',
      detail: 'A general shift…', action: '/admin/attendance/policies',
      measured: false, done: false },
    { key: 'first_task', title: 'Create your first task',
      detail: 'Raise one…', action: '/tasks', measured: false, done: false },
  ],
  progress: { completed: 1, total: 5, percent: 20 },
};

const wrap = () => render(
  <MemoryRouter><OnboardingWizard /></MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: STATE });
});

describe('when it appears', () => {
  it('welcomes them by organization and says how long the trial has left',
    async () => {
      wrap();
      await waitFor(() => expect(
        screen.getByText('Welcome to your Sunrise School workspace')).toBeInTheDocument());
      expect(screen.getByText(/14/)).toBeInTheDocument();
    });

  it('renders nothing at all once the server stops asking for it', async () => {
    api.get.mockResolvedValue({ data: { ...STATE, show_wizard: false } });
    const { container } = wrap();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing for an account with no workspace', async () => {
    // A platform operator has no workspace to be onboarded into.
    api.get.mockResolvedValue({ data: { applicable: false } });
    const { container } = wrap();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('says nothing rather than erroring when the call fails', async () => {
    api.get.mockRejectedValue(new Error('boom'));
    const { container } = wrap();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    // An error banner over the home page on somebody's first morning is
    // worse than no wizard.
    expect(container.firstChild).toBeNull();
  });
});

describe('the two lists', () => {
  it('separates what already works from what to do next', async () => {
    // Merging them would produce one list of ten items in which the five
    // finished ones read as work.
    wrap();
    await waitFor(() => expect(
      screen.getByText('Ready to use')).toBeInTheDocument());
    expect(screen.getByText('Next steps')).toBeInTheDocument();
    expect(screen.getByText('Leave management ready')).toBeInTheDocument();
    expect(screen.getByText('Upload your logo')).toBeInTheDocument();
  });

  it('shows the health verdict in the console’s own words', async () => {
    wrap();
    await waitFor(() => expect(
      screen.getByText('Everything is configured')).toBeInTheDocument());
  });

  it('raises a missing-configuration verdict where somebody will see it',
    async () => {
      api.get.mockResolvedValue({
        data: { ...STATE, health: {
          verdict: 'Missing Configuration',
          configuration_gaps: { leave_types: ['SPECIAL'] } } },
      });
      wrap();
      await waitFor(() => expect(
        screen.getByText(/Some setup is still missing: leave types/))
        .toBeInTheDocument());
    });

  it('reports progress as a figure and a bar', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('1 of 5')).toBeInTheDocument());
    expect(screen.getByRole('progressbar'))
      .toHaveAttribute('aria-valuenow', '20');
  });
});

describe('ticking steps', () => {
  it('offers a tick on every step that is not done yet', async () => {
    // Every step in this brief is measured, and the tick is an override for
    // a customer who does not need that step -- one shift, no interest in
    // attendance rules. A checklist that cannot be satisfied is one people
    // learn to ignore.
    wrap();
    await waitFor(() => expect(
      screen.getByText('Invite your team members')).toBeInTheDocument());
    expect(screen.getAllByText('Mark done')).toHaveLength(4);
  });

  it('offers no tick on a step already done', async () => {
    wrap();
    await waitFor(() => expect(
      screen.getByText('Invite your team members')).toBeInTheDocument());
    const row = screen.getByText('Invite your team members').closest('li');
    expect(row.className).toContain('is-done');
    expect(row.querySelector('button')).toBeNull();
  });

  it('sends the tick and takes the server’s new state', async () => {
    api.post.mockResolvedValue({
      data: { ...STATE, progress: { completed: 2, total: 5, percent: 40 } },
    });
    wrap();
    await waitFor(() => expect(
      screen.getByText('Invite your team members')).toBeInTheDocument());

    fireEvent.click(screen.getAllByText('Mark done')[0]);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/tenant/onboarding/', { step_done: 'logo' }));
    await waitFor(() => expect(screen.getByText('2 of 5')).toBeInTheDocument());
  });

  it('can be dismissed by somebody who wants to get on with their job',
    async () => {
      api.post.mockResolvedValue({ data: { ...STATE, show_wizard: false } });
      const { container } = wrap();
      await waitFor(() => expect(
        screen.getByText('Dismiss')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Dismiss'));
      await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        '/tenant/onboarding/', { dismissed: true }));
      await waitFor(() => expect(container.firstChild).toBeNull());
    });
});
