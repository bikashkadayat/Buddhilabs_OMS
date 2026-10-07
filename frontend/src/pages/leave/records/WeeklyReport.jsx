import React, { useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts';
import { useWeeklySummaries } from '../../../hooks/useLeaveRecords';
import { num, days } from '../../../utils/leaveFormat';
import AttendanceIndicator from '../../../components/leave-records/AttendanceIndicator';
import LeaveTypeChip from '../../../components/leave-records/LeaveTypeChip';
import YearSelector from '../../../components/leave-records/YearSelector';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';
import { FileDown, Loader } from 'lucide-react';
import { leaveRecordService } from '../../../services/leaveRecordService';
import { saveBlob } from '../../../services/reportService';

const WeeklyReport = () => {
  const [year, setYear] = useState(new Date().getFullYear());
  const [pdfBusy, setPdfBusy] = useState(false);
  const { data = [], isLoading, isError, error, refetch } = useWeeklySummaries(year);

  const downloadPdf = async () => {
    setPdfBusy(true);
    try {
      saveBlob(await leaveRecordService.reportPdf('weekly', year), `weekly-leave-report-${year}`);
    } catch { /* the blob request failed; button re-enables */ } finally { setPdfBusy(false); }
  };

  const rows = [...data].sort((a, b) => a.week_number - b.week_number);
  const chartData = rows.map((w) => ({
    week: `W${w.week_number}`,
    attendance: num(w.attendance_percentage),
  }));

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Weekly Report</h2>
          <div className="lr-page-sub">Attendance and leave by ISO week</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <YearSelector currentYear={year} onChange={setYear} minYear={2023} />
          <button className="btn btn-primary" onClick={downloadPdf} disabled={pdfBusy || rows.length === 0}>
            {pdfBusy ? <Loader size={16} className="lr-spin" /> : <FileDown size={16} />} Download PDF
          </button>
        </div>
      </div>

      {isLoading && <Skeleton rows={2} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState message={`No weekly records for ${year}.`} />
      )}

      {!isLoading && !isError && rows.length > 0 && (
        <>
          <div className="lr-chart-card">
            <div className="lr-chart-title">Attendance % over weeks</div>
            <div style={{ width: '100%', height: 260 }}>
              <ResponsiveContainer>
                <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light)" />
                  <XAxis dataKey="week" tick={{ fontSize: 'var(--fs-label)' }} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 'var(--fs-label)' }} />
                  <Tooltip formatter={(v) => [`${v}%`, 'Attendance']} />
                  <ReferenceLine y={95} stroke="var(--success)" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="attendance" stroke="var(--brand-blue)" strokeWidth={2} dot={{ r: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">Weekly leave report for {year}</caption>
              <thead>
                <tr>
                  <th scope="col">Week</th><th scope="col">Date range</th>
                  <th scope="col">Working days</th><th scope="col">Leave days</th>
                  <th scope="col">Attendance</th><th scope="col">Breakdown</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((w) => (
                  <tr key={w.week_number}>
                    <td>W{w.week_number}</td>
                    <td>{w.week_start_date} → {w.week_end_date}</td>
                    <td>{w.working_days}</td>
                    <td>{days(w.total_leave_days)}</td>
                    <td><AttendanceIndicator percentage={w.attendance_percentage} /></td>
                    <td>
                      <div className="lr-chip-row">
                        {Object.entries(w.by_type || {}).map(([code, count]) => (
                          <LeaveTypeChip key={code} leaveType={code} count={count} />
                        ))}
                        {Object.keys(w.by_type || {}).length === 0 && <span style={{ color: 'var(--text-muted)' }}>—</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
};

export default WeeklyReport;
