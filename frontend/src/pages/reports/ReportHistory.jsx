import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { reportService, saveBlob } from '../../services/reportService';
import { Skeleton, EmptyState, ErrorState } from '../../components/leave-records/States';

const ReportHistory = () => {
  const { data: reports = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['reports', 'mine'], queryFn: () => reportService.listReports(true),
  });

  const download = async (id, name) => {
    const res = await reportService.download(id);
    saveBlob(res, name);
  };

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Report History</h2><div className="lr-page-sub">Your generated reports (downloadable for 30 days)</div></div>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && reports.length === 0 && <EmptyState message="You have not generated any reports yet." ctaLabel="Go to reports" ctaTo="/reports" />}

      {!isLoading && !isError && reports.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead><tr><th scope="col">Report</th><th scope="col">Requested</th><th scope="col">Completed</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead>
            <tbody>
              {reports.map((run) => (
                <tr key={run.id}>
                  <td>{run.report_type_display}</td>
                  <td>{new Date(run.created_at).toLocaleString()}</td>
                  <td>{run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</td>
                  <td style={{ textTransform: 'capitalize' }}>{run.status}{run.status === 'failed' && run.error ? ` — ${run.error}` : ''}</td>
                  <td>{run.status === 'ready' ? <button type="button" className="lr-btn lr-btn-ghost" onClick={() => download(run.id, run.report_type_display)}>Download</button> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default ReportHistory;
