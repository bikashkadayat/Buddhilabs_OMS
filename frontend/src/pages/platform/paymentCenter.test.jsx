import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * The Payment Center: an operator decides a payment without an engineer.
 */
vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
}));

import api from '../../services/api';
import PlatformPayments from './Payments';

const PAYMENT = {
  id: 'p1', organization_slug: 'abc', organization_name: 'ABC School',
  payment_reference: 'PAY-ABCS-2026-0001', plan_name: 'Annual', plan_code: 'annual',
  amount_minor: 999000, currency: 'NPR', method_display: 'Bank Transfer',
  transaction_id: 'TXN-1', paid_at: '2026-10-05', status: 'submitted',
  status_display: 'Proof submitted', submitted_at: '2026-10-05T10:00:00Z',
  submitted_by: 'principal@abc.test', has_proof: true, payer_note: '',
};

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: [PAYMENT] });
});

const mount = () => render(<MemoryRouter><PlatformPayments /></MemoryRouter>);

describe('the payment center', () => {
  it('shows what a reviewer needs to decide', async () => {
    mount();
    expect(await screen.findByText('ABC School')).toBeInTheDocument();
    for (const text of ['TXN-1', 'Bank Transfer', 'principal@abc.test', 'Annual plan · PAY-ABCS-2026-0001']) {
      expect(screen.getByText(text, { exact: false })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: /view receipt/i })).toBeInTheDocument();
  });

  it('approves in one confirmed step and says what happened', async () => {
    api.post.mockResolvedValue({ data: { ...PAYMENT, status: 'verified', subscription: { current_period_end: '2027-10-05' } } });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve payment' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/platform/payments/p1/approve/', { note: '' }));
    expect(await screen.findByRole('status')).toHaveTextContent('active until 2027-10-05');
  });

  it('will not reject without a reason the customer can read', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    const button = screen.getByRole('button', { name: 'Reject payment' });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Amount does not match.' } });
    expect(button).not.toBeDisabled();
  });

  it('can ask for more instead of rejecting', async () => {
    api.post.mockResolvedValue({ data: { ...PAYMENT, status: 'needs_info' } });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Ask for more' }));
    fireEvent.change(screen.getByLabelText('What do you need?'), { target: { value: 'Full screenshot please.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send request' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/platform/payments/p1/request-info/', { message: 'Full screenshot please.' }));
  });
});
