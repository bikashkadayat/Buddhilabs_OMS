import React, { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  Megaphone,
  Activity, Building2, ClipboardList, CreditCard, Wallet, Gauge, Globe, HeartPulse, LifeBuoy,
  LayoutDashboard, LogOut, Receipt, SlidersHorizontal, Tags,
} from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import PlatformTopbar from './PlatformTopbar';
import GuidedTour from '../help/GuidedTour';

/**
 * The Platform Console shell (Phase S6 Part 1).
 *
 * DELIBERATELY NOT THE TENANT LAYOUT. It shares no sidebar, no header, no
 * create-sheet and no command palette with the product, because every one of
 * those is built around "the organization I am in" and an operator is in none.
 * Reusing Layout would have meant a nav full of items that 404 and a search
 * palette querying tenant endpoints.
 *
 * The dark chrome is a safety feature rather than a style choice: an operator
 * with both open must never have to read the URL to know which one they are
 * typing into.
 */
// ORDERED BY HOW OFTEN AN OPERATOR OPENS IT, not by the brief's listing order:
// customers first, then what they owe, then the platform's own state. Platform
// metrics is the Dashboard -- a second page of the same figures would be two
// screens to keep in agreement, so the dashboard IS that page and the nav says
// so rather than linking the same route twice under two names.
// TEN ENTRIES, GROUPED, AND LAUNCH READINESS IS NO LONGER ONE OF THEM.
//
// It is the platform's own pre-flight check, opened at deploy time and
// almost never afterwards -- a permanent rail entry for something consulted
// twice a year is how a rail stops being scannable. It now lives on the
// Platform health page, which is where somebody already goes to ask "is the
// platform all right".
//
// Grouped rather than flat: ten undifferentiated links read as a list to be
// searched. Three groups of three or four read as a place with rooms in it.
const NAV = [
  {
    heading: null,
    items: [
      { to: '/platform', end: true, label: 'Dashboard', Icon: LayoutDashboard },
    ],
  },
  {
    heading: 'Customers',
    items: [
      { to: '/platform/organizations', label: 'Organizations', Icon: Building2 },
      { to: '/platform/subscriptions', label: 'Subscriptions', Icon: Receipt },
      { to: '/platform/payments', label: 'Payments', Icon: CreditCard },
      { to: '/platform/payment-methods', label: 'Payment methods', Icon: Wallet },
      { to: '/platform/domains', label: 'Domains', Icon: Globe },
      { to: '/platform/customer-health', label: 'Customer success', Icon: HeartPulse },
      { to: '/platform/support', label: 'Support', Icon: LifeBuoy },
    ],
  },
  {
    heading: 'Platform',
    items: [
      { to: '/platform/plans', label: 'Plans', Icon: Tags },
      { to: '/platform/usage', label: 'Usage', Icon: Gauge },
      { to: '/platform/health', label: 'Health', Icon: Activity },
      { to: '/platform/updates', label: 'Updates & status', Icon: Megaphone },
      { to: '/platform/audit', label: 'Audit', Icon: ClipboardList },
      { to: '/platform/settings', label: 'Settings', Icon: SlidersHorizontal },
    ],
  },
];

const PlatformLayout = () => {
  const { logout } = useAuth();
  const [railOpen, setRailOpen] = useState(false);
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('platform:theme') || 'light'; }
    catch { return 'light'; }
  });
  const navigate = useNavigate();

  const signOut = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="pf-shell" data-theme={theme}>
      <PlatformTopbar onMenu={() => setRailOpen((o) => !o)} theme={theme}
                      onTheme={setTheme} />
      <GuidedTour />
      <div className="pf-body">
      {/* The rail is a drawer below 1024px. An operator reviewing payments
          from a tablet should not lose a third of the screen to navigation
          they are not using. */}
      {railOpen && (
        <button type="button" className="pf-scrim" aria-label="Close navigation"
                onClick={() => setRailOpen(false)} />
      )}
      <aside className={`pf-side${railOpen ? ' is-open' : ''}`}>
        {/* THE CONSOLE WEARS THE PLATFORM'S BRAND, NEVER A TENANT'S.
            `BrandingProvider` deliberately excludes platform staff for the
            same reason: an operator looking at a customer's colours is how
            somebody suspends the wrong organization. This is the one surface
            in the product that is always Buddhi Labs. */}
        <nav className="pf-nav" aria-label="Platform console">
          {NAV.map((group) => (
            <div className="pf-nav-group" key={group.heading || 'top'}>
              {group.heading && (
                <p className="pf-nav-heading">{group.heading}</p>
              )}
              {group.items.map(({ to, end, label, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  onClick={() => setRailOpen(false)}
                  className={({ isActive }) => `pf-nav-item${isActive ? ' is-on' : ''}`}
                  data-tour={to === '/platform/payments' ? 'pf-payments' : to === '/platform/customer-health' ? 'pf-customers' : undefined}
                >
                  <Icon size={16} aria-hidden="true" />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        {/* The identity block moved to the top bar, where an operator can
            reach their own profile and password. What stays here is the one
            thing a rail is good at: a permanent way out. */}
        <div className="pf-side-foot">
          <button type="button" className="pf-signout" onClick={signOut}>
            <LogOut size={14} aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>
      <main className="pf-main">
        <Outlet />
      </main>
      </div>
    </div>
  );
};

export default PlatformLayout;
