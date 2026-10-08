import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { platformService } from '../../../services/platformService';
import { money, day } from './format';

/**
 * The founder view: is the business healthy, today, on one screen?
 * Revenue (MRR, ARR), customers (active, at risk, health bands), support
 * (open, escalated, satisfaction, what the assistant answered) and the
 * renewal pipeline -- 30, 60 and 90 days out, with what it is worth.
 *
 * Headline numbers are stat tiles, not charts: each is one number whose job
 * is to be read, and the lists below them say who is behind each one.
 */
const BANDS = [['healthy', 'Healthy'], ['watch', 'Watch'], ['onboarding', 'Onboarding'], ['at_risk', 'Needs attention']];

const Metric = ({ label, value, sub, tone }) => (
  <div className={`pf-metric${tone ? ` ${tone}` : ''}`}>
    <span className="pf-metric-label">{label}</span>
    <span className="pf-metric-value">{value}</span>
    {sub && <span className="pf-metric-sub">{sub}</span>}
  </div>
);

const Executive = () => {
  const [d, setD] = useState(null);
  useEffect(() => { platformService.successOperations().then(({ data }) => setD(data)).catch(() => setD({})); }, []);
  if (!d) return <p className="pc-muted">Loading…</p>;
  const s = d.support_load || {};
  const pipe = d.renewal_pipeline || {};
  const bands = d.bands || {};
  const totalBands = Object.values(bands).reduce((n, x) => n + x, 0);
  return (
    <div className="cs-exec">
      <section className="pf-metrics" aria-label="Founder view">
        <Metric label="MRR" value={money(d.mrr_minor, d.currency)} sub={`${d.paying_customers ?? 0} paying customer${d.paying_customers === 1 ? '' : 's'}`} />
        <Metric label="ARR" value={money(d.arr_minor, d.currency)} sub="MRR × 12, trials excluded" />
        <Metric label="Active customers" value={d.active_customers ?? '—'} sub={`average health ${d.average_health ?? '—'}`} />
        <Metric label="At-risk customers" value={d.at_risk_customers ?? '—'} tone={d.at_risk_customers ? 'is-bad' : ''}
                sub={<Link to="/platform/customer-health?tab=center">See who</Link>} />
        <Metric label="Open tickets" value={d.open_tickets ?? '—'}
                sub={`${d.escalated_open ?? 0} escalated · ${s.overdue ?? 0} overdue`} tone={d.escalated_open ? 'is-bad' : ''} />
        <Metric label="Support satisfaction (30d)" value={d.satisfaction?.average ? `${d.satisfaction.average}/5` : '—'}
                sub={`${d.satisfaction?.count || 0} ratings`} />
        <Metric label="Renewals due (30d)" value={money(pipe['30']?.value_minor, d.currency)}
                sub={`${pipe['30']?.count ?? 0} customers${pipe['30']?.unpriced ? ` · ${pipe['30'].unpriced} unpriced` : ''}`} />
        <Metric label="Answered by the assistant (30d)" value={d.deflection_rate_30d == null ? '—' : `${d.deflection_rate_30d}%`}
                sub={`${d.tickets_deflected_30d ?? 0} of ${d.assist_shown_30d ?? 0} drafts · ${d.tickets_30d ?? 0} tickets opened`} />
      </section>

      <div className="cs-grid">
        <section className="pc-card" aria-labelledby="cs-pipe"><h3 id="cs-pipe" className="pc-org">Renewal pipeline</h3>
          <div className="dk-table-wrap"><table className="lr-table">
            <caption className="sr-only">Renewals and their value by window</caption>
            <thead><tr><th scope="col">Within</th><th scope="col">Customers</th><th scope="col">Value</th></tr></thead>
            <tbody>{['30', '60', '90'].map((w) => (
              <tr key={w}><th scope="row">{w} days</th><td>{pipe[w]?.count ?? 0}</td><td>{money(pipe[w]?.value_minor, d.currency)}</td></tr>
            ))}</tbody>
          </table></div>
          {(d.renewals_upcoming || []).length === 0 ? <p className="pc-muted">No renewals in the next 90 days.</p> : (
            <ol className="cs-rank">{d.renewals_upcoming.map((r) => (
              <li key={r.slug}><Link to={`/platform/organizations/${r.slug}`}>{r.name}</Link>
                <span className="pc-muted">{r.status} · {r.plan || 'no plan'} · {day(r.ends_on)} ({r.days_left}d) · {money(r.value_minor, d.currency)}</span></li>))}</ol>)}
        </section>
        <section className="pc-card" aria-labelledby="cs-bands"><h3 id="cs-bands" className="pc-org">Customer health</h3>
          <ul className="dk-bands" aria-label="Customers by health band">
            {BANDS.map(([key, label]) => (
              <li key={key} className={`is-${key}`}>
                <span>{label}</span>
                <span className="cs-bar" aria-hidden="true"><i style={{ width: `${totalBands ? (100 * (bands[key] || 0)) / totalBands : 0}%` }} /></span>
                <b>{bands[key] || 0}</b>
              </li>
            ))}
          </ul>
          <h4 className="dk-h">Customers at risk</h4>
          {(d.at_risk || []).length === 0 ? <p className="pc-muted">None right now.</p> : (
            <ol className="cs-rank">{d.at_risk.map((o) => (
              <li key={o.slug}><Link to={`/platform/organizations/${o.slug}`}>{o.name}</Link>
                <span className="pc-muted">health {o.health} · {o.reasons[0]?.text}</span></li>))}</ol>)}
        </section>
        <section className="pc-card" aria-labelledby="cs-top"><h3 id="cs-top" className="pc-org">Top active customers</h3>
          <ol className="cs-rank">{(d.top_active || []).map((o) => (
            <li key={o.slug}><Link to={`/platform/organizations/${o.slug}`}>{o.name}</Link>
              <span className="pc-muted">{o.active_person_days_30d} person-days · health {o.health}</span></li>))}</ol>
        </section>
        <section className="pc-card" aria-labelledby="cs-sup"><h3 id="cs-sup" className="pc-org">Support operations</h3>
          <dl className="sd-context">
            <dt>Open</dt><dd>{s.open}</dd><dt>Critical</dt><dd>{s.critical}</dd>
            <dt>Escalated</dt><dd>{d.escalated_open ?? 0}</dd>
            <dt>Overdue</dt><dd>{s.overdue}</dd><dt>Waiting for customer</dt><dd>{s.waiting_customer}</dd>
            <dt>Avg first reply</dt><dd>{s.avg_first_response_hours_30d ?? '—'}h</dd>
            <dt>Avg resolution</dt><dd>{s.avg_resolution_hours_30d ?? '—'}h</dd>
            <dt>Success tasks overdue</dt><dd>{d.overdue_tasks ?? 0}</dd>
          </dl>
          <Link to="/platform/support?section=sla">Open the SLA board</Link>
        </section>
      </div>
    </div>
  );
};

export default Executive;
