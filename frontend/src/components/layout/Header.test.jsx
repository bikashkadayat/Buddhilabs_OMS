import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => navigate,
}));

import Header from './Header';

/**
 * The header's account menu (Phase MOBILE-DASHBOARD-V3).
 *
 * At 360px the bar carried a hamburger, a 42px logo, a separator, two lines of
 * branding, search, the bell, an avatar with name and email, AND a Logout
 * button. Logout is the rarest control on it and was taking width from the ones
 * people use every day, so it moved behind the avatar.
 *
 * What is pinned here is the part that could silently go wrong: that signing
 * out is still REACHABLE on a phone. A control that has moved into a menu
 * nobody can open has not been tidied away, it has been lost.
 */

const logout = vi.fn();
let mobile = true;

vi.mock('../../hooks/useAuth.jsx', () => ({
  useAuth: () => ({
    role: 'maker',
    user: { full_name: 'Min Bahadur', email: 'min@nif.org.np', initials: 'MB' },
    logout,
  }),
}));
vi.mock('../../hooks/useIsMobile.js', () => ({ useIsMobile: () => mobile }));
vi.mock('../notifications/NotificationBell.jsx', () => ({ default: () => <div /> }));
vi.mock('../search/GlobalSearch.jsx', () => ({ default: () => <div /> }));
vi.mock('../../services/bsDate.js', () => ({
  todayBS: () => '2082-05-18', msUntilMidnight: () => 10 ** 8,
}));

const wrap = () => render(<MemoryRouter><Header /></MemoryRouter>);

beforeEach(() => { logout.mockClear(); navigate.mockClear(); mobile = true; });

describe('the avatar', () => {
  // It opens the account menu, the same way at every width. (It was briefly a
  // plain link to /me; that left Security, Sessions and Preferences with no
  // way in, and hid Sign out behind a page load on a phone.)
  it.each([true, false])('opens the account menu (phone=%s)', (isPhone) => {
    mobile = isPhone;
    wrap();
    fireEvent.click(screen.getByRole('button', { name: 'Your account' }));
    const menu = screen.getByRole('menu', { name: 'Account' });
    const labels = Array.from(menu.querySelectorAll('[role="menuitem"]'))
      .map((el) => el.textContent.trim());
    expect(labels).toEqual([
      'My profile', 'Security', 'Recent activity', 'Attendance summary',
      'Leave summary', 'Sessions', 'Preferences', 'Help & support', 'Sign out',
    ]);
    expect(screen.getByRole('menuitem', { name: 'My profile' })).toHaveAttribute('href', '/me');
  });

  it('closes on Escape', () => {
    wrap();
    fireEvent.click(screen.getByRole('button', { name: 'Your account' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('shows the name and email beside it on a desktop', () => {
    mobile = false;
    wrap();
    expect(screen.getByText('Min Bahadur')).toBeInTheDocument();
    expect(screen.getAllByText(/min@nif\.org\.np/).length).toBeGreaterThan(0);
  });
});

describe('signing out', () => {
  it.each([true, false])('is in the account menu (phone=%s)', (isPhone) => {
    mobile = isPhone;
    wrap();
    fireEvent.click(screen.getByRole('button', { name: 'Your account' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
    expect(logout).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/login');
  });
});
