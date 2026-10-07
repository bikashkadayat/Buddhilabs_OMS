import React from 'react';
import { useAnalyticsMeta, useWfhAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import {
  STATUS_COLOURS, fmtDays, fmtPct, seriesColour,
} from '../../components/analytics/chartTheme';

/**
 * /analytics/wfh — trend, approval rate and the approved-but-not-worked gap.
 *
 * That gap is the reason this page exists as something other than a count.
 * Phase 8 established that approval alone is not attendance — a granted day
 * with no check-in produces no work record — and neither the approval count nor
 * the worked count shows it on its own.
 */
const WfhAnalytics = () => {
  const { data: meta } = useAnalyticsMeta();
  const define = (key) => meta?.definitions?.[key];

  return (
    <AnalyticsPage
      title="Work-from-home analytics"
      description="Requests, approvals, and whether approved days were actually worked."
      query={useWfhAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst={meta?.scope?.level === 'organization'}
      exportTypes={['workforce_analytics']}
      showCompare={false}
    >
      {({ payload, window, scope }) => {
        const summary = payload.summary || {};
        const requests = summary.requests || {};
        const byDepartment = (payload.by_department || []).filter((row) => !row.redacted);
        const approvalTime = payload.approval_time || {};

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Approval rate" value={summary.approval_rate_pct}
                       suffix="%" tone="brand"
                       definition={define('wfh_approval_rate_pct')} />
              <KpiTile label="Days worked from home"
                       value={summary.worked_from_home_days} />
              <KpiTile label="Approved day rows" value={summary.approved_day_rows}
                       hint="days covered by an approval" />
              <KpiTile label="Approved, not worked"
                       value={summary.approved_not_worked_days}
                       higherIsBetter={false} tone="bad"
                       definition="Days an approval covered where no work was recorded. Approval alone is not attendance." />
              <KpiTile label="Conversion" value={summary.conversion_pct} suffix="%"
                       definition="Days actually worked from home as a share of days approved." />
              <KpiTile label="Requests raised" value={summary.total_requests} />
              <KpiTile label="Pending" value={requests.pending}
                       higherIsBetter={false} to="/workforce/wfh" />
              <KpiTile
                label="Time to decide"
                value={approvalTime.hours} suffix="h" higherIsBetter={false}
                hint={approvalTime.approximate ? 'approximate' : undefined}
                definition="Mean hours from request to decision. WFH requests carry no decision timestamp, so this reads the last update and is an upper bound."
              />
            </KpiGrid>

            <ChartFrame
              title="Work-from-home over time"
              subtitle="Requests raised against days actually worked from home"
              note="A widening gap between the two lines means approvals are being granted for days that produce no work record."
              isEmpty={!payload.trend?.length}
              rows={payload.trend}
              columns={[
                { key: 'label', label: 'Period' },
                { key: 'requests_raised', label: 'Requests raised' },
                { key: 'wfh_days', label: 'Days worked' },
              ]}
              height={280}
            >
              <TrendChart
                data={payload.trend}
                series={[
                  { key: 'requests_raised', label: 'Requests raised',
                    colour: STATUS_COLOURS.on_leave },
                  { key: 'wfh_days', label: 'Days worked from home',
                    colour: STATUS_COLOURS.wfh },
                ]}
                height={280}
              />
            </ChartFrame>

            <ChartFrame
              title="Work from home by department"
              subtitle="Days per head"
              isEmpty={!byDepartment.some((row) => row.wfh_days)}
              empty="No work-from-home days recorded in this window."
              rows={byDepartment}
              columns={[
                { key: 'department', label: 'Department' },
                { key: 'headcount', label: 'Headcount' },
                { key: 'wfh_days', label: 'WFH days', render: (r) => fmtDays(r.wfh_days) },
                { key: 'days_per_capita', label: 'Per head', render: (r) => fmtDays(r.days_per_capita) },
              ]}
              height={Math.max(200, byDepartment.length * 34)}
            >
              <ComparisonBarChart
                data={byDepartment} labelKey="department"
                bars={[{ key: 'days_per_capita', label: 'WFH days per head' }]}
                colourFor={(row) => seriesColour(
                  byDepartment.findIndex((item) => item.department === row.department))}
                yUnit="d" height={Math.max(200, byDepartment.length * 34)}
              />
            </ChartFrame>

            <section className="wf-card">
              <div className="wf-card-head"><h2>Request outcomes</h2></div>
              <div className="an-kpi-grid">
                <KpiTile label="Approved" value={requests.approved} />
                <KpiTile label="Rejected" value={requests.rejected} higherIsBetter={false} />
                <KpiTile label="Cancelled" value={requests.cancelled} />
                <KpiTile label="Pending" value={requests.pending} higherIsBetter={false} />
              </div>
              <p className="an-chart-note">
                The approval rate divides approved by approved plus rejected.
                Cancelled and pending requests are excluded — a request the
                employee withdrew says nothing about how HR decides.
                {summary.conversion_pct !== null && (
                  <> Conversion is {fmtPct(summary.conversion_pct)} of approved
                  days.</>
                )}
              </p>
            </section>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default WfhAnalytics;
