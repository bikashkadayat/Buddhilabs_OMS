import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

/**
 * The handover: what an operator gives a new customer.
 *
 * WHAT THESE GUARD. The password is shown masked, copied exactly, and never
 * invented — where the browser does not have it, the text says how the
 * customer will get it instead of printing something that looks like one.
 * And emailing without the password REISSUES it, which invalidates whatever
 * the customer was given before, so that path is confirmed before it runs.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(), patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import HandoverCard from './HandoverCard';
import RecentActivity from './RecentActivity';
import {
  credentialsText, fullPackage, welcomeMessage,
} from '../../utils/handover';

const ACCESS = {
  organization: { name: 'ABC School', slug: 'abcschool' },
  login_url: 'https://abcschool.buddhilabs.com/',
  workspace_url: 'https://abcschool.buddhilabs.com/',
  custom_domain: null,
  administrator: {
    name: 'Sita Sharma', email: 'admin@abc.edu.np',
    last_login: null, signed_in: false, awaiting_first_sign_in: true,
  },
  plan: { code: 'annual', name: 'Annual' },
  trial: { days: 14, ends_on: '2026-10-20', days_remaining: 14 },
  can_sign_in: true,
  support_email: 'help@buddhilabs.com',
  powered_by: 'Powered by Buddhi Labs',
};

let clipboard;
beforeEach(() => {
  vi.clearAllMocks();
  clipboard = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(async (t) => { clipboard.push(t); }) },
  });
});

const mount = (props) => render(
  <MemoryRouter><HandoverCard access={ACCESS} slug="abcschool" {...props} /></MemoryRouter>,
);

describe('the handover texts', () => {
  it('put the password in when it is known, and never invent one', () => {
    expect(credentialsText(ACCESS, 'pw-123')).toContain('Temporary password: pw-123');
    expect(credentialsText(ACCESS, null)).toContain('(sent to you separately)');
    expect(credentialsText(ACCESS, null)).not.toMatch(/\*{4}/);
  });

  it('write a welcome message a customer can act on alone', () => {
    const text = welcomeMessage(ACCESS, 'pw-123');
    expect(text).toMatch(/^Hello ABC School team,/);
    for (const part of ['https://abcschool.buddhilabs.com/', 'admin@abc.edu.np',
      'pw-123', 'choose your own password', '14-day trial',
      'help@buddhilabs.com', 'Powered by Buddhi Labs']) {
      expect(text).toContain(part);
    }
  });

  it('say which domain is live in the full package', () => {
    const text = fullPackage({
      ...ACCESS,
      custom_domain: { hostname: 'hr.abc.edu.np', serving: false,
        status_display: 'Awaiting DNS verification' },
    }, null);
    expect(text).toContain('hr.abc.edu.np — Awaiting DNS verification');
    expect(text).toContain('Login URL: https://abcschool.buddhilabs.com/');
  });
});

describe('the access card', () => {
  it('masks the password until asked, then shows it', async () => {
    mount({ password: 'pw-123' });
    expect(screen.queryByText('pw-123')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /show/i }));
    expect(screen.getByText('pw-123')).toBeInTheDocument();
  });

  it('copies the exact credentials', async () => {
    mount({ password: 'pw-123' });
    await userEvent.click(screen.getByRole('button', { name: /copy credentials/i }));
    await waitFor(() => expect(clipboard).toHaveLength(1));
    expect(clipboard[0]).toBe(credentialsText(ACCESS, 'pw-123'));
    expect(screen.getByRole('status')).toHaveTextContent(/copied/i);
  });

  it('emails the shown password straight away', async () => {
    api.post.mockResolvedValue({ data: { sent_to: 'admin@abc.edu.np', reissued: false } });
    mount({ password: 'pw-123' });
    await userEvent.click(screen.getByRole('button', { name: /send welcome email to/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/platform/organizations/abcschool/access/send/', { password: 'pw-123' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/emailed to admin@abc.edu.np/);
  });

  it('confirms before issuing a new password, because the old one stops working',
    async () => {
      api.post.mockResolvedValue({ data: { sent_to: 'admin@abc.edu.np', reissued: true } });
      mount();
      await userEvent.click(screen.getByRole('button', { name: /email new sign-in details/i }));
      expect(api.post).not.toHaveBeenCalled();
      expect(screen.getByText(/stops working/)).toBeInTheDocument();
      await userEvent.click(screen.getByRole('button', { name: /yes, email new details/i }));
      await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        '/platform/organizations/abcschool/access/send/', {}));
    });

  it('offers nothing to send once the customer has their own password', () => {
    mount({
      access: {
        ...ACCESS,
        administrator: { ...ACCESS.administrator, awaiting_first_sign_in: false,
          signed_in: true, last_login: '2026-10-07T09:00:00Z' },
      },
    });
    expect(screen.queryByRole('button', { name: /email/i })).toBeNull();
    expect(screen.getAllByText(/nothing to send/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/^Signed in ·/)).toBeInTheDocument();
  });

  it('warns when the workspace is closed, before anybody sends a welcome', () => {
    mount({ access: { ...ACCESS, can_sign_in: false } });
    expect(screen.getByRole('alert')).toHaveTextContent(/not open right now/);
  });

  it('shows the server’s reason when an email is refused', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'The email could not be sent (SMTPException). Nothing was changed.' } } });
    mount({ password: 'pw-123' });
    await userEvent.click(screen.getByRole('button', { name: /send welcome email to/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(/Nothing was changed/);
  });
});

describe('the dashboard’s recent lists', () => {
  it('shows one list at a time and says what each sign-in is', async () => {
    api.get.mockResolvedValue({
      data: {
        organizations: [{ slug: 'abcschool', name: 'ABC School',
          status_display: 'Trial', created_at: '2026-10-06T10:00:00Z' }],
        domains: [], payments: [], registrations: [],
        sign_ins: [{ kind: 'client', who: 'admin@abc.edu.np',
          organization_name: 'ABC School', organization_slug: 'abcschool',
          at: '2026-10-06T11:00:00Z' }],
      },
    });
    render(<MemoryRouter><RecentActivity /></MemoryRouter>);
    expect(await screen.findByText('ABC School')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /domains/i }));
    expect(screen.getByText(/no customer has claimed a domain/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /sign-ins/i }));
    expect(screen.getByText(/first sign-in/)).toBeInTheDocument();
  });
});
