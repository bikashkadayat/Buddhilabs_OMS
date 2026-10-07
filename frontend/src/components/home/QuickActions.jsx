import React from 'react';
import NavIcon from '../layout/NavIcon';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';
import { memoService } from '../../services/memoService';
import { minuteService } from '../../services/minuteService';
import { circularService } from '../../services/circularService';

/**
 * Start something (Phase 204A, redesigned).
 *
 * Four one-line shortcuts that start the common workflows without opening a
 * module, and three quieter pills under them for the less frequent ones.
 *
 * BADGES COST NO REQUESTS. Every count below comes from a query key the
 * workspace rail has already populated - ['memos','dashboard'] and its
 * siblings - so mounting this adds nothing to the busiest screen in the system.
 * A badge that cannot be read simply does not render.
 *
 * ROLE GATING. roles.js excludes Admin from createMemo and applyLeave, so those
 * shortcuts would lead an Admin to a page they cannot use. Gated with the same
 * can() the sidebar and the mobile Create sheet use. createTask is every role,
 * and gated the same way so the list reads from one source of truth.
 */

/** Module glyphs from the shared navigation registry; the tint is this file's. */
const ACTION_ICONS = {
  task: 'tasks',
  memo: 'memo-create',
  minute: 'minute-create',
  leave: 'leave-apply',
};

/* 18px, the Home tile size. The stroke is NavIcon's and deliberately not
   overridden here: these shortcuts are a navigation surface, and
   navIcons.test pins every surface to the one registry weight. */
const Icon = ({ name }) => (
  <span className={`qa-ico is-${name}`} aria-hidden="true">
    <NavIcon name={ACTION_ICONS[name]} size={18} />
  </span>
);

const QuickActions = () => {
  const { role } = useAuth();
  const opts = { staleTime: 60_000, retry: false };

  const { data: memo } = useQuery({
    queryKey: ['memos', 'dashboard'], queryFn: memoService.getDashboard, ...opts,
  });
  const { data: minute } = useQuery({
    queryKey: ['minutes', 'dashboard'], queryFn: minuteService.getDashboard, ...opts,
  });
  const { data: circular } = useQuery({
    queryKey: ['circulars', 'dashboard'], queryFn: circularService.getDashboard, ...opts,
  });

  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  const primary = [
    { key: 'task', title: 'Create task', to: '/tasks/create', gate: 'createTask' },
    {
      key: 'memo',
      title: 'Create memo',
      to: '/memos/create',
      gate: 'createMemo',
      badge: memo?.drafts ? plural(memo.drafts, 'draft') : null,
    },
    {
      key: 'minute',
      title: 'Create minute',
      to: '/minutes/create',
      badge: minute?.my_drafts ? plural(minute.my_drafts, 'draft') : null,
    },
    {
      key: 'leave',
      title: 'Apply for leave',
      to: '/leave/apply',
      gate: 'applyLeave',
      // No badge: the count of the user's own pending applications is not
      // among the queries Home already holds.
      badge: null,
    },
  ].filter((c) => !c.gate || can(role, c.gate));

  const secondary = [
    {
      key: 'circular',
      title: 'Publish a circular',
      to: '/circulars/create',
      badge: circular?.unread ? `${circular.unread} unread` : null,
    },
    { key: 'asset', title: 'Request an asset', to: '/inventory/requests' },
    { key: 'attendance', title: 'My attendance', to: '/my-attendance' },
  ];

  // An administrator's everyday jobs, one click from Home instead of three
  // menus deep: people, structure, the rules people work under, the numbers.
  const admin = role === 'admin' ? [
    { key: 'users', title: 'Add or manage users', to: '/admin/users' },
    { key: 'departments', title: 'Departments', to: '/admin/leaves/departments' },
    { key: 'attrules', title: 'Attendance rules', to: '/admin/attendance/policies' },
    { key: 'policies', title: 'Leave policies', to: '/admin/leaves/policies' },
    { key: 'reports', title: 'Reports', to: '/reports/overview' },
    { key: 'settings', title: 'Settings', to: '/settings' },
  ] : [];

  if (!primary.length && !secondary.length) return null;

  return (
    <section className="hm-sec qa-sec" aria-labelledby="qa-heading" data-tour="create">
      <div className="hm-sec-h">
        <h2 id="qa-heading">Start something</h2>
      </div>
      {primary.length > 0 && (
        <ul className="qa-grid">
          {primary.map((c) => (
            <li key={c.key}>
              <Link to={c.to} className="hm-card qa-card">
                <Icon name={c.key} />
                <span className="qa-title">{c.title}</span>
                {c.badge && <span className="qa-badge">{c.badge}</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
      {admin.length > 0 && (
        <ul className="qa-pills qa-admin" aria-label="Run your organization" data-tour="admin-actions">
          {admin.map((c) => (
            <li key={c.key}>
              <Link to={c.to} className="qa-pill">{c.title}</Link>
            </li>
          ))}
        </ul>
      )}
      <ul className="qa-pills">
        {secondary.map((c) => (
          <li key={c.key}>
            <Link to={c.to} className="qa-pill">
              {c.title}
              {c.badge && <span className="qa-badge">{c.badge}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
};

export default QuickActions;
