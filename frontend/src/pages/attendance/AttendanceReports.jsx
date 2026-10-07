import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileDown, Archive, User, Users, Loader } from 'lucide-react';
import { attendanceService } from '../../services/attendanceService';
import { saveBlob } from '../../services/reportService';

const todayISO = () => new Date().toISOString().slice(0, 10);
const now = new Date();

const AttendanceReports = () => {
  const [tab, setTab] = useState('single');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  // HR-safe options endpoint (employees + departments) — reachable by HR and
  // Admin, so the dropdowns populate for both (the old admin-only endpoints 403'd
  // for HR and left the dropdown silently empty).
  const { data: options, isLoading: optLoading, isError: optError, refetch: refetchOptions } = useQuery({
    queryKey: ['att-report-options'],
    queryFn: () => attendanceService.reportOptions(),
  });
  const employees = options?.employees || [];
  const departments = options?.departments || [];

  // Single-employee form
  const [empId, setEmpId] = useState('');
  const [mode, setMode] = useState('monthly');
  const [week, setWeek] = useState(todayISO());
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);

  // All-employees form
  const [bulkPeriod, setBulkPeriod] = useState('monthly');
  const [start, setStart] = useState(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`);
  const [end, setEnd] = useState(todayISO());
  const [dept, setDept] = useState('');

  const run = async (key, fn) => {
    setError(''); setBusy(key);
    try {
      const resp = await fn();
      saveBlob(resp, 'attendance-report');
    } catch (e) {
      setError(e?.response?.status === 403 ? 'Only HR and Admin can export attendance reports.' : 'Export failed. Please try again.');
    } finally {
      setBusy('');
    }
  };

  const downloadSingle = () => {
    if (!empId) { setError('Please select an employee.'); return; }
    if (mode === 'weekly') return run('single', () => attendanceService.reportWeekly(empId, week));
    return run('single', () => attendanceService.reportMonthly(empId, year, month));
  };

  const bulkParams = (output) => ({ period: bulkPeriod, start, end, ...(dept ? { department: dept } : {}), ...(output ? { output } : {}) });

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Attendance Reports</h2><div className="lr-page-sub">Export attendance as a PDF on your organization’s letterhead (HR and administrators only)</div></div>
      </div>

      <div className="att-tabs">
        <button type="button" className={`att-tab ${tab === 'single' ? 'on' : ''}`} onClick={() => setTab('single')}><User size={15} /> Single Employee</button>
        <button type="button" className={`att-tab ${tab === 'all' ? 'on' : ''}`} onClick={() => setTab('all')}><Users size={15} /> All Employees</button>
      </div>

      {error && <div className="att-err" style={{ marginBottom: 16 }}>{error}</div>}

      {optError && (
        <div className="att-err" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span>Could not load employees/departments — you may not have permission, or the server is unavailable.</span>
          <button type="button" className="btn btn-ghost" onClick={() => refetchOptions()}>Retry</button>
        </div>
      )}

      {tab === 'single' && (
        <div className="table-card" style={{ padding: 24, maxWidth: 640 }}>
          <label className="lr-field"><span>Employee</span>
            <select value={empId} onChange={(e) => setEmpId(e.target.value)} disabled={optLoading || optError}>
              <option value="">{optLoading ? 'Loading employees…' : optError ? 'Unavailable' : 'Select employee…'}</option>
              {employees.map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.employee_id || '—'})</option>)}
            </select>
          </label>
          <div className="att-seg">
            <button type="button" className={mode === 'weekly' ? 'on' : ''} onClick={() => setMode('weekly')}>Weekly</button>
            <button type="button" className={mode === 'monthly' ? 'on' : ''} onClick={() => setMode('monthly')}>Monthly</button>
          </div>
          {mode === 'weekly' ? (
            <label className="lr-field"><span>Any date in the week (Sun–Sat)</span><input type="date" value={week} onChange={(e) => setWeek(e.target.value)} /></label>
          ) : (
            <div className="att-row2">
              <label className="lr-field"><span>Month</span>
                <select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
                  {Array.from({ length: 12 }, (_, i) => <option key={i + 1} value={i + 1}>{new Date(2000, i, 1).toLocaleString('en', { month: 'long' })}</option>)}
                </select>
              </label>
              <label className="lr-field"><span>Year</span><input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} /></label>
            </div>
          )}
          <button type="button" className="btn btn-primary" disabled={busy === 'single' || optLoading || optError || !empId} onClick={downloadSingle}>
            {busy === 'single' ? <Loader size={16} className="lr-spin" /> : <FileDown size={16} />} Download PDF
          </button>
        </div>
      )}

      {tab === 'all' && (
        <div className="table-card" style={{ padding: 24, maxWidth: 720 }}>
          <div className="att-seg">
            <button type="button" className={bulkPeriod === 'weekly' ? 'on' : ''} onClick={() => setBulkPeriod('weekly')}>Weekly</button>
            <button type="button" className={bulkPeriod === 'monthly' ? 'on' : ''} onClick={() => setBulkPeriod('monthly')}>Monthly</button>
          </div>
          <div className="att-row2">
            <label className="lr-field"><span>Start date</span><input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label className="lr-field"><span>End date</span><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
          </div>
          <label className="lr-field"><span>Department (optional)</span>
            <select value={dept} onChange={(e) => setDept(e.target.value)}>
              <option value="">All departments</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <div className="att-actions" style={{ marginTop: 8 }}>
            <button type="button" className="btn btn-primary" disabled={!!busy} onClick={() => run('combined', () => attendanceService.reportAll(bulkParams()))}>
              {busy === 'combined' ? <Loader size={16} className="lr-spin" /> : <FileDown size={16} />} Download Combined PDF
            </button>
            <button type="button" className="btn btn-ghost" disabled={!!busy} onClick={() => run('zip', () => attendanceService.reportAll(bulkParams('zip')))}>
              {busy === 'zip' ? <Loader size={16} className="lr-spin" /> : <Archive size={16} />} Download ZIP
            </button>
          </div>
          <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginTop: 12 }}>Combined PDF has a cover page (org totals + department breakdown) then one section per employee. ZIP contains one PDF per employee.</div>
        </div>
      )}
    </div>
  );
};

export default AttendanceReports;
