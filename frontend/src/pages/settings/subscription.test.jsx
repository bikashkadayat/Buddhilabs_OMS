import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * Phase S8: the customer's subscription page.
 *
 * What is worth testing here is not that figures render. It is that the page
 * never implies something has been bought. A customer who believes "Request
 * upgrade" upgraded them will not send the money, and will be surprised when
 * their trial ends — so the inertness of every action is asserted directly,
 * in the copy as well as in the call.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import Subscription from './Subscription';
import { money } from '../../services/subscriptionService';

const STATE = {
  available: true,
  organization: { name: 'ABC School', slug: 'abcschool', seats_used: 12 },
  plan: { code: 'monthly', name: 'Monthly', interval_months: 1,
          included_seats: 25, amount_minor: 99900, currency: 'NPR' },
  status: 'trial', status_display: 'Trial',
  workspace_status: 'trial', is_admitted: true,
  starts_on: '2026-06-15', expires_on: '2026-06-29', days_remaining: 9,
  trial: { is_trial: true, starts_on: '2026-06-15', ends_on: '2026-06-29' },
  grace: { in_grace: false, until: null },
  auto_renew: true,
  explanation: 'You are on a trial with 9 days remaining. No payment details were needed to start.',
  renewal_due: true,
};

const PLANS = [
  { code: 'monthly', name: 'Monthly', interval_months: 1, included_seats: 25,
    amount_minor: 99900, currency: 'NPR', monthly_equivalent_minor: 99900,
    is_current: true, is_upgrade: false, description: '' },
  { code: 'annual', name: 'Annual', interval_months: 12, included_seats: 25,
    amount_minor: 999000, currency: 'NPR', monthly_equivalent_minor: 83250,
    is_current: false, is_upgrade: true, description: 'Best value' },
];

const INSTRUCTIONS = [
  { method: 'bank_transfer', label: 'Bank transfer',
    account_name: 'Platform Pvt Ltd', account_number: '01234567891',
    bank_name: 'Nepal Bank', branch: 'Kathmandu', esewa_id: '',
    qr_image: null, instructions_html: '' },
  { method: 'esewa', label: 'eSewa', account_name: '', account_number: '',
    bank_name: '', branch: '', esewa_id: '9800000000',
    qr_image: '/media/platform/payment-instructions/qr.png',
    instructions_html: '<p>Send to the id above.</p>' },
];

const serve = (overrides = {}) => {
  const data = {
    '/tenant/subscription/': STATE,
    '/tenant/plans/': PLANS,
    '/tenant/payment-instructions/': INSTRUCTIONS,
    '/tenant/payments/': [],
    ...overrides,
  };
  api.get.mockImplementation((url) => (
    url in data ? Promise.resolve({ data: data[url] })
      : Promise.reject(new Error(`unexpected ${url}`))));
};

const wrap = () => render(<MemoryRouter><Subscription /></MemoryRouter>);

beforeEach(() => vi.clearAllMocks());

describe('money', () => {
  it('formats minor units without float arithmetic', () => {
    expect(money(99900)).toBe('NPR 999');
    expect(money(999000)).toBe('NPR 9,990');
    expect(money(259950)).toBe('NPR 2,599.50');
    expect(money(null)).toBe('—');
  });
});

describe('the current plan', () => {
  it('shows the state in the server’s own words', async () => {
    // The same six states are described in the support runbook. A front end
    // that writes its own wording is how a customer is told something
    // different from what support reads back to them.
    serve();
    wrap();
    await waitFor(() => expect(
      screen.getByText(/trial with 9 days remaining/)).toBeInTheDocument());
  });

  it('shows the dates and the seats in use', async () => {
    serve();
    wrap();
    // The date appears twice on purpose -- as "Expires" and as "Trial ends"
    // -- so this asserts both rows rather than a unique match.
    await waitFor(() => expect(
      screen.getAllByText('2026-06-29')).toHaveLength(2));
    expect(screen.getByText('12 of 25')).toBeInTheDocument();
  });

  it('explains a lockout rather than showing a healthy subscription',
    async () => {
      // Billing status and workspace status differ exactly when an operator
      // has intervened, and a page that showed only the first would be lying.
      serve({
        '/tenant/subscription/': {
          ...STATE, is_admitted: false, workspace_status: 'suspended',
          status: 'suspended',
          explanation: 'Your subscription is suspended. Contact support; your data is intact.',
        },
      });
      wrap();
      await waitFor(() => expect(
        screen.getByText(/Contact support; your data is intact/))
        .toBeInTheDocument());
    });
});

