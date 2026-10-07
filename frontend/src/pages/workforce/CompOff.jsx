import React, { useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Check, Coffee, X } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import {
  useCompOffSummary, useConfirmCompOff, useMyWorkforce, useRejectCompOff,
} from '../../hooks/useWorkforce';
import { EmptyState, ErrorState, Skeleton } from '../../components/leave-records/States';
import StatTile from '../../components/workforce/StatTile';

const TrendChart = React.memo(function TrendChart({ trend = [] }) {
  const series = useMemo(
    () => trend.map((m) => ({
      month: m.month, Confirmed: Number(m.confirmed) || 0, Pending: Number(m.pending) || 0,
    })),
    [trend],
  );
  if (!series.length) return <p className="wf-muted">No compensatory days earned yet.</p>;
  return (
    <div className="wf-chart" style={{ height: 220 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={series} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-light, #e5e7eb)" />
          <XAxis dataKey="month" tick={{ fontSize: 'var(--fs-label)' }} />
          <YAxis tick={{ fontSize: 'var(--fs-label)' }} allowDecimals={false} />
          <Tooltip contentStyle={{
            background: 'var(--bg-card)', border: '1px solid var(--border-light)',
            borderRadius: 8, fontSize: 'var(--fs-meta)',
          }} />
          <Legend wrapperStyle={{ fontSize: 'var(--fs-meta)' }} />
          <Bar dataKey="Confirmed" fill="var(--success, #16a34a)" radius={[4, 4, 0, 0]} />
          <Bar dataKey="Pending" fill="var(--warning, #d97706)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
});

const PendingRow = React.memo(function PendingRow({ row, actions, canConfirm, isBusy }) {
  return (
    <tr>
      <td className="wf-strong">{row.employee}</td>
      <td>{row.source_date || '—'}</td>
      <td>{row.days}</td>
      <td className="wf-reason wf-muted wf-small">{row.note}</td>
      <td className="wf-row-actions">
        {canConfirm ? (
          <>
            <button type="button" className="wf-btn wf-btn-primary wf-btn-sm"
                    disabled={isBusy} onClick={() => actions.confirm(row)}>
              <Check size={14} /> Confirm
            </button>
            <button type="button" className="wf-btn wf-btn-danger wf-btn-sm"
                    disabled={isBusy} onClick={() => actions.reject(row)}>
              <X size={14} /> Reject
            </button>
          </>
        ) : <span className="wf-muted wf-small">Awaiting HR</span>}
      </td>
    </tr>
  );
});

const CompOff = () => {
  const { role } = useAuth();
  const canSeeTeam = ['checker', 'approver', 'admin'].includes(role);
  const [actionError, setActionError] = useState('');

  const mine = useMyWorkforce();
  const summary = useCompOffSummary();
  const confirm = useConfirmCompOff();
  const reject = useRejectCompOff();
  const isBusy = confirm.isPending || reject.isPending;

  const run = async (fn, args) => {
    setActionError('');
    try { await fn.mutateAsync(args); }
    catch (err) { setActionError(err?.response?.data?.detail || 'That didn’t go through. Please try again.'); }
  };

  const actions = useMemo(() => ({
    confirm: (row) => run(confirm, { id: row.id, note: '' }),
    reject: (row) => {
      const reason = window.prompt('Why is this compensatory day being rejected?');
      if (reason === null) return undefined;
      return run(reject, { id: row.id, reason });
    },
  }), [confirm, reject]);

  const myComp = mine.data?.comp_off;
  const data = summary.data;
  const canConfirm = Boolean(data?.can_confirm);

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">Compensatory off</h1>
          <p className="wf-sub">
            Earned by working a Saturday or public holiday. A day is only
            spendable once HR confirms it.
          </p>
        </div>
      </header>

      {/* --- personal balance, for everyone ----------------------------- */}
      <section className="wf-card">
        <div className="wf-card-head"><h2><Coffee size={18} /> My balance</h2></div>
        {mine.isLoading && <Skeleton rows={1} height={70} />}
        {mine.isError && <ErrorState error={mine.error} onRetry={mine.refetch} />}
        {myComp && (
          <div className="wf-tiles">
            <StatTile label="Available" value={myComp.available} tone="ok" />
            <StatTile label="Pending confirmation" value={myComp.pending} tone="warn"
                      hint="Not spendable yet" />
            <StatTile label="Earned (confirmed)" value={myComp.earned} tone="muted" />
            <StatTile label="Used" value={myComp.used} tone="muted" />
          </div>
        )}
      </section>

      {!canSeeTeam && (
        <p className="wf-muted">
          Compensatory days are confirmed by HR. You will see the balance update here
          once yours is approved.
        </p>
      )}

      {canSeeTeam && (
        <>
          <section className="wf-card">
            <div className="wf-card-head">
              <h2>{canConfirm ? 'Awaiting confirmation' : 'Pending with HR'}</h2>
            </div>
            {actionError && <p className="wf-inline-error" role="alert">{actionError}</p>}
            {summary.isLoading && <Skeleton rows={3} height={48} />}
            {summary.isError && <ErrorState error={summary.error} onRetry={summary.refetch} />}
            {data && data.pending_queue.length === 0 && (
              <EmptyState message="Nothing is waiting for confirmation." ctaLabel="" ctaTo="" />
            )}
            {data && data.pending_queue.length > 0 && (
              <div className="wf-table-scroll">
                <table className="wf-table">
                  <thead>
                    <tr><th>Employee</th><th>Worked on</th><th>Days</th><th>Note</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {data.pending_queue.map((row) => (
                      <PendingRow key={row.id} row={row} actions={actions}
                                  canConfirm={canConfirm} isBusy={isBusy} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {data && (
            <>
              <section className="wf-card">
                <div className="wf-card-head"><h2>Team balance</h2></div>
                <div className="wf-tiles">
                  <StatTile label="Available" value={data.summary.available} tone="ok" />
                  <StatTile label="Pending" value={data.summary.pending} tone="warn" />
                  <StatTile label="Earned" value={data.summary.earned} tone="muted" />
                  <StatTile label="Used" value={data.summary.used} tone="muted" />
                </div>
              </section>

              <section className="wf-card">
                <div className="wf-card-head"><h2>Earned per month</h2></div>
                <TrendChart trend={data.trend} />
              </section>

              <section className="wf-card">
                <div className="wf-card-head"><h2>History</h2></div>
                {data.history.length === 0
                  ? <p className="wf-muted">No ledger entries yet.</p>
                  : (
                    <div className="wf-table-scroll">
                      <table className="wf-table">
                        <thead>
                          <tr>
                            <th>Employee</th><th>Type</th><th>Source</th>
                            <th>Worked on</th><th>Days</th><th>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.history.map((row) => (
                            <tr key={row.id}>
                              <td className="wf-strong">{row.employee}</td>
                              <td className="wf-capitalise">{row.entry_type}</td>
                              <td className="wf-capitalise">{row.source.replace(/_/g, ' ')}</td>
                              <td>{row.source_date || '—'}</td>
                              <td>{row.days}</td>
                              <td>
                                <span className={`wf-badge wf-badge-${row.status === 'confirmed' ? 'ok' : 'warn'}`}>
                                  {row.status}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
};

export default CompOff;
