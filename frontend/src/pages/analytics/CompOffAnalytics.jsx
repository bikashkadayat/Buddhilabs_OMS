import React from 'react';
import { useAnalyticsMeta, useCompOffAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import { STATUS_COLOURS, fmtDays } from '../../components/analytics/chartTheme';

/**
 * /analytics/comp-off — earned, used, pending and the outstanding liability.
 *
 * Balances are cumulative and deliberately ignore the period filter: a day
 * earned two years ago and never taken is still owed today. Only the trend and
 * the "earned in window" tile respect the window, and both say so.
 */
const CompOffAnalytics = () => {
  const { data: meta } = useAnalyticsMeta();

  return (
    <AnalyticsPage
      title="Comp-off analytics"
      description="Compensatory days earned, used and still owed."
      query={useCompOffAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst={meta?.scope?.level === 'organization'}
      exportTypes={['workforce_analytics']}
      showCompare={false}
    >
      {({ payload, window, scope }) => {
        const balances = payload.balances || {};
        const earned = payload.earned_in_window || {};
        const byDepartment = (payload.by_department || []).filter((row) => !row.redacted);

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Earned (confirmed)" value={balances.earned}
                       hint="all time" />
              <KpiTile label="Used" value={balances.used} hint="all time" />
              <KpiTile label="Pending confirmation" value={balances.pending}
                       higherIsBetter={false} to="/workforce/comp-off"
                       hint="awaiting HR" />
              <KpiTile label="Available" value={balances.available} tone="brand"
                       higherIsBetter={false}
                       definition="Confirmed days earned minus days used. This is the outstanding liability." />
              <KpiTile label="Earned in window" value={earned.confirmed}
                       hint={`${window.from} to ${window.to}`} />
              <KpiTile label="Liability per head" value={payload.liability_per_capita}
                       higherIsBetter={false} suffix="d"
                       definition="Outstanding comp days divided by headcount in scope." />
            </KpiGrid>

            <ChartFrame
              title="Comp days over time"
              subtitle="Earned on the day worked; used on the day taken"
              isEmpty={!payload.trend?.some(
                (point) => point.earned_confirmed || point.earned_pending || point.used)}
              empty="No compensatory activity in this window."
              rows={payload.trend}
              columns={[
                { key: 'label', label: 'Period' },
                { key: 'earned_confirmed', label: 'Earned (confirmed)', render: (r) => fmtDays(r.earned_confirmed) },
                { key: 'earned_pending', label: 'Earned (pending)', render: (r) => fmtDays(r.earned_pending) },
                { key: 'used', label: 'Used', render: (r) => fmtDays(r.used) },
              ]}
              height={280}
            >
              <TrendChart
                data={payload.trend}
                series={[
                  { key: 'earned_confirmed', label: 'Earned (confirmed)',
                    colour: STATUS_COLOURS.present },
                  { key: 'earned_pending', label: 'Earned (pending)',
                    colour: STATUS_COLOURS.late },
                  { key: 'used', label: 'Used', colour: STATUS_COLOURS.on_leave },
                ]}
                yUnit="d" height={280}
              />
            </ChartFrame>

            <ChartFrame
              title="Position by department"
              subtitle="Cumulative — not limited to the selected window"
              isEmpty={!byDepartment.some(
                (row) => row.earned || row.used || row.pending)}
              empty="No compensatory ledger entries for any department."
              rows={byDepartment}
              columns={[
                { key: 'department', label: 'Department' },
                { key: 'earned', label: 'Earned', render: (r) => fmtDays(r.earned) },
                { key: 'used', label: 'Used', render: (r) => fmtDays(r.used) },
                { key: 'pending', label: 'Pending', render: (r) => fmtDays(r.pending) },
                { key: 'available', label: 'Available', render: (r) => fmtDays(r.available) },
              ]}
              height={Math.max(220, byDepartment.length * 40)}
            >
              <ComparisonBarChart
                data={byDepartment} labelKey="department"
                bars={[
                  { key: 'used', label: 'Used', colour: STATUS_COLOURS.on_leave,
                    stackId: 'comp' },
                  { key: 'available', label: 'Available', colour: STATUS_COLOURS.present,
                    stackId: 'comp' },
                  { key: 'pending', label: 'Pending', colour: STATUS_COLOURS.late,
                    stackId: 'comp' },
                ]}
                yUnit="d" height={Math.max(220, byDepartment.length * 40)}
              />
            </ChartFrame>

            <p className="an-chart-note">
              Outstanding comp days are a real accrual: {fmtDays(payload.liability_days)}
              {' '}are owed across {scope.headcount} employees. Pending days are not
              yet a liability — HR has not confirmed them.
            </p>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default CompOffAnalytics;
