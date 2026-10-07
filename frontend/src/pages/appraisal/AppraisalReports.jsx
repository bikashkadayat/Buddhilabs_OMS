import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, FileText } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import ExportButtons from '../../components/common/ExportButtons';

/**
 * Appraisal reports (Phase APM-03b).
 *
 * ONE TABLE COMPONENT FOR EVERY REPORT
 * ------------------------------------
 * Each report comes back in the same envelope — `columns`, `rows`, `summary` —
 * as the task module's, so this page renders all six without knowing what any
 * of them contains, and a seventh report needs no frontend change at all. The
 * COLUMN ORDER is the server's; re-ordering here would mean a CSV and a screen
 * that disagree about what the report is.
 *
 * ROW ORDER IS THE SERVER'S, AND THERE IS NO SORT CONTROL
 * -------------------------------------------------------
 * Every people-shaped report is returned alphabetically. A sortable column on a
 * promotion-readiness report is a ranking control, and it would be used as one.
 * The summary block is rendered verbatim, including its caveats — those
 * sentences are the report, not decoration around it.
 */
const AppraisalReports = () => {
  const [slug, setSlug] = useState(null);

  const list = useQuery({
    queryKey: ['appraisal', 'reports'],
    queryFn: appraisalService.getReports,
  });

  // The endpoint returns a bare array of {slug, label, description}.
  const reports = Array.isArray(list.data) ? list.data : [];
  const active = slug || reports[0]?.slug;

  const report = useQuery({
    queryKey: ['appraisal', 'report', active],
    queryFn: () => appraisalService.getReport(active),
    enabled: Boolean(active),
  });

  if (list.isLoading) return <div className="page"><Skeleton rows={3} /></div>;
  if (list.isError) {
    return (
      <div className="page">
        <ErrorState error={list.error} onRetry={list.refetch} />
      </div>
    );
  }

  const current = reports.find((r) => r.slug === active);

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Appraisal Reports</h1>
          <p className="lr-page-sub">
            Every report is ordered by name, never by any figure it contains.
          </p>
        </div>
      </div>

      <nav className="apr-report-tabs" aria-label="Reports">
        {reports.map((row) => (
          <button key={row.slug} type="button"
            className={`btn btn-sm ${row.slug === active ? 'btn-primary' : 'btn-ghost'}`}
            aria-current={row.slug === active ? 'page' : undefined}
            onClick={() => setSlug(row.slug)}>
            {row.label}
          </button>
        ))}
      </nav>

      {current?.description && (
        <p className="lr-page-sub">{current.description}</p>
      )}

      {active && (
        <div className="apr-goal-actions">
          <ExportButtons
            download={(format) =>
              appraisalService.downloadReport(active, format)}
            name={`appraisal-${active}`} size="btn btn-ghost btn-sm"
            labelPrefix="Download " />
        </div>
      )}

      {report.isLoading && <Skeleton rows={3} />}
      {report.isError && (
        <ErrorState error={report.error} onRetry={report.refetch} />
      )}

      {report.data && (
        <>
          {report.data.rows?.length ? (
            <div className="lr-table-wrap">
              <table className="lr-table">
                <caption className="sr-only">
                  {current?.label}. Rows in the order the server returned them.
                </caption>
                <thead>
                  <tr>
                    {report.data.columns.map((col) => (
                      <th key={col.key} scope="col">{col.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.data.rows.map((row, i) => (
                    <tr key={i}>
                      {report.data.columns.map((col, j) => (
                        j === 0
                          ? <th key={col.key} scope="row">{row[col.key]}</th>
                          : <td key={col.key}>{row[col.key]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="lr-page-sub">This report has no rows yet.</p>
          )}

          {report.data.summary && (
            <section className="memo-dash-section" aria-label="Report summary">
              <div className="memo-dash-head"><h3>Summary</h3></div>
              <dl className="apr-goal-body">
                {Object.entries(report.data.summary).map(([key, value]) => (
                  <React.Fragment key={key}>
                    <dt>{key.replace(/_/g, ' ')}</dt>
                    <dd>{String(value)}</dd>
                  </React.Fragment>
                ))}
              </dl>
            </section>
          )}
        </>
      )}
    </div>
  );
};

export default AppraisalReports;
