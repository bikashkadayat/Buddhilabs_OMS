import React from 'react';
import { Lock } from 'lucide-react';
import { useAnalyticsMeta, useDepartmentAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage, { ScopeNote } from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import DepartmentRankTable from '../../components/analytics/DepartmentRankTable';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import { fmtNum, fmtPct, seriesColour } from '../../components/analytics/chartTheme';

/**
 * /analytics/departments — comparison and the compliance ranking.
 *
 * A department head sees their own department in full, plus the organisation
 * average and their percentile — and every other department reduced to a rank.
 * That is a deliberate product decision, not a limitation: comparison is what
 * makes a number actionable, but another team's figures are not theirs to read.
 */
const Departments = () => {
  const { data: meta } = useAnalyticsMeta();
  const define = (key) => meta?.definitions?.[key];

  return (
    <AnalyticsPage
      title="Department analytics"
      description="Every department compared, ranked on attendance compliance."
      query={useDepartmentAnalytics}
      defaults={{ period: 'last_12m' }}
      departments={meta?.departments || []}
      orgAnalyst={meta?.scope?.level === 'organization'}
      exportTypes={['department_analytics', 'workforce_analytics']}
    >
      {({ payload, window, scope }) => {
        const rows = payload.departments || [];
        const visible = rows.filter((row) => !row.redacted);
        const average = payload.org_average || {};
        const own = payload.own_department;
        const trends = payload.trends || { series: [] };
        const redactedCount = rows.length - visible.length;

        // One dense series per department, coloured by stable position so
        // toggling or re-sorting never repaints the survivors.
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
              <KpiTile label="Departments" value={payload.department_count} />
              <KpiTile label="Org compliance" value={average.compliance_pct}
                       suffix="%" tone="brand" definition={define('compliance_pct')} />
              <KpiTile label="Org present rate" value={average.present_pct} suffix="%" />
              <KpiTile label="Org health score" value={average.health_score}
                       definition={define('department_health_score')} />
              {own && (
                <>
                  <KpiTile label={`${own.department} compliance`}
                           value={own.compliance_pct} suffix="%" tone="brand"
                           hint={own.rank ? `rank ${own.rank}` : 'not ranked'} />
                  <KpiTile label="Your percentile" value={payload.percentile}
                           suffix="%" hint="among ranked departments"
                           definition="The share of ranked departments with a lower compliance rate than yours." />
                </>
              )}
            </KpiGrid>

            <ChartFrame
              title="Attendance compliance by department"
              subtitle="Dashed line is the organisation average"
              isEmpty={!visible.length}
              rows={visible}
              columns={[
                { key: 'department', label: 'Department' },
                { key: 'headcount', label: 'Headcount' },
                { key: 'compliance_pct', label: 'Compliance', render: (r) => fmtPct(r.compliance_pct) },
                { key: 'health_score', label: 'Health', render: (r) => fmtNum(r.health_score) },
              ]}
              height={Math.max(220, visible.length * 36)}
              note={redactedCount > 0
                ? `${redactedCount} department${redactedCount === 1 ? '' : 's'} outside your scope are ranked but not shown.`
                : undefined}
            >
              <ComparisonBarChart
                data={visible.filter((row) => row.compliance_pct !== null)}
                labelKey="department"
                bars={[{ key: 'compliance_pct', label: 'Compliance' }]}
                colourFor={(row) => seriesColour(
                  visible.findIndex((item) => item.department === row.department))}
                referenceValue={average.compliance_pct}
                referenceLabel="org average"
                yUnit="%" domain={[0, 100]}
                height={Math.max(220, visible.length * 36)}
              />
            </ChartFrame>

            <ChartFrame
              title="Compliance trend by department"
              isEmpty={!trendRows.length}
              rows={trendRows}
              columns={[{ key: 'label', label: 'Period' },
                        ...trendSeries.map((entry) => ({
                          key: entry.key, label: entry.label,
                          render: (row) => fmtPct(row[entry.key]),
                        }))]}
              note={trends.folded_departments > 0
                ? `${trends.folded_departments} smaller department${trends.folded_departments === 1 ? '' : 's'} folded into "Others" — the chart shows at most ${trends.max_series} named series.`
                : undefined}
              height={300}
            >
              <TrendChart data={trendRows} series={trendSeries} yUnit="%"
                          domain={[0, 100]} height={300} />
            </ChartFrame>

            <section className="wf-card">
              <div className="wf-card-head">
                <h2>Ranked by attendance compliance</h2>
                {redactedCount > 0 && (
                  <span className="wf-badge wf-badge-info wf-badge-sm">
                    <Lock size={12} aria-hidden="true" /> {redactedCount} outside your scope
                  </span>
                )}
              </div>
              <DepartmentRankTable rows={rows} orgAverage={average.compliance_pct} />
              <p className="an-chart-note">
                Ranking uses attendance compliance only. Departments with fewer
                than {meta?.min_department_sample ?? 3} people are listed but not
                ranked — at that size a department rate is one person&apos;s
                attendance record. Individual employees are never scored.
              </p>
            </section>
          </>
        );
      }}
    </AnalyticsPage>
  );
};

export default Departments;
