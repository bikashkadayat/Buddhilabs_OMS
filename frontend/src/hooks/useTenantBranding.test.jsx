import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../services/api', () => ({
  default: {
    get: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../services/api';
import { useTenantBranding } from './useTenantBranding';

const Probe = () => {
  const branding = useTenantBranding();
  return <div data-testid="out">{branding ? branding.name : 'none'}</div>;
};

beforeEach(() => vi.clearAllMocks());

describe('useTenantBranding (Phase S6 Part 7)', () => {
  it('applies the branding the host resolves to', async () => {
    api.get.mockResolvedValue({
      data: {
        known: true, name: 'ABC School', logo_login: '/signed/abc.png',
        color_primary: '#0F766E', color_secondary: '', login_tagline: 'Learning',
      },
    });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('out')).toHaveTextContent('ABC School'));
    expect(api.get).toHaveBeenCalledWith('/tenant/public/branding/');
  });

  it('ignores an unresolved host instead of rendering half a brand', async () => {
    // The endpoint answers `known: false` rather than 404ing, because a 404
    // would turn the login page into a customer-list oracle. A partial brand —
    // somebody else's colour with no logo — looks like a broken page, so the
    // hook treats "unknown" as "no branding".
    api.get.mockResolvedValue({ data: { known: false, name: '' } });
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('out')).toHaveTextContent('none'));
  });

  it('puts the tenant\'s own favicon in the tab, and takes it back out',
    async () => {
      // It could be uploaded through the console and stored against the
      // tenant, and then nothing served it -- so every customer's browser tab
      // showed the platform's icon. There is no React element for a favicon,
      // so the hook reaches for the <link> itself.
      const link = document.createElement('link');
      link.setAttribute('rel', 'icon');
      link.setAttribute('href', '/favicon.ico');
      document.head.appendChild(link);
      try {
        api.get.mockResolvedValue({
          data: {
            known: true, name: 'ABC School', logo_login: '/signed/abc.png',
            favicon: '/signed/abc-icon.png', color_primary: '#0F766E',
            color_secondary: '', login_tagline: 'Learning',
          },
        });
        const view = render(<Probe />);
        await waitFor(() => expect(
          link.getAttribute('href')).toBe('/signed/abc-icon.png'));

        // Reverted on unmount: a sign-out must not leave one customer's icon
        // sitting on the next page somebody opens.
        view.unmount();
        expect(link.getAttribute('href')).toBe('/favicon.ico');
      } finally {
        link.remove();
      }
    });

  it('never stops anybody signing in when the lookup fails', async () => {
    api.get.mockRejectedValue(new Error('network'));
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('out')).toHaveTextContent('none'));
  });
});
