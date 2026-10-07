import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Building2, CreditCard, Globe, LogIn, UserPlus,
} from 'lucide-react';

import { platformService, money } from '../../services/platformService';

/**
 * "What just happened", across the five things an operator watches.
 *
 * TABS, NOT FIVE CARDS. Five lists of six rows is thirty rows of scrolling on
 * the one page meant to be read at a glance. One card, one list at a time,
 * the tab remembering nothing — the operator opens the dashboard, looks, and
 * goes where the list sends them.
 *
 * Sign-ins are labelled by kind on purpose: the platform sees its own
 * operators' sessions and a new customer's FIRST sign-in, and nothing of what
 * a customer's staff do inside their workspace. A list that did not say so
 * would read as a full login log.
 */
const when = (value) => (value
  ? new Date(value).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
  : '');

const orgLink = (slug, name) => (slug
  ? <Link className="pf-feed-what" to={`/platform/organizations/${slug}`}>{name}</Link>
  : <span className="pf-feed-what">{name}</span>);

const TABS = [
  {
    key: 'organizations', label: 'Organizations', icon: Building2,
    empty: 'No organizations yet. The first one you create appears here.',
    more: { to: '/platform/organizations', label: 'All organizations' },
    row: (r) => (
      <li key={r.slug}>
        {orgLink(r.slug, r.name)}
        <span className="pf-feed-who">{r.status_display}</span>
        <span className="pf-feed-when">{when(r.created_at)}</span>
      </li>
    ),
  },
  {
    key: 'domains', label: 'Domains', icon: Globe,
    empty: 'No customer has claimed a domain of their own yet.',
    more: { to: '/platform/domains', label: 'All domains' },
    row: (r) => (
      <li key={r.hostname}>
        <span className="pf-feed-what">{r.hostname}</span>
        <span className="pf-feed-who">
          {r.organization_name} · {r.status_display}
        </span>
        <span className="pf-feed-when">{when(r.created_at)}</span>
      </li>
    ),
  },
  {
    key: 'payments', label: 'Payments', icon: CreditCard,
    empty: 'No payments yet.',
    more: { to: '/platform/payments', label: 'Payment queue' },
    row: (r) => (
      <li key={r.reference}>
        {orgLink(r.organization_slug, r.organization_name)}
        <span className="pf-feed-who">
          {money(r.amount_minor, r.currency)} · {r.plan_name} · {r.status_display}
        </span>
        <span className="pf-feed-when">{when(r.updated_at)}</span>
      </li>
    ),
  },
  {
    key: 'registrations', label: 'Signups', icon: UserPlus,
    empty: 'Nobody has signed up through the public form yet.',
    more: null,
    row: (r) => (
      <li key={`${r.slug}-${r.created_at}`}>
        <span className="pf-feed-what">{r.organization_name}</span>
        <span className="pf-feed-who">{r.slug} · {r.status_display}</span>
        <span className="pf-feed-when">{when(r.created_at)}</span>
      </li>
    ),
  },
  {
    key: 'sign_ins', label: 'Sign-ins', icon: LogIn,
    empty: 'No sign-ins recorded yet.',
    more: { to: '/platform/audit', label: 'Audit trail' },
    row: (r, i) => (
      <li key={`${r.who}-${r.at}-${i}`}>
        {r.kind === 'client'
          ? orgLink(r.organization_slug, r.organization_name)
          : <span className="pf-feed-what">Platform team</span>}
        <span className="pf-feed-who">
          {r.who} · {r.kind === 'client' ? 'first sign-in' : 'last active'}
        </span>
        <span className="pf-feed-when">{when(r.at)}</span>
      </li>
    ),
  },
];

const RecentActivity = () => {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [tab, setTab] = useState('organizations');

  useEffect(() => {
    let alive = true;
    platformService.recent()
      .then(({ data: body }) => { if (alive) setData(body || {}); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);

  const current = TABS.find((t) => t.key === tab);
  const rows = Array.isArray(data?.[tab]) ? data[tab] : [];

  return (
    <section className="pf-card pf-recent" aria-label="Recent">
      <div className="pf-tabs" role="tablist" aria-label="Recent activity">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} type="button" role="tab" id={`recent-tab-${key}`}
                  aria-selected={tab === key} aria-controls="recent-panel"
                  className={`pf-tab${tab === key ? ' is-active' : ''}`}
                  onClick={() => setTab(key)}>
            <Icon size={14} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <div id="recent-panel" role="tabpanel" aria-labelledby={`recent-tab-${tab}`}>
        {failed ? (
          <p className="pf-muted">Recent activity could not be loaded.</p>
        ) : data === null ? (
          <p className="pf-muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="pf-muted">{current.empty}</p>
        ) : (
          <ul className="pf-feed">{rows.map(current.row)}</ul>
        )}
        {current.more && (
          <Link className="pf-link" to={current.more.to}>{current.more.label} →</Link>
        )}
      </div>
    </section>
  );
};

export default RecentActivity;
