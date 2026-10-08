import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, CheckCircle2, ClipboardList, CreditCard,
  Globe, Plus, Activity, ArrowUpRight, Clock, TrendingUp, Rocket,
} from 'lucide-react';

import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { platformService, money, bytes } from '../../services/platformService';
import RegistrationFunnel from '../../components/platform/RegistrationFunnel';
import RecentActivity from '../../components/platform/RecentActivity';

/**
 * The platform's control centre.
 *
 * WHAT THIS REPLACED. Two rows of statistic tiles and three key/value lists
 * — every number correct, and nothing to do with any of them. An operator
 * opening it learned the platform's size and not its state: nothing said
 * what was waiting, what had just happened, or what to do next. It read as
 * a report that had been left on a screen.
 *
 * The structure is now the three questions an operator actually arrives
 * with, in the order they arrive:
 *
 *   1. Is anything wrong?        — the attention strip, first, or absent
 *   2. How big is this?          — the figures, as one scannable band
 *   3. What has been happening?  — recent activity, and what to do next
 *
 * EVERY FIGURE IS STILL A LINK to the filtered list that produced it. A
 * number an operator cannot act on belongs in a report.
 *
 * None of these counts runs over tenant data: headcount and storage are
 * maintained counters (backend `tenancy/counters.py`). That is not an
 * optimisation — under row-level security a count over a tenant table from
 * the platform console returns zero for every tenant, silently.
 */
const Metric = ({ to, label, value, sub, tone = '' }) => (
  <Link className={`pf-metric${tone ? ` is-${tone}` : ''}`} to={to}>
    <span className="pf-metric-label">{label}</span>
    <span className="pf-metric-value">{value}</span>
    {sub && <span className="pf-metric-sub">{sub}</span>}
    <ArrowUpRight size={14} className="pf-metric-go" aria-hidden="true" />
  </Link>
);

const Action = ({ to, icon, label, hint }) => (
  <Link className="pf-action" to={to}>
    <span className="pf-action-ic" aria-hidden="true">{icon}</span>
    <span>
      <strong>{label}</strong>
      <small>{hint}</small>
    </span>
  </Link>
);

