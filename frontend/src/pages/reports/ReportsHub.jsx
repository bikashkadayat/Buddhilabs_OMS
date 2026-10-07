import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FileSpreadsheet, FileText, ArrowRight } from 'lucide-react';
import { reportService, REPORT_TYPES, saveBlob } from '../../services/reportService';
import { Skeleton } from '../../components/leave-records/States';

const ReportsHub = () => {
  const navigate = useNavigate();
  const { data: recent = [], isLoading } = useQuery({ queryKey: ['reports', 'mine'], queryFn: () => reportService.listReports(true) });

  const download = async (id, name) => {
    const res = await reportService.download(id);
    saveBlob(res, name);
  };

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Reports</h2><div className="lr-page-sub">Generate and download enterprise leave reports</div></div>
        <button type="button" className="lr-btn lr-btn-ghost" onClick={() => navigate('/reports/history')}>History <ArrowRight size={14} /></button>
      </div>

      <div className="lr-report-grid">
        {REPORT_TYPES.map((r) => (
          <button key={r.key} type="button" className="lr-report-card" onClick={() => navigate(`/reports/build/${r.key}`)}>
            <div className="lr-report-ico">{r.formats.includes('pdf') && !r.formats.includes('excel') ? <FileText size={22} /> : <FileSpreadsheet size={22} />}</div>
            <div className="lr-report-name">{r.name}</div>
            <div className="lr-report-desc">{r.desc}</div>
            <div className="lr-report-fmt">{r.formats.map((f) => <span key={f} className="lr-fmt-badge">{f.toUpperCase()}</span>)}</div>
          </button>
        ))}
      </div>

      <h3 className="lr-chart-title" style={{ marginTop: 28 }}>Recent reports</h3>
      {isLoading ? <Skeleton rows={2} /> : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead><tr><th scope="col">Report</th><th scope="col">Requested</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead>
            <tbody>
              {recent.slice(0, 10).map((run) => (
                <tr key={run.id}>
                  <td>{run.report_type_display}</td>
                  <td>{new Date(run.created_at).toLocaleString()}</td>
                  <td style={{ textTransform: 'capitalize' }}>{run.status}</td>
                  <td>{run.status === 'ready' ? <button type="button" className="lr-btn lr-btn-ghost" onClick={() => download(run.id, run.report_type_display)}>Download</button> : '—'}</td>
                </tr>
              ))}
              {recent.length === 0 && <tr><td colSpan={4} style={{ color: 'var(--text-muted)' }}>No reports generated yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default ReportsHub;
