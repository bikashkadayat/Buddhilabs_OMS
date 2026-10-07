import React, { useMemo, useState } from 'react';
import { Check, Home, Plus, X } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import {
  useApproveWfh, useCancelWfh, useRejectWfh, useSubmitWfh, useWfhRequests, useWfhSummary,
} from '../../hooks/useWorkforce';
import { EmptyState, ErrorState, Skeleton } from '../../components/leave-records/States';
import { RequestBadge } from '../../components/workforce/StatusBadge';
import StatTile from '../../components/workforce/StatTile';
import Pagination from '../../components/workforce/Pagination';
import DateRangePicker from '../../components/workforce/DateRangePicker';

const PAGE_SIZE = 20;
const today = () => new Date().toISOString().slice(0, 10);
const monthAgo = () => {
  const d = new Date();
  d.setDate(d.getDate() - 29);
  return d.toISOString().slice(0, 10);
};

const RequestRow = React.memo(function RequestRow({ row, actions, canApprove, isMine, isBusy }) {
  const pending = row.status === 'pending';
  return (
    <tr>
      <td className="wf-strong">{row.user_name}</td>
      <td>{row.start_date}{row.end_date !== row.start_date ? ` → ${row.end_date}` : ''}</td>
      <td className="wf-reason">{row.reason || '—'}</td>
      <td><RequestBadge status={row.status} /></td>
      <td className="wf-muted wf-small">{row.reviewed_by_name || '—'}</td>
      <td className="wf-row-actions">
        {canApprove && pending && (
          <>
            <button type="button" className="wf-btn wf-btn-primary wf-btn-sm"
                    disabled={isBusy} onClick={() => actions.approve(row)}>
              <Check size={14} /> Approve
            </button>
            <button type="button" className="wf-btn wf-btn-danger wf-btn-sm"
                    disabled={isBusy} onClick={() => actions.reject(row)}>
              <X size={14} /> Reject
            </button>
          </>
        )}
        {isMine && pending && (
          <button type="button" className="wf-btn wf-btn-ghost wf-btn-sm"
                  disabled={isBusy} onClick={() => actions.cancel(row)}>
            Cancel
          </button>
        )}
      </td>
    </tr>
  );
});

const NewRequestForm = ({ onSubmit, isPending, onClose }) => {
  const [form, setForm] = useState({ start_date: today(), end_date: today(), reason: '' });
  const [error, setError] = useState('');
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <form
      className="wf-inline-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setError('');
        if (form.end_date < form.start_date) {
          setError('The end date cannot be before the start date.');
          return;
        }
        try {
          await onSubmit(form);
          onClose();
        } catch (err) {
          setError(err?.response?.data?.detail || 'Could not submit that request.');
        }
      }}
    >
      <label className="wf-field">
        <span className="wf-field-label">From</span>
        <input type="date" className="wf-input" value={form.start_date}
               onChange={set('start_date')} required />
      </label>
      <label className="wf-field">
        <span className="wf-field-label">To</span>
        <input type="date" className="wf-input" value={form.end_date}
               min={form.start_date} onChange={set('end_date')} required />
      </label>
      <label className="wf-field wf-field-grow">
        <span className="wf-field-label">Reason</span>
        <input type="text" className="wf-input" value={form.reason} onChange={set('reason')}
               placeholder="e.g. Fibre cut at the office" />
      </label>
      <div className="wf-inline-form-actions">
        <button type="submit" className="wf-btn wf-btn-primary" disabled={isPending}>
          {isPending ? 'Submitting…' : 'Request'}
        </button>
        <button type="button" className="wf-btn wf-btn-ghost" onClick={onClose}>Cancel</button>
      </div>
      {error && <p className="wf-inline-error" role="alert">{error}</p>}
    </form>
  );
};

