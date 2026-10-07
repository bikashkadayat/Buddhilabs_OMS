import React from 'react';
import { Link } from 'react-router-dom';
import { useAnalyticsMeta, useHrAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import DepartmentRankTable from '../../components/analytics/DepartmentRankTable';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import { STATUS_COLOURS, fmtNum } from '../../components/analytics/chartTheme';

/**
 * /analytics/hr — how well the process itself is running.
 *
 * Distinct from the HR command centre in Phase 9: that page is a queue you act
 * on, this one is the shape of the queue over time. Raised-against-resolved and
 * the age profile are the two views a snapshot count cannot give you.
 */
const QUEUES = [
  { key: 'corrections_department_head', label: 'Corrections — dept head',
    to: '/workforce/corrections?queue=manager' },
  { key: 'corrections_hr', label: 'Corrections — HR',
    to: '/workforce/corrections?queue=hr' },
  { key: 'wfh_pending', label: 'WFH pending', to: '/workforce/wfh' },
  { key: 'leave_department_head', label: 'Leave — dept head', to: '/leave/pending' },
  { key: 'leave_hr', label: 'Leave — HR', to: '/leave/pending' },
  { key: 'comp_off_pending', label: 'Comp days to confirm', to: '/workforce/comp-off' },
];

const AGE_BANDS = [
  { key: 'under_1d', label: 'Under a day', colour: STATUS_COLOURS.present },
  { key: '1_to_3d', label: '1–3 days', colour: STATUS_COLOURS.on_leave },
  { key: '3_to_7d', label: '3–7 days', colour: STATUS_COLOURS.late },
  { key: 'over_7d', label: 'Over a week', colour: STATUS_COLOURS.absent },
];

const WORKFLOWS = [
  { key: 'leave_department_head', label: 'Leave — dept head' },
  { key: 'leave_hr', label: 'Leave — HR' },
  { key: 'correction_department_head', label: 'Correction — dept head' },
  { key: 'correction_hr', label: 'Correction — HR' },
  { key: 'wfh', label: 'Work from home' },
];

const HRKpi = () => {
  const { data: meta } = useAnalyticsMeta();
  const define = (key) => meta?.definitions?.[key];

  return (
    <AnalyticsPage
      title="HR KPI dashboard"
      description="Compliance, correction volume, approval turnaround and queue health."
      query={useHrAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst
    >
      {({ payload, window, scope }) => {
        const kpis = payload.kpis || {};
        const corrections = payload.corrections || { totals: {}, trend: [] };
        const times = payload.approval_times || {};
        const queues = payload.queues || {};
        const mapping = payload.mapping || {};
        const wfh = payload.wfh || {};
        const comp = payload.comp_off || {};

        const turnaround = WORKFLOWS
          .map((workflow) => ({
            label: workflow.label,
            median: times[workflow.key]?.median_hours,
            p90: times[workflow.key]?.p90_hours,
            sample: times[workflow.key]?.sample ?? 0,
          }))
          .filter((row) => row.sample > 0);

        const ageRows = QUEUES
          .filter((queue) => queues[queue.key])
          .map((queue) => ({ label: queue.label, ...queues[queue.key].ages,
                             total: queues[queue.key].count }));

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Attendance compliance" value={kpis.compliance_pct}
                       suffix="%" tone="brand" definition={define('compliance_pct')} />
              <KpiTile label="Unexplained absences" value={kpis.unexplained_days}
                       higherIsBetter={false} tone="bad"
                       hint="expected days with no record and no leave" />
              <KpiTile label="Corrections raised" value={corrections.totals.raised}
                       hint="in this window" />
              <KpiTile label="Corrections applied" value={corrections.totals.approved} />
              <KpiTile label="Corrections reverted" value={corrections.totals.reverted}
                       higherIsBetter={false} />
              <KpiTile label="Open requests" value={queues.total_open}
                       higherIsBetter={false} />
              <KpiTile label="Unmapped device users" value={mapping.unmapped}
                       higherIsBetter={false} to="/analytics/devices" />
              <KpiTile label="Employees with no device"
                       value={mapping.employees_without_device}
                       higherIsBetter={false} to="/analytics/devices" />
              <KpiTile label="WFH approval rate" value={wfh.approval_rate_pct}
                       suffix="%" to="/analytics/wfh"
                       definition={define('wfh_approval_rate_pct')} />
              <KpiTile label="Comp days pending" value={comp.pending}
                       higherIsBetter={false} to="/workforce/comp-off" />
            </KpiGrid>

            <ChartFrame
              title="Correction requests: raised against resolved"
              subtitle="Two lines diverging is a backlog forming"
              isEmpty={!corrections.trend?.some((point) => point.raised || point.resolved)}
              empty="No correction requests in this window."
              rows={corrections.trend}
              columns={[
                { key: 'label', label: 'Period' },
                { key: 'raised', label: 'Raised' },
                { key: 'resolved', label: 'Resolved' },
              ]}
            >
              <TrendChart
                data={corrections.trend}
                series={[
                  { key: 'raised', label: 'Raised', colour: STATUS_COLOURS.late },
                  { key: 'resolved', label: 'Resolved', colour: STATUS_COLOURS.present },
                ]}
              />
            </ChartFrame>

            <div className="an-grid-2">
              <ChartFrame
                title="Approval turnaround"
                subtitle="Median hours from submission to decision"
                note={`Median, not mean — one forgotten request distorts a mean beyond use. The 90th percentile is in the data table.${times.wfh?.approximate ? ' WFH has no decision timestamp, so its figure is an upper bound.' : ''}`}
                isEmpty={!turnaround.length}
                empty="No decisions recorded in this window."
                rows={turnaround}
                columns={[
                  { key: 'label', label: 'Workflow' },
                  { key: 'median', label: 'Median', render: (r) => (r.median === null ? '—' : `${fmtNum(r.median)}h`) },
                  { key: 'p90', label: '90th percentile', render: (r) => (r.p90 === null ? '—' : `${fmtNum(r.p90)}h`) },
                  { key: 'sample', label: 'Decisions' },
                ]}
                height={Math.max(200, turnaround.length * 42)}
              >
                <ComparisonBarChart
                  data={turnaround} labelKey="label"
                  bars={[
                    { key: 'median', label: 'Median hours', colour: STATUS_COLOURS.compliance },
                    { key: 'p90', label: '90th percentile', colour: STATUS_COLOURS.late },
                  ]}
                  yUnit="h" height={Math.max(200, turnaround.length * 42)}
                />
              </ChartFrame>

              <ChartFrame
                title="How long open requests have been waiting"
                subtitle="Ages matter more than counts"
                isEmpty={!ageRows.some((row) => row.total)}
                empty="Every queue is empty."
                rows={ageRows}
                columns={[
                  { key: 'label', label: 'Queue' },
                  ...AGE_BANDS.map((band) => ({ key: band.key, label: band.label })),
                  { key: 'total', label: 'Total' },
                ]}
                height={Math.max(200, ageRows.length * 40)}
              >
                <ComparisonBarChart
                  data={ageRows} labelKey="label"
                  bars={AGE_BANDS.map((band) => ({
                    key: band.key, label: band.label, colour: band.colour,
                    stackId: 'age',
                  }))}
                  height={Math.max(200, ageRows.length * 40)}
                />
              </ChartFrame>
            </div>

            <section className="wf-card">
              <div className="wf-card-head"><h2>Open queues</h2></div>
              <div className="wf-queues">
                {QUEUES.filter((queue) => queues[queue.key]).map((queue) => (
                  <Link key={queue.key} to={queue.to} className="wf-queue">
                    <span className={`wf-queue-value wf-tone-${queues[queue.key].count ? 'warn' : 'muted'}`}>
                      {queues[queue.key].count}
                    </span>
                    <span className="wf-queue-label">{queue.label}</span>
                    {queues[queue.key].ages.over_7d > 0 && (
                      <span className="wf-badge wf-badge-warn wf-badge-sm">
                        {queues[queue.key].ages.over_7d} over a week
                      </span>
                    )}
                  </Link>
                ))}
              </div>
              {times.comp_off?.unmeasurable_reason === 'no_confirmation_timestamp' && (
                <p className="an-chart-note">
                  Comp-off turnaround cannot be measured: the ledger records no
                  confirmation timestamp. {times.comp_off.pending_count} day
                  {times.comp_off.pending_count === 1 ? '' : 's'} are pending
                  {times.comp_off.oldest_pending_hours
                    ? `, the oldest for ${fmtNum(times.comp_off.oldest_pending_hours / 24)} days.`
                    : '.'}
                </p>
              )}
            </section>

            <section className="wf-card">
              <div className="wf-card-head">
                <h2>Compliance by department</h2>
              </div>
              <DepartmentRankTable rows={payload.compliance_by_department || []}
                                   showHealth={false} />
            </section>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default HRKpi;
