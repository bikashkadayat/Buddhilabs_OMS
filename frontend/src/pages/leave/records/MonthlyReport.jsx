import React, { useMemo, useState } from 'react';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { useMonthlySummaries, useLeaveTypes } from '../../../hooks/useLeaveRecords';
import { num, days, monthName } from '../../../utils/leaveFormat';
import AttendanceIndicator from '../../../components/leave-records/AttendanceIndicator';
import LeaveTypeChip from '../../../components/leave-records/LeaveTypeChip';
import YearSelector from '../../../components/leave-records/YearSelector';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';
import { FileDown, Loader } from 'lucide-react';
import { leaveRecordService } from '../../../services/leaveRecordService';
import { saveBlob } from '../../../services/reportService';

const MonthlyReport = () => {
  const [year, setYear] = useState(new Date().getFullYear());
  const [pdfBusy, setPdfBusy] = useState(false);
  const { data = [], isLoading, isError, error, refetch } = useMonthlySummaries(year);

  const downloadPdf = async () => {
    setPdfBusy(true);
    try {
      saveBlob(await leaveRecordService.reportPdf('monthly', year), `monthly-leave-report-${year}`);
    } catch { /* request failed; button re-enables */ } finally { setPdfBusy(false); }
  };
  const { data: leaveTypes = [] } = useLeaveTypes();

  const rows = [...data].sort((a, b) => a.month - b.month);

  // Bar chart: leave days by type per month (stacked).
  const { chartData, typeCodes, typeColor } = useMemo(() => {
    const codes = new Set();
    const colorMap = {};
    for (const t of leaveTypes) colorMap[t.code] = t.display_color;
    const cd = rows.map((m) => {
      const entry = { month: monthName(m.month).slice(0, 3) };
      for (const [code, count] of Object.entries(m.by_type || {})) {
        entry[code] = num(count);
        codes.add(code);
      }
      return entry;
    });
    return { chartData: cd, typeCodes: [...codes], typeColor: colorMap };
  }, [rows, leaveTypes]);

  const fallback = ['#2563EB', '#EF4444', '#F59E0B', '#10B981', '#EC4899'];

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Monthly Report</h2>
          <div className="lr-page-sub">Leave days by type and attendance per month</div>
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
        <EmptyState message={`No monthly records for ${year}.`} />
      )}

      {!isLoading && !isError && rows.length > 0 && (
        <>
          <div className="lr-chart-card">
            <div className="lr-chart-title">Leave days by type per month</div>
            <div style={{ width: '100%', height: 280 }}>
              <ResponsiveContainer>
                <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light)" />
                  <XAxis dataKey="month" tick={{ fontSize: 'var(--fs-label)' }} />
                  <YAxis allowDecimals tick={{ fontSize: 'var(--fs-label)' }} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 'var(--fs-meta)' }} />
                  {typeCodes.map((code, i) => (
                    <Bar key={code} dataKey={code} stackId="leave" fill={typeColor[code] || fallback[i % fallback.length]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">Monthly leave report for {year}</caption>
              <thead>
                <tr>
                  <th scope="col">Month</th><th scope="col">Working days</th>
                  <th scope="col">Approved</th><th scope="col">Pending</th>
                  <th scope="col">Attendance</th><th scope="col">Breakdown</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.month}>
                    <td>{monthName(m.month)}</td>
                    <td>{m.working_days}</td>
                    <td>{days(m.approved_days)}</td>
                    <td>{days(m.pending_days)}</td>
                    <td><AttendanceIndicator percentage={m.attendance_percentage} /></td>
                    <td>
                      <div className="lr-chip-row">
                        {Object.entries(m.by_type || {}).map(([code, count]) => (
                          <LeaveTypeChip key={code} leaveType={code} count={count} color={typeColor[code]} />
                        ))}
                        {Object.keys(m.by_type || {}).length === 0 && <span style={{ color: 'var(--text-muted)' }}>—</span>}
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

export default MonthlyReport;
