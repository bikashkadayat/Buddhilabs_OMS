import React from 'react';
import NavIcon from './NavIcon';
import UserAvatar from '../common/UserAvatar.jsx';
import { NavLink, useLocation } from 'react-router-dom';
import { useWorkQueue } from '../../hooks/useWorkQueue';

/**
 * The mobile bottom bar (Phase F).
 *
 * Five fixed tabs, never scrolling, never role-dependent in POSITION - a tab
 * that moves between users defeats the muscle memory that makes a bottom bar
 * worth having. Role only affects what a tab's destination contains.
 *
 * Create and Search are overlays rather than routes: they open over whatever
 * you were looking at and give it back when dismissed. Home, Queue and Profile
 * are real navigation.
 *
 * The bar owns the bottom safe area, so pages beneath it never need to know
 * about the notch; `.main` gets the padding in index.css.
 */
const TABS = [
  {
    key: 'home', label: 'Home', to: '/', end: true, icon: 'home',
  },
  {
    key: 'queue', label: 'Queue', to: '/queue', badge: true, icon: 'queue',
  },
  {
    key: 'create', label: 'Create', action: 'create', mid: true, icon: 'create',
  },
  {
    key: 'search', label: 'Search', action: 'search', icon: 'search',
  },
  {
    key: 'profile', label: 'Profile', to: '/me', icon: 'profile',
  },
];

/**
 * The tab glyph comes from the shared registry, so Home and Queue here are the
 * same drawings as Home and My Work Queue in the sidebar. They used to be
 * separate hand-written paths, which is why the two surfaces had quietly
 * different house and checklist shapes.
 *
 * The centre Create tab stays larger: that is a touch-target decision, and the
 * one size override NavIcon allows.
 */
const Glyph = ({ name, mid }) => {
  /* The Profile tab shows the PERSON, not a generic person icon (Phase
     OMS-USER-AVATAR-CONSISTENCY). On mobile this tab is the main way back to
     your own account, and a stock silhouette there while the header a few
     pixels above shows your face is the same split identity this phase
     removed from the sidebar. Falls back to initials on its own, since that
     is what UserAvatar does when there is no photo. */
  if (name === 'profile') {
    return <UserAvatar className="mtb-av" size={22} radius="7px" fontSize={9} />;
  }
  return <NavIcon name={name} size={mid ? 24 : 20} />;
};

const MobileTabBar = ({ onCreate, onSearch }) => {
  const { pathname } = useLocation();
  const { counts } = useWorkQueue({ enabled: false });
  const pending = counts?.total || 0;

  return (
    <nav className="mtb" aria-label="Primary">
      <ul className="mtb-list">
        {TABS.map((t) => {
          const content = (
            <>
              <span className="mtb-ico">
                <Glyph name={t.icon} mid={t.mid} />
              </span>
              {/* A sibling of the icon, not a child: nested inside a 20px span
                  the badge overhung its own parent, which the QA probe flags.
                  It is positioned against the 54px tab instead. */}
              {t.badge && pending > 0 && (
                <span className="mtb-dot" aria-hidden="true">{pending > 9 ? '9+' : pending}</span>
              )}
              <span className="mtb-label">{t.label}</span>
              {t.badge && pending > 0 && (
                <span className="sr-only">, {pending} waiting</span>
              )}
            </>
          );

          if (t.action) {
            return (
              <li key={t.key}>
                <button
                  type="button"
                  className={`mtb-tab${t.mid ? ' is-mid' : ''}`}
                  onClick={t.action === 'create' ? onCreate : onSearch}
                  data-tour={t.action === 'search' ? 'search' : 'create'}
                  aria-haspopup="dialog"
                >
                  {content}
                </button>
              </li>
            );
          }

          return (
            <li key={t.key}>
              <NavLink
                to={t.to}
                end={t.end}
                className={({ isActive }) => `mtb-tab${isActive ? ' is-on' : ''}`}
                aria-current={
                  (t.end ? pathname === t.to : pathname.startsWith(t.to)) ? 'page' : undefined
                }
              >
                {content}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
};

export default MobileTabBar;
