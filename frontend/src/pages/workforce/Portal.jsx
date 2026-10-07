import React, { useMemo, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import {
  Clock, Coffee, FileEdit, Home, LogIn, LogOut, Timer, TrendingUp,
} from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { useMyWorkforce } from '../../hooks/useWorkforce';
import { attendanceService, getCurrentLocation } from '../../services/attendanceService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import StatusBadge from '../../components/workforce/StatusBadge';
import StatTile from '../../components/workforce/StatTile';
import { useQueryClient } from '@tanstack/react-query';

const hhmm = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';

const RecentRow = React.memo(function RecentRow({ day }) {
  return (
    <tr>
      <td>{day.date}</td>
      <td><StatusBadge status={day.status || 'not_applicable'} size="sm" /></td>
      <td>{hhmm(day.check_in)}</td>
      <td>{hhmm(day.check_out)}</td>
      <td>{day.working_hours}</td>
      <td>{Number(day.overtime_hours) > 0 ? day.overtime_hours : '—'}</td>
      <td>{day.late_minutes ? `${day.late_minutes}m` : '—'}</td>
    </tr>
  );
});

const Portal = () => {
  const { role } = useAuth();
  const client = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useMyWorkforce();
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  // Admin is an oversight role with no personal attendance — send them to the
  // command centre rather than showing an empty self-service page.
  const redirect = role === 'admin' ? <Navigate to="/workforce/hr" replace /> : null;

  const recent = useMemo(() => data?.recent_attendance ?? [], [data]);

  if (redirect) return redirect;
  if (isLoading) return <div className="wf-page"><Skeleton rows={4} /></div>;
  if (isError) return <div className="wf-page"><ErrorState error={error} onRetry={refetch} /></div>;

  const today = data.today || {};
  const mtd = data.month_to_date || {};
  const comp = data.comp_off || {};

  const punch = async (kind) => {
    setBusy(true);
    setActionError('');
    try {
      const coords = await getCurrentLocation();
      const payload = coords
        ? { latitude: coords.latitude, longitude: coords.longitude, accuracy: coords.accuracy }
        : {};
      await (kind === 'in' ? attendanceService.checkIn(payload) : attendanceService.checkOut(payload));
      client.invalidateQueries({ queryKey: ['workforce'] });
      client.invalidateQueries({ queryKey: ['attendance'] });
    } catch (err) {
      setActionError(err?.response?.data?.detail || 'Could not record that. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">My Workforce</h1>
          <p className="wf-sub">
            {data.employee?.name} · {data.employee?.department || 'No department'} · {data.date}
          </p>
        </div>
        <Link to="/workforce/corrections" className="wf-btn wf-btn-ghost">
          <FileEdit size={16} /> Corrections
        </Link>
      </header>

      {/* --- today ------------------------------------------------------- */}
      <section className="wf-card">
        <div className="wf-card-head">
          <h2><Clock size={18} /> Today</h2>
          <StatusBadge status={today.status || 'not_applicable'} />
        </div>

        <div className="wf-today">
          <div className="wf-today-times">
            <div><span className="wf-field-label">Check in</span><b>{hhmm(today.check_in)}</b></div>
            <div><span className="wf-field-label">Check out</span><b>{hhmm(today.check_out)}</b></div>
            <div><span className="wf-field-label">Worked</span><b>{today.working_hours} h</b></div>
            <div><span className="wf-field-label">Regular</span><b>{today.regular_hours} h</b></div>
            <div>
              <span className="wf-field-label">Overtime</span>
              <b className={Number(today.overtime_hours) > 0 ? 'wf-tone-accent' : ''}>
                {today.overtime_hours} h
              </b>
            </div>
            {today.late_minutes > 0 && (
              <div><span className="wf-field-label">Late by</span>
                <b className="wf-tone-warn">{today.late_minutes} min</b></div>
            )}
          </div>

          <div className="wf-today-actions">
            {today.can_check_in && (
              <button type="button" className="wf-btn wf-btn-primary"
                      disabled={busy} onClick={() => punch('in')}>
                <LogIn size={16} /> Check in
              </button>
            )}
            {today.can_check_out && (
              <button type="button" className="wf-btn wf-btn-primary"
                      disabled={busy} onClick={() => punch('out')}>
                <LogOut size={16} /> Check out
              </button>
            )}
            {!today.can_check_in && !today.can_check_out && (
              <span className="wf-muted">Nothing to do right now.</span>
            )}
          </div>
        </div>

        {actionError && <p className="wf-inline-error" role="alert">{actionError}</p>}

        {/* The rules that decided the status, so "why am I late?" is answerable
            without asking HR. */}
        <p className="wf-rulebar">
          <b>{data.shift?.name || data.policy?.name}</b>
          {data.shift?.start_time && <> · {data.shift.start_time}–{data.shift.end_time}</>}
          {' '}· Present until <b>{data.policy?.late_after}</b>
          {data.policy?.half_day_after && <> · Half day from <b>{data.policy.half_day_after}</b></>}
        </p>
      </section>

      {/* --- balances and summary --------------------------------------- */}
      <div className="wf-grid-2">
        <section className="wf-card">
          <div className="wf-card-head"><h2><TrendingUp size={18} /> This month</h2></div>
          <div className="wf-tiles">
            <StatTile label="Hours worked" value={mtd.working_hours} tone="ok" />
            <StatTile label="Regular" value={mtd.regular_hours} tone="muted" />
            <StatTile label="Overtime" value={mtd.overtime_hours} tone="accent" />
            <StatTile label="Late days" value={mtd.late_days} tone="warn"
                      hint={mtd.late_minutes ? `${mtd.late_minutes} min total` : undefined} />
          </div>
        </section>

        <section className="wf-card">
          <div className="wf-card-head">
            <h2><Coffee size={18} /> Comp off</h2>
            <Link to="/workforce/comp-off" className="wf-link">Details</Link>
          </div>
          <div className="wf-tiles">
            <StatTile label="Available" value={comp.available} tone="ok" />
            <StatTile label="Pending HR" value={comp.pending} tone="warn"
                      hint="Not spendable until confirmed" />
            <StatTile label="Earned" value={comp.earned} tone="muted" />
            <StatTile label="Used" value={comp.used} tone="muted" />
          </div>
        </section>
      </div>

      <div className="wf-grid-2">
        <section className="wf-card">
          <div className="wf-card-head"><h2>Leave balance</h2></div>
          {(data.leave_balances || []).length === 0
            ? <p className="wf-muted">No leave balances allocated for this year yet.</p>
            : (
              <table className="wf-table">
                <thead><tr><th>Type</th><th>Allocated</th><th>Used</th><th>Remaining</th></tr></thead>
                <tbody>
                  {data.leave_balances.map((b) => (
                    <tr key={b.leave_type}>
                      <td className="wf-strong wf-capitalise">{b.leave_type}</td>
                      <td>{b.total_allocated}</td>
                      <td>{b.used_so_far}</td>
                      <td><b>{b.remaining}</b></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </section>

        <section className="wf-card">
          <div className="wf-card-head">
            <h2><Home size={18} /> Work from home</h2>
            <Link to="/workforce/wfh" className="wf-link">Manage</Link>
          </div>
          <p className={data.wfh?.approved_today ? 'wf-tone-accent wf-strong' : 'wf-muted'}>
            {data.wfh?.approved_today
              ? 'You have approved work-from-home today.'
              : 'No approved work-from-home for today.'}
          </p>
          <div className="wf-tiles">
            <StatTile label="Pending" value={data.wfh?.counts?.pending} tone="warn" />
            <StatTile label="Approved" value={data.wfh?.counts?.approved} tone="ok" />
            <StatTile label="Rejected" value={data.wfh?.counts?.rejected} tone="bad" />
          </div>
        </section>
      </div>

      {/* --- corrections ------------------------------------------------- */}
      <section className="wf-card">
        <div className="wf-card-head">
          <h2><FileEdit size={18} /> My corrections</h2>
          <Link to="/workforce/corrections" className="wf-link">Open</Link>
        </div>
        <div className="wf-tiles">
          <StatTile label="In review" value={data.corrections?.open} tone="warn"
                    to="/workforce/corrections?queue=open" />
          <StatTile label="Applied" value={data.corrections?.applied} tone="ok" />
          <StatTile label="Rejected" value={data.corrections?.rejected} tone="bad" />
        </div>
      </section>

      {/* --- recent attendance ------------------------------------------ */}
      <section className="wf-card">
        <div className="wf-card-head"><h2><Timer size={18} /> Recent attendance</h2></div>
        <div className="wf-table-scroll">
          <table className="wf-table">
            <thead>
              <tr>
                <th>Date</th><th>Status</th><th>In</th><th>Out</th>
                <th>Hours</th><th>Overtime</th><th>Late</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((day) => <RecentRow key={day.date} day={day} />)}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default Portal;
