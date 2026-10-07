import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * Phase S9 Part 1: the branding centre, and Part 3: the domain page.
 *
 * What is worth testing here is not that fields render. It is the two places
 * this screen can mislead somebody expensively:
 *
 *   1. A colour that will make the product unreadable must be flagged BEFORE
 *      it is saved, in a number the customer can act on.
 *   2. A claimed domain must not read as a working domain. A customer who
 *      thinks claiming was enough will report the working product as broken,
 *      and support will spend an hour on it.
 */
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

const refresh = vi.fn();
vi.mock('../../hooks/useBranding', () => ({
  useBranding: () => ({ branding: null, loading: false, refresh }),
}));

import api from '../../services/api';
import Branding from './Branding';
import Domains from './Domains';

const BRANDING = {
  applicable: true,
  organization: { name: 'ABC School', slug: 'abc-school' },
  display_name: 'ABC School Kathmandu',
  color_primary: '#1D4ED8',
  color_secondary: '#F59E0B',
  color_accent: '',
  login_tagline: 'Learning, organised.',
  dashboard_welcome: '',
  report_footer_text: '',
  logo_primary: '/media/signed/logo.png',
  logo_login: null,
  logo_email: null,
  logo_letterhead: null,
  favicon: null,
};

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: BRANDING });
  api.patch.mockResolvedValue({ data: BRANDING });
});

