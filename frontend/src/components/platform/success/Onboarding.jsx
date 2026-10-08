import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Circle } from 'lucide-react';
import { platformService } from '../../../services/platformService';
import { day } from './format';

/** Part 9: onboarding milestones per organization, least complete first. */
const Onboarding = () => {
  const [d, setD] = useState(null);
  useEffect(() => { platformService.successOnboarding().then(({ data }) => setD(data)).catch(() => setD({ organizations: [], completion: [] })); }, []);
  if (!d) return <p className="pc-muted">Loading…</p>;
  return (
    <div>
      <section className="pf-metrics" aria-label="Completion across customers">
        {d.completion.map((c) => (
          <div className="pf-metric" key={c.key}><span className="pf-metric-label">{c.label}</span>
            <span className="pf-metric-value">{c.percent}%</span><span className="pf-metric-sub">{c.organizations} organizations</span></div>
        ))}
      </section>
      <div className="lr-table-wrap">
        <table className="lr-table">
          <caption className="sr-only">Onboarding milestones by organization</caption>
          <thead><tr><th scope="col">Organization</th>{d.completion.map((c) => <th scope="col" key={c.key}>{c.label}</th>)}<th scope="col">Done</th></tr></thead>
          <tbody>{d.organizations.map((o) => (
            <tr key={o.slug}>
              <td><Link to={`/platform/organizations/${o.slug}`}>{o.name}</Link></td>
              {o.milestones.map((m) => (
                <td key={m.key} className={m.reached_on ? 'cs-done' : 'cs-todo'}>
                  {m.reached_on ? <><CheckCircle2 size={14} aria-hidden="true" /> {day(m.reached_on)}</>
                    : <><Circle size={14} aria-hidden="true" /> <span className="sr-only">Not yet</span></>}
                </td>
              ))}
              <td><b>{o.completed}/{o.total}</b></td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
};

export default Onboarding;
