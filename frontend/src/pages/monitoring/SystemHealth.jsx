import React from 'react';
import {
  AlertTriangle, BellOff, BellRing, CheckCircle2, Circle, RefreshCw, XCircle,
} from 'lucide-react';
import { useAlertStatus, useSystemHealth } from '../../hooks/useMonitoring';
import { ErrorState, Skeleton } from '../../components/leave-records/States';

/**
 * /monitoring — the system health board.
 *
 * Three deliberate choices:
 *
 * 1. **Red first.** Sections sort by severity, so the thing that is wrong is at
 *    the top of the page rather than wherever it happens to fall alphabetically.
 * 2. **Thresholds are shown, not hidden.** "Disk free 12%" means nothing on its
 *    own; "12% (amber below 20, red below 10)" tells you whether to act now.
 * 3. **An unconfigured alerter is itself a warning.** A monitoring page that
 *    looks green while nobody would ever be told about a failure is worse than
 *    no page at all.
 */
const STATE_META = {
  green: { icon: CheckCircle2, label: 'Healthy', className: 'mon-green' },
  amber: { icon: AlertTriangle, label: 'Degraded', className: 'mon-amber' },
  red: { icon: XCircle, label: 'Failing', className: 'mon-red' },
};

const SEVERITY_ORDER = { red: 0, amber: 1, green: 2 };

const StateIcon = ({ state, size = 16 }) => {
  const meta = STATE_META[state] || STATE_META.green;
  const Icon = meta.icon;
  return <Icon size={size} className={meta.className} aria-hidden="true" />;
};

const formatValue = (metric) => {
  const { value, unit } = metric;
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return unit ? `${value}${unit.startsWith('%') ? '' : ' '}${unit}` : String(value);
};

const thresholdHint = (metric) => {
  const t = metric.thresholds;
  if (!t) return null;
  if (t.interval_minutes) return `expected every ${t.interval_minutes} min`;
  if (t.amber === undefined) return null;
  return `amber ${t.amber} · red ${t.red}`;
};

const MetricRow = ({ metric }) => (
  <li className="mon-metric">
    <StateIcon state={metric.state} size={13} />
    <span className="mon-metric-label">{metric.label}</span>
    <span className={`mon-metric-value ${STATE_META[metric.state]?.className || ''}`}>
      {formatValue(metric)}
    </span>
    {(metric.detail || thresholdHint(metric)) && (
      <span className="mon-metric-detail">
        {metric.detail}
        {metric.detail && thresholdHint(metric) ? ' · ' : ''}
        {thresholdHint(metric)}
      </span>
    )}
  </li>
);

const Section = ({ section }) => (
  <section className={`wf-card mon-section mon-border-${section.state}`}>
    <div className="wf-card-head">
      <h2><StateIcon state={section.state} /> {section.label}</h2>
      <span className={`wf-badge wf-badge-sm ${STATE_META[section.state]?.className}`}>
        {STATE_META[section.state]?.label}
      </span>
    </div>
    <ul className="mon-metrics">
      {section.metrics.map((metric) => (
        <MetricRow key={metric.key} metric={metric} />
      ))}
    </ul>
  </section>
);

const SystemHealth = () => {
  const { data, isLoading, isError, error, refetch, isFetching } = useSystemHealth();
  const { data: alertData } = useAlertStatus();

  if (isLoading) return <div className="wf-page"><Skeleton rows={5} /></div>;
  if (isError) {
    return (
      <div className="wf-page">
        {/* A failed health check IS the answer, so it is shown rather than
            retried silently behind a spinner. */}
        <ErrorState error={error} onRetry={refetch} />
      </div>
    );
  }

  const sections = [...(data.sections || [])].sort(
    (a, b) => SEVERITY_ORDER[a.state] - SEVERITY_ORDER[b.state],
  );
  const overall = STATE_META[data.status] || STATE_META.green;
  const firing = alertData?.firing || [];

  return (
    <div className="wf-page mon-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">
            <StateIcon state={data.status} size={22} /> System health
          </h1>
          <p className="wf-sub">
            {overall.label} · {data.summary.red} failing, {data.summary.amber} degraded,
            {' '}{data.summary.green} healthy · as of{' '}
            {new Date(data.generated_at).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </p>
        </div>
        <button type="button" className="wf-btn wf-btn-ghost" onClick={() => refetch()}>
          <RefreshCw size={14} className={isFetching ? 'an-spin' : undefined}
                     aria-hidden="true" />
          Refresh
        </button>
      </header>

      {alertData && !alertData.configured && (
        <div className="wf-card mon-alert-banner">
          <BellOff size={16} aria-hidden="true" />
          <span>
            <strong>No alert recipients configured.</strong> Conditions are being
            detected and recorded, but nobody would be told. Set
            {' '}<code>ALERT_EMAILS</code> or <code>ALERT_WEBHOOK_URL</code>.
          </span>
        </div>
      )}

      {firing.length > 0 && (
        <section className="wf-card mon-firing">
          <div className="wf-card-head">
            <h2><BellRing size={18} aria-hidden="true" /> Active alerts ({firing.length})</h2>
          </div>
          <ul className="mon-alert-list">
            {firing.map((alert) => (
              <li key={alert.key} className={`mon-alert mon-sev-${alert.severity}`}>
                <span className="mon-alert-sev">{alert.severity}</span>
                <div>
                  <strong>{alert.title}</strong>
                  <span className="mon-alert-value">
                    {formatValue(alert)}{alert.detail ? ` · ${alert.detail}` : ''}
                  </span>
                  <span className="mon-alert-action">{alert.action}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {firing.length === 0 && (
        <p className="mon-allclear">
          <Circle size={10} className="mon-green" aria-hidden="true" /> No active alerts.
        </p>
      )}

      <div className="mon-grid">
        {sections.map((section) => (
          <Section key={section.key} section={section} />
        ))}
      </div>

      <p className="an-chart-note">
        Thresholds are defined server-side beside each measurement, so this page,
        the alert rules and the runbook cannot drift apart. Remediation steps for
        every alert are in <code>docs/RUNBOOK.md</code>.
      </p>
    </div>
  );
};

export default SystemHealth;