const PlatformDashboard = () => {
  const [data, setData] = useState(null);
  const [recent, setRecent] = useState([]);
  const [error, setError] = useState(null);
  const [support, setSupport] = useState(null);
  const [success, setSuccess] = useState(null);

  useEffect(() => {
    let alive = true;
    platformService.successCommandCenter()
      .then(({ data: d }) => { if (alive) setSuccess(d); })
      .catch(() => {});
    platformService.supportOverview()
      .then(({ data: d }) => { if (alive) setSupport(d); })
      .catch(() => {});
    platformService.dashboard()
      .then((response) => { if (alive) setData(response.data); })
      .catch(() => { if (alive) setError('The dashboard could not be loaded.'); });
    platformService.audit({ limit: 8 })
      .then(({ data: rows }) => {
        if (alive) setRecent(Array.isArray(rows) ? rows : (rows.results || []));
      })
      .catch(() => setRecent([]));
    return () => { alive = false; };
  }, []);

  if (error) {
    return (
      <div className="pf-page">
        <EmptyState variant="error" title="Dashboard unavailable" body={error} />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="pf-page">
        <Skeleton rows={5} height={64} label="Loading the dashboard" />
      </div>
    );
  }

  const health = data.system_health || {};
  const forecast = data.revenue_forecast || {};
  const stuck = health.organizations_stuck_provisioning || [];
  const drift = Object.keys(health.subscription_mirror_drift || {}).length;

  // Everything that wants a human, in one place. Built as a list rather than
  // rendered inline so the whole strip can be absent when nothing is wrong —
  // a permanently present "all clear" banner is noise that trains people to
  // skip the row where the real one will appear.
  const attention = [];
  (health.problems || []).forEach((problem) => {
    attention.push({
      tone: 'urgent', to: '/platform/health',
      icon: <AlertTriangle size={15} />,
      text: problem,
      hint: 'Reported by the platform health check.',
    });
  });
  if (data.payments_pending_verification > 0) {
    attention.push({
      tone: 'urgent', to: '/platform/payments',
      icon: <CreditCard size={15} />,
      text: `${data.payments_pending_verification} payment${data.payments_pending_verification === 1 ? '' : 's'} awaiting review`,
      hint: 'A customer has paid and is waiting for you to confirm it.',
    });
  }
  const revenue = data.revenue || {};
  if (revenue.provisioning_failed_30d > 0) {
    attention.push({
      tone: 'urgent', to: '/platform/health',
      icon: <AlertTriangle size={15} />,
      text: `${revenue.provisioning_failed_30d} workspace${revenue.provisioning_failed_30d === 1 ? '' : 's'} failed to provision in 30 days`,
      hint: 'Somebody verified their email and got no workspace.',
    });
  }
  const endingTrials = revenue.trials_ending_7d || [];
  if (endingTrials.length > 0) {
    attention.push({
      tone: '', to: '/platform/subscriptions?status=trial',
      icon: <Clock size={15} />,
      text: `${endingTrials.length} trial${endingTrials.length === 1 ? '' : 's'} ending this week`,
      hint: endingTrials.slice(0, 3).map((t) => t.name).join(', '),
    });
  }
  if (data.subscriptions_expiring_30d > 0) {
    attention.push({
      tone: '', to: '/platform/subscriptions',
      icon: <Clock size={15} />,
      text: `${data.subscriptions_expiring_30d} subscription${data.subscriptions_expiring_30d === 1 ? '' : 's'} expiring within 30 days`,
      hint: 'A renewal nobody chases becomes a suspension.',
    });
  }

  return (
    <div className="pf-page">
      <header className="pf-page-head">
        <div>
          <h1 className="pf-page-title">Platform overview</h1>
          <p className="pf-page-sub">
            Every organization on the platform, what it is costing, and what
            is waiting on you.
          </p>
        </div>
        <Link to="/platform/organizations?new=1" className="btn btn-primary">
          <Plus size={16} aria-hidden="true" /> New organization
        </Link>
      </header>

      {/* Platform alerts. Said when there are none, too: an empty space
          where alerts go reads as "not loaded", and "all clear" is news. */}
      {attention.length === 0 && (
        <p className="pf-allclear" role="status">
          <CheckCircle2 size={16} aria-hidden="true" />
          No platform alerts. Nothing needs you right now.
        </p>
      )}
      {attention.length > 0 && (
        <section className="pf-attention" aria-label="Needs attention">
          {attention.map((item) => (
            <Link key={item.text} to={item.to}
                  className={`pf-attn${item.tone ? ` is-${item.tone}` : ''}`}>
              <span className="pf-attn-ic" aria-hidden="true">{item.icon}</span>
              <span>
                <strong>{item.text}</strong>
                <small>{item.hint}</small>
              </span>
            </Link>
          ))}
        </section>
      )}

      <section className="pf-metrics" aria-label="Platform at a glance">
        <Metric to="/platform/organizations" label="Organizations"
                value={data.organizations_total}
                sub={`${data.organizations_active} active · ${data.organizations_trial} on trial`} />
        <Metric to="/platform/subscriptions?status=trial" label="Trials"
                value={data.organizations_trial}
                sub={data.subscriptions_expiring_30d
                  ? `${data.subscriptions_expiring_30d} expiring in 30 days` : 'None expiring soon'}
                tone={data.subscriptions_expiring_30d ? 'warn' : ''} />
        {/* Money moved to the Revenue section below: the same figure in two
            places is two places to disagree. */}
        <Metric to="/platform/usage" label="Seats in use"
                value={data.users_total} sub="Across every tenant" />
        <Metric to="/platform/usage" label="Storage"
                value={bytes(data.storage_bytes)} sub="Tenant media and exports" />
      </section>

      {/* Customer Success 2.0: who needs us, before they say so. */}
      {success?.bands && success?.segments && (
        <section className="pf-metrics" aria-label="Customer health">
          <Metric to="/platform/customer-health" label="Average health" value={success.average_health ?? '—'}
                  sub={`${success.bands.healthy} healthy · ${success.bands.watch} to watch`} />
          <Metric to="/platform/customer-health" label="Customers at risk" value={success.bands.at_risk}
                  tone={success.bands.at_risk ? 'warn' : ''} sub="Health under 50" />
          <Metric to="/platform/customer-health" label="Trial ending soon"
                  value={success.segments.find((x) => x.key === 'trial_ending')?.count ?? 0} sub="Within 7 days" />
          <Metric to="/platform/customer-health?tab=executive" label="New customers"
                  value={success.segments.find((x) => x.key === 'new')?.count ?? 0} sub="Joined in 30 days" />
        </section>
      )}

      {/* Support Overview: the desk's health at a glance. Overdue is the
          number to act on -- those tickets are past their SLA. */}
      {support && (
        <section className="pf-metrics" aria-label="Support overview">
          <Metric to="/platform/support" label="Open tickets" value={support.open}
                  sub={`${support.assigned} assigned · ${support.unassigned} unassigned`} />
          <Metric to="/platform/support" label="Critical" value={support.critical}
                  tone={support.critical ? 'warn' : ''} sub={`${support.unread} unread`} />
          <Metric to="/platform/support" label="Overdue" value={support.overdue}
                  tone={support.overdue ? 'warn' : ''} sub="Past their SLA" />
          <Metric to="/platform/support" label="Avg resolution"
                  value={support.avg_resolution_hours_30d != null ? `${support.avg_resolution_hours_30d}h` : '—'}
                  sub={support.csat_30d?.average ? `Satisfaction ${support.csat_30d.average}/5` : 'Last 30 days'} />
        </section>
      )}

      {/* Part 14: the executive summary. Collected is cash that arrived
          (verified payments, by the day they were verified); MRR and ARR
          are what active subscriptions are worth. Both, because each
          answers a different question. */}
      <section className="pf-revenue" aria-label="Revenue" data-tour="pf-revenue">
        <h2 className="pf-section-title">Revenue</h2>
        <div className="pf-revenue-grid">
          <div className="pf-rev is-lead">
            <span>Collected this month</span>
            <b>{money(revenue.collected_this_month_minor || 0, revenue.currency || 'NPR')}</b>
            <small>Last month {money(revenue.collected_last_month_minor || 0, revenue.currency || 'NPR')}</small>
          </div>
          <div className="pf-rev">
            <span>Monthly recurring</span>
            <b>{money(revenue.mrr_minor ?? forecast.monthly_minor ?? 0, revenue.currency || 'NPR')}</b>
            <small>From active subscriptions</small>
          </div>
          <div className="pf-rev">
            <span>Annual run-rate</span>
            <b>{money(revenue.arr_minor ?? forecast.annual_minor ?? 0, revenue.currency || 'NPR')}</b>
            <small>Monthly × 12</small>
          </div>
          <Link className="pf-rev" to="/platform/payments">
            <span>Payments</span>
            <b>{data.payments_pending_verification ?? 0} to review</b>
            <small>{data.payments_verified_30d ?? 0} approved · {data.payments_rejected_30d ?? 0} rejected in 30 days</small>
          </Link>
        </div>
      </section>

      <section className="pf-states" aria-label="Organizations by lifecycle state">
        {/* The six lifecycle states. Dropped when the metric band replaced
            the old tiles, and the dashboard test was right to object: "in
            grace" is the state that becomes a support call if nobody looks,
            and a band of headline figures has no room to say it. */}
        {[
          ['Active', data.organizations_active, '/platform/organizations?status=active'],
          ['On trial', data.organizations_trial, '/platform/organizations?status=trial'],
          ['In grace', data.organizations_grace, '/platform/organizations?status=grace'],
          ['Suspended', data.organizations_suspended, '/platform/organizations?status=suspended'],
          ['Provisioning', data.organizations_provisioning, '/platform/organizations?status=provisioning'],
          ['Cancelled', data.organizations_cancelled, '/platform/organizations?status=cancelled'],
        ].map(([label, count, to]) => (
          <Link key={label} to={to}
                className={`pf-state${count ? '' : ' is-zero'}`}>
            <b>{count ?? 0}</b>
            <span>{label}</span>
          </Link>
        ))}
      </section>

      <section className="pf-quick" aria-label="Quick actions">
        <h2 className="pf-section-title">Quick actions</h2>
        <div className="pf-quick-grid">
          <Action to="/platform/organizations?new=1" icon={<Plus size={16} />}
                  label="New organization" hint="Set up, branded and ready to hand over" />
          <Action to="/platform/payments" icon={<CreditCard size={16} />}
                  label="Payments to review" hint="Receipts customers have sent" />
          <Action to="/platform/domains" icon={<Globe size={16} />}
                  label="Review domains" hint="Claims that will not verify" />
          <Action to="/platform/audit" icon={<ClipboardList size={16} />}
                  label="Audit trail" hint="Who did what, to which customer" />
          <Action to="/platform/health" icon={<Activity size={16} />}
                  label="Platform health" hint="Database, cache, mirrors, readiness" />
          <Action to="/platform/launch" icon={<Rocket size={16} />}
                  label="Launch readiness" hint="Is this deployment fit to sell?" />
        </div>
      </section>

      {/* Part 9: what just happened, in the five places an operator looks. */}
      <RecentActivity />

      <div className="pf-split">
        <section className="pf-card" aria-label="Recent platform activity">
          <h2 className="pf-card-title">
            <ClipboardList size={16} aria-hidden="true" /> Recent activity
          </h2>
          {recent.length === 0 ? (
            <p className="pf-muted">Nothing has happened yet. Every change you or your team make appears here.</p>
          ) : (
            <ul className="pf-feed">
              {recent.map((row) => (
                <li key={row.id}>
                  <span className="pf-feed-what">{row.action_display || row.action}</span>
                  <span className="pf-feed-who">
                    {row.organization_name || '—'}
                    {row.actor_email ? ` · ${row.actor_email.split('@')[0]}` : ''}
                  </span>
                  <span className="pf-feed-when">
                    {row.created_at ? new Date(row.created_at).toLocaleString() : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link className="pf-link" to="/platform/audit">The whole trail →</Link>
        </section>
        <section className="pf-card" aria-label="Signups">
          <h2 className="pf-card-title">
            <TrendingUp size={16} aria-hidden="true" /> Registration funnel
          </h2>
          <RegistrationFunnel embedded />
        </section>

      </div>

      <div className="pf-split">
        <section className="pf-card" aria-label="System health">
          <h2 className="pf-card-title">
            {health.status === 'ok'
              ? <CheckCircle2 size={16} aria-hidden="true" className="pf-ok-ic" />
              : <AlertTriangle size={16} aria-hidden="true" className="pf-warn-ic" />}
            System health
          </h2>
          <ul className="pf-kv">
            <li><span>Database</span><b>{health.database || '—'}</b></li>
            <li><span>Cache</span><b>{health.cache || '—'}</b></li>
            <li><span>Stuck provisioning</span><b>{stuck.length}</b></li>
            <li><span>Mirror drift</span><b>{drift}</b></li>
          </ul>
          <div className="pf-card-links">
            <Link className="pf-link" to="/platform/health">Platform health →</Link>
            <Link className="pf-link" to="/platform/launch">Launch readiness →</Link>
          </div>
        </section>
      </div>
    </div>
  );
};

export default PlatformDashboard;
