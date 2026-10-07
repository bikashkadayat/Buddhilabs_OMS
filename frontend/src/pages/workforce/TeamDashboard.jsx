import React from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, FileEdit, Home, UserX, Users } from 'lucide-react';
import { useTeamDashboard } from '../../hooks/useWorkforce';
import { useAttendanceStream } from '../../hooks/useAttendanceStream';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { ErrorState, Skeleton } from '../../components/leave-records/States';
import { StatusTileRow } from '../../components/workforce/StatTile';
import AttendanceTrendChart from '../../components/workforce/AttendanceTrendChart';
import DepartmentSummaryTable from '../../components/workforce/DepartmentSummaryTable';

const QueueCard = ({ icon, label, value, to, tone = 'warn' }) => (
  <Link to={to} className="wf-queue">
    <span className={`wf-queue-value wf-tone-${value ? tone : 'muted'}`}>{value ?? 0}</span>
    <span className="wf-queue-label">{icon}{label}</span>
  </Link>
);

const TeamDashboard = () => {
  const { data, isLoading, isError, error, refetch } = useTeamDashboard();

  // Reuses the Phase 7 socket: events are invalidation signals, so a live
  // dashboard and a plain refresh can never show different numbers.
  const { connected } = useAttendanceStream({ onEvent: refetch });
  useAutoRefresh(refetch, connected ? 0 : 20000);

  if (isLoading) return <div className="wf-page"><Skeleton rows={4} /></div>;
  if (isError) return <div className="wf-page"><ErrorState error={error} onRetry={refetch} /></div>;

  const queues = data.queues || {};
  const mtd = data.month_to_date || {};

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">Team dashboard</h1>
          <p className="wf-sub">
            {data.team_size} people · {data.date}
            {data.is_holiday && ` · ${data.holiday_name}`}
            {connected && <span className="wf-live"> ● Live</span>}
          </p>
        </div>
      </header>

      <section className="wf-card">
        <div className="wf-card-head"><h2><Users size={18} /> Today</h2></div>
        <StatusTileRow counts={data.counts} presentNow={data.present_now} />
      </section>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Waiting on someone</h2></div>
        <div className="wf-queues">
          <QueueCard icon={<FileEdit size={14} />} label="Corrections to review"
                     value={queues.corrections_manager_stage}
                     to="/workforce/corrections?queue=manager" />
          <QueueCard icon={<FileEdit size={14} />} label="Corrections with HR"
                     value={queues.corrections_hr_stage} tone="info"
                     to="/workforce/corrections?queue=hr" />
          <QueueCard icon={<Home size={14} />} label="WFH requests pending"
                     value={queues.wfh_pending} to="/workforce/wfh" />
          <QueueCard icon={<AlertTriangle size={14} />} label="Leave/attendance conflicts"
                     value={data.conflicts?.total} tone="bad" to="/workforce/conflicts" />
        </div>
      </section>

      <div className="wf-grid-2">
        <section className="wf-card">
          <div className="wf-card-head"><h2>Month to date</h2></div>
          <div className="wf-tiles">
            <div className="wf-tile"><span className="wf-tile-value wf-tone-ok">{mtd.working}</span>
              <span className="wf-tile-label">Hours worked</span></div>
            <div className="wf-tile"><span className="wf-tile-value wf-tone-accent">{mtd.overtime}</span>
              <span className="wf-tile-label">Overtime hours</span></div>
            <div className="wf-tile"><span className="wf-tile-value wf-tone-warn">{mtd.late_days}</span>
              <span className="wf-tile-label">Late days</span></div>
            <div className="wf-tile"><span className="wf-tile-value">{mtd.late_minutes}</span>
              <span className="wf-tile-label">Late minutes</span></div>
          </div>
        </section>

        <section className="wf-card">
          <div className="wf-card-head"><h2><UserX size={18} /> Absent today</h2></div>
          {(data.absent_employees || []).length === 0
            ? <p className="wf-muted">Nobody is marked absent.</p>
            : (
              <ul className="wf-list">
                {data.absent_employees.map((person) => (
                  <li key={person.id}>
                    <span className="wf-strong">{person.name}</span>
                    <span className="wf-muted wf-small"> {person.employee_id || ''}</span>
                  </li>
                ))}
              </ul>
            )}
        </section>
      </div>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Attendance trend — last 30 days</h2></div>
        <AttendanceTrendChart data={data.trends} />
      </section>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Department summary</h2></div>
        <DepartmentSummaryTable rows={data.department_summary} />
      </section>
    </div>
  );
};

export default TeamDashboard;
