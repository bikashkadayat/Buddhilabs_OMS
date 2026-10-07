import React from 'react';
import { useExecutiveAnalytics, useAnalyticsMeta } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import DepartmentRankTable from '../../components/analytics/DepartmentRankTable';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import {
  HEALTH_COLOURS, STATUS_COLOURS, fmtHours, fmtNum, fmtPct, healthBand,
} from '../../components/analytics/chartTheme';

/**
 * /analytics/executive — the headline health of the organisation.
 *
 * The whole page answers one question: is the workforce showing up, and where
 * is it not. Everything else is a drill-down behind a tile.
 */
const TREND_SERIES = [
  { key: 'present_pct', label: 'Present', colour: STATUS_COLOURS.present },
  { key: 'wfh_pct', label: 'Work from home', colour: STATUS_COLOURS.wfh },
  { key: 'leave_pct', label: 'On leave', colour: STATUS_COLOURS.on_leave },
  { key: 'absent_pct', label: 'Absent', colour: STATUS_COLOURS.absent },
];

const TREND_COLUMNS = [
  { key: 'label', label: 'Period' },
  { key: 'compliance_pct', label: 'Compliance', render: (r) => fmtPct(r.compliance_pct) },
  { key: 'present_pct', label: 'Present', render: (r) => fmtPct(r.present_pct) },
  { key: 'wfh_pct', label: 'WFH', render: (r) => fmtPct(r.wfh_pct) },
  { key: 'leave_pct', label: 'Leave', render: (r) => fmtPct(r.leave_pct) },
  { key: 'absent_pct', label: 'Absent', render: (r) => fmtPct(r.absent_pct) },
  { key: 'overtime_hours', label: 'Overtime', render: (r) => fmtHours(r.overtime_hours) },
];

const Executive = () => {
  const { data: meta } = useAnalyticsMeta();
  const define = (key) => meta?.definitions?.[key];

  return (
    <AnalyticsPage
      title="Executive analytics"
      description="Organisation-wide workforce health. Insight, not transactions."
      query={useExecutiveAnalytics}
      defaults={{ period: 'mtd', compare: 'previous' }}
      departments={meta?.departments || []}
      orgAnalyst
    >
      {({ payload, window, scope }) => {
        const kpis = payload.kpis || {};
        const departments = payload.departments || [];
        const rankable = departments.filter(
          (row) => !row.redacted && row.compliance_pct !== null);

        return (
          <>
            <ScopeNote scope={scope} window={window} />

            <KpiGrid>
              <KpiTile label="Headcount" value={kpis.headcount}
                       definition="Active employees in scope right now." />
              <KpiTile label="Attendance compliance" value={kpis.compliance_pct}
                       suffix="%" tone="brand" to="/analytics/attendance"
                       definition={define('compliance_pct')} />
              <KpiTile label="Present" value={kpis.present_pct} suffix="%"
                       to="/analytics/attendance" definition={define('present_pct')} />
              <KpiTile label="Absent" value={kpis.absent_pct} suffix="%"
                       higherIsBetter={false} to="/analytics/attendance"
                       definition={define('absent_pct')} />
              <KpiTile label="Late" value={kpis.late_pct} suffix="%"
                       higherIsBetter={false} to="/analytics/attendance"
                       definition={define('late_pct')} />
              <KpiTile label="Work from home" value={kpis.wfh_pct} suffix="%"
                       to="/analytics/wfh" definition={define('wfh_pct')} />
              <KpiTile label="On leave" value={kpis.leave_pct} suffix="%"
                       to="/analytics/leave" definition={define('leave_pct')} />
              <KpiTile label="Overtime hours" value={kpis.overtime_hours}
                       higherIsBetter={false} to="/analytics/management"
                       definition={define('overtime_hours')} />
              <KpiTile label="Comp days earned" value={kpis.comp_off_earned}
                       to="/analytics/comp-off" hint="in this window" />
              <KpiTile label="Comp days used" value={kpis.comp_off_used}
                       to="/analytics/comp-off" hint="all time" />
              <KpiTile label="Comp days pending" value={kpis.comp_off_pending}
                       higherIsBetter={false} to="/analytics/comp-off"
                       hint="awaiting HR confirmation" />
              <KpiTile label="Department health" value={kpis.department_health_score}
                       tone="brand" to="/analytics/departments"
                       definition={define('department_health_score')} />
            </KpiGrid>

            <div className="an-grid-2">
              <ChartFrame
                title="Monthly workforce trends"
                subtitle="Share of expected working days"
                isEmpty={!payload.trend?.length}
                rows={payload.trend} columns={TREND_COLUMNS}
              >
                <TrendChart data={payload.trend} series={TREND_SERIES} area stacked
                            yUnit="%" domain={[0, 100]} />
              </ChartFrame>

              <ChartFrame
                title="Department health"
                subtitle="Composite score, 0–100"
                note={`Weights: ${Object.entries(payload.health_weights || {})
                  .map(([name, weight]) => `${name.replace('_', ' ')} ${Math.round(weight * 100)}%`)
                  .join(' · ')}`}
                isEmpty={!rankable.length}
                empty="No department has enough data to score in this window."
                rows={rankable}
                columns={[
                  { key: 'department', label: 'Department' },
                  { key: 'health_score', label: 'Health', render: (r) => fmtNum(r.health_score) },
                  { key: 'compliance_pct', label: 'Compliance', render: (r) => fmtPct(r.compliance_pct) },
                ]}
              >
                <ComparisonBarChart
                  data={rankable.filter((row) => row.health_score !== null)}
                  labelKey="department"
                  bars={[{ key: 'health_score', label: 'Health score' }]}
                  colourFor={(row) => HEALTH_COLOURS[healthBand(row.health_score).tone]}
                  domain={[0, 100]}
                  height={280}
                />
              </ChartFrame>
            </div>

            <section className="wf-card">
              <div className="wf-card-head">
                <h2>Attendance compliance by department</h2>
              </div>
              <DepartmentRankTable rows={departments} />
            </section>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default Executive;
