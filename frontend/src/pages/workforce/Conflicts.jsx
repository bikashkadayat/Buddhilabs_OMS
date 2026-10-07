import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useConflicts } from '../../hooks/useWorkforce';
import { EmptyState, ErrorState, Skeleton } from '../../components/leave-records/States';
import StatusBadge from '../../components/workforce/StatusBadge';
import StatTile from '../../components/workforce/StatTile';
import DateRangePicker from '../../components/workforce/DateRangePicker';

const today = () => new Date().toISOString().slice(0, 10);
const quarterAgo = () => {
  const d = new Date();
  d.setDate(d.getDate() - 89);
  return d.toISOString().slice(0, 10);
};

const ConflictRow = React.memo(function ConflictRow({ row }) {
  return (
    <tr>
      <td>
        <div className="wf-strong">{row.employee_name}</div>
        <div className="wf-muted wf-small">{row.department || '—'}</div>
      </td>
      <td>{row.date}</td>
      <td className="wf-capitalise">{row.leave_type}</td>
      <td><StatusBadge status={row.attendance_status} size="sm" /></td>
      <td>{row.working_hours} h</td>
      <td className="wf-capitalise wf-muted wf-small">{row.attendance_source}</td>
    </tr>
  );
});

const Conflicts = () => {
  const [range, setRange] = useState({ from: quarterAgo(), to: today() });
  const { data, isLoading, isError, error, refetch } = useConflicts(range);

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">Leave &amp; attendance conflicts</h1>
          <p className="wf-sub">
            Days where a full day of approved leave met real recorded attendance.
            The day shows as worked <em>and</em> costs a leave day.
          </p>
        </div>
        <DateRangePicker from={range.from} to={range.to} onChange={setRange} />
      </header>

      <section className="wf-card">
        <div className="wf-card-head"><h2><AlertTriangle size={18} /> Summary</h2></div>
        {isLoading && <Skeleton rows={1} height={70} />}
        {isError && <ErrorState error={error} onRetry={refetch} />}
        {data && (
          <div className="wf-tiles">
            <StatTile label="Conflicting days" value={data.summary.total}
                      tone={data.summary.total ? 'bad' : 'ok'} />
            <StatTile label="Employees affected" value={data.summary.employees_affected}
                      tone="warn" />
            <StatTile label="Leave days double-counted"
                      value={data.summary.leave_days_double_counted} tone="bad" />
          </div>
        )}
        <p className="wf-muted wf-small">
          This report detects only. No leave is refunded and no attendance is
          altered — resolving a conflict stays an explicit decision.
        </p>
      </section>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Detail</h2></div>
        {!isLoading && !isError && data?.conflicts?.length === 0 && (
          <EmptyState message="No conflicts in this period. Leave and attendance agree."
                      ctaLabel="" ctaTo="" />
        )}
        {data?.conflicts?.length > 0 && (
          <div className="wf-table-scroll">
            <table className="wf-table">
              <thead>
                <tr>
                  <th>Employee</th><th>Date</th><th>Leave type</th>
                  <th>Recorded as</th><th>Hours</th><th>Source</th>
                </tr>
              </thead>
              <tbody>
                {data.conflicts.map((row) => (
                  <ConflictRow key={`${row.employee_id}-${row.date}`} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default Conflicts;
