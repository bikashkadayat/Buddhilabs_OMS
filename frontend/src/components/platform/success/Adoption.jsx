import React, { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { platformService } from '../../../services/platformService';
import { day } from './format';

/**
 * Part 8: adoption by ORGANIZATION. Single-series charts in the brand colour
 * (small multiples rather than one chart with three coloured lines, so no
 * series is identified by colour alone), each with a hover tooltip and the
 * numbers available as a table.
 */
const MiniTrend = ({ title, data, dataKey, unit = '' }) => (
  <figure className="cs-chart">
    <figcaption>{title}</figcaption>
    <ResponsiveContainer width="100%" height={140}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid stroke="var(--border-light)" vertical={false} />
        <XAxis dataKey="label" tick={{ fill: 'var(--text-muted)', className: 'cs-tick' }} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis tick={{ fill: 'var(--text-muted)', className: 'cs-tick' }} tickLine={false} axisLine={false} allowDecimals={false}
               domain={unit === '%' ? [0, 100] : [0, 'auto']} />
        <Tooltip formatter={(v) => [`${v}${unit}`, title]} labelFormatter={(l) => `Week of ${l}`}
                 contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8 }} />
        <Line type="monotone" dataKey={dataKey} stroke="var(--brand-blue)" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
      </LineChart>
    </ResponsiveContainer>
  </figure>
);

const Adoption = () => {
  const [d, setD] = useState(null);
  useEffect(() => { platformService.successAdoption(12).then(({ data }) => setD(data)).catch(() => setD(null)); }, []);
  if (!d) return <p className="pc-muted">Loading…</p>;
  const weeks = d.weekly_trend.map((w) => ({ ...w, label: day(w.week_start) }));
  const a = d.adoption_30d;
  return (
    <div>
      <section className="pf-metrics" aria-label="Active organizations">
        {[['Daily active orgs', d.active_orgs.daily], ['Weekly active orgs', d.active_orgs.weekly],
          ['Monthly active orgs', d.active_orgs.monthly]].map(([label, n]) => (
            <div className="pf-metric" key={label}><span className="pf-metric-label">{label}</span>
              <span className="pf-metric-value">{n}</span><span className="pf-metric-sub">of {d.organizations} organizations</span></div>
        ))}
        {[['Attendance adoption', a.attendance], ['Task adoption', a.tasks], ['Document adoption', a.documents]].map(([label, x]) => (
          <div className="pf-metric" key={label}><span className="pf-metric-label">{label}</span>
            <span className="pf-metric-value">{x.percent}%</span><span className="pf-metric-sub">{x.organizations} active orgs used it (30d)</span></div>
        ))}
      </section>
      <div className="cs-grid">
        <MiniTrend title="Weekly active organizations" data={weeks} dataKey="active_orgs" />
        <MiniTrend title="Attendance adoption" data={weeks} dataKey="attendance_pct" unit="%" />
        <MiniTrend title="Task adoption" data={weeks} dataKey="tasks_pct" unit="%" />
        <MiniTrend title="Document adoption" data={weeks} dataKey="documents_pct" unit="%" />
      </div>
      <details className="cs-table">
        <summary>View as table</summary>
        <table className="lr-table">
          <caption className="sr-only">Weekly adoption</caption>
          <thead><tr><th scope="col">Week of</th><th scope="col">Active orgs</th><th scope="col">Attendance</th><th scope="col">Tasks</th><th scope="col">Documents</th></tr></thead>
          <tbody>{weeks.map((w) => (
            <tr key={w.week_start}><td>{w.label}</td><td>{w.active_orgs}</td><td>{w.attendance_pct}%</td><td>{w.tasks_pct}%</td><td>{w.documents_pct}%</td></tr>))}</tbody>
        </table>
      </details>
      <p className="pc-muted">Adoption % = share of that week’s active organizations that used the module. From daily counts only — no customer records are read.</p>
    </div>
  );
};

export default Adoption;
