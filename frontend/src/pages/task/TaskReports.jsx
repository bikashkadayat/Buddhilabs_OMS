import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BarChart3, Download, FileText } from 'lucide-react';

import { taskService } from '../../services/taskService';
import ExportButtons from '../../components/common/ExportButtons';
import TaskFilters from '../../components/task/TaskFilters';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The reports page (Phase T3, Part 8): five reports, one renderer.
 *
 * ONE TABLE FOR ALL FIVE
 * ----------------------
 * Every report returns the same {columns, rows, summary} envelope, so this page
 * renders whichever one is selected without knowing anything about it. That is
 * the whole reason the envelope is uniform: a report with its own shape needs
 * its own renderer, and the fifth one always ends up with a worse table than the
 * first. Adding a sixth report server-side makes it appear here with no change.
 *
 * The filter bar is the SAME component the list view uses, so "this quarter,
 * Finance only" means the same thing on a report as it does on a list — and the
 * CSV export carries the filters too, rather than silently exporting everything.
 */
const TaskReports = () => {
  const [slug, setSlug] = useState('completion');
  const [filters, setFilters] = useState({});

  const { data: catalogue } = useQuery({
    queryKey: ['tasks', 'reports'],
    queryFn: taskService.getReports,
    staleTime: 10 * 60 * 1000,
  });

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'report', slug, filters],
    queryFn: () => taskService.getReport(slug, filters),
  });

  const reports = catalogue || [];
  const chosen = reports.find((r) => r.slug === slug);

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Task Reports</h1>
          <p className="lr-page-sub">
            {chosen?.description
              || 'Completion, status, department, workload and overdue'}
          </p>
        </div>
        <div className="task-head-badges">
          {/* Plain links, not fetches: the browser downloads them with the
              session it already has, and a blob built in JavaScript would have
              to re-authenticate and re-implement the filename. */}
          {/* The screen's current filters travel with the export, so a report
              downloaded from a filtered view contains what was on the view. */}
          <ExportButtons
            download={(format) =>
              taskService.downloadReport(slug, format, filters)}
            name={`task-${slug}`} />
        </div>
      </div>

      <div className="task-report-picker" role="group" aria-label="Report">
        {reports.map((report) => (
          <button key={report.slug} type="button"
            aria-pressed={slug === report.slug}
            className={`lr-btn${slug === report.slug ? ' is-on' : ''}`}
            onClick={() => setSlug(report.slug)}>
            {report.label}
          </button>
        ))}
      </div>

      <TaskFilters value={filters} onChange={setFilters} />

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}

      {!isLoading && !isError && data && (
        <>
          {Object.keys(data.summary || {}).length > 0 && (
            <div className="memo-tiles">
              {Object.entries(data.summary).map(([key, value]) => (
                <span key={key} className="memo-tile tone-neutral">
                  <span className="memo-tile-value">
                    {/* null is "no data", not zero — see
                        analytics.average_completion_days. */}
                    {value === null || value === undefined ? '—' : String(value)}
                  </span>
                  <span className="memo-tile-label">
                    {key.replace(/_/g, ' ')}
                  </span>
                </span>
              ))}
            </div>
          )}

          {data.rows.length === 0 && (
            <p className="task-sub">
              <BarChart3 size={13} aria-hidden="true" /> Nothing to report for
              this selection.
            </p>
          )}

          {data.rows.length > 0 && (
            <div className="lr-table-wrap">
              <table className="lr-table">
                <caption className="sr-only">{data.label}</caption>
                <thead>
                  <tr>
                    {data.columns.map((column) => (
                      <th key={column.key} scope="col">{column.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, index) => (
                    <tr key={row.task_id || row.employee || row.department
                      || row.status || index}>
                      {data.columns.map((column) => {
                        const value = row[column.key];
                        return (
                          <td key={column.key}
                            style={column.numeric
                              ? { fontVariantNumeric: 'tabular-nums' } : undefined}>
                            {value === null || value === undefined ? '—'
                              : (typeof value === 'boolean'
                                ? (value ? 'Yes' : 'No') : String(value))}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default TaskReports;
