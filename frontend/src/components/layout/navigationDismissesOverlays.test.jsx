import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Layout from './Layout';

/**
 * A navigation dismisses transient UI (Phase CRITICAL-NAVIGATION-STATE-FIX).
 *
 * The production bug: Layout owned three overlay states — the drawer, the
 * search palette and the create sheet — and its route-change effect reset only
 * the drawer. The bottom tabs are plain NavLinks with no onClick, so nothing
 * else put the other two away; the palette locks body scroll while mounted, so
 * a stuck overlay also froze the page.
 *
 * The subtle half is the DEPENDENCY. These tests deliberately navigate to the
 * route the user is ALREADY on, because that is the case a `location.pathname`
 * dependency misses: the pathname is identical and only `location.key` moves.
 * A fix keyed on pathname passes every other case here and still strands the
 * overlay on "open search on Home, tap Home".
 */

// Mutable, because the two search triggers now live on OPPOSITE sides of this
// breakpoint (Phase MOBILE-NAVIGATION-CLEANUP) and both still have to be
// exercised — see the note on the describe block below.
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
    items: [],
    counts: {},
    isWorkspace: true,
  }),
  default: () => ({
    context: { key: 'workspace', title: 'Workspace' },
    items: [], counts: {}, isWorkspace: true,
  }),
}));

const mount = (path = '/') => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><Layout /></MemoryRouter>
    </QueryClientProvider>,
  );
};

// The palette renders a dialog; the create sheet renders its own surface.
const searchOpen = () => !!document.querySelector('[role="dialog"]');
const scrollLocked = () => document.body.style.overflow === 'hidden';

// jsdom has no matchMedia; Layout listens on it for the desktop breakpoint.
beforeEach(() => {
  mobile = true;                 // the mobile shell is this file's default
  document.body.style.overflow = '';
  window.matchMedia = window.matchMedia || ((query) => ({
    matches: false, media: query,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null,
    dispatchEvent: () => false,
  }));
});

describe('navigation dismisses the search overlay', () => {
  /**
   * There are still TWO search code paths and both are still exercised — but
   * they no longer coexist. The header magnifier (GlobalSearch, which owns its
   * own state) is DESKTOP ONLY now, and the bottom tab bar (Layout's state) is
   * mobile only: one trigger per viewport, which is what Phase
   * MOBILE-NAVIGATION-CLEANUP was for.
   *
   * That makes the viewport part of each case rather than incidental to it.
   * Both had the same route-change defect, and fixing one alone would still
   * look fixed while leaving the other broken — so each is opened at the width
   * it actually appears at.
   */
  const searchButtons = () =>
    screen.getAllByRole('button', { name: /search/i });
  const headerSearch = () =>
    searchButtons().find((b) => b.className.includes('hd-search'));
  const tabSearch = () =>
    searchButtons().find((b) => !b.className.includes('hd-search'));

  const openVia = (getButton) => {
    fireEvent.click(getButton());
    expect(searchOpen(), 'palette should be open').toBe(true);
  };

  it.each([
    ['tab bar', true, () => tabSearch()],
    ['header magnifier', false, () => headerSearch()],
  ])('%s: closes when the user taps the tab they are already on', (_label, atMobile, getButton) => {
    mobile = atMobile;
    mount('/');
    openVia(getButton);

    const tab = screen.getAllByRole('link').find((a) => /home/i.test(a.textContent))
      || screen.getAllByRole('link')[0];
    fireEvent.click(tab);

    expect(searchOpen(), 'palette must not survive the navigation').toBe(false);
    expect(scrollLocked(), 'body scroll must be restored').toBe(false);
  });

  it('survives being opened and dismissed repeatedly', () => {
    mount('/');
    const links = () => screen.getAllByRole('link');
    for (let i = 0; i < 3; i += 1) {
      openVia(() => tabSearch());
      fireEvent.click(links()[i % links().length]);
      expect(searchOpen()).toBe(false);
    }
    expect(scrollLocked()).toBe(false);
  });
});
