import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { UserMinus, CalendarCheck, Clock, AlertTriangle } from 'lucide-react';
import { reportService } from '../../services/reportService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

const PIE_COLORS = ['#2563EB', '#EF4444', '#F59E0B', '#10B981', '#EC4899', '#6366F1', '#14B8A6'];

const Kpi = ({ icon, label, value, tone }) => (
  <div className="lr-kpi-card">
    <div className="lr-kpi-ico" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone})` }}>{icon}</div>
    <div>
      <div className="lr-kpi-value">{value}</div>
      <div className="lr-kpi-label">{label}</div>
    </div>
  </div>
);

const ChartCard = ({ title, children, height = 240 }) => (
  <div className="lr-chart-card">
    <div className="lr-chart-title">{title}</div>
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>{children}</ResponsiveContainer>
    </div>
  </div>
);

const Analytics = () => {
  const year = new Date().getFullYear();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['analytics', year], queryFn: () => reportService.getAnalytics(year),
  });

  if (isLoading) return <div className="page"><Skeleton rows={2} /><Skeleton rows={3} /></div>;
  if (isError) return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;

  const k = data.kpis;

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Analytics</h2><div className="lr-page-sub">Live leave &amp; attendance analytics for {data.year}</div></div>
      </div>

      <div className="lr-kpi-row">
        <Kpi icon={<UserMinus size={20} />} tone="info" label="On leave today" value={k.on_leave_today} />
        <Kpi icon={<CalendarCheck size={20} />} tone="success" label="Attendance this month" value={`${k.attendance_this_month}%`} />
        <Kpi icon={<Clock size={20} />} tone="warning" label="Pending approvals" value={k.pending_approvals} />
        <Kpi icon={<AlertTriangle size={20} />} tone="danger" label="Approaching limit (>80%)" value={k.approaching_limit} />
      </div>

      <div className="lr-analytics-grid">
        <ChartCard title="Leave trend (last 12 months)">
          <LineChart data={data.leave_trend} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light)" />
            <XAxis dataKey="label" tick={{ fontSize: 'var(--fs-label)' }} interval={0} angle={-30} textAnchor="end" height={50} />
            <YAxis tick={{ fontSize: 'var(--fs-label)' }} />
            <Tooltip />
            <Line type="monotone" dataKey="leave_days" stroke="var(--brand-blue)" strokeWidth={2} dot={{ r: 2 }} name="Leave days" />
          </LineChart>
        </ChartCard>

        <ChartCard title="Leave by type">
          <PieChart>
            <Pie data={data.by_type} dataKey="value" nameKey="label" outerRadius={90} innerRadius={50} label>
              {data.by_type.map((entry, i) => <Cell key={entry.label} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
            </Pie>
            <Legend wrapperStyle={{ fontSize: 'var(--fs-meta)' }} />
            <Tooltip />
          </PieChart>
        </ChartCard>

        <ChartCard title="Leave by department">
          <BarChart data={data.by_department} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light)" />
            <XAxis dataKey="label" tick={{ fontSize: 'var(--fs-label)' }} />
            <YAxis tick={{ fontSize: 'var(--fs-label)' }} />
            <Tooltip />
            <Bar dataKey="value" fill="var(--brand-blue)" name="Days" />
          </BarChart>
        </ChartCard>

        <ChartCard title="Top 10 by leave usage">
          <BarChart data={data.top10} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 40 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light)" />
            <XAxis type="number" tick={{ fontSize: 'var(--fs-label)' }} />
            <YAxis type="category" dataKey="employee" width={90} tick={{ fontSize: 'var(--fs-label)' }} />
            <Tooltip />
            <Bar dataKey="used" fill="var(--success)" name="Days used" />
          </BarChart>
        </ChartCard>
      </div>

      <div className="lr-chart-card">
        <div className="lr-chart-title">Approval turnaround</div>
        <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>
          Average time from submission to approval: <b style={{ color: 'var(--text-primary)' }}>{data.approval_turnaround_hours} hours</b>.
        </p>
      </div>

      <div className="lr-chart-card">
        <div className="lr-chart-title">Leave density (last 60 days)</div>
        <div className="lr-heatmap" role="img" aria-label="Leave density heatmap for the last 60 days">
          {data.heatmap.length === 0 ? <span style={{ color: 'var(--text-muted)' }}>No approved leave in this window.</span> :
            data.heatmap.map((d) => (
              <span
                key={d.date}
                className="lr-heat-cell"
                title={`${d.date}: ${d.count} on leave`}
                style={{ background: `color-mix(in srgb, var(--brand-blue) ${Math.min(100, d.count * 25)}%, var(--bg-main))` }}
              />
            ))}
        </div>
      </div>
    </div>
  );
};

export default Analytics;
