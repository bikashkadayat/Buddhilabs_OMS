import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Users, CalendarCheck, Palmtree, ListChecks, FileText } from 'lucide-react';

import PageHeader from '../../components/common/PageHeader';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';
import HealthScore from '../../components/platform/success/HealthScore';
import Executive from '../../components/platform/success/Executive';
import Adoption from '../../components/platform/success/Adoption';
import Onboarding from '../../components/platform/success/Onboarding';
import Tasks from '../../components/platform/success/Tasks';
import Alerts from '../../components/platform/success/Alerts';
import Campaigns from '../../components/platform/success/Campaigns';

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

const TABS = [
  ['center', 'Command center'], ['executive', 'Founder view'], ['adoption', 'Adoption'],
  ['onboarding', 'Onboarding'], ['campaigns', 'Campaigns'], ['tasks', 'Tasks'], ['alerts', 'Alerts'],
];

const CustomerHealth = () => {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'center';
  const [segment, setSegment] = useState(null);
  const [data, setData] = useState(null);
  const [center, setCenter] = useState(null);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    platformService.customerHealth(30).then(({ data: d }) => setData(d)).catch(() => setError('Customer success couldn’t be loaded.'));
    platformService.successCommandCenter().then(({ data: d }) => setCenter(d)).catch(() => setCenter({ segments: [], organizations: [] }));
    platformService.supportAgents().then(({ data: d }) => setAgents(d)).catch(() => setAgents([]));
  }, []);

  if (error) return <div className="page"><EmptyState variant="error" title="Unavailable" body={error} /></div>;
  if (!data || !center) return <div className="page"><p className="pc-muted">Loading…</p></div>;

  const { platform } = data;
  // Open on "at risk" when somebody is, otherwise on everyone -- an empty
  // first view reads as "nothing loaded".
  const atRisk = center.segments.find((x) => x.key === 'at_risk');
  const active = segment || (atRisk?.count ? 'at_risk' : 'all');
  const seg = center.segments.find((x) => x.key === active);
  const members = new Set(seg ? seg.organizations : []);
  const shown = active === 'all' ? center.organizations : center.organizations.filter((o) => members.has(o.slug));

  return (
    <div className="page">
      <PageHeader breadcrumb="Customers" title="Customer success"
                  description="Who needs us before they say so. From usage counts, subscriptions and support only — no customer records are read." />
      <div className="pf-tabs" role="tablist" aria-label="Customer success views">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={`pf-tab${tab === key ? ' is-active' : ''}`} onClick={() => setParams({ tab: key })}>{label}</button>
        ))}
      </div>

      {tab === 'executive' && <Executive />}
      {tab === 'adoption' && <Adoption />}
      {tab === 'onboarding' && <Onboarding />}
      {tab === 'campaigns' && <Campaigns agents={agents} />}
      {tab === 'tasks' && <Tasks agents={agents} />}
      {tab === 'alerts' && <Alerts />}

      {tab === 'center' && (
        <>
          <section className="ch-adopt" aria-label="Activity">
            <div className="pf-rev is-lead">
              <span><Users size={13} aria-hidden="true" /> Active people today</span>
              <b>{platform.dau_today}</b>
              <small>Average health {center.average_health ?? '—'} · {center.bands.at_risk} at risk · {center.bands.watch} to watch</small>
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

          <div className="cs-segments" role="tablist" aria-label="Segment">
            {[...center.segments, { key: 'all', label: 'All customers', count: center.organizations.length }].map((x) => (
              <button key={x.key} type="button" role="tab" aria-selected={active === x.key}
                      className={`cs-seg${active === x.key ? ' is-on' : ''}${x.key === 'at_risk' && x.count ? ' is-bad' : ''}`}
                      onClick={() => setSegment(x.key)}>
                <b>{x.count}</b><span>{x.label}</span>
              </button>
            ))}
          </div>

          {shown.length === 0 ? (
            <EmptyState variant="cleared" title="Nobody here" body="No customer is in this segment right now." />
          ) : (
            <div className="pc-list">
              {shown.map((o) => (
                <article className="pc-card ch-org" key={o.slug} aria-label={o.name}>
                  <HealthScore org={o} detailed />
                  <div className="ch-main">
                    <Link className="pc-org" to={`/platform/organizations/${o.slug}`}>{o.name}</Link>
                    <p className="pc-muted">
                      {o.status_display} · {o.plan || 'no plan'} · {o.seats} {o.seats === 1 ? 'person' : 'people'}
                      {o.last_active ? ` · last active ${o.last_active}` : ' · never active'}
                      {o.is_new && ` · joined ${o.age_days} days ago`}
                    </p>
                    {o.reasons.length > 0 && (
                      <ul className="ch-reasons">{o.reasons.map((r) => <li key={r.code}>{r.text}</li>)}</ul>
                    )}
                    <Link className="btn btn-ghost btn-xs" to={`/platform/organizations/${o.slug}#success`}>Timeline & tasks</Link>
                  </div>
                  <ul className="ch-usage" aria-label="Used in 30 days">
                    {USAGE.map(([key, label]) => (
                      <li key={key} className={o.usage_30d[key] ? 'is-on' : ''}><b>{o.usage_30d[key] ?? 0}</b> {label.toLowerCase()}</li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          )}
          <p className="pc-muted">
            Health = {center.weights.map((w) => `${w.label} ${w.weight}%`).join(' · ')}. 75+ healthy, 50–74 watch, under 50 needs attention.
          </p>
        </>
      )}
    </div>
  );
};

export default CustomerHealth;
