import React from 'react';
import { NavLink, Link } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { roleLabel } from '../../services/roles';
import { useNavContext } from '../../hooks/useNavContext';
import { X } from 'lucide-react';
import NavIcon from './NavIcon';
import UserAvatar from '../common/UserAvatar.jsx';

/**
 * The contextual rail (Phase E / blueprint §06).
 *
 * Shows the workspace when you are in the workspace, and one module's menu when
 * you are inside a module: 8 links instead of 76, and 9 inside a module.
 *
 * No accordion, no persisted open/closed state. The old rail's shape depended
 * on which sections you last expanded, so the same URL looked different to
 * different people; this one is a pure function of the route.
 */


/* The initials tile's fill. A gradient rather than a flat block of brand blue,
   which was the single most placeholder-looking element on the rail — and it
   lives here rather than in the stylesheet because Avatar writes `background`
   inline for the fallback tile, so CSS would lose. */
const AVATAR_GRADIENT = 'linear-gradient(140deg, #5b7cf0 0%, #3355c9 58%, #274095 100%)';

const AppSidebar = ({ open = false, onClose }) => {
  const { user, role } = useAuth();
  const { context, items, counts, isWorkspace } = useNavContext();

  // Close the drawer when a LINK is used, not when the rail itself is clicked.
  const handleClick = (e) => { if (e.target.closest('a')) onClose?.(); };

  return (
    <nav
      id="app-sidebar"
      className={`sidebar ${open ? 'open' : ''}`}
      aria-label={isWorkspace ? 'Main navigation' : `${context.title} navigation`}
      onClick={handleClick}
    >
      <button type="button" className="sb-close" aria-label="Close menu" onClick={onClose}>
        <X size={20} strokeWidth={2.25} aria-hidden="true" />
      </button>

      {!isWorkspace && (
        <div className="sb-ctx">
          <Link to={context.back.to} className="sb-back">{context.back.label}</Link>
          <p className="sb-ctx-title">{context.title}</p>
        </div>
      )}

      <ul className="sb-items">
        {items.map((item) => {
          if (item.group) {
            return <li key={item.group} className="sb-grp" aria-hidden="true">{item.group}</li>;
          }
          const n = item.badge ? item.badge(counts) : null;
          return (
            <li key={item.key}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}
              >
                {/* Every entry, not just the workspace rail. Module menus were
                    text-only, so the same destination had an icon at workspace
                    level and none one click deeper — the rail appeared to lose
                    its iconography as you went further in. */}
                <span className="sb-ico" aria-hidden="true">
                  {/* 18px on the 24px .sb-ico box (Phase HOME-POLISH): the
                      rail's glyphs sit a step under its 14px labels. */}
                  <NavIcon name={item.icon} size={18} />
                </span>
                <span className="sb-item-label">{item.label}</span>
                {n ? (
                  <span className={`sb-badge${item.quiet ? ' is-quiet' : ''}`}>
                    {n}
                    {/* The number alone reads as decoration to a screen reader. */}
                    <span className="sr-only"> waiting</span>
                  </span>
                ) : null}
              </NavLink>
            </li>
          );
        })}
      </ul>

      {/*
        * Phase E1. The "Use the classic menu" escape is gone: the contextual
        * rail is the navigation now, not a trial of one. What is left is the
        * identity block alone, so the footer reads as a finished panel rather
        * than as a control with a caption under it.
        *
        * Role and department sit on one line separated by a middot, and the
        * department half is dropped rather than rendered empty - `department`
        * is nullable on the profile, and " · " hanging off a role is exactly
        * the kind of debris a footer this small cannot hide.
        */}
      <div className="sb-foot">
        {/* The whole identity block is ONE link to the profile (Phase
            UX-PRODUCTION-FINAL-IMPLEMENTATION). Avatar, name and role are all
            inside it rather than being three separate links: three tab stops
            and three tap targets for one destination is worse than one, and
            people click whichever part their eye landed on.

            /me, not /profile: the hub carries attendance, leave, assets,
            tasks and appraisal, and links on to /profile for account details.
            /profile alone is the account form, which is not what somebody
            clicking their own name is looking for. */}
        <Link to="/me" className="sb-foot-who">
          <span className="sb-foot-av" aria-hidden="true">
            {/* THE SAME AVATAR THE HEADER SHOWS (Phase
                OMS-USER-AVATAR-CONSISTENCY). This was `{user?.initials}` in a
                styled span — its own copy of the identity — so a user with a
                profile photo saw their face in the header and "DM" here, on
                the same screen.

                The gradient is passed as `color` rather than set in CSS
                because Avatar writes the fallback background inline, where a
                stylesheet cannot reach it; the ring and the radius are still
                CSS, since those it does not touch. */}
            <UserAvatar className="sb-foot-av-img" size={40} radius="12px"
                        fontSize={15} color={AVATAR_GRADIENT} />
          </span>
          <span className="sb-foot-txt">
            <strong>{user?.full_name || user?.username || 'User'}</strong>
            {/* Role and department as separate chips rather than one
                "Employee · ENG" string (Phase
                OMS-NAVIGATION-PREMIUM-ICON-UPGRADE). They are two different
                facts — what you may do, and where you sit — and the middle dot
                made them read as one long muted line that truncated from the
                wrong end on a narrow rail. Department is omitted entirely when
                unset instead of leaving a trailing separator. */}
            <span className="sb-foot-tags">
              <span className={`sb-tag is-role is-role-${role}`} data-role={role}>{roleLabel(role)}</span>
              {user?.department && (
                <span className="sb-tag is-dept">{user.department}</span>
              )}
            </span>
            {/* THE PRESENCE INDICATOR IS NOW LABELLED, and there is only one
                of it (Phase OMS-SIDEBAR-FOOTER-ENTERPRISE).
 
                It used to be an unlabelled dot on the avatar's corner. A bare
                green dot is a convention, not a statement — people read it as
                anything from "online" to "unread" to "saved" — and pairing it
                with the word costs one line and removes the guess. Keeping
                BOTH would have been the same fact twice in a 280px rail.
 
                What it actually means: this session is signed in. The system
                has no presence service, so it is not a claim about whether
                anyone else can see you. That is honest for the person reading
                their own footer, and it is why nothing anywhere else renders
                this for OTHER users. */}
            <span className="sb-foot-status">
              <span className="sb-foot-dot" aria-hidden="true" />
              Online
            </span>
          </span>
        </Link>
      </div>
    </nav>
  );
};

export default AppSidebar;
