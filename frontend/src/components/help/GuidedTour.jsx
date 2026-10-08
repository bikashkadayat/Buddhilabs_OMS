import React, { useCallback, useEffect, useLayoutEffect, useState } from 'react';

import api from '../../services/api';
import { useAuth } from '../../hooks/useAuth';
import { tourFor } from '../../services/tours';

/**
 * The first-login tour: four to six stops, pointing at the real controls.
 *
 * ONE TOUR PER KIND OF PERSON. An employee needs to find Check in; a manager
 * needs to find their team and their approvals; an administrator needs setup;
 * a platform operator needs payments and customers. Each stop points at the
 * actual element (a `data-tour` attribute), so the tour cannot describe a
 * control that isn't there -- a stop whose target is not on screen (a phone
 * layout, a role without that control) is simply skipped.
 *
 * Shown once, remembered on the server (`ui_state.tours_done`), so a new
 * phone does not replay it. "Take the tour again" in Help restarts it.
 */
export const TOURS = { // eslint-disable-line react-refresh/only-export-components -- read by the anchor guard test
  employee: [
    { target: 'attendance', title: 'Your day starts here', body: 'Check in and out with one button. Your working time and location show here.' },
    { target: 'create', title: 'Start anything', body: 'Create a task, apply for leave or write a memo from here.' },
    { target: 'search', title: 'Find anything', body: 'Press Ctrl+K to search people, tasks, leave, documents and pages.' },
    { target: 'notifications', title: 'Stay up to date', body: 'Approvals, decisions and reminders arrive here.' },
    { target: 'account', title: 'Your account', body: 'Your profile, password, leave and attendance summaries — and Help.' },
  ],
  manager: [
    { target: 'attendance', title: 'Your own attendance', body: 'Check in and out here, like everyone else.' },
    { target: 'team', title: 'Your team today', body: 'Who’s in, late, on leave or absent — and the team dashboard for detail.' },
    { target: 'reviews', title: 'Things waiting for you', body: 'Leave, task and document approvals. Open your queue to act on them.' },
    { target: 'search', title: 'Find anything', body: 'Ctrl+K finds people, your team’s attendance and leave, tasks and documents.' },
    { target: 'account', title: 'Your account and help', body: 'Profile, security, preferences — and Help when you need it.' },
  ],
  admin: [
    { target: 'setup', title: 'Set up your workspace', body: 'These steps tick themselves as you go. Most take a minute.' },
    { target: 'attendance', title: 'Attendance', body: 'You check in here too. Office hours are set in Settings → Attendance rules.' },
    { target: 'admin-actions', title: 'Run your organization', body: 'Users, departments, rules, reports and settings — one click each.' },
    { target: 'search', title: 'Find anything', body: 'Ctrl+K reaches every page and setting by name.' },
    { target: 'account', title: 'Help is always here', body: 'Your account menu has Help: how-tos, answers, and a direct line to us.' },
  ],
  platform: [
    { target: 'pf-revenue', title: 'The business at a glance', body: 'Cash collected, recurring revenue and payments waiting for you.' },
    { target: 'pf-payments', title: 'Approve payments here', body: 'Read the receipt, approve or reject — the subscription follows.' },
    { target: 'pf-customers', title: 'Customer health', body: 'Who isn’t using the product, who has no staff yet, who’s about to expire.' },
    { target: 'pf-search', title: 'Find a customer', body: 'Search any organization by name or address.' },
  ],
};



// The first VISIBLE element with this name: the header search on a desktop,
// the bottom-bar search on a phone.
const visibleTarget = (name) => Array.from(document.querySelectorAll(`[data-tour="${name}"]`))
  .find((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }) || null;

const GuidedTour = () => {
  const { user, role, refreshUser } = useAuth();
  const key = tourFor(user, role);
  const [steps, setSteps] = useState(null);       // null = not running
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState(null);

  const start = useCallback(() => {
    const available = (TOURS[key] || []).filter((s) => visibleTarget(s.target));
    if (available.length) { setSteps(available); setIndex(0); }
  }, [key]);

  // First sign-in: once the page has painted, if this tour isn't done.
  useEffect(() => {
    if (!user || user.must_change_password) return undefined;
    const done = user.ui_state?.tours_done || [];
    if (done.includes(key)) return undefined;
    const t = setTimeout(start, 1500);
    return () => clearTimeout(t);
  }, [user, key, start]);

  useEffect(() => {
    const again = () => start();
    window.addEventListener('tour:start', again);
    return () => window.removeEventListener('tour:start', again);
  }, [start]);

  const step = steps?.[index];

  useLayoutEffect(() => {
    if (!step) return undefined;
    const el = visibleTarget(step.target);
    el?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    const measure = () => setRect(el ? el.getBoundingClientRect() : null);
    const frame = requestAnimationFrame(measure);
    const t = setTimeout(measure, 350);           // after the smooth scroll
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(t);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [step]);

  const finish = useCallback(async () => {
    setSteps(null);
    try {
      await api.post('/profile/me/tour/', { tour: key });
      refreshUser?.();
    } catch { /* it will simply be offered again next time */ }
  }, [key, refreshUser]);

  useEffect(() => {
    if (!step) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') finish();
      if (e.key === 'ArrowRight') setIndex((i) => Math.min(i + 1, steps.length - 1));
      if (e.key === 'ArrowLeft') setIndex((i) => Math.max(i - 1, 0));
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [step, steps, finish]);

  if (!step) return null;
  const last = index === steps.length - 1;
  const pad = 8;
  const spot = rect && {
    top: rect.top - pad, left: rect.left - pad,
    width: rect.width + pad * 2, height: rect.height + pad * 2,
  };
  // Below the target if there is room, otherwise above it.
  const below = !rect || rect.bottom + 220 < window.innerHeight;
  const cardStyle = rect ? {
    top: below ? rect.bottom + 16 : Math.max(12, rect.top - 16),
    left: Math.min(Math.max(12, rect.left), window.innerWidth - 352),
    transform: below ? undefined : 'translateY(-100%)',
  } : { top: '30%', left: '50%', transform: 'translateX(-50%)' };

  return (
    <div className="gt" role="dialog" aria-modal="true" aria-labelledby="gt-title">
      {spot ? <div className="gt-spot" style={spot} /> : <div className="gt-dim" />}
      <div className="gt-card" style={cardStyle}>
        <p className="gt-count">{index + 1} of {steps.length}</p>
        <h2 id="gt-title" className="gt-title">{step.title}</h2>
        <p className="gt-body">{step.body}</p>
        <div className="gt-actions">
          <button type="button" className="gt-skip" onClick={finish}>Skip tour</button>
          <span>
            {index > 0 && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setIndex(index - 1)}>Back</button>
            )}
            <button type="button" className="btn btn-primary btn-sm" autoFocus
                    onClick={() => (last ? finish() : setIndex(index + 1))}>
              {last ? 'Done' : 'Next'}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
};

export default GuidedTour;
