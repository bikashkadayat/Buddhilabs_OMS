import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Phase S9 Part 2: R28, as the browser experiences it.
 *
 * THE ASSERTION THAT MATTERS is on `document.documentElement.style`. Until
 * this phase a tenant's colours reached the login page and stopped: every
 * employee of every customer worked inside Nepal Internet Foundation's blue.
 * Checking that a context value is populated would not have caught that —
 * the context was populated then too. What was missing was the theme
 * reaching the document, so that is what is checked.
 */
vi.mock('../services/api', () => ({
  default: {
    get: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

let auth = { isAuthenticated: true, isPlatformStaff: false };
vi.mock('./useAuth', () => ({ useAuth: () => auth }));

import api from '../services/api';
import { BrandingProvider, useBranding } from './useBranding';

const PAYLOAD = {
  applicable: true,
  organization: { name: 'ABC School', slug: 'abc-school' },
  display_name: 'ABC School Kathmandu',
  color_primary: '#7C3AED',
  color_secondary: '#F59E0B',
  logo_primary: '/media/signed/logo.png',
  favicon: '/media/signed/favicon.png',
};

const Probe = () => {
  const { branding, name, logo } = useBranding();
  return (
    <div>
      <span data-testid="name">{name}</span>
      <span data-testid="logo">{logo}</span>
      <span data-testid="applicable">{String(Boolean(branding))}</span>
    </div>
  );
};

const mount = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BrandingProvider><Probe /></BrandingProvider>
    </QueryClientProvider>,
  );
};

const cssVar = (name) =>
  document.documentElement.style.getPropertyValue(name);

beforeEach(() => {
  vi.clearAllMocks();
  auth = { isAuthenticated: true, isPlatformStaff: false };
  document.documentElement.removeAttribute('style');
  document.head.innerHTML = '<link rel="icon" href="/buddhi-labs.png">';
  document.title = 'Office Management System';
});

afterEach(() => { document.documentElement.removeAttribute('style'); });

describe('the branding provider', () => {
  it('themes the DOCUMENT, not just a context value', async () => {
    api.get.mockResolvedValue({ data: PAYLOAD });
    mount();
    await waitFor(() => {
      expect(cssVar('--brand-blue')).toBe('#7c3aed');
    });
    // And the derived family, which is what the rest of the stylesheet uses.
    expect(cssVar('--brand-blue-rgb')).toBe('124, 58, 237');
    expect(cssVar('--border')).toContain('124, 58, 237');
    expect(cssVar('--bg-sidebar')).not.toBe('');
  });

  it('applies the tab icon and the window title', async () => {
    api.get.mockResolvedValue({ data: PAYLOAD });
    mount();
    await waitFor(() => {
      expect(document.querySelector("link[rel~='icon']"))
        .toHaveAttribute('href', '/media/signed/favicon.png');
    });
    expect(document.title).toContain('ABC School Kathmandu');
  });

  it('leaves the product exactly as it ships when a tenant has no branding',
     async () => {
    api.get.mockResolvedValue({ data: { applicable: true } });
    mount();
    await waitFor(() => {
      expect(screen.getByTestId('applicable')).toHaveTextContent('true');
    });
    // Nothing is set, so :root wins — rather than the shipped colour being
    // written back explicitly, which would go stale the next time the
    // stylesheet changes.
    expect(cssVar('--brand-blue')).toBe('');
    expect(document.querySelector("link[rel~='icon']"))
      .toHaveAttribute('href', '/buddhi-labs.png');
  });

  it('never themes the console for a platform operator', async () => {
    // An operator wearing a customer's colours is how somebody suspends the
    // wrong organization.
    auth = { isAuthenticated: true, isPlatformStaff: true };
    api.get.mockResolvedValue({ data: PAYLOAD });
    mount();
    await waitFor(() => {
      expect(screen.getByTestId('applicable')).toHaveTextContent('false');
    });
    expect(api.get).not.toHaveBeenCalled();
    expect(cssVar('--brand-blue')).toBe('');
  });

  it('asks for nothing at all before anybody has signed in', async () => {
    auth = { isAuthenticated: false, isPlatformStaff: false };
    mount();
    await waitFor(() => {
      expect(screen.getByTestId('applicable')).toHaveTextContent('false');
    });
    expect(api.get).not.toHaveBeenCalled();
  });

  it('keeps the product usable when branding cannot be fetched', async () => {
    // Nobody should be unable to approve a leave request because a logo
    // could not be loaded.
    api.get.mockRejectedValue(new Error('network'));
    mount();
    await waitFor(() => {
      expect(screen.getByTestId('name'))
        .toHaveTextContent('Buddhi Labs');
    });
    expect(screen.getByTestId('logo')).toHaveTextContent('/buddhi-labs.png');
  });

  it('falls back to the shipped logo rather than rendering a broken image',
     async () => {
    api.get.mockResolvedValue({
      data: { ...PAYLOAD, logo_primary: null } });
    mount();
    await waitFor(() => {
      expect(screen.getByTestId('logo')).toHaveTextContent('/buddhi-labs.png');
    });
  });

  it('reverts the theme when the provider goes away', async () => {
    api.get.mockResolvedValue({ data: PAYLOAD });
    const { unmount } = mount();
    await waitFor(() => expect(cssVar('--brand-blue')).toBe('#7c3aed'));
    unmount();
    // Signing out of one tenant and into another, on a single-host
    // deployment, must not keep the first one's colours.
    expect(cssVar('--brand-blue')).toBe('');
    expect(document.querySelector("link[rel~='icon']"))
      .toHaveAttribute('href', '/buddhi-labs.png');
  });
});
