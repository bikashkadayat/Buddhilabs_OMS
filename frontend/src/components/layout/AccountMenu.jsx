import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ChevronDown, User as UserIcon, ShieldCheck, MonitorSmartphone, Activity,
  CalendarCheck, Palmtree, SlidersHorizontal, LogOut, LifeBuoy, Rocket,
} from 'lucide-react';

import UserAvatar from '../common/UserAvatar.jsx';
import { supportService } from '../../services/supportService';

/**
 * The account menu behind the avatar.
 *
 * A REVERSAL, RECORDED. The avatar used to be a plain link to /me, on the
 * reasoning that the profile page already carried everything. In practice
 * that hid Sign out behind a page load on a phone, and left security,
 * sessions and preferences with no entry point at all. Every product people
 * already use opens a menu from the avatar; this one now does too, the same
 * way at every width.
 */
const ITEMS = [
  { to: '/me', label: 'My profile', icon: UserIcon },
  { to: '/profile#security', label: 'Security', icon: ShieldCheck },
  { to: '/notifications', label: 'Recent activity', icon: Activity },
  { to: '/my-attendance', label: 'Attendance summary', icon: CalendarCheck },
  { to: '/leave/balance', label: 'Leave summary', icon: Palmtree },
  { to: '/profile#sessions', label: 'Sessions', icon: MonitorSmartphone },
  { to: '/notifications?tab=prefs', label: 'Preferences', icon: SlidersHorizontal },
  { to: '/help', label: 'Help & support', icon: LifeBuoy },
];

const AccountMenu = ({ user, roleLabel, isAdmin = false, compact = false, onSignOut }) => {
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(0);
  const ref = useRef(null);

  // Fetched when the menu opens, not on every page: a dot that costs a
  // request per navigation is not worth it.
  useEffect(() => {
    if (!open) return;
    supportService.badges()
      .then((d) => setHelp((d?.tickets_unread || 0) + (d?.updates_unseen || 0)))
      .catch(() => {});
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', key);
    };
  }, [open]);

  const name = user?.full_name || user?.username || 'You';
  const detail = [roleLabel, user?.department].filter(Boolean).join(' · ');

  return (
    <div className="am" ref={ref}>
      <button type="button" className="hd-user hd-user-btn am-trigger" data-tour="account"
              aria-haspopup="menu" aria-expanded={open}
              aria-label="Your account"
              onClick={() => setOpen((o) => !o)}>
        <UserAvatar className="hd-av"
                    size={compact ? 36 : 40} radius={compact ? '10px' : '12px'}
                    fontSize={compact ? 13 : 14} />
        <div className="hd-user-id">
          <div className="hd-user-name">{name}</div>
          <div className="hd-user-meta">{user?.email || ''} · {roleLabel}</div>
        </div>
        {!compact && <ChevronDown size={16} aria-hidden="true" className="am-chev" />}
      </button>

      {open && (
        <div className="am-menu" role="menu" aria-label="Account">
          <div className="am-head">
            <UserAvatar size={44} radius="12px" fontSize={15} />
            <div className="am-head-id">
              <strong>{name}</strong>
              <span>{user?.email}</span>
              {detail && <small>{detail}</small>}
            </div>
          </div>
          {isAdmin && (
            <Link to="/getting-started" role="menuitem" className="am-item"
                  onClick={() => setOpen(false)}>
              <Rocket size={16} aria-hidden="true" />
              Getting started
            </Link>
          )}
          {ITEMS.map(({ to, label, icon: Icon }) => (
            <Link key={to} to={to} role="menuitem" className="am-item"
                  onClick={() => setOpen(false)}>
              <Icon size={16} aria-hidden="true" />
              {label}
              {to === '/help' && help > 0 && (
                <span className="am-new" aria-label={`${help} new`}>{help} new</span>
              )}
            </Link>
          ))}
          <button type="button" role="menuitem" className="am-item am-out"
                  onClick={() => { setOpen(false); onSignOut(); }}>
            <LogOut size={16} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
};

export default AccountMenu;