describe('choosing a plan', () => {
  it('says that nothing changes until the payment is confirmed', async () => {
    serve();
    wrap();
    await waitFor(() => expect(
      screen.getByText(/Nothing changes/)).toBeInTheDocument());
  });

  it('marks the current plan instead of hiding it', async () => {
    // Renewing means picking the plan you are already on, which is the
    // commonest action on this page.
    serve();
    wrap();
    await waitFor(() => expect(
      screen.getByText('Your current plan')).toBeInTheDocument());
    // The other plan is a longer interval, so its button says so.
    expect(screen.getByText('Request upgrade')).toBeInTheDocument();
  });

  it('shows the monthly equivalent so plans are comparable', async () => {
    serve();
    wrap();
    await waitFor(() => expect(
      screen.getByText('NPR 832.50 a month')).toBeInTheDocument());
  });

  it('requests a plan and reports what is now owed, not what was bought',
    async () => {
      serve();
      api.post.mockResolvedValue({
        data: { reference: 'PAY-ABCS-2026-0001', plan: 'annual',
                amount_minor: 999000, currency: 'NPR',
                status: 'awaiting_proof', status_display: 'Awaiting proof',
                detail: 'Nothing is active yet. Send the amount using one of the methods shown, quoting this reference, then upload your receipt.' },
      });
      wrap();
      fireEvent.click(await screen.findByText('Request upgrade'));

      await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        '/tenant/subscription/request/', { plan_code: 'annual' }));
      await waitFor(() => expect(
        screen.getByText(/Nothing is active yet/)).toBeInTheDocument());
      // Shown twice deliberately: in the confirmation, and again in the
      // receipt form that opens underneath it, because that is the number
      // the customer has to quote when they pay.
      expect(screen.getAllByText(/PAY-ABCS-2026-0001/).length)
        .toBeGreaterThanOrEqual(2);
    });

  it('sends no amount, because the server resolves the price', async () => {
    serve();
    api.post.mockResolvedValue({
      data: { reference: 'R1', amount_minor: 999000, currency: 'NPR',
              status: 'awaiting_proof', detail: 'Nothing is active yet.' },
    });
    wrap();
    fireEvent.click(await screen.findByText('Request upgrade'));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const [, body] = api.post.mock.calls[0];
    expect(Object.keys(body)).toEqual(['plan_code']);
  });
});

describe('how to pay', () => {
  it('shows the console-managed details, not hardcoded ones', async () => {
    serve();
    wrap();
    await waitFor(() => expect(
      screen.getByText('01234567891')).toBeInTheDocument());
    expect(screen.getByText('Nepal Bank')).toBeInTheDocument();
    expect(screen.getByText('9800000000')).toBeInTheDocument();
    expect(screen.getByAltText('eSewa QR code')).toBeInTheDocument();
  });

  it('warns the customer not to send money when nothing is published',
    async () => {
      // The gap the portal exposed: it could tell a customer what they owed
      // and show them no way to pay it.
      serve({ '/tenant/payment-instructions/': [] });
      wrap();
      await waitFor(() => expect(
        screen.getByText(/do not send money until then/)).toBeInTheDocument());
    });
});

describe('the payment history', () => {
  it('explains each payment in the customer’s terms', async () => {
    serve({
      '/tenant/payments/': [{
        reference: 'PAY-ABCS-2026-0001', plan: 'annual',
        amount_minor: 999000, currency: 'NPR', status: 'rejected',
        status_display: 'Rejected', method: 'bank_transfer',
        transaction_id: 'WRONG', paid_at: '2026-06-20',
        submitted_at: '2026-06-20T10:00:00Z', verified_at: null,
        rejection_reason: 'The transaction id does not exist.',
        explanation: 'The transaction id does not exist.',
        can_submit_proof: true,
      }],
    });
    wrap();
    // The reason is the whole point of a rejection: a customer cannot fix
    // what they are not told.
    await waitFor(() => expect(
      screen.getAllByText('The transaction id does not exist.').length)
      .toBeGreaterThan(0));
  });

  it('offers the receipt form for a payment still awaiting proof', async () => {
    serve({
      '/tenant/payments/': [{
        reference: 'PAY-ABCS-2026-0002', plan: 'annual',
        amount_minor: 999000, currency: 'NPR', status: 'awaiting_proof',
        status_display: 'Awaiting proof', method: '', transaction_id: '',
        paid_at: null, submitted_at: null, verified_at: null,
        rejection_reason: '', explanation: 'Waiting for your payment.',
        can_submit_proof: true,
      }],
    });
    wrap();
    await waitFor(() => expect(
      screen.getByText('Send your receipt')).toBeInTheDocument());
    expect(screen.getByLabelText('Date you paid')).toBeInTheDocument();
  });

  it('says there is nothing to show while they are on a trial', async () => {
    serve();
    wrap();
    await waitFor(() => expect(
      screen.getByText('No payments yet')).toBeInTheDocument());
  });
});

describe('authority', () => {
  it('tells an employee this page is not theirs, rather than failing', async () => {
    api.get.mockRejectedValue({ response: { status: 403, data: {} } });
    wrap();
    await waitFor(() => expect(
      screen.getByText(/Only an administrator/)).toBeInTheDocument());
  });
});
