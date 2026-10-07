import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Bell, ChevronDown, LogOut, Moon, Search, Shield, Sun, User as UserIcon,
  ClipboardList, Settings as SettingsIcon, MonitorSmartphone,
} from 'lucide-react';

import { PLATFORM_MARK, PLATFORM_NAME } from '../../config/platform';
import { useAuth } from '../../hooks/useAuth';
import PlatformNotifications from './PlatformNotifications';
import UserAvatar from '../common/UserAvatar';

/**
 * The console's top bar: who you are, what needs you, and the way out.
 *
 * WHAT WAS WRONG BEFORE. The console had a sidebar and nothing else. An
 * operator's own identity appeared once, as an email address in grey text at
 * the bottom of the rail, next to a sign-out button — so there was no way to
 * reach your own profile, change your own password, or see what you had
 * done. The person with the most power in the product had the least account
 * surface of anybody using it.
 *
 * ALWAYS THE PLATFORM'S BRAND. `BrandingProvider` excludes platform staff
 * deliberately, so nothing here can pick up a tenant's colours — an operator
 * looking at a customer's branding is how somebody suspends the wrong
 * organization.
 */
const useDismiss = (onDismiss) => {
  const ref = useRef(null);
  useEffect(() => {
    const away = (event) => {
      if (ref.current && !ref.current.contains(event.target)) onDismiss();
    };
    // Escape as well as a click. A menu that can only be closed by clicking
    // elsewhere is a menu that traps a keyboard user.
    const key = (event) => { if (event.key === 'Escape') onDismiss(); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', key);
    };
  }, [onDismiss]);
  return ref;
};

const THEME_KEY = 'platform:theme';

const PlatformTopbar = ({ onMenu, theme: controlled, onTheme }) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [ownTheme, setOwnTheme] = useState(() => {
    try { return localStorage.getItem(THEME_KEY) || 'light'; } catch { return 'light'; }
  });
  const theme = controlled ?? ownTheme;
  const setTheme = (next) => {
    const value = typeof next === 'function' ? next(theme) : next;
    setOwnTheme(value);
    if (onTheme) onTheme(value);
  };

  const menuRef = useDismiss(() => setMenuOpen(false));
  const bellRef = useDismiss(() => setBellOpen(false));

  // SCOPED TO THE CONSOLE, not the whole product.
  //
  // A dark palette for the tenant application would mean auditing several
  // hundred components, and a half-converted dark mode is worse than none:
  // the pages nobody checked come out as white panels in a dark frame. The
  // console is one shell and ten pages built on the `pf-` layer, so it can
  // be done properly — and it is the surface an operator keeps open all day.
  //
  // The attribute goes on the shell, so `.pf-shell[data-theme="dark"]` in
  // the stylesheet is the whole implementation and nothing outside the
  // console can be affected by it.
  useEffect(() => {
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }, [theme]);

  const signOut = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <header className="pf-top">
      <button type="button" className="pf-top-menu" onClick={onMenu}
              aria-label="Open navigation">
        <span /><span /><span />
      </button>

      <Link to="/platform" className="pf-top-brand">
        <img src={PLATFORM_MARK} alt="" className="pf-top-mark" />
        <span className="pf-top-names">
          <strong>{PLATFORM_NAME}</strong>
          <small>Platform Console</small>
        </span>
      </Link>

      <form data-tour="pf-search"
        className="pf-top-search"
        onSubmit={(event) => {
          event.preventDefault();
          const term = new FormData(event.currentTarget).get('q');
          // Organizations is the only list worth searching from here —
          // everything else in this console is reached through one.
          navigate(`/platform/organizations?search=${encodeURIComponent(term || '')}`);
        }}
      >
        <Search size={15} aria-hidden="true" />
        <input name="q" type="search" placeholder="Search organizations…"
               aria-label="Search organizations" />
      </form>

      <div className="pf-top-actions">
        <div className="pf-top-pop" ref={bellRef}>
          <button type="button" className="pf-top-btn" aria-haspopup="true"
                  aria-expanded={bellOpen}
                  onClick={() => { setBellOpen((o) => !o); setMenuOpen(false); }}>
            <Bell size={17} aria-hidden="true" />
            <span className="sr-only">Notifications</span>
          </button>
          {bellOpen && <PlatformNotifications onClose={() => setBellOpen(false)} />}
        </div>

        <button type="button" className="pf-top-btn"
                onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
                aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
          {theme === 'dark' ? <Sun size={17} aria-hidden="true" />
                            : <Moon size={17} aria-hidden="true" />}
        </button>

        <div className="pf-top-pop" ref={menuRef}>
          <button type="button" className="pf-top-you" aria-haspopup="true"
                  aria-expanded={menuOpen}
                  onClick={() => { setMenuOpen((o) => !o); setBellOpen(false); }}>
            {/* The shared avatar for the signed-in user: their photo where
                they have uploaded one, so the console and their profile page
                cannot show two different faces. */}
            <UserAvatar size={28} className="pf-avatar" color="var(--brand-blue)" />
            <span className="pf-top-you-name">{user?.name || user?.email}</span>
            <ChevronDown size={14} aria-hidden="true" />
          </button>
          {menuOpen && (
            <div className="pf-menu" role="menu">
              <div className="pf-menu-head">
                <UserAvatar size={40} className="pf-avatar pf-avatar-lg" color="var(--brand-blue)" />
                <div>
                  <strong>{user?.name || 'Platform operator'}</strong>
                  <small>{user?.email}</small>
                  <span className="pf-badge-role">
                    <Shield size={11} aria-hidden="true" /> Platform staff
                  </span>
                </div>
              </div>
              <Link className="pf-menu-item" role="menuitem" to="/platform/profile"
                    onClick={() => setMenuOpen(false)}>
                <UserIcon size={15} aria-hidden="true" /> My profile
              </Link>
              <Link className="pf-menu-item" role="menuitem" to="/platform/profile#security"
                    onClick={() => setMenuOpen(false)}>
                <Shield size={15} aria-hidden="true" /> Security &amp; password
              </Link>
              <Link className="pf-menu-item" role="menuitem" to="/platform/profile#sessions"
                    onClick={() => setMenuOpen(false)}>
                <MonitorSmartphone size={15} aria-hidden="true" /> Sessions
              </Link>
              <Link className="pf-menu-item" role="menuitem" to="/platform/audit"
                    onClick={() => setMenuOpen(false)}>
                <ClipboardList size={15} aria-hidden="true" /> Activity log
              </Link>
              <Link className="pf-menu-item" role="menuitem" to="/platform/settings"
                    onClick={() => setMenuOpen(false)}>
                <SettingsIcon size={15} aria-hidden="true" /> Platform settings
              </Link>
              <button type="button" className="pf-menu-item pf-menu-out"
                      role="menuitem" onClick={signOut}>
                <LogOut size={15} aria-hidden="true" /> Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default PlatformTopbar;
