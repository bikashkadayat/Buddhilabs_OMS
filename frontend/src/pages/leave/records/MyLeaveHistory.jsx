import React, { useState } from 'react';
import { useMyHistory } from '../../../hooks/useLeaveRecords';
import { num, days, monthName } from '../../../utils/leaveFormat';
import BalanceCard from '../../../components/leave-records/BalanceCard';
import LeaveTypeChip from '../../../components/leave-records/LeaveTypeChip';
import AttendanceIndicator from '../../../components/leave-records/AttendanceIndicator';
import YearSelector from '../../../components/leave-records/YearSelector';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';

const MonthRow = ({ summary }) => {
  const [open, setOpen] = useState(false);
  const byType = Object.entries(summary.by_type || {});
  return (
    <>
      <tr
        className="lr-month-row"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => !o); } }}
      >
        <td>{monthName(summary.month)}</td>
        <td>{days(summary.approved_days)}</td>
        <td>{days(summary.pending_days)}</td>
        <td>
          <div className="lr-chip-row">
            {byType.length === 0 ? <span style={{ color: 'var(--text-muted)' }}>—</span> :
              byType.map(([code, count]) => <LeaveTypeChip key={code} leaveType={code} count={count} />)}
          </div>
        </td>
        <td><AttendanceIndicator percentage={summary.attendance_percentage} /></td>
      </tr>
      {open && (
        <tr className="lr-detail-row">
          <td colSpan={5}>
            Working days this month: <b>{summary.working_days}</b> ·
            {' '}Total leave: <b>{days(summary.total_leave_days)}</b> days
          </td>
        </tr>
      )}
    </>
  );
};

const MyLeaveHistory = () => {
  const [year, setYear] = useState(new Date().getFullYear());
  const { data, isLoading, isError, error, refetch } = useMyHistory(year);

  const balances = data?.balances ?? [];
  const monthly = data?.monthly_summaries ?? [];
  const recent = data?.recent_leaves ?? [];

  const totalTaken = balances.reduce((s, b) => s + num(b.used_days), 0);
  const totalAvailable = balances.reduce((s, b) => s + num(b.available_days), 0);
  const mostUsed = balances.reduce(
    (best, b) => (num(b.used_days) > num(best?.used_days ?? -1) ? b : best), null,
  );

  const hasData = balances.length > 0 || monthly.length > 0 || recent.length > 0;

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>My Leave History</h2>
          <div className="lr-page-sub">Balances, monthly breakdown and recent applications</div>
        </div>
        <YearSelector currentYear={year} onChange={setYear} minYear={2023} />
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}

      {!isLoading && !isError && !hasData && <EmptyState />}

      {!isLoading && !isError && hasData && (
        <div className="lr-history-layout" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 300px', gap: 28 }}>
          <div>
            {/* Row 1: Balance cards */}
            <section aria-label="Leave balances">
              <div className="lr-balance-grid">
                {balances.map((b) => (
                  <BalanceCard
                    key={b.leave_type_code}
                    leaveType={b.leave_type_name || b.leave_type_code}
                    entitled={b.entitled_days}
                    used={b.used_days}
                    pending={b.pending_days}
                    available={b.available_days}
                    carriedForward={b.carried_forward_days}
                  />
                ))}
              </div>
            </section>

            {/* Row 2: Monthly breakdown */}
            <section aria-label="Monthly breakdown">
              <h3 className="lr-chart-title">Monthly breakdown</h3>
              <div className="lr-table-wrap">
                <table className="lr-table">
                  <caption className="sr-only">Monthly leave breakdown for {year}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Month</th>
                      <th scope="col">Approved</th>
                      <th scope="col">Pending</th>
                      <th scope="col">By type</th>
                      <th scope="col">Attendance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monthly.length === 0 ? (
                      <tr><td colSpan={5} style={{ color: 'var(--text-muted)' }}>No monthly records.</td></tr>
                    ) : monthly.map((m) => <MonthRow key={`${m.year}-${m.month}`} summary={m} />)}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Row 3: Recent applications */}
            <section aria-label="Recent applications" style={{ marginTop: 24 }}>
              <h3 className="lr-chart-title">Recent applications</h3>
              <div className="lr-table-wrap">
                <table className="lr-table">
                  <thead>
                    <tr>
                      <th scope="col">Type</th><th scope="col">From</th>
                      <th scope="col">To</th><th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.slice(0, 10).map((l) => (
                      <tr key={l.id}>
                        <td>{l.leave_type}</td><td>{l.start_date}</td>
                        <td>{l.end_date}</td>
                        <td style={{ textTransform: 'capitalize' }}>{l.status}</td>
                      </tr>
                    ))}
                    {recent.length === 0 && (
                      <tr><td colSpan={4} style={{ color: 'var(--text-muted)' }}>No applications this year.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          {/* Sidebar: quick stats */}
          <aside className="side-card" style={{ padding: 24, height: 'fit-content' }} aria-label="Quick stats">
            <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1.5, marginBottom: 20, color: 'var(--text-muted)' }}>
              {year} at a glance
            </h3>
            <div className="lr-side-stats">
              <div className="lr-side-stat"><span>Total taken</span><b>{days(totalTaken)}</b></div>
              <div className="lr-side-stat"><span>Remaining</span><b>{days(totalAvailable)}</b></div>
              <div className="lr-side-stat">
                <span>Most-used type</span>
                <b style={{ fontSize: 'var(--fs-body)' }}>{mostUsed && num(mostUsed.used_days) > 0 ? (mostUsed.leave_type_name || mostUsed.leave_type_code) : '—'}</b>
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
};

export default MyLeaveHistory;
