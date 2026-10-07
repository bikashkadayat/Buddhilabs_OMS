import React from 'react';
import { useAnalyticsMeta, useManagementAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import DepartmentRankTable from '../../components/analytics/DepartmentRankTable';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import {
  STATUS_COLOURS, fmtDays, fmtHours, fmtPct, seriesColour,
} from '../../components/analytics/chartTheme';

/**
 * /analytics/management — trends, utilization and forward capacity.
 *
 * The only forward-looking page, and deliberately the least speculative one:
 * capacity subtracts approved leave from the working calendar and predicts
 * nothing. The leave projection sits below it, clearly separated and labelled,
 * so a planning number and a guess never share a card.
 *
 * Department-scoped for a department head; organisation-wide for HR and Admin.
 */
const Management = () => {
  const { data: meta } = useAnalyticsMeta();
  const define = (key) => meta?.definitions?.[key];

  return (
    <AnalyticsPage
      title="Management KPI dashboard"
      description="Department trends, workforce utilization and capacity ahead."
      query={useManagementAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst={meta?.scope?.level === 'organization'}
      exportTypes={['department_analytics', 'overtime_analytics', 'workforce_analytics']}
    >
      {({ payload, window, scope }) => {
        const kpis = payload.kpis || {};
        const utilization = payload.utilization || {};
        const overtime = payload.overtime || {};
        const capacity = payload.capacity || { departments: [] };
        const trends = payload.department_trends || { series: [] };
        const departments = (payload.departments || []).filter((row) => !row.redacted);
        const forecast = payload.leave_forecast || { points: [] };

        const trendRows = (trends.series[0]?.points || []).map((point, index) => {
          const row = { label: point.label, period: point.period };
          trends.series.forEach((entry) => {
            row[entry.department || 'Unknown'] = entry.points[index]?.compliance_pct ?? null;
          });
          return row;
        });
        const trendSeries = trends.series.map((entry, index) => ({
          key: entry.department || 'Unknown',
          label: entry.department || 'Unknown',
          colour: seriesColour(index, entry.is_aggregate),
        }));

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Headcount" value={kpis.headcount} />
              <KpiTile label="Workforce utilization" value={utilization.utilization_pct}
                       suffix="%" tone="brand" definition={define('utilization_pct')} />
              <KpiTile label="Attendance" value={kpis.present_pct} suffix="%"
                       definition={define('present_pct')} />
              <KpiTile label="Absent" value={kpis.absent_pct} suffix="%"
                       higherIsBetter={false} definition={define('absent_pct')} />
              <KpiTile label="Compliance" value={kpis.compliance_pct} suffix="%"
                       definition={define('compliance_pct')} />
              <KpiTile label="Overtime hours" value={kpis.overtime_hours}
                       higherIsBetter={false} />
              <KpiTile label="Overtime per head" value={kpis.overtime_per_capita}
                       suffix="h" higherIsBetter={false} />
              <KpiTile label="Staff working overtime" value={overtime.employees_pct}
                       suffix="%" higherIsBetter={false}
                       hint={`${overtime.employees_with_overtime ?? 0} people`}
                       definition="Share of employees with any overtime. Total hours alone hides whether the load is spread or carried by a few." />
            </KpiGrid>

            <ChartFrame
              title="Department compliance trends"
              isEmpty={!trendRows.length}
              rows={trendRows}
              columns={[{ key: 'label', label: 'Period' },
                        ...trendSeries.map((entry) => ({
                          key: entry.key, label: entry.label,
                          render: (row) => fmtPct(row[entry.key]),
                        }))]}
              note={trends.folded_departments > 0
                ? `${trends.folded_departments} smaller department${trends.folded_departments === 1 ? '' : 's'} folded into "Others".`
                : undefined}
              height={300}
            >
              <TrendChart data={trendRows} series={trendSeries} yUnit="%"
                          domain={[0, 100]} height={300} />
            </ChartFrame>

            <div className="an-grid-2">
              <ChartFrame
                title="Workforce capacity, next 30 days"
                subtitle="Working days available after approved leave"
                note="Nothing here is predicted: this is the working calendar minus leave that is already approved."
                isEmpty={!capacity.departments?.length}
                rows={capacity.departments}
                columns={[
                  { key: 'department', label: 'Department' },
                  { key: 'capacity_days', label: 'Capacity', render: (r) => fmtDays(r.capacity_days) },
                  { key: 'leave_days', label: 'On leave', render: (r) => fmtDays(r.leave_days) },
                  { key: 'available_days', label: 'Available', render: (r) => fmtDays(r.available_days) },
                  { key: 'availability_pct', label: 'Availability', render: (r) => fmtPct(r.availability_pct) },
                ]}
                height={Math.max(200, (capacity.departments?.length || 1) * 40)}
              >
                <ComparisonBarChart
                  data={capacity.departments} labelKey="department"
                  bars={[
                    { key: 'available_days', label: 'Available',
                      colour: STATUS_COLOURS.present, stackId: 'capacity' },
                    { key: 'leave_days', label: 'On leave',
                      colour: STATUS_COLOURS.on_leave, stackId: 'capacity' },
                  ]}
                  yUnit="d"
                  height={Math.max(200, (capacity.departments?.length || 1) * 40)}
                />
              </ChartFrame>

              <ChartFrame
                title="Overtime per head by department"
                subtitle="Per head, so the biggest team does not simply top the chart"
                isEmpty={!departments.some((row) => row.overtime_hours)}
                empty="No overtime recorded in this window."
                rows={departments}
                columns={[
                  { key: 'department', label: 'Department' },
                  { key: 'overtime_hours', label: 'Total', render: (r) => fmtHours(r.overtime_hours) },
                  { key: 'overtime_per_capita', label: 'Per head', render: (r) => fmtHours(r.overtime_per_capita) },
                  { key: 'overtime_days', label: 'Days with OT' },
                ]}
                height={Math.max(200, departments.length * 36)}
              >
                <ComparisonBarChart
                  data={departments} labelKey="department"
                  bars={[{ key: 'overtime_per_capita', label: 'Overtime hours per head' }]}
                  colourFor={(row) => seriesColour(
                    departments.findIndex((item) => item.department === row.department))}
                  yUnit="h" height={Math.max(200, departments.length * 36)}
                />
              </ChartFrame>
            </div>

            <section className="wf-card">
              <div className="wf-card-head"><h2>Attendance overview by department</h2></div>
              <DepartmentRankTable rows={payload.departments || []} />
              <p className="an-chart-note">
                Utilization compares {fmtHours(utilization.regular_hours)} of
                regular hours worked against {fmtHours(utilization.capacity_hours)} of
                calendar capacity ({utilization.expected_days} expected working
                days at {utilization.standard_day_hours}h, read from the
                attendance policy).
                {forecast.method !== 'insufficient_history' && (
                  <> Leave projected for the next three months:
                  {' '}{forecast.points.map((point) => `${point.label} ${fmtDays(point.projected_days)}`).join(' · ')}.
                  {' '}See Leave analytics for the method.</>
                )}
              </p>
            </section>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default Management;
