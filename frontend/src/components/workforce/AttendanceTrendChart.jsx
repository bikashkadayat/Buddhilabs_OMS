import React, { useMemo } from 'react';
import {
  Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

const SERIES = [
  { key: 'present', label: 'Present', colour: 'var(--success, #16a34a)' },
  { key: 'late', label: 'Late', colour: 'var(--warning, #d97706)' },
  { key: 'wfh', label: 'WFH', colour: '#7c3aed' },
  { key: 'absent', label: 'Absent', colour: 'var(--danger, #dc2626)' },
];

/**
 * 30-day attendance trend.
 *
 * The reshape is memoised on `data`: recharts re-renders on every parent state
 * change, and rebuilding a 30-point series each time is the usual cause of
 * jank on a dashboard that also polls.
 */
const AttendanceTrendChart = ({ data = [], height = 260 }) => {
  const series = useMemo(
    () => data.map((day) => ({
      date: day.date?.slice(5) ?? '',
      present: day.present ?? 0,
      late: day.late ?? 0,
      wfh: day.wfh ?? 0,
      absent: day.absent ?? 0,
    })),
    [data],
  );

  if (!series.length) {
    return <p className="wf-muted">No attendance recorded in this window.</p>;
  }

  return (
    <div className="wf-chart" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={series} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light, #e5e7eb)" />
          <XAxis dataKey="date" tick={{ fontSize: 'var(--fs-label)' }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 'var(--fs-label)' }} allowDecimals={false} />
          <Tooltip
            contentStyle={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-light)',
              borderRadius: 8,
              fontSize: 'var(--fs-meta)',
            }}
          />
          <Legend wrapperStyle={{ fontSize: 'var(--fs-meta)' }} />
          {SERIES.map((s) => (
            <Area key={s.key} type="monotone" dataKey={s.key} name={s.label}
                  stackId="1" stroke={s.colour} fill={s.colour} fillOpacity={0.18} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

export default React.memo(AttendanceTrendChart);