const WFH = () => {
  const { role, user } = useAuth();
  const [range, setRange] = useState({ from: monthAgo(), to: today() });
  const [page, setPage] = useState(1);
  const [showForm, setShowForm] = useState(false);
  const [scope, setScope] = useState('mine');
  const [actionError, setActionError] = useState('');

  // Approval stays HR-only, matching the backend. Managers see the queue so
  // they know what is coming, but the buttons are simply not rendered.
  const canApprove = role === 'approver' || role === 'admin';
  const canSeeTeam = role === 'checker' || canApprove;
  const canRequest = role !== 'admin';

  const listParams = useMemo(
    () => ({ page, ...(scope === 'mine' ? {} : {}) }),
    [page, scope],
  );
  const summary = useWfhSummary(range);
  const requests = useWfhRequests(listParams);

  const approve = useApproveWfh();
  const reject = useRejectWfh();
  const cancel = useCancelWfh();
  const submit = useSubmitWfh();
  const isBusy = approve.isPending || reject.isPending || cancel.isPending;

  const run = async (fn, args) => {
    setActionError('');
    try { await fn.mutateAsync(args); }
    catch (err) { setActionError(err?.response?.data?.detail || 'That didn’t go through. Please try again.'); }
  };

  const actions = useMemo(() => ({
    approve: (row) => run(approve, { id: row.id, note: '' }),
    reject: (row) => {
      const note = window.prompt('Reason for rejecting this WFH request?');
      if (note === null) return undefined;
      return run(reject, { id: row.id, note });
    },
    cancel: (row) => run(cancel, { id: row.id }),
  }), [approve, reject, cancel]);

  const rows = requests.data?.results ?? [];
  const visible = scope === 'mine' ? rows.filter((r) => r.user === user?.id) : rows;
  const stats = summary.data?.summary;

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">Work from home</h1>
          <p className="wf-sub">
            An approved request is not attendance on its own — the day only
            counts once you also check in.
          </p>
        </div>
        {canRequest && !showForm && (
          <button type="button" className="wf-btn wf-btn-primary" onClick={() => setShowForm(true)}>
            <Plus size={16} /> Request WFH
          </button>
        )}
      </header>

      {showForm && (
        <section className="wf-card">
          <NewRequestForm
            onSubmit={(payload) => submit.mutateAsync(payload)}
            isPending={submit.isPending}
            onClose={() => setShowForm(false)}
          />
        </section>
      )}

      {canSeeTeam && (
        <section className="wf-card">
          <div className="wf-card-head">
            <h2><Home size={18} /> Statistics</h2>
            <DateRangePicker from={range.from} to={range.to} onChange={setRange} />
          </div>
          {summary.isLoading && <Skeleton rows={1} height={70} />}
          {summary.isError && <ErrorState error={summary.error} onRetry={summary.refetch} />}
          {stats && (
            <div className="wf-tiles">
              <StatTile label="Approved day rows" value={stats.approved_day_rows} tone="ok" />
              <StatTile label="Actually worked from home" value={stats.worked_from_home_days}
                        tone="accent" />
              <StatTile label="Conversion"
                        value={stats.conversion === null ? '—' : `${Math.round(stats.conversion * 100)}%`}
                        tone="info" hint="Approved days that produced a check-in" />
              <StatTile label="Pending" value={stats.requests?.pending} tone="warn" />
            </div>
          )}
          {!canApprove && (
            <p className="wf-muted wf-small">
              Work-from-home is approved by HR. You can see your team&apos;s queue here.
            </p>
          )}
        </section>
      )}

      <section className="wf-card">
        <div className="wf-card-head">
          <h2>Requests</h2>
          {canSeeTeam && (
            <div className="wf-filters">
              <button type="button" className={`wf-chip ${scope === 'mine' ? 'on' : ''}`}
                      onClick={() => setScope('mine')}>Mine</button>
              <button type="button" className={`wf-chip ${scope === 'team' ? 'on' : ''}`}
                      onClick={() => setScope('team')}>
                {canApprove ? 'Everyone' : 'My department'}
              </button>
            </div>
          )}
        </div>

        {actionError && <p className="wf-inline-error" role="alert">{actionError}</p>}
        {requests.isLoading && <Skeleton rows={3} height={52} />}
        {requests.isError && <ErrorState error={requests.error} onRetry={requests.refetch} />}
        {!requests.isLoading && !requests.isError && visible.length === 0 && (
          <EmptyState message="No work-from-home requests here." ctaLabel="" ctaTo="" />
        )}
        {!requests.isLoading && !requests.isError && visible.length > 0 && (
          <>
            <div className="wf-table-scroll">
              <table className="wf-table">
                <thead>
                  <tr>
                    <th>Employee</th><th>Dates</th><th>Reason</th>
                    <th>Status</th><th>Reviewed by</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <RequestRow key={row.id} row={row} actions={actions}
                                canApprove={canApprove}
                                isMine={row.user === user?.id} isBusy={isBusy} />
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} count={requests.data.count} pageSize={PAGE_SIZE}
                        onChange={setPage} />
          </>
        )}
      </section>
    </div>
  );
};

export default WFH;
