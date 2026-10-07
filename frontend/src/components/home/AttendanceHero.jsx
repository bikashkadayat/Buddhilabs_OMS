import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CheckCircle2, Clock, MapPin, LogIn, LogOut, Sun, Plane, Fingerprint,
} from 'lucide-react';

import { useAttendancePunch } from '../../hooks/useAttendancePunch';
import { fmtDistance } from '../../utils/geo';

/**
 * The top of Home: who you are, where your day stands, and the one button
 * you came for.
 *
 * WHY ATTENDANCE IS HERE. Most people open this system to check in and to
 * check out. That was a widget at the bottom of the page reading "In — · Out
 * — · Absent", and the button itself lived on another screen. Now the first
 * thing on the page says whether you are checked in, how long you have been
 * working and where, and offers exactly the next action -- never both
 * buttons, because only one of them can be right.
 */
const hhmm = (iso) => (iso
  ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  : null);

const duration = (fromIso, toIso) => {
  if (!fromIso) return null;
  const ms = (toIso ? new Date(toIso) : new Date()) - new Date(fromIso);
  if (!(ms > 0)) return null;
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return null;          // the caller says "just now"
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
};

/** Re-render once a minute while somebody is on the clock. */
const useMinuteTick = (active) => {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [active]);
};

const Chip = ({ tone = '', icon, children }) => (
  <span className={`ah-chip${tone ? ` is-${tone}` : ''}`}>
    {icon}
    {children}
  </span>
);

const where = (t) => {
  const office = t.office?.name || 'the office';
  if (t.check_in_within_office === true) return `At ${office}`;
  if (t.check_in_within_office === false && t.check_in_distance_m != null) {
    return `${fmtDistance(t.check_in_distance_m)} from ${office}`;
  }
  return t.check_in_address || null;
};

const AttendanceHero = ({ greeting, name, welcome, dateLine }) => {
  const {
    today: t, isLoading, isError, punch, pending, pendingKind, locating, error,
  } = useAttendancePunch();
  const onClock = Boolean(t?.check_in && !t?.check_out);
  useMinuteTick(onClock);

  let chips = null;
  let action = null;
  let note = null;

  if (isLoading) {
    chips = <Chip>Checking your attendance…</Chip>;
  } else if (isError || !t) {
    chips = null;
  } else if (t.status === 'holiday') {
    chips = <Chip tone="info" icon={<Sun size={14} aria-hidden="true" />}>Today is a holiday</Chip>;
  } else if (t.status === 'on_leave') {
    chips = <Chip tone="info" icon={<Plane size={14} aria-hidden="true" />}>You're on leave today</Chip>;
  } else {
    const place = t.check_in ? where(t) : null;
    chips = (
      <>
        {t.check_in ? (
          <Chip tone={t.status === 'late' || t.status === 'half_day' ? 'warn' : 'good'}
                icon={<CheckCircle2 size={14} aria-hidden="true" />}>
            {t.check_out
              ? `Checked out at ${hhmm(t.check_out)}`
              : `Checked in at ${hhmm(t.check_in)}`}
            {t.status === 'late' ? ' · late' : ''}
            {t.status === 'half_day' ? ' · counts as a half day' : ''}
          </Chip>
        ) : (
          <Chip tone="muted">Not checked in yet</Chip>
        )}
        {t.check_in && (
          <Chip icon={<Clock size={14} aria-hidden="true" />}>
            {t.check_out
              ? `Worked ${duration(t.check_in, t.check_out) || '—'}`
              : `Working ${duration(t.check_in) || 'just now'}`}
          </Chip>
        )}
        {place && <Chip icon={<MapPin size={14} aria-hidden="true" />}>{place}</Chip>}
      </>
    );

    if (t.app_check_in_allowed === false && !t.can_check_out) {
      note = (
        <p className="ah-note">
          <Fingerprint size={14} aria-hidden="true" />
          Your attendance is recorded at the office device.
        </p>
      );
    } else if (t.can_check_in) {
      action = { kind: 'in', label: 'Check in', icon: <LogIn size={20} aria-hidden="true" /> };
      note = (
        <p className="ah-note">
          Office starts at {t.office_start || '10:00'}. Your location is
          recorded when you check in.
        </p>
      );
    } else if (t.can_check_out) {
      action = { kind: 'out', label: 'Check out', icon: <LogOut size={20} aria-hidden="true" /> };
    } else if (t.check_out) {
      note = <p className="ah-note">You're done for today. See you tomorrow.</p>;
    }
  }

  const busyLabel = locating
    ? 'Finding your location…'
    : pendingKind === 'out' ? 'Checking out…' : 'Checking in…';

  return (
    <section className="ah" aria-label="Today" data-tour="attendance">
      <div className="ah-main">
        <h1 className="ah-greet">{greeting}, {name}</h1>
        {welcome && <p className="ah-welcome">{welcome}</p>}
        {chips && <div className="ah-chips" aria-live="polite">{chips}</div>}
        {note}
        {error && <p className="ah-err" role="alert">{error}</p>}
        <p className="ah-date">
          {dateLine}
          {' · '}
          <Link to="/my-attendance" className="ah-link">My attendance</Link>
        </p>
      </div>
      {action && (
        <div className="ah-act">
          <button type="button"
                  className={`ah-btn ${action.kind === 'in' ? 'is-in' : 'is-out'}`}
                  onClick={() => punch(action.kind)} disabled={pending}>
            {action.icon}
            {pending ? busyLabel : action.label}
          </button>
        </div>
      )}
    </section>
  );
};

export default AttendanceHero;