describe('the branding centre', () => {
  it('shows the tenant what it currently looks like', async () => {
    wrap(<Branding />);
    await waitFor(() => {
      expect(screen.getByLabelText('Organisation name'))
        .toHaveValue('ABC School Kathmandu');
    });
    expect(screen.getByLabelText('Primary colour')).toHaveValue('#1D4ED8');
    expect(screen.getByLabelText('Login page tagline'))
      .toHaveValue('Learning, organised.');
  });

  it('reports the contrast of a chosen colour as a usable number', async () => {
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Primary colour'));
    // #1D4ED8 carries white text. `getAllByText` because the hint sentence
    // and the span inside it both match — scoping to the span's class would
    // assert the markup rather than the message.
    expect(screen.getAllByText(/6\.7:1 — readable/).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('Primary colour'),
                     { target: { value: '#FFFF00' } });
    // And a pale yellow does not — said plainly, with the consequence.
    await waitFor(() => {
      expect(screen.getAllByText(/hard to read/).length).toBeGreaterThan(0);
    });
  });

  it('does not silently correct a colour the customer chose', async () => {
    // The swatch and the field must agree, or somebody spends the afternoon
    // re-entering a value that keeps changing under them.
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Primary colour'));
    fireEvent.change(screen.getByLabelText('Primary colour'),
                     { target: { value: '#FFFF00' } });
    expect(screen.getByLabelText('Primary colour')).toHaveValue('#FFFF00');
  });

  it('sends only what was edited', async () => {
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Organisation name'));
    fireEvent.change(screen.getByLabelText('Organisation name'),
                     { target: { value: 'ABC School' } });
    fireEvent.click(screen.getByRole('button', { name: /save branding/i }));
    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/tenant/branding/',
                                             { display_name: 'ABC School' });
    });
  });

  it('repaints the rest of the application after a save', async () => {
    // R28. Saving a colour and having to sign out to see it is the version
    // of this feature that gets reported as broken.
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Organisation name'));
    fireEvent.change(screen.getByLabelText('Organisation name'),
                     { target: { value: 'ABC School' } });
    fireEvent.click(screen.getByRole('button', { name: /save branding/i }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it('offers no save until something has changed', async () => {
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Organisation name'));
    expect(screen.getByRole('button', { name: /save branding/i })).toBeDisabled();
    expect(screen.getByText(/everything here is saved/i)).toBeInTheDocument();
  });

  it('reports a refused colour without losing what was typed', async () => {
    api.patch.mockRejectedValue({
      response: { data: { detail: 'color primary must be a six-digit hex colour, like #1D4ED8.' } },
    });
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Primary colour'));
    fireEvent.change(screen.getByLabelText('Primary colour'),
                     { target: { value: 'rebeccapurple' } });
    fireEvent.click(screen.getByRole('button', { name: /save branding/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/six-digit hex/);
    });
    expect(screen.getByLabelText('Primary colour')).toHaveValue('rebeccapurple');
  });

  it('says which image goes where, because five logos is confusing', async () => {
    wrap(<Branding />);
    await waitFor(() => screen.getByLabelText('Organisation name'));
    for (const label of ['Main logo', 'Login logo', 'Email logo',
      'Letterhead logo', 'Browser tab icon']) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByText(/notification email/i)).toBeInTheDocument();
  });
});

describe('the custom domain page', () => {
  const PENDING = [{
    hostname: 'hr.abc-school.edu.np',
    method: 'txt',
    record_type: 'TXT',
    record_name: '_nifn-verify.hr.abc-school.edu.np',
    record_value: 'nifn-verify=abc123',
    serving_record: { name: 'hr.abc-school.edu.np', type: 'CNAME',
                      value: 'app.platform.test',
                      note: 'This is what sends visitors to us.' },
    status: 'pending',
    last_error: '',
  }];

  it('never implies a claimed domain is working', async () => {
    api.get.mockResolvedValue({ data: PENDING });
    wrap(<Domains />);
    await waitFor(() => screen.getByRole('heading',
                                        { name: 'hr.abc-school.edu.np' }));
    expect(screen.getByText(/waiting for your DNS record/i)).toBeInTheDocument();
    expect(screen.queryByText(/your team can sign in/i)).toBeNull();
  });

  it('shows BOTH records, labelled by what each one does', async () => {
    // Every support conversation about custom domains is somebody who
    // published the verification record and expected traffic to arrive.
    api.get.mockResolvedValue({ data: PENDING });
    wrap(<Domains />);
    await waitFor(() => screen.getByRole('heading',
                                        { name: 'hr.abc-school.edu.np' }));
    expect(screen.getByText(/1\. Prove the domain is yours/)).toBeInTheDocument();
    expect(screen.getByText(/2\. Send visitors here/)).toBeInTheDocument();
    expect(screen.getByText('nifn-verify=abc123')).toBeInTheDocument();
    expect(screen.getByText('app.platform.test')).toBeInTheDocument();
  });

  it('says a live domain is live', async () => {
    api.get.mockResolvedValue({
      data: [{ ...PENDING[0], status: 'active' }] });
    wrap(<Domains />);
    await waitFor(() => {
      expect(screen.getByText(/your team can sign in at this address/i))
        .toBeInTheDocument();
    });
  });

  it('does not blame the customer when WE could not look', async () => {
    // A 503 means the platform has no resolver. Telling somebody their
    // record is missing when we never checked costs them an afternoon on a
    // zone file that was right.
    api.get.mockResolvedValue({ data: PENDING });
    api.post.mockRejectedValue({ response: { status: 503 } });
    wrap(<Domains />);
    await waitFor(() => screen.getByRole('heading',
                                        { name: 'hr.abc-school.edu.np' }));
    fireEvent.click(screen.getByRole('button', { name: /check now/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert'))
        .toHaveTextContent(/nothing is wrong with your records/i);
    });
  });

  it('explains a failed check in terms of what to go and fix', async () => {
    api.get.mockResolvedValue({
      data: [{ ...PENDING[0], status: 'failed',
               last_error: 'No TXT record at _nifn-verify.hr.abc-school.edu.np.' }] });
    wrap(<Domains />);
    await waitFor(() => {
      expect(screen.getByText(/we looked and could not find the record/i))
        .toBeInTheDocument();
    });
    expect(screen.getByText(/No TXT record at/)).toBeInTheDocument();
  });

  it('claims a hostname and says nothing happened yet', async () => {
    api.get.mockResolvedValue({ data: [] });
    api.post.mockResolvedValue({ data: PENDING[0] });
    wrap(<Domains />);
    await waitFor(() => screen.getByLabelText('Address'));
    fireEvent.change(screen.getByLabelText('Address'),
                     { target: { value: 'hr.abc-school.edu.np' } });
    fireEvent.click(screen.getByRole('button', { name: /claim this address/i }));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/tenant/domains/',
        { hostname: 'hr.abc-school.edu.np', method: 'txt' });
    });
    expect(screen.getByRole('status'))
      .toHaveTextContent(/publish the two records below/i);
  });

  it('tells a tenant with no custom domain that theirs already works', async () => {
    api.get.mockResolvedValue({ data: [] });
    wrap(<Domains />);
    await waitFor(() => {
      expect(screen.getByText(/reachable at the address we gave you/i))
        .toBeInTheDocument();
    });
  });
});
