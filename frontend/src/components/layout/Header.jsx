import { Menu } from 'lucide-react';
import BrandLogo from '../branding/BrandLogo';
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth.jsx';
import { ROLE_LABELS } from '../../services/roles';
import { useIsMobile } from '../../hooks/useIsMobile.js';
import NotificationBell from '../notifications/NotificationBell.jsx';
import GlobalSearch from '../search/GlobalSearch.jsx';
import AccountMenu from './AccountMenu.jsx';
import { useBranding } from '../../hooks/useBranding';
import { todayBS, msUntilMidnight } from '../../services/bsDate.js';

const Header = ({ menuOpen = false, onMenu }) => {
  const navigate = useNavigate();
  const { role, user, logout } = useAuth();
  const isMobile = useIsMobile();
  const { name: orgName } = useBranding();


  // Live Nepali (BS) date, refreshed automatically at midnight.
  const [bsDate, setBsDate] = useState(() => todayBS());
  useEffect(() => {
    let timer;
    const tick = () => {
      setBsDate(todayBS());
      timer = setTimeout(tick, msUntilMidnight());
    };
    timer = setTimeout(tick, msUntilMidnight());
    return () => clearTimeout(timer);
  }, []);

  const handleLogout = () => {
    localStorage.setItem('justLoggedOut', 'true');
    logout();
    navigate('/login');
  };

  return (
    <header className="header">

      {/* Mobile-only hamburger (CSS hides it on desktop). Toggles the nav drawer. */}
      <button
        id="menu-hamburger"
        type="button"
        className="hd-hamburger"
        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={menuOpen}
        aria-controls="app-sidebar"
        onClick={onMenu}
      >
        {/* The menu button that opens the rail — navigation chrome, so it
            comes from the same icon family as the rail it opens. */}
        <Menu size={22} strokeWidth={2.25} aria-hidden="true" />
      </button>

      <div className="hd-brand">
        <button
          type="button"
          className="hd-logo hd-logo-btn"
          onClick={() => navigate('/')}
          aria-label="Go to home"
          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
        >
          {/* Height comes from .hd-logo img in CSS, not an inline style: the
              mobile rule shrinks it to 28px, and an inline height would win. */}
          <BrandLogo />
        </button>
        <div className="hd-sep"></div>
        <div className="hd-meta" style={{ display: 'flex', flexDirection: 'column' }}>
          {/* The organization's own name, where "PORTAL SYSTEM" used to sit:
              a label naming the software, in capitals, on every page. */}
          <span className="hd-portal" style={{ fontSize: 'var(--fs-sm)', fontWeight: 650, color: 'var(--text-primary)' }}>{orgName}</span>
          <span className="hd-bs" style={{ fontSize: 'var(--fs-label)', color: 'var(--text-muted)' }}>B.S. {bsDate || '—'}</span>
        </div>
      </div>
      <div className="hd-right" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
        {/* Phase 204. Ctrl/Cmd-K from anywhere; the button is the discoverable
            route in for people who do not know the shortcut exists.
 
            DESKTOP ONLY (Phase MOBILE-NAVIGATION-CLEANUP). Below 1024px the
            bottom tab bar carries Search as one of its five tabs, so the
            header button was a second trigger for the same palette — two
            controls, one action, on the bar with the least room for either.
            Gated on the same `useIsMobile` the tab bar mounts from, not on a
            CSS breakpoint of its own: the two rules had drifted apart (901px
            vs 1023px), which is why both were visible across that whole
            range. One source, so they cannot disagree again. */}
        {!isMobile && <GlobalSearch />}
        <NotificationBell />
        {/* The avatar opens the account menu, at every width. Sign out
            lives there, so it is one tap away on a phone too. */}
        <AccountMenu user={user} roleLabel={ROLE_LABELS[role] || 'Member'} isAdmin={role === 'admin'}
                     compact={isMobile} onSignOut={handleLogout} />
      </div>
    </header>
  );
};

export default Header;
