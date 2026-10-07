import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * The sign-in page: the customer's brand, plain words when it goes wrong.
 */
const login = vi.fn();
let tenant = null;
let registrationOpen = false;

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ login, isAuthenticated: false, isPlatformStaff: false }),
}));
vi.mock('../hooks/useTenantBranding', () => ({
  useTenantBranding: () => ({ branding: tenant, registrationOpen }),
}));

import Login from './Login';

const mount = () => render(<MemoryRouter><Login /></MemoryRouter>);
const fill = () => {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@abc.test' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'nope' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
};

beforeEach(() => {
  login.mockReset();
  tenant = { name: 'ABC School', color_primary: '#4F46E5', known: true };
  registrationOpen = true;
});

describe('the sign-in page', () => {
  it('is the customer’s: their name, their monogram, the platform only as Powered by', () => {
    mount();
    expect(screen.getByRole('heading', { name: 'Sign in to ABC School' })).toBeInTheDocument();
    expect(screen.getAllByText('AS').length).toBeGreaterThan(0);
    expect(screen.getByText(/Powered by/)).toBeInTheDocument();
    expect(screen.queryByAltText(/Buddhi Labs/)).toBeNull();
  });

  it('labels the password field "Password", not "Password Show"', () => {
    mount();
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
  });

  it('says a wrong password in plain words, inline, not in a modal', async () => {
    login.mockRejectedValue({ response: { status: 400, data: { detail: 'Invalid email or password.' } } });
    mount();
    fill();
    expect(await screen.findByRole('alert'))
      .toHaveTextContent('That email and password don’t match. Check them and try again.');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('keeps a specific server reason, such as a suspended workspace', async () => {
    login.mockRejectedValue({ response: { status: 402, data: { detail: 'This workspace is not active. Please contact your administrator.' } } });
    mount();
    fill();
    expect(await screen.findByRole('alert')).toHaveTextContent('This workspace is not active.');
  });

  it('never shows axios’s "Network Error"', async () => {
    login.mockRejectedValue({ message: 'Network Error', request: {} });
    mount();
    fill();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/can’t reach the server/));
  });

  it('offers public signup only on the platform’s own page', () => {
    mount();
    expect(screen.queryByText(/Create a workspace/)).toBeNull();
    tenant = null;
    mount();
    expect(screen.getByText(/Create a workspace/)).toBeInTheDocument();
  });
});
