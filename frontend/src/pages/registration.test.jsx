import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

/**
 * Phase S7: the public signup pages.
 *
 * The assertions worth having here are about what the page PROMISES. A signup
 * form that implies a workspace now exists sends somebody off to sign in to
 * something that will not be built until they open their email, and they will
 * conclude the product is broken rather than that they have mail waiting.
 */
vi.mock('../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../services/api';
import Register from './Register';
import VerifyEmail from './VerifyEmail';

const FORM = {
  organization_name: 'New School',
  slug: 'newschool',
  organization_email: 'office@newschool.test',
  admin_email: 'head@newschool.test',
  password: 'Str0ng-Pass-2026',
  password_confirmation: 'Str0ng-Pass-2026',
};

const fill = () => {
  fireEvent.change(screen.getByLabelText('Organization name'),
    { target: { value: FORM.organization_name } });
  fireEvent.change(screen.getByLabelText('Organization email'),
    { target: { value: FORM.organization_email } });
  fireEvent.change(screen.getByLabelText('Your email'),
    { target: { value: FORM.admin_email } });
  fireEvent.change(screen.getByLabelText('Password'),
    { target: { value: FORM.password } });
  fireEvent.change(screen.getByLabelText('Confirm password'),
    { target: { value: FORM.password_confirmation } });
};

const wrap = (ui, path = '/register') => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/register" element={ui} />
      <Route path="/verify-email" element={ui} />
      <Route path="/login" element={<div>sign in page</div>} />
    </Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({
    data: { slug: 'newschool', available: true, reason: null,
            detail: 'newschool is available.' },
  });
});

describe('the registration form', () => {
  it('says that nothing is created yet, before and after submitting',
    async () => {
      // The next thing that happens is an email. Somebody who believes they
      // already have a workspace will not go looking for it.
      api.post.mockResolvedValue({
        data: { status: 'verification_sent', slug: 'newschool',
                expires_in_hours: 24 },
      });
      wrap(<Register />);
      expect(screen.getByText(/Nothing is created until you confirm/))
        .toBeInTheDocument();

      fill();
      fireEvent.click(screen.getByText('Create workspace'));
      await waitFor(() => expect(
        screen.getByText('Check your email')).toBeInTheDocument());
      expect(screen.getByText(/Nothing has been created yet/))
        .toBeInTheDocument();
    });

  it('tells them we cannot send the password they just chose', async () => {
    api.post.mockResolvedValue({
      data: { status: 'verification_sent', slug: 'newschool',
              expires_in_hours: 24 },
    });
    wrap(<Register />);
    fill();
    fireEvent.click(screen.getByText('Create workspace'));
    await waitFor(() => expect(
      screen.getByText(/never saw it and cannot send it/)).toBeInTheDocument());
  });

  it('checks the subdomain as they type, once, after a pause', async () => {
    // COUNTED PER ENDPOINT, not across every GET. The page also reads the
    // public offer once on mount (Phase S11 Part 7 — the trial length the
    // signup copy advertises), so a bare `api.get` call count would make
    // this test fail for a reason that has nothing to do with debouncing.
    const slugCalls = () => api.get.mock.calls
      .filter(([url]) => url === '/register/slug/');

    vi.useFakeTimers();
    try {
      wrap(<Register />);
      const field = screen.getByPlaceholderText('abcschool');
      fireEvent.change(field, { target: { value: 'abc' } });
      fireEvent.change(field, { target: { value: 'abcsch' } });
      fireEvent.change(field, { target: { value: 'abcschool' } });
      // Typing nine characters must not be nine requests: the endpoint is
      // rate limited to 30/min.
      expect(slugCalls()).toHaveLength(0);
      await act(async () => { await vi.advanceTimersByTimeAsync(500); });
      expect(slugCalls()).toHaveLength(1);
      expect(api.get).toHaveBeenCalledWith('/register/slug/',
        { params: { slug: 'abcschool' } });
    } finally {
      vi.useRealTimers();
    }
  });

  it('states the trial on the page where somebody decides to sign up',
     async () => {
    // Phase S11 Part 7. This page promised nothing and disclaimed a card;
    // the trial was stated in the verification email and on the first-login
    // page, i.e. everywhere except here. Read from the server so changing
    // TENANCY_SELF_SERVICE_TRIAL_DAYS cannot leave the copy stale.
    api.get.mockImplementation((url) => (
      url === '/tenant/public/branding/'
        ? Promise.resolve({ data: { trial_days: 14, registration_open: true } })
        : Promise.resolve({ data: { available: true } })
    ));
    wrap(<Register />);
    expect(await screen.findByText(/Free for 14 days/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is charged during/i)).toBeInTheDocument();
  });

  it('still works when the offer cannot be read', async () => {
    api.get.mockRejectedValue(new Error('network'));
    wrap(<Register />);
    // The form is usable; it just promises less.
    expect(screen.getByPlaceholderText('abcschool')).toBeInTheDocument();
    expect(screen.queryByText(/Free for/)).toBeNull();
  });

  it('strips characters a hostname cannot contain, as they type', async () => {
    wrap(<Register />);
    const field = screen.getByPlaceholderText('abcschool');
    fireEvent.change(field, { target: { value: 'ABC School!' } });
    expect(field).toHaveValue('abcschool');
  });

  it('shows the hostname they are actually choosing', async () => {
    // The commonest mistake in testing was typing the whole domain into the
    // field. Showing the suffix stops it.
    wrap(<Register />);
    expect(screen.getByText('.platform.com')).toBeInTheDocument();
    expect(screen.getByText(/cannot be changed later/)).toBeInTheDocument();
  });

  it('says WHICH problem a subdomain has, and blocks submission', async () => {
    api.get.mockResolvedValue({
      data: { slug: 'admin', available: false, reason: 'reserved',
              detail: "'admin' is reserved by the platform." },
    });
    wrap(<Register />);
    fireEvent.change(screen.getByPlaceholderText('abcschool'),
      { target: { value: 'admin' } });
    await waitFor(() => expect(
      screen.getByText(/reserved by the platform/)).toBeInTheDocument());
    expect(screen.getByText('Create workspace')).toBeDisabled();
  });

  it('shows the password rules the server refused', async () => {
    api.post.mockRejectedValue({
      response: { status: 400, data: {
        password: ['This password is too common.',
                   'This password is too short.'] } },
    });
    wrap(<Register />);
    fill();
    fireEvent.click(screen.getByText('Create workspace'));
    await waitFor(() => expect(
      screen.getByText(/too common\. This password is too short/))
      .toBeInTheDocument());
  });

  it('says so plainly when it has been rate limited', async () => {
    api.post.mockRejectedValue({ response: { status: 429, data: {} } });
    wrap(<Register />);
    fill();
    fireEvent.click(screen.getByText('Create workspace'));
    await waitFor(() => expect(
      screen.getByText(/Too many attempts/)).toBeInTheDocument());
  });

  it('explains itself when the deployment does not sell workspaces',
    async () => {
      api.post.mockRejectedValue({ response: { status: 404, data: {} } });
      wrap(<Register />);
      fill();
      fireEvent.click(screen.getByText('Create workspace'));
      await waitFor(() => expect(
        screen.getByText('Not available')).toBeInTheDocument());
    });

  it('carries a honeypot that no human can reach', () => {
    wrap(<Register />);
    const honeypot = screen.getByLabelText('Website');
    expect(honeypot).toHaveAttribute('tabindex', '-1');
    expect(honeypot.closest('[aria-hidden="true"]')).not.toBeNull();
  });
});

