import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, Users, CalendarCheck, Palmtree, ListChecks, FileText } from 'lucide-react';

import PageHeader from '../../components/common/PageHeader';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';

/**
 * Customer health and adoption: who is using the product, and who needs a
 * call before they quietly leave.
 *
 * FROM COUNTS ONLY (tenancy.adoption): active people per day and how many
 * attendance, leave, task and document records were created -- never what
 * they were. The console still cannot read a customer's records to answer
 * "are they using it?", and does not need to.
 */
const Spark = ({ series, label }) => {
  const max = Math.max(1, ...series.map((p) => p.count));
  return (
    <div className="ch-spark" role="img" aria-label={`${label}: ${series.reduce((n, p) => n + p.count, 0)} in the period`}>
      {series.map((p) => (
        <span key={p.day} style={{ height: `${Math.max(4, (p.count / max) * 100)}%` }}
              className={p.count ? 'is-on' : ''} title={`${p.day}: ${p.count}`} />
      ))}
    </div>
  );
};

const USAGE = [
  ['attendance', 'Attendance', CalendarCheck],
  ['leave', 'Leave', Palmtree],
  ['task', 'Tasks', ListChecks],
  ['document', 'Documents', FileText],
];

const FILTERS = [
  ['', 'All'],
  ['attention', 'Needs attention'],
  ['inactive', 'Inactive'],
  ['no_attendance', 'Not using attendance'],
  ['no_employees', 'No employees'],
  ['expiring', 'Near expiry'],
];

const CustomerHealth = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('attention');

  useEffect(() => {
    platformService.customerHealth(30)
      .then(({ data: d }) => setData(d))
      .catch(() => setError('Customer health couldn’t be loaded.'));
  }, []);

  if (error) return <div className="page"><EmptyState variant="error" title="Unavailable" body={error} /></div>;
  if (!data) return <div className="page"><p className="pc-muted">Loading…</p></div>;

  const { platform, organizations } = data;
  const shown = organizations.filter((o) => (!filter ? true
    : filter === 'attention' ? o.reasons.length > 0
      : o.reasons.some((r) => r.code === filter || (filter === 'expiring' && r.code === 'expired'))));

  return (
    <div className="page">
      <PageHeader breadcrumb="Customers" title="Customer health"
                  description="Who is using the product, and who needs a call. From usage counts only — no customer records are read." />

      <section className="ch-adopt" aria-label="Adoption">
        <div className="pf-rev is-lead">
          <span><Users size={13} aria-hidden="true" /> Active people today</span>
          <b>{platform.dau_today}</b>
          <small>{platform.active_person_days_7d} person-days this week</small>
          <Spark series={platform.series.active_users} label="Active people" />
        </div>
        {USAGE.map(([key, label, Icon]) => (
          <div className="pf-rev" key={key}>
            <span><Icon size={13} aria-hidden="true" /> {label}</span>
            <b>{platform.totals_30d[key] ?? 0}</b>
            <small>created in 30 days</small>
            <Spark series={platform.series[key] || []} label={label} />
          </div>
        ))}
      </section>

      <div className="pf-tabs" role="tablist" aria-label="Show">
        {FILTERS.map(([key, label]) => {
          const n = key === '' ? organizations.length
            : key === 'attention' ? organizations.filter((o) => o.reasons.length).length
              : organizations.filter((o) => o.reasons.some((r) => r.code === key || (key === 'expiring' && r.code === 'expired'))).length;
          return (
            <button key={key} type="button" role="tab" aria-selected={filter === key}
                    className={`pf-tab${filter === key ? ' is-active' : ''}`} onClick={() => setFilter(key)}>
              {label} <span className="ch-n">{n}</span>
            </button>
          );
        })}
      </div>

      {shown.length === 0 ? (
        <EmptyState variant="cleared" title="Nobody here"
                    body={filter === 'attention' ? 'Every customer is active and set up. Nice.' : 'No customer matches this filter.'} />
      ) : (
        <div className="pc-list">
          {shown.map((o) => (
            <article className="pc-card ch-org" key={o.slug} aria-label={o.name}>
              <div className={`ch-score is-${o.health >= 75 ? 'good' : o.health >= 50 ? 'warn' : 'bad'}`}
                   aria-label={`Health ${o.health} of 100`}>
                <Activity size={14} aria-hidden="true" /> {o.health}
              </div>
              <div className="ch-main">
                <Link className="pc-org" to={`/platform/organizations/${o.slug}`}>{o.name}</Link>
                <p className="pc-muted">
                  {o.status_display} · {o.seats} {o.seats === 1 ? 'person' : 'people'}
                  {o.last_active ? ` · last active ${o.last_active}` : ' · never active'}
                </p>
                {o.reasons.length > 0 && (
                  <ul className="ch-reasons">
                    {o.reasons.map((r) => <li key={r.code}>{r.text}</li>)}
                  </ul>
                )}
              </div>
              <ul className="ch-usage" aria-label="Used in 30 days">
                {USAGE.map(([key, label]) => (
                  <li key={key} className={o.usage_30d[key] ? 'is-on' : ''}>
                    <b>{o.usage_30d[key] ?? 0}</b> {label.toLowerCase()}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
    </div>
  );
};

export default CustomerHealth;
