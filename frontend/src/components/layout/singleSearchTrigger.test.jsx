import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Layout from './Layout';

/**
 * Phase MOBILE-NAVIGATION-CLEANUP.
 *
 * Search had two triggers below 1024px: the header magnifier and the Search
 * tab in the bottom bar — two controls for one action, on the bar with the
 * least room for either.
 *
 * The cause was two breakpoints that had drifted. The tab bar mounts from
 * `useIsMobile` (max-width: 1023px); the header button had its own CSS rule at
 * max-width: 900px which only made it icon-only. Across 901–1023px both were
 * fully visible. The header button is now gated on the SAME hook the tab bar
 * mounts from, so the two cannot disagree again — and this test asserts the
 * count rather than the mechanism, so it holds however that is implemented.
 */
let mobile = true;
vi.mock('../../hooks/useIsMobile', () => ({
  useIsMobile: () => mobile,
  MOBILE_MQ: '(max-width: 1023px)',
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { full_name: 'Bikash Kadayat', initials: 'BK', email: 'b@nif.org.np' },
    role: 'maker',
    logout: vi.fn(),
  }),
}));
vi.mock('../../hooks/useNavContext', () => ({
  useNavContext: () => ({
    context: { key: 'workspace', title: 'Workspace' },
    items: [], counts: {}, isWorkspace: true,
  }),
  default: () => ({
    context: { key: 'workspace', title: 'Workspace' },
    items: [], counts: {}, isWorkspace: true,
  }),
}));

const mount = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}><Layout /></MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  mobile = true;
  window.matchMedia = window.matchMedia || ((query) => ({
    matches: false, media: query,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null,
    dispatchEvent: () => false,
  }));
});

const triggers = () => screen.queryAllByRole('button', { name: /search/i });

describe('search has exactly one trigger', () => {
  it('offers only the bottom tab below the desktop breakpoint', () => {
    mobile = true;
    const { container } = mount();
    expect(triggers()).toHaveLength(1);
    // …and it is the tab, not the header magnifier.
    expect(container.querySelector('.hd-search')).toBeNull();
    expect(container.querySelector('.mtb')).toBeInTheDocument();
  });

  it('offers only the header magnifier on desktop', () => {
    mobile = false;
    const { container } = mount();
    expect(triggers()).toHaveLength(1);
    expect(container.querySelector('.hd-search')).toBeInTheDocument();
    // The tab bar is not mounted at all, so there is nothing to duplicate.
    expect(container.querySelector('.mtb')).toBeNull();
  });

  it('keeps the header free of the search control on mobile', () => {
    // The header is the bar with the least room; this is what the phase was
    // asking for in plain terms.
    mobile = true;
    const { container } = mount();
    const header = container.querySelector('.header');
    expect(header).toBeInTheDocument();
    expect(header.querySelector('.hd-search')).toBeNull();
  });
});