describe('the verification page', () => {
  const VERIFIED = {
    status: 'provisioned',
    organization: { name: 'New School', slug: 'newschool', status: 'trial' },
    workspace_url: 'https://newschool.platform.test/',
    admin_email: 'head@newschool.test',
    trial: { days: 14, ends_on: '2026-10-19', status: 'trial' },
    health: { verdict: 'Tenant Ready', configuration_gaps: {} },
  };

  it('verifies once, not twice, because verifying provisions a tenant',
    async () => {
      api.post.mockResolvedValue({ data: VERIFIED });
      wrap(<VerifyEmail />, '/verify-email?token=abc');
      await waitFor(() => expect(
        screen.getByText('Your workspace is ready')).toBeInTheDocument());
      expect(api.post).toHaveBeenCalledTimes(1);
      expect(api.post).toHaveBeenCalledWith('/register/verify/',
        { token: 'abc' });
    });

  it('reports the trial and where to sign in', async () => {
    api.post.mockResolvedValue({ data: VERIFIED });
    wrap(<VerifyEmail />, '/verify-email?token=abc');
    await waitFor(() => expect(
      screen.getByText(/14-day trial/)).toBeInTheDocument());
    expect(screen.getByText(/password you chose/)).toBeInTheDocument();
    expect(screen.getByText('Sign in to newschool')).toBeInTheDocument();
  });

  it('shows a missing-configuration verdict rather than a welcome', async () => {
    // A welcome screen over a half-built workspace sends somebody off to add
    // employees to a system that cannot approve their leave.
    api.post.mockResolvedValue({
      data: { ...VERIFIED, health: {
        verdict: 'Missing Configuration',
        configuration_gaps: { leave_types: ['ANNUAL'] } } },
    });
    wrap(<VerifyEmail />, '/verify-email?token=abc');
    await waitFor(() => expect(
      screen.getByText(/Missing configuration/)).toBeInTheDocument());
    expect(screen.getByText(/leave types: ANNUAL/)).toBeInTheDocument();
  });

  it('treats a second click as success, not failure', async () => {
    api.post.mockResolvedValue({
      data: { ...VERIFIED, status: 'already_provisioned' },
    });
    wrap(<VerifyEmail />, '/verify-email?token=abc');
    await waitFor(() => expect(
      screen.getByText('Already confirmed')).toBeInTheDocument());
  });

  it('says nothing was created when the link is dead', async () => {
    api.post.mockRejectedValue({
      response: { data: { token: 'That verification link has expired.' } },
    });
    wrap(<VerifyEmail />, '/verify-email?token=old');
    await waitFor(() => expect(
      screen.getByText(/link has expired/)).toBeInTheDocument());
    expect(screen.getByText(/nothing was created/)).toBeInTheDocument();
  });

  it('handles a link that arrived without its token', async () => {
    wrap(<VerifyEmail />, '/verify-email');
    expect(screen.getByText('Incomplete link')).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });
});
