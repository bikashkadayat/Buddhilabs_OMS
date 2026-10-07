import React from 'react';
import { TrendingUp } from 'lucide-react';
import { useAnalyticsMeta, useLeaveAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import DistributionPieChart from '../../components/analytics/DistributionPieChart';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import {
  STATUS_COLOURS, fmtDays, fmtPct, seriesColour,
} from '../../components/analytics/chartTheme';

/**
 * /analytics/leave — consumption, utilization, distribution and a projection.
 *
 * The forecast is labelled a projection everywhere it appears, carries the
 * method that produced it, and renders nothing at all when there is too little
 * history. A confident-looking number with no basis is the failure mode this
 * page is most exposed to.
 */
const FORECAST_METHODS = {
  seasonal_mean_2y: 'Seasonal baseline — the mean of this calendar month over the past two years.',
  trailing_6m: 'Trailing baseline — the mean of the last six months. Not enough history for a seasonal view yet.',
  insufficient_history: 'Not enough history to project. At least six months of leave records are needed.',
};

const LeaveAnalytics = () => {
  const { data: meta } = useAnalyticsMeta();

  return (
    <AnalyticsPage
      title="Leave analytics"
      description="What leave is used, by whom in aggregate, and what is coming."
      query={useLeaveAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst={meta?.scope?.level === 'organization'}
      exportTypes={['workforce_analytics', 'department_analytics']}
      showCompare={false}
    >
      {({ payload, window, scope }) => {
        const byType = payload.by_type || [];
        const utilization = payload.balance_utilization || { types: [] };
        const byDepartment = (payload.by_department || []).filter((row) => !row.redacted);
        const forecast = payload.forecast || { points: [] };
        const totalDays = byType.reduce((sum, row) => sum + (row.days || 0), 0);
        const canProject = forecast.method !== 'insufficient_history';

        // The forecast chart continues the history line with a dashed segment,
        // so the projected part is visually distinct from measured data.
        const forecastRows = [
          ...(payload.consumption_trend || []).slice(-6).map((point) => ({
            label: point.label, actual: point.leave_days, projected: null })),
          ...forecast.points.map((point) => ({
            label: point.label, actual: null, projected: point.projected_days })),
        ];

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Leave days taken" value={totalDays}
                       hint="approved, working days only" />
              <KpiTile label="Leave types used" value={byType.length} />
              <KpiTile
                label="Most used type"
                value={byType[0]?.label || '—'}
                hint={byType[0] ? `${fmtDays(byType[0].days)} · ${fmtPct(byType[0].share_pct)}` : undefined}
              />
              <KpiTile
                label="Entitlement used"
                value={utilization.types.length
                  ? utilization.types.reduce((sum, row) => sum + (row.used_days || 0), 0)
                  : null}
                hint={`${utilization.year} allowance`}
              />
            </KpiGrid>

            <div className="an-grid-2">
              <ChartFrame
                title="Most used leave types"
                subtitle="Share of approved leave days"
                isEmpty={!byType.length}
                rows={byType}
                columns={[
                  { key: 'label', label: 'Leave type' },
                  { key: 'days', label: 'Days', render: (r) => fmtDays(r.days) },
                  { key: 'share_pct', label: 'Share', render: (r) => fmtPct(r.share_pct) },
                ]}
              >
                <DistributionPieChart
                  data={byType.map((row) => ({ ...row, colour: row.colour }))}
                  valueKey="days" unit="d"
                />
              </ChartFrame>

              <ChartFrame
                title="Leave consumption"
                subtitle="Approved working-day leave per period"
                isEmpty={!payload.consumption_trend?.length}
                rows={payload.consumption_trend}
                columns={[
                  { key: 'label', label: 'Period' },
                  { key: 'leave_days', label: 'Working days', render: (r) => fmtDays(r.leave_days) },
                  { key: 'total_days', label: 'All days', render: (r) => fmtDays(r.total_days) },
                ]}
              >
                <TrendChart
                  data={payload.consumption_trend} area
                  series={[{ key: 'leave_days', label: 'Leave days',
                             colour: STATUS_COLOURS.on_leave }]}
                  yUnit="d"
                />
              </ChartFrame>
            </div>

            <div className="an-grid-2">
              <ChartFrame
                title="Balance utilization"
                subtitle={`Used against entitlement, ${utilization.year}`}
                isEmpty={!utilization.types.length}
                empty="No entitlement balances recorded for this year."
                rows={utilization.types}
                columns={[
                  { key: 'label', label: 'Leave type' },
                  { key: 'entitled_days', label: 'Entitled', render: (r) => fmtDays(r.entitled_days) },
                  { key: 'used_days', label: 'Used', render: (r) => fmtDays(r.used_days) },
                  { key: 'remaining_days', label: 'Remaining', render: (r) => fmtDays(r.remaining_days) },
                  { key: 'utilization_pct', label: 'Utilization', render: (r) => fmtPct(r.utilization_pct) },
                ]}
                height={Math.max(200, utilization.types.length * 40)}
              >
                <ComparisonBarChart
                  data={utilization.types.filter((row) => row.utilization_pct !== null)}
                  labelKey="label"
                  bars={[{ key: 'utilization_pct', label: 'Utilization' }]}
                  colourFor={(row) => row.colour || seriesColour(0)}
                  referenceValue={100} referenceLabel="full allowance"
                  yUnit="%" height={Math.max(200, utilization.types.length * 40)}
                />
              </ChartFrame>

              <ChartFrame
                title="Leave by department"
                subtitle="Days per head, so a large team does not simply win"
                isEmpty={!byDepartment.length}
                rows={byDepartment}
                columns={[
                  { key: 'department', label: 'Department' },
                  { key: 'leave_days', label: 'Days', render: (r) => fmtDays(r.leave_days) },
                  { key: 'days_per_capita', label: 'Per head', render: (r) => fmtDays(r.days_per_capita) },
                  { key: 'share_pct', label: 'Share', render: (r) => fmtPct(r.share_pct) },
                ]}
                height={Math.max(200, byDepartment.length * 34)}
              >
                <ComparisonBarChart
                  data={byDepartment} labelKey="department"
                  bars={[{ key: 'days_per_capita', label: 'Leave days per head' }]}
                  colourFor={(row) => seriesColour(
                    byDepartment.findIndex((item) => item.department === row.department))}
                  yUnit="d" height={Math.max(200, byDepartment.length * 34)}
                />
              </ChartFrame>
            </div>

            <ChartFrame
              title="Leave projection"
              subtitle="Next three months — a projection, not a measurement"
              isEmpty={!canProject}
              empty={FORECAST_METHODS.insufficient_history}
              note={`${FORECAST_METHODS[forecast.method]} Based on ${forecast.months_of_history} month${forecast.months_of_history === 1 ? '' : 's'} of history. Already-approved leave is a floor: the projection is never below what is booked.`}
              rows={forecast.points}
              columns={[
                { key: 'label', label: 'Month' },
                { key: 'approved_days', label: 'Already approved', render: (r) => fmtDays(r.approved_days) },
                { key: 'baseline_days', label: 'Historical baseline', render: (r) => fmtDays(r.baseline_days) },
                { key: 'projected_days', label: 'Projected', render: (r) => fmtDays(r.projected_days) },
              ]}
              actions={(
                <span className="wf-badge wf-badge-info wf-badge-sm">
                  <TrendingUp size={12} aria-hidden="true" /> Projection
                </span>
              )}
            >
              <TrendChart
                data={forecastRows}
                series={[
                  { key: 'actual', label: 'Recorded', colour: STATUS_COLOURS.on_leave },
                  { key: 'projected', label: 'Projected', colour: STATUS_COLOURS.on_leave,
                    dashed: true },
                ]}
                yUnit="d"
              />
            </ChartFrame>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default LeaveAnalytics;
