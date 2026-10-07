import React from 'react';
import { AlertTriangle, Cpu, Link2Off, Radio } from 'lucide-react';
import { useDeviceAnalytics } from '../../hooks/useAnalytics';
import AnalyticsPage from '../../components/analytics/AnalyticsPage';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import TrendChart from '../../components/analytics/TrendChart';
import { STATUS_COLOURS, fmtPct, seriesColour } from '../../components/analytics/chartTheme';

/**
 * /analytics/devices — fleet health and mapping progress. HR and Admin only.
 *
 * Unscoped by department on purpose: a terminal at the main gate belongs to the
 * organisation, and a department head has nothing to do with its firmware.
 *
 * Mapping progress is the metric that matters most on this page. An unmapped
 * device user produces punches that never become attendance, so every figure on
 * every other dashboard is quietly short by that person's days until someone
 * maps them.
 */
const relativeTime = (iso) => {
  if (!iso) return 'never';
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1440)}d ago`;
};

const DeviceAnalytics = () => (
  <AnalyticsPage
    title="Device analytics"
    description="Biometric fleet health, ingest quality and employee mapping."
    query={useDeviceAnalytics}
    defaults={{ period: 'last_30d' }}
    orgAnalyst
    showCompare={false}
    showExport={false}
  >
    {({ payload }) => {
      const summary = payload.summary || {};
      const sync = payload.sync || {};
      const mapping = payload.mapping || {};
      const devices = payload.devices || [];

      return (
        <>
          <KpiGrid>
            <KpiTile label="Devices online" value={summary.online} tone="ok" />
            <KpiTile label="Devices offline" value={summary.offline}
                     higherIsBetter={false} tone={summary.offline ? 'bad' : 'default'} />
            <KpiTile label="Sync success rate" value={sync.success_rate_pct}
                     suffix="%" tone="brand"
                     definition="Successful ingest batches over total batches. Partial batches count as neither." />
            <KpiTile label="Failed syncs" value={sync.failed} higherIsBetter={false} />
            <KpiTile label="Pending punches" value={summary.pending_punches}
                     higherIsBetter={false}
                     definition="Punches queued on the terminals, not yet delivered." />
            <KpiTile label="Unprocessed punches" value={payload.unprocessed_punches}
                     higherIsBetter={false}
                     definition="Punches ingested but not yet turned into attendance rows." />
            <KpiTile label="Mapping progress" value={mapping.mapping_pct} suffix="%"
                     tone="brand"
                     definition="Device enrolments linked to a user account. An unmapped enrolment produces punches that never become attendance." />
            <KpiTile label="Employees with no device"
                     value={mapping.employees_without_device} higherIsBetter={false}
                     definition="Active employees with no active biometric enrolment — the reverse mapping gap." />
          </KpiGrid>

          {mapping.unmapped > 0 && (
            <div className="wf-card an-alert">
              <AlertTriangle size={16} aria-hidden="true" />
              <span>
                <strong>{mapping.unmapped}</strong> device enrolment
                {mapping.unmapped === 1 ? '' : 's'} are not linked to an account.
                Their punches will not become attendance until they are mapped.
              </span>
            </div>
          )}

          <ChartFrame
            title="Sync success over time"
            subtitle="Ingest batches per period"
            isEmpty={!payload.sync_trend?.some((point) => point.batches)}
            empty="No ingest batches recorded in this window."
            rows={payload.sync_trend}
            columns={[
              { key: 'label', label: 'Period' },
              { key: 'batches', label: 'Batches' },
              { key: 'failed', label: 'Failed' },
              { key: 'records_received', label: 'Records' },
              { key: 'success_rate_pct', label: 'Success', render: (r) => fmtPct(r.success_rate_pct) },
            ]}
          >
            <TrendChart
              data={payload.sync_trend}
              series={[{ key: 'success_rate_pct', label: 'Success rate',
                         colour: STATUS_COLOURS.present }]}
              yUnit="%" domain={[0, 100]}
            />
          </ChartFrame>

          <div className="an-grid-2">
            <ChartFrame
              title="Pending punches per device"
              isEmpty={!devices.some((device) => device.pending_punches)}
              empty="No device is holding a queue."
              rows={devices}
              columns={[
                { key: 'name', label: 'Device' },
                { key: 'pending_punches', label: 'Pending' },
                { key: 'failed_batches', label: 'Failed batches' },
              ]}
              height={Math.max(200, devices.length * 36)}
            >
              <ComparisonBarChart
                data={devices} labelKey="name"
                bars={[{ key: 'pending_punches', label: 'Pending punches' }]}
                colourFor={(_row, index) => seriesColour(index)}
                height={Math.max(200, devices.length * 36)}
              />
            </ChartFrame>

            <ChartFrame
              title="Mapping completion per device"
              isEmpty={!devices.length}
              empty="No active devices registered."
              rows={devices}
              columns={[
                { key: 'name', label: 'Device' },
                { key: 'mapped_count', label: 'Mapped' },
                { key: 'unmapped_count', label: 'Unmapped' },
                { key: 'mapping_pct', label: 'Complete', render: (r) => fmtPct(r.mapping_pct) },
              ]}
              height={Math.max(200, devices.length * 36)}
            >
              <ComparisonBarChart
                data={devices.filter((device) => device.mapping_pct !== null)}
                labelKey="name"
                bars={[{ key: 'mapping_pct', label: 'Mapped' }]}
                colourFor={(row) => (row.mapping_pct === 100
                  ? STATUS_COLOURS.present : STATUS_COLOURS.late)}
                yUnit="%" domain={[0, 100]}
                height={Math.max(200, devices.length * 36)}
              />
            </ChartFrame>
          </div>

          <section className="wf-card">
            <div className="wf-card-head">
              <h2><Cpu size={18} aria-hidden="true" /> Fleet</h2>
              {mapping.unmapped > 0 && (
                <span className="wf-badge wf-badge-warn">
                  <Link2Off size={12} aria-hidden="true" /> {mapping.unmapped} unmapped
                </span>
              )}
            </div>
            <div className="an-table-wrap">
              <table className="an-table">
                <caption className="sr-only">Biometric device fleet</caption>
                <thead>
                  <tr>
                    <th scope="col">Device</th>
                    <th scope="col">Status</th>
                    <th scope="col">Last seen</th>
                    <th scope="col">Last sync</th>
                    <th scope="col">Drift</th>
                    <th scope="col">Pending</th>
                    <th scope="col">Failed</th>
                    <th scope="col">Mapped</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((device) => (
                    <tr key={device.id}>
                      <th scope="row">
                        {device.name}
                        {device.location && (
                          <span className="wf-muted wf-small"> · {device.location}</span>
                        )}
                      </th>
                      <td>
                        <span className={device.connection_status === 'online'
                          ? 'wf-badge wf-badge-ok wf-badge-sm'
                          : 'wf-badge wf-badge-warn wf-badge-sm'}>
                          <Radio size={11} aria-hidden="true" /> {device.connection_status}
                        </span>
                      </td>
                      <td>{relativeTime(device.last_seen_at)}</td>
                      <td>{relativeTime(device.last_sync_at)}</td>
                      <td className="an-num">{device.clock_drift_seconds ?? 0}s</td>
                      <td className="an-num">{device.pending_punches}</td>
                      <td className="an-num">{device.failed_batches}</td>
                      <td className="an-num">{fmtPct(device.mapping_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="an-chart-note">
              Ingest yield in this window: {fmtPct(sync.ingest_yield_pct)} of
              {' '}{sync.records_received} records became new punches.
              {sync.records_duplicate > 0 && ` ${sync.records_duplicate} were duplicates (expected on re-sync).`}
              {sync.records_unmapped > 0 && ` ${sync.records_unmapped} belonged to unmapped device users.`}
              {sync.records_invalid > 0 && ` ${sync.records_invalid} were rejected as invalid.`}
            </p>
          </section>
        </>
      );
    }}
  </AnalyticsPage>
);

export default DeviceAnalytics;
