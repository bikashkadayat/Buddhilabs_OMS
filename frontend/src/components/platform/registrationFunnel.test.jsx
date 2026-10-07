import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

/**
 * Phase S7 Part 11: the funnel panel.
 *
 * The assertion that matters is that drop-off and failure are not presented
 * as the same thing. A registrant who never opens the email and a verified
 * registration with no workspace are both "incomplete", and only one of them
 * is somebody's fault — adding them into a single figure would bury the one
 * that needs waking somebody up.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import RegistrationFunnel from './RegistrationFunnel';

const FUNNEL = {
  window_days: 30,
  stages: [
    { key: 'started', label: 'Registered', count: 41, of_previous: null },
    { key: 'verified', label: 'Email verified', count: 29, of_previous: 70.7 },
    { key: 'provisioned', label: 'Workspace created', count: 26,
      of_previous: 89.7 },
  ],
  rates: { verification: 70.7, provision_success: 89.7,
           provision_failure: 10.3, overall: 63.4 },
  drop_off: { awaiting_verification: 4, expired_unverified: 8,
              verification_resends: 5 },
  failures: { verified_without_workspace: 3 },
  trial_activations: 26,
};

beforeEach(() => vi.clearAllMocks());

describe('the registration funnel', () => {
  it('shows each stage with its conversion from the one before', async () => {
    api.get.mockResolvedValue({ data: FUNNEL });
    render(<RegistrationFunnel />);
    await waitFor(() => expect(screen.getByText('Registered')).toBeInTheDocument());

    expect(screen.getByText('Email verified')).toBeInTheDocument();
    expect(screen.getByText('70.7%')).toBeInTheDocument();
    expect(screen.getByText('89.7%')).toBeInTheDocument();
  });

  it('raises verified-without-workspace as an alert, not a statistic',
    async () => {
      // Every one of those is a customer who was told their email was
      // confirmed and has nothing to sign in to.
      api.get.mockResolvedValue({ data: FUNNEL });
      render(<RegistrationFunnel />);
      await waitFor(() => expect(
        screen.getByRole('alert')).toBeInTheDocument());
      expect(screen.getByRole('alert')).toHaveTextContent(
        'verified without a workspace');
    });

  it('says so plainly when every verified registration got a workspace',
    async () => {
      api.get.mockResolvedValue({
        data: { ...FUNNEL, failures: { verified_without_workspace: 0 } },
      });
      render(<RegistrationFunnel />);
      await waitFor(() => expect(
        screen.getByText(/Every verified registration became a workspace/))
        .toBeInTheDocument());
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

  it('keeps drop-off separate from failure', async () => {
    api.get.mockResolvedValue({ data: FUNNEL });
    render(<RegistrationFunnel />);
    await waitFor(() => expect(
      screen.getByText('Awaiting verification')).toBeInTheDocument());
    expect(screen.getByText('Expired unverified')).toBeInTheDocument();
    expect(screen.getByText('Verification emails re-sent')).toBeInTheDocument();
  });

  it('renders nothing but a note when nobody has registered', async () => {
    // An operator-provisioned platform should not carry an empty funnel.
    api.get.mockResolvedValue({
      data: {
        ...FUNNEL,
        stages: [{ key: 'started', label: 'Registered', count: 0,
                   of_previous: null }],
      },
    });
    render(<RegistrationFunnel />);
    await waitFor(() => expect(
      screen.getByText(/No self-service registrations/)).toBeInTheDocument());
  });

  it('renders nothing at all when the figures cannot be read', async () => {
    api.get.mockRejectedValue(new Error('boom'));
    const { container } = render(<RegistrationFunnel />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });
});
