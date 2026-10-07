import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

/**
 * Creating a tenant, branding and all, in one step.
 *
 * WHAT THIS GUARDS. Branding used to be a second, separate step after
 * provisioning, so a customer's staff signed in on the first day to a
 * workspace wearing the platform's colours — on the one day the branding
 * matters most. The form now carries it, and the test that matters is that
 * the files actually reach the request: a file input whose value is never
 * read is indistinguishable, on screen, from one that works.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(), patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../services/api';
import { platformService } from '../../services/platformService';
import NewOrganization from './NewOrganization';

const PLANS = [{ code: 'monthly', name: 'Monthly', is_active: true, trial_days: 14 }];

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: PLANS });
  api.post.mockResolvedValue({ data: { name: 'ABC School', slug: 'abcschool', provisioning: {} } });
});

const fill = async () => {
  await userEvent.type(screen.getByLabelText(/organization name/i), 'ABC School');
};

// IN A ROUTER, because the success state renders a <Link> to the new
// organization. Without one, react-router's LinkWithRef destructures a null
// context and throws -- AFTER the assertion has already passed, so vitest
// reported "1547 passed, 2 errors" and the tests looked fine. Mounting the
// real thing the way the app mounts it is the fix.
const mount = () =>
  render(
    <MemoryRouter>
      <NewOrganization onClose={vi.fn()} onCreated={vi.fn()} />
    </MemoryRouter>,
  );

describe('the create-organization form', () => {
  it('offers branding at creation, not as a second step', async () => {
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    expect(screen.getByLabelText(/organization logo/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^favicon/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('#1D4ED8')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('#F59E0B')).toBeInTheDocument();
  });

  it('offers the domain, industry and country the brief lists', async () => {
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    expect(screen.getByPlaceholderText('hr.theircompany.com')).toBeInTheDocument();
    expect(screen.getByLabelText(/industry/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/country/i)).toBeInTheDocument();
  });

  it('derives the subdomain and previews where the tenant will open', async () => {
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    await fill();
    expect(screen.getByLabelText(/workspace address/i)).toHaveValue('abcschool');
    expect(screen.getAllByText(/abcschool\.buddhilabs\.com/).length).toBeGreaterThan(0);
  });

  it('says a custom domain is CLAIMED, not granted', async () => {
    // An operator who believes typing a hostname makes it work will tell the
    // customer it works. Phase S9 made that untrue on purpose.
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    expect(screen.getByText(/claims/i)).toBeInTheDocument();
    expect(screen.getByText(/only once DNS proves they own it/i))
      .toBeInTheDocument();
  });

  it('sends plain JSON when no file is attached', async () => {
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    await fill();
    await userEvent.type(screen.getByLabelText(/billing email/i), 'a@abc.test');
    await userEvent.click(screen.getByRole('button', { name: /create organization/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const [, body] = api.post.mock.calls[0];
    expect(body).not.toBeInstanceOf(FormData);
    expect(body.name).toBe('ABC School');
  });

  it('upgrades to multipart and carries the file when one is attached', async () => {
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    await fill();
    await userEvent.type(screen.getByLabelText(/billing email/i), 'a@abc.test');
    const file = new File(['x'], 'logo.png', { type: 'image/png' });
    await userEvent.upload(screen.getByLabelText(/organization logo/i), file);
    await userEvent.click(screen.getByRole('button', { name: /create organization/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const [, body] = api.post.mock.calls[0];
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('logo')).toBeInstanceOf(File);
    expect(body.get('name')).toBe('ABC School');
  });

  it('shows a field error back on the field it belongs to', async () => {
    api.post.mockRejectedValue({
      response: { data: { color_primary: 'The primary colour must be a six-digit hex colour, like #1D4ED8.' } },
    });
    mount();
    await waitFor(() => screen.getByLabelText(/organization name/i));
    await fill();
    await userEvent.type(screen.getByLabelText(/billing email/i), 'a@abc.test');
    await userEvent.click(screen.getByRole('button', { name: /create organization/i }));
    expect(await screen.findByText(/six-digit hex colour/)).toBeInTheDocument();
  });
});

describe('platformService.provision', () => {
  it('leaves a bodiless create as JSON', () => {
    platformService.provision({ name: 'X', slug: 'x' });
    expect(api.post.mock.calls[0][1]).not.toBeInstanceOf(FormData);
  });

  it('drops empty values from the multipart body', () => {
    // "" in a FormData arrives as the string "", which a serializer with a
    // blank=False field reads as a supplied empty value rather than as absent.
    platformService.provision({
      name: 'X', slug: 'x', industry: '', color_primary: '',
      logo: new File(['x'], 'l.png', { type: 'image/png' }),
    });
    const body = api.post.mock.calls[0][1];
    expect(body.get('industry')).toBeNull();
    expect(body.get('color_primary')).toBeNull();
    expect(body.get('name')).toBe('X');
  });
});
