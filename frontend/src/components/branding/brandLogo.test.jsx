import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

/**
 * Phase S9 Part 2. `/NIF.png` was written literally into the application
 * header and into six print letterheads across the leave module. Each was
 * correct for a single-tenant deployment and each is a customer's leave
 * application printed under another organization's logo now.
 *
 * A component is the only version of that fix the next screen cannot miss,
 * so what is tested is the fallback chain and the alt text — the two things
 * a per-file edit would have got subtly different in each file.
 */
let branding = null;
vi.mock('../../hooks/useBranding', () => ({
  useBranding: () => ({ branding }),
}));

import BrandLogo from './BrandLogo';

describe('BrandLogo', () => {
  it('ships NIF for the deployment that has no tenant branding', () => {
    branding = null;
    render(<BrandLogo />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/buddhi-labs.png');
    expect(screen.getByRole('img'))
      .toHaveAccessibleName('Buddhi Labs logo');
  });

  it("uses the tenant's logo and names the tenant in the alt text", () => {
    branding = { logo_primary: '/media/abc.png',
                 display_name: 'ABC School' };
    render(<BrandLogo />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/media/abc.png');
    expect(screen.getByRole('img')).toHaveAccessibleName('ABC School logo');
  });

  it('prefers the letterhead lockup on a printed page', () => {
    branding = { logo_primary: '/media/mark.png',
                 logo_letterhead: '/media/wide.png',
                 display_name: 'ABC School' };
    render(<BrandLogo variant="letterhead" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/media/wide.png');
  });

  it('falls through to the one logo a customer did upload', () => {
    // Most customers upload one image and expect it everywhere. A letterhead
    // slot that renders empty because they did not fill it separately is a
    // document that goes out with no logo at all.
    branding = { logo_primary: '/media/mark.png', display_name: 'ABC School' };
    render(<BrandLogo variant="letterhead" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/media/mark.png');
  });

  it('can be decorative where a heading already carries the name', () => {
    branding = { logo_primary: '/media/abc.png', display_name: 'ABC School' };
    render(<BrandLogo alt="" />);
    expect(screen.getByAltText('')).toBeInTheDocument();
  });

  it("shows the customer's own initials, never the platform's logo, before they upload one", () => {
    branding = { display_name: 'ABC School', color_primary: '#7C3AED' };
    render(<BrandLogo />);
    const mark = screen.getByRole('img');
    expect(mark).toHaveTextContent('AS');
    expect(mark).toHaveAccessibleName('ABC School logo');
    expect(mark.tagName).toBe('SPAN');
  });
});
