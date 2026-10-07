import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
}));
vi.mock('../hooks/useTenantBranding', () => ({
  useTenantBranding: () => ({ branding: { name: 'ABC School', known: true }, registrationOpen: false }),
}));

import api from '../services/api';
import ForgotPassword from './ForgotPassword';
import ResetPassword from './ResetPassword';

beforeEach(() => vi.clearAllMocks());

describe('forgot password', () => {
  it('shows what the server said, whatever the address', async () => {
    api.post.mockResolvedValue({ data: { detail: 'If an account exists for that email, we’ve sent a link.' } });
    render(<MemoryRouter><ForgotPassword /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@abc.test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }));
    expect(await screen.findByRole('status')).toHaveTextContent('If an account exists');
    expect(api.post).toHaveBeenCalledWith('/auth/password-reset/', { email: 'a@abc.test' });
  });

  it('wears the organization’s brand', () => {
    render(<MemoryRouter><ForgotPassword /></MemoryRouter>);
    expect(screen.getAllByText('ABC School').length).toBeGreaterThan(0);
  });
});

describe('reset password', () => {
  const mount = () => render(
    <MemoryRouter initialEntries={['/reset-password?uid=MQ&token=abc']}><ResetPassword /></MemoryRouter>,
  );

  it('says an expired link is expired before asking for a password', async () => {
    api.get.mockResolvedValue({ data: { valid: false, detail: 'This reset link has expired or has already been used.' } });
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('expired');
    expect(screen.queryByLabelText('New password')).toBeNull();
    expect(screen.getByRole('link', { name: 'Send a new link' })).toHaveAttribute('href', '/forgot-password');
  });

  it('sets the new password and offers sign-in', async () => {
    api.get.mockResolvedValue({ data: { valid: true, email: 'a@abc.test' } });
    api.post.mockResolvedValue({ data: { detail: 'Your password has been changed.' } });
    mount();
    fireEvent.change(await screen.findByLabelText('New password'), { target: { value: 'Brand-New-77!' } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'Brand-New-77!' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/auth/password-reset/confirm/',
      { uid: 'MQ', token: 'abc', new_password: 'Brand-New-77!' }));
    expect(await screen.findByRole('status')).toHaveTextContent('changed');
  });

  it('shows the password rules in words when one is refused', async () => {
    api.get.mockResolvedValue({ data: { valid: true, email: 'a@abc.test' } });
    api.post.mockRejectedValue({ response: { status: 400, data: { new_password: ['This password is too short.', 'This password is too common.'] } } });
    mount();
    fireEvent.change(await screen.findByLabelText('New password'), { target: { value: '123' } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: '123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('too short. This password is too common.');
  });
});
