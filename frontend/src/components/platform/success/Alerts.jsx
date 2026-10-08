import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { platformService } from '../../../services/platformService';
import { day } from './format';

/** Parts 3 and 11: what needs the team now, tickets and customers. */
const GROUPS = [['escalated', 'Escalated tickets'], ['critical', 'Critical tickets'], ['overdue', 'Overdue tickets'],
  ['no_response', 'No response yet'], ['stale', 'Stale (no activity 3+ days)']];

const Alerts = () => {
  const [d, setD] = useState(null);
  useEffect(() => { platformService.successAlerts().then(({ data }) => setD(data)).catch(() => setD({ tickets: {}, customers: [] })); }, []);
  if (!d) return <p className="pc-muted">Loading…</p>;
  return (
    <div className="cs-grid">
      {GROUPS.map(([key, label]) => (
        <section key={key} className="pc-card" aria-labelledby={`al-${key}`}>
          <h3 id={`al-${key}`} className="pc-org">{label} <span className="ch-n">{(d.tickets[key] || []).length}</span></h3>
          {(d.tickets[key] || []).length === 0 ? <p className="pc-muted">None.</p> : (
            <ul className="cs-rank">{d.tickets[key].map((t) => (
              <li key={t.id}><Link to={`/platform/support?ticket=${t.id}`}>{t.reference} · {t.subject || '(no title)'}</Link>
                <span className="pc-muted">{t.organization} · {t.priority} · {t.assigned_to || 'unassigned'}
                  {t.escalation_level ? ` · level ${t.escalation_level}` : ''}</span></li>))}</ul>
          )}
        </section>
      ))}
      <section className="pc-card cs-wide" aria-labelledby="al-cust">
        <h3 id="al-cust" className="pc-org">Customers to reach out to <span className="ch-n">{d.customers.length}</span></h3>
        {d.customers.length === 0 ? <p className="pc-muted">Nobody needs a call today.</p> : (
          <ul className="cs-rank">{d.customers.map((c) => (
            <li key={`${c.slug}-${c.code}`}><Link to={`/platform/organizations/${c.slug}`}>{c.name}</Link>
              <span className="pc-muted">{c.text} · health {c.health}</span></li>))}</ul>
        )}
        <p className="pc-muted">The daily job opens a task for each of these and emails the team; ticket alerts are emailed every 15 minutes. Checked {day(new Date().toISOString())}.</p>
      </section>
    </div>
  );
};

export default Alerts;
