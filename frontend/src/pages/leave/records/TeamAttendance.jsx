import React, { useState } from 'react';
import { useTeamAttendance } from '../../../hooks/useLeaveRecords';
import { days } from '../../../utils/leaveFormat';
import AttendanceIndicator from '../../../components/leave-records/AttendanceIndicator';
import LeaveTypeChip from '../../../components/leave-records/LeaveTypeChip';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';

const currentMonth = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

const muted = { color: 'var(--text-muted)' };

// Collapse the biometric block (from the device, matched by biometric_id) into
// the three cells the table shows, plus a short hint for the non-ready states.
const bioCells = (b) => {
  if (!b || b.linked === false) return { present: '—', late: '—', inout: <span style={muted}>Not linked</span> };
  if (b.available === false) return { present: '—', late: '—', inout: <span style={muted}>Device offline</span> };
  if (b.found === false) return { present: '—', late: '—', inout: <span style={muted}>ID not on device</span> };
  const arr = b.median_arrival, dep = b.median_departure;
  const inout = arr || dep ? `${arr || '—'} – ${dep || '—'}` : <span style={muted}>No punches</span>;
  return {
    present: b.days_present ?? 0,
    late: b.late_days ? <span style={{ color: 'var(--warning)', fontWeight: 600 }}>{b.late_days}</span> : 0,
    inout,
  };
};

const TeamAttendance = () => {
  const [month, setMonth] = useState(currentMonth());
  const [dept, setDept] = useState('');
  const { data, isLoading, isError, error, refetch } = useTeamAttendance(dept || undefined, month);

  const team = data?.team ?? [];

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Team Attendance</h2>
          <div className="lr-page-sub">
            Monthly leave summary and biometric presence from the device
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <label className="lr-year-select">
            <span className="sr-only">Department code</span>
            <input
              type="text" placeholder="Dept code (optional)" value={dept}
              onChange={(e) => setDept(e.target.value.toUpperCase())}
              aria-label="Department code"
              style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '8px 12px', fontSize: 'var(--fs-body)' }}
            />
          </label>
          <label className="lr-year-select">
            <span className="sr-only">Month</span>
            <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month"
              style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '8px 12px', fontSize: 'var(--fs-body)' }} />
          </label>
        </div>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && team.length === 0 && (
        <EmptyState message="Nobody in this department yet. Choose another department, or add people from Administration → Users." ctaTo={undefined} />
      )}

      {!isLoading && !isError && team.length > 0 && data?.biometric_available === false && (
        <div className="lr-note" style={{
          margin: '0 0 12px', padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--text-muted)',
          background: 'var(--surface-2, #f8fafc)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
        }}>
          The biometric device is unreachable right now — presence columns show “Device offline”.
        </div>
      )}

      {!isLoading && !isError && team.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">Team attendance for {month}</caption>
            <thead>
              <tr>
                <th scope="col">Employee</th><th scope="col">Role</th>
                <th scope="col">Working days</th>
                <th scope="col" title="Days present on the biometric device this month">Present</th>
                <th scope="col" title="Days the first punch was after office start">Late</th>
                <th scope="col" title="Typical first / last punch">In · Out</th>
                <th scope="col">Approved</th>
                <th scope="col">Pending</th><th scope="col">Leave %</th>
                <th scope="col">By type</th>
              </tr>
            </thead>
            <tbody>
              {team.map((row) => {
                const bio = bioCells(row.biometric);
                return (
                <tr key={row.user.id}>
                  <td>{row.user.full_name}</td>
                  <td style={{ textTransform: 'capitalize' }}>{row.user.role}</td>
                  <td>{row.working_days}</td>
                  <td style={{ fontWeight: 600 }}>{bio.present}</td>
                  <td>{bio.late}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{bio.inout}</td>
                  <td>{days(row.approved_days)}</td>
                  <td>{days(row.pending_days)}</td>
                  <td><AttendanceIndicator percentage={row.attendance_percentage} /></td>
                  <td>
                    <div className="lr-chip-row">
                      {Object.entries(row.by_type || {}).map(([code, count]) => (
                        <LeaveTypeChip key={code} leaveType={code} count={count} />
                      ))}
                      {Object.keys(row.by_type || {}).length === 0 && <span style={{ color: 'var(--text-muted)' }}>—</span>}
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default TeamAttendance;
