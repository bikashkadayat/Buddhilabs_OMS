import React from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, Coffee, Cpu, FileEdit, Home, Link2Off, Radio, Users,
} from 'lucide-react';
import { useHRDashboard } from '../../hooks/useWorkforce';
import { useAttendanceStream } from '../../hooks/useAttendanceStream';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { ErrorState, Skeleton } from '../../components/leave-records/States';
import StatTile, { StatusTileRow } from '../../components/workforce/StatTile';
import AttendanceTrendChart from '../../components/workforce/AttendanceTrendChart';
import DepartmentSummaryTable from '../../components/workforce/DepartmentSummaryTable';
import RecentPunchesFeed from '../../components/attendance/RecentPunchesFeed';

const QueueCard = ({ icon, label, value, to, tone = 'warn' }) => (
  <Link to={to} className="wf-queue">
    <span className={`wf-queue-value wf-tone-${value ? tone : 'muted'}`}>{value ?? 0}</span>
    <span className="wf-queue-label">{icon}{label}</span>
  </Link>
);

const DeviceRow = React.memo(function DeviceRow({ device, offline }) {
  return (
    <li className={offline ? 'wf-device wf-device-off' : 'wf-device'}>
      <span className="wf-strong">{device.name}</span>
      <span className="wf-muted wf-small">{device.label}</span>
      {device.unmapped_count > 0 && (
        <span className="wf-badge wf-badge-warn wf-badge-sm">
          {device.unmapped_count} unmapped
        </span>
      )}
      {device.pending_punches > 0 && (
        <span className="wf-badge wf-badge-info wf-badge-sm">
          {device.pending_punches} queued
        </span>
      )}
    </li>
  );
});

const CommandCenter = () => {
  const { data, isLoading, isError, error, refetch } = useHRDashboard();
  const { connected } = useAttendanceStream({ onEvent: refetch });
  useAutoRefresh(refetch, connected ? 0 : 20000);

  if (isLoading) return <div className="wf-page"><Skeleton rows={5} /></div>;
  if (isError) return <div className="wf-page"><ErrorState error={error} onRetry={refetch} /></div>;

  const devices = data.devices || { online: [], offline: [], pending_mapping: 0 };
  const queues = data.queues || {};
  const comp = data.comp_off || {};
  const mtd = data.month_to_date || {};

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">HR command center</h1>
          <p className="wf-sub">
            {data.headcount} employees · {data.date}
            {data.is_holiday && ` · ${data.holiday_name}`}
            {connected && <span className="wf-live"> ● Live</span>}
          </p>
        </div>
      </header>

      <section className="wf-card">
        <div className="wf-card-head"><h2><Users size={18} /> Attendance today</h2></div>
        <StatusTileRow
          counts={data.counts}
          presentNow={data.present_now}
          extra={
            <StatTile label="Comp-off eligible" value={data.comp_off_eligible_today}
                      tone="accent" hint="Worked a Saturday or holiday"
                      to="/workforce/comp-off" />
          }
        />
      </section>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Approval queues</h2></div>
        <div className="wf-queues">
          <QueueCard icon={<FileEdit size={14} />} label="Corrections with HR"
                     value={queues.corrections_hr_stage}
                     to="/workforce/corrections?queue=hr" />
          <QueueCard icon={<FileEdit size={14} />} label="Corrections with dept heads"
                     value={queues.corrections_manager_stage} tone="info"
                     to="/workforce/corrections?queue=manager" />
          <QueueCard icon={<Home size={14} />} label="WFH pending"
                     value={queues.wfh_pending} to="/workforce/wfh" />
          <QueueCard icon={<Coffee size={14} />} label="Comp days to confirm"
                     value={queues.comp_off_pending} to="/workforce/comp-off" />
          <QueueCard icon={<AlertTriangle size={14} />} label="Leave awaiting HR"
                     value={queues.leave_pending_hr} tone="info" to="/leave/pending" />
        </div>
      </section>

      <div className="wf-grid-2">
        <section className="wf-card">
          <div className="wf-card-head">
            <h2><Cpu size={18} /> Devices</h2>
            {devices.pending_mapping > 0 && (
              <span className="wf-badge wf-badge-warn">
                <Link2Off size={12} /> {devices.pending_mapping} pending mapping
              </span>
            )}
          </div>
          {devices.online.length === 0 && devices.offline.length === 0 && (
            <p className="wf-muted">No devices registered.</p>
          )}
          {devices.online.length > 0 && (
            <>
              <h3 className="wf-subhead wf-tone-ok">Online ({devices.online.length})</h3>
              <ul className="wf-list">
                {devices.online.map((d) => <DeviceRow key={d.id} device={d} />)}
              </ul>
            </>
          )}
          {devices.offline.length > 0 && (
            <>
              <h3 className="wf-subhead wf-tone-bad">Offline ({devices.offline.length})</h3>
              <ul className="wf-list">
                {devices.offline.map((d) => <DeviceRow key={d.id} device={d} offline />)}
              </ul>
            </>
          )}
        </section>

        <section className="wf-card">
          <div className="wf-card-head">
            <h2><Coffee size={18} /> Comp off (org)</h2>
            <Link to="/workforce/comp-off" className="wf-link">Manage</Link>
          </div>
          <div className="wf-tiles">
            <StatTile label="Available" value={comp.available} tone="ok" />
            <StatTile label="Pending" value={comp.pending} tone="warn" />
            <StatTile label="Earned" value={comp.earned} tone="muted" />
            <StatTile label="Used" value={comp.used} tone="muted" />
          </div>
          <h3 className="wf-subhead">Month to date</h3>
          <div className="wf-tiles">
            <StatTile label="Hours worked" value={mtd.working} tone="ok" />
            <StatTile label="Overtime" value={mtd.overtime} tone="accent" />
            <StatTile label="Late days" value={mtd.late_days} tone="warn" />
          </div>
        </section>
      </div>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Attendance trend — last 30 days</h2></div>
        <AttendanceTrendChart data={data.trends} />
      </section>

      <section className="wf-card">
        <div className="wf-card-head"><h2>Department breakdown</h2></div>
        <DepartmentSummaryTable rows={data.department_breakdown} />
      </section>

      <section className="wf-card">
        <div className="wf-card-head"><h2><Radio size={18} /> Recent punches</h2></div>
        {/* Reused verbatim from Phase 7 — the same feed, the same shape. */}
        <RecentPunchesFeed punches={data.recent_punches} />
      </section>
    </div>
  );
};

export default CommandCenter;
