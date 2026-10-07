import React, { useState } from 'react';
import { useAnalyticsMeta, useAttendanceAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import {
  STATUS_COLOURS, fmtHours, fmtPct, seriesColour,
} from '../../components/analytics/chartTheme';

/**
 * /analytics/attendance — rates, exception trends and period comparison.
 *
 * The comparison strip is three views of one 36-month series, rolled up on the
 * server. Quarterly is not an average of monthly percentages: it is recomputed
 * from the raw counts, because a 19-working-day month and a 23-day one do not
 * carry equal weight.
 */
const EXCEPTION_SERIES = [
  { key: 'late_pct', label: 'Late', colour: STATUS_COLOURS.late },
  { key: 'absent_pct', label: 'Absent', colour: STATUS_COLOURS.absent },
  { key: 'half_day_pct', label: 'Half day', colour: STATUS_COLOURS.half_day },
];

const COMPARISON_VIEWS = [
  { key: 'monthly', label: 'Monthly' },
  { key: 'quarterly', label: 'Quarterly' },
  { key: 'yearly', label: 'Yearly' },
];

const RATE_COLUMNS = [
  { key: 'label', label: 'Period' },
  { key: 'expected_days', label: 'Expected days' },
  { key: 'attended_days', label: 'Attended' },
  { key: 'compliance_pct', label: 'Compliance', render: (r) => fmtPct(r.compliance_pct) },
  { key: 'present_pct', label: 'Present', render: (r) => fmtPct(r.present_pct) },
  { key: 'late_pct', label: 'Late', render: (r) => fmtPct(r.late_pct) },
  { key: 'absent_pct', label: 'Absent', render: (r) => fmtPct(r.absent_pct) },
];

const AttendanceTrends = () => {
  const { data: meta } = useAnalyticsMeta();
  const [view, setView] = useState('monthly');
  const define = (key) => meta?.definitions?.[key];

  return (
    <AnalyticsPage
      title="Attendance analytics"
      description="How attendance is moving, and where the exceptions sit."
      query={useAttendanceAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst={meta?.scope?.level === 'organization'}
      exportTypes={['attendance_analytics', 'department_analytics', 'workforce_analytics']}
    >
      {({ payload, window, scope }) => {
        const kpis = payload.kpis || {};
        const deltas = payload.comparison?.deltas || {};
        const comparison = payload.comparisons?.[view] || [];
        const departments = (payload.by_department || [])
          .filter((row) => !row.redacted);

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Attendance rate" value={kpis.present_pct} suffix="%"
                       delta={deltas.present_pct} tone="brand"
                       definition={define('present_pct')} />
              <KpiTile label="Compliance" value={kpis.compliance_pct} suffix="%"
                       delta={deltas.compliance_pct}
                       definition={define('compliance_pct')} />
              <KpiTile label="Late" value={kpis.late_pct} suffix="%"
                       delta={deltas.late_pct} higherIsBetter={false}
                       definition={define('late_pct')} />
              <KpiTile label="Absent" value={kpis.absent_pct} suffix="%"
                       delta={deltas.absent_pct} higherIsBetter={false}
                       definition={define('absent_pct')} />
              <KpiTile label="Half day" value={kpis.half_day_pct} suffix="%"
                       delta={deltas.half_day_pct} higherIsBetter={false}
                       definition={define('half_day_pct')} />
              <KpiTile label="Overtime" value={kpis.overtime_hours}
                       delta={deltas.overtime_hours} higherIsBetter={false}
                       definition={define('overtime_hours')} />
              <KpiTile label="Expected working days" value={kpis.expected_days}
                       definition={define('expected_working_days')} />
              <KpiTile label="Unexplained absences" value={kpis.unexplained_days}
                       higherIsBetter={false} tone="bad"
                       hint="no record, no leave" />
            </KpiGrid>

            <ChartFrame
              title="Attendance rate"
              subtitle="Share of expected working days"
              isEmpty={!payload.trend?.length}
              rows={payload.trend} columns={RATE_COLUMNS}
              note={payload.comparison
                ? `Dashed line: ${payload.comparison.window.from} to ${payload.comparison.window.to}`
                : undefined}
            >
              <TrendChart
                data={payload.trend}
                series={[
                  { key: 'present_pct', label: 'Present', colour: STATUS_COLOURS.present },
                  { key: 'compliance_pct', label: 'Compliance', colour: STATUS_COLOURS.compliance },
                ]}
                yUnit="%" domain={[0, 100]} height={280}
              />
            </ChartFrame>

            <div className="an-grid-2">
              <ChartFrame
                title="Exception trends"
                subtitle="Late, absent and half days"
                isEmpty={!payload.trend?.length}
                rows={payload.trend}
                columns={[
                  { key: 'label', label: 'Period' },
                  { key: 'late_pct', label: 'Late', render: (r) => fmtPct(r.late_pct) },
                  { key: 'absent_pct', label: 'Absent', render: (r) => fmtPct(r.absent_pct) },
                  { key: 'half_day_pct', label: 'Half day', render: (r) => fmtPct(r.half_day_pct) },
                ]}
              >
                <TrendChart data={payload.trend} series={EXCEPTION_SERIES} yUnit="%" />
              </ChartFrame>

              <ChartFrame
                title="Overtime"
                subtitle="Hours recorded beyond the policy threshold"
                isEmpty={!payload.trend?.some((point) => point.overtime_hours)}
                empty="No overtime recorded in this window."
                rows={payload.trend}
                columns={[
                  { key: 'label', label: 'Period' },
                  { key: 'overtime_hours', label: 'Overtime', render: (r) => fmtHours(r.overtime_hours) },
                  { key: 'worked_hours', label: 'Total worked', render: (r) => fmtHours(r.worked_hours) },
                ]}
              >
                <TrendChart
                  data={payload.trend} area
                  series={[{ key: 'overtime_hours', label: 'Overtime hours',
                             colour: STATUS_COLOURS.overtime }]}
                  yUnit="h"
                />
              </ChartFrame>
            </div>

            <ChartFrame
              title="Department comparison"
              subtitle="Attendance rate over the selected window"
              isEmpty={!departments.length}
              rows={departments}
              columns={[
                { key: 'department', label: 'Department' },
                { key: 'headcount', label: 'Headcount' },
                { key: 'present_pct', label: 'Present', render: (r) => fmtPct(r.present_pct) },
                { key: 'compliance_pct', label: 'Compliance', render: (r) => fmtPct(r.compliance_pct) },
              ]}
              height={Math.max(220, departments.length * 34)}
            >
              <ComparisonBarChart
                data={departments} labelKey="department"
                bars={[{ key: 'present_pct', label: 'Attendance rate' }]}
                colourFor={(row) => seriesColour(
                  departments.findIndex((item) => item.department === row.department))}
                yUnit="%" domain={[0, 100]}
                height={Math.max(220, departments.length * 34)}
              />
            </ChartFrame>

            <ChartFrame
              title="Period comparison"
              subtitle="Rates recomputed from raw counts, never averaged across periods"
              isEmpty={!comparison.length}
              rows={comparison} columns={RATE_COLUMNS}
              actions={(
                <div className="an-tabs" role="tablist" aria-label="Comparison view">
                  {COMPARISON_VIEWS.map((option) => (
                    <button
                      key={option.key} type="button" role="tab"
                      aria-selected={view === option.key}
                      className={view === option.key ? 'an-tab an-tab-on' : 'an-tab'}
                      onClick={() => setView(option.key)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
            >
              <ComparisonBarChart
                data={comparison} layout="vertical" labelKey="label"
                bars={[
                  { key: 'present_pct', label: 'Present', colour: STATUS_COLOURS.present },
                  { key: 'late_pct', label: 'Late', colour: STATUS_COLOURS.late },
                  { key: 'absent_pct', label: 'Absent', colour: STATUS_COLOURS.absent },
                ]}
                yUnit="%" height={280}
              />
            </ChartFrame>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default AttendanceTrends;
