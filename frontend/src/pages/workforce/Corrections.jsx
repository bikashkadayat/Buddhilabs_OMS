import React, { useMemo, useState } from 'react';
import { Check, Download, FileEdit, Plus, RotateCcw, X } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import {
  useApproveCorrection, useCancelCorrection, useCorrections,
  useRejectCorrection, useRevertCorrection, useSubmitCorrection,
} from '../../hooks/useWorkforce';
import { workforceService } from '../../services/workforceService';
import { saveBlob } from '../../services/reportService';
import { EmptyState, ErrorState, Skeleton } from '../../components/leave-records/States';
import { RequestBadge } from '../../components/workforce/StatusBadge';
import Pagination from '../../components/workforce/Pagination';
import SubmitCorrectionModal from '../../components/workforce/SubmitCorrectionModal';

const PAGE_SIZE = 20;
const hhmm = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';

const QUEUES = [
  { key: '', label: 'All' },
  { key: 'open', label: 'In review' },
  { key: 'manager', label: 'Awaiting me (Dept Head)' },
  { key: 'hr', label: 'Awaiting HR' },
];

/**
 * One request row. Memoised because the list re-renders whenever a mutation
 * settles, and only the touched row actually changes.
 */
const CorrectionRow = React.memo(function CorrectionRow({ row, actions, isBusy }) {
  const canApprove = actions.canApprove(row);
  const canReject = actions.canReject(row);
  const canCancel = actions.canCancel(row);
  const canRevert = actions.canRevert(row);

  return (
    <tr>
      <td>
        <div className="wf-strong">{row.employee_name}</div>
        <div className="wf-muted wf-small">{row.department || '—'}</div>
      </td>
      <td>{row.attendance_date}</td>
      <td>
        <div className="wf-small">
          <span className="wf-muted">was</span> {hhmm(row.previous_check_in)}–{hhmm(row.previous_check_out)}
        </div>
        <div className="wf-small wf-strong">
          <span className="wf-muted">ask</span> {hhmm(row.requested_check_in)}–{hhmm(row.requested_check_out)}
          {row.requested_status ? ` · ${row.requested_status.replace(/_/g, ' ')}` : ''}
        </div>
      </td>
      <td className="wf-reason">{row.reason}</td>
      <td><RequestBadge status={row.status} /></td>
      <td className="wf-row-actions">
        {row.has_attachment && (
          <button type="button" className="wf-btn wf-btn-ghost wf-btn-sm"
                  onClick={() => actions.download(row)} title="Download attachment">
            <Download size={14} />
          </button>
        )}
        {canApprove && (
          <button type="button" className="wf-btn wf-btn-primary wf-btn-sm"
                  disabled={isBusy} onClick={() => actions.approve(row)}>
            <Check size={14} /> Approve
          </button>
        )}
        {canReject && (
          <button type="button" className="wf-btn wf-btn-danger wf-btn-sm"
                  disabled={isBusy} onClick={() => actions.reject(row)}>
            <X size={14} /> Reject
          </button>
        )}
        {canCancel && (
          <button type="button" className="wf-btn wf-btn-ghost wf-btn-sm"
                  disabled={isBusy} onClick={() => actions.cancel(row)}>
            Cancel
          </button>
        )}
        {canRevert && (
          <button type="button" className="wf-btn wf-btn-ghost wf-btn-sm"
                  disabled={isBusy} onClick={() => actions.revert(row)} title="Undo an applied correction">
            <RotateCcw size={14} /> Revert
          </button>
        )}
      </td>
    </tr>
  );
});

const Corrections = () => {
  const { role, user } = useAuth();
  const [queue, setQueue] = useState('');
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [actionError, setActionError] = useState('');

  const params = useMemo(
    () => ({ ...(queue ? { queue } : {}), page }),
    [queue, page],
  );
  const { data, isLoading, isError, error, refetch } = useCorrections(params);

  const submit = useSubmitCorrection();
  const approve = useApproveCorrection();
  const reject = useRejectCorrection();
  const cancel = useCancelCorrection();
  const revert = useRevertCorrection();
  const isBusy = approve.isPending || reject.isPending || cancel.isPending || revert.isPending;

  const isHR = role === 'approver' || role === 'admin';
  const isManager = role === 'checker';
  // Admin is an oversight role: it reviews corrections but does not raise them
  // (the API returns 403), so the button is hidden rather than failing on click.
  const canSubmit = role !== 'admin';

  const run = async (fn, args) => {
    setActionError('');
    try {
      await fn.mutateAsync(args);
    } catch (err) {
      setActionError(err?.response?.data?.detail || 'That action could not be completed.');
    }
  };

  const actions = useMemo(() => ({
    canApprove: (row) =>
      (row.status === 'pending' && (isManager || isHR)) ||
      (row.status === 'manager_approved' && isHR),
    canReject: (row) =>
      (row.status === 'pending' && (isManager || isHR)) ||
      (row.status === 'manager_approved' && isHR),
    canCancel: (row) =>
      row.employee === user?.id &&
      (row.status === 'pending' || row.status === 'manager_approved'),
    canRevert: (row) => isHR && row.status === 'hr_approved',
    approve: (row) => run(approve, { id: row.id, remarks: '' }),
    reject: (row) => {
      const reason = window.prompt('Reason for rejecting this correction?');
      if (reason === null) return undefined;
      return run(reject, { id: row.id, reason });
    },
    cancel: (row) => run(cancel, { id: row.id }),
    revert: (row) => {
      const reason = window.prompt('Why is this applied correction being reverted?');
      if (reason === null) return undefined;
      return run(revert, { id: row.id, reason });
    },
    download: async (row) => {
      const response = await workforceService.correctionAttachment(row.id);
      saveBlob(response, `correction-${row.attendance_date}`);
    },
  }), [isHR, isManager, user?.id, approve, reject, cancel, revert]);

  const rows = data?.results ?? [];

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">Attendance corrections</h1>
          <p className="wf-sub">
            Employee → department head → HR. An applied correction overrides the
            device permanently.
          </p>
        </div>
        {canSubmit && (
          <button type="button" className="wf-btn wf-btn-primary"
                  onClick={() => setModalOpen(true)}>
            <Plus size={16} /> New request
          </button>
        )}
      </header>

      <div className="wf-filters">
        {QUEUES.map((q) => (
          <button
            key={q.key || 'all'} type="button"
            className={`wf-chip ${queue === q.key ? 'on' : ''}`}
            onClick={() => { setQueue(q.key); setPage(1); }}
          >
            {q.label}
          </button>
        ))}
      </div>

      {actionError && <p className="wf-inline-error" role="alert">{actionError}</p>}

      <section className="wf-card">
        {isLoading && <Skeleton rows={4} height={56} />}
        {isError && <ErrorState error={error} onRetry={refetch} />}
        {!isLoading && !isError && rows.length === 0 && (
          <EmptyState
            message="No correction requests here."
            ctaLabel={canSubmit ? 'Raise a correction' : ''}
            ctaTo={canSubmit ? '' : ''}
          />
        )}
        {!isLoading && !isError && rows.length > 0 && (
          <>
            <div className="wf-table-scroll">
              <table className="wf-table">
                <thead>
                  <tr>
                    <th>Employee</th><th>Date</th><th>Change</th>
                    <th>Reason</th><th>Status</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <CorrectionRow key={row.id} row={row} actions={actions} isBusy={isBusy} />
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} count={data.count} pageSize={PAGE_SIZE}
                        onChange={setPage} />
          </>
        )}
      </section>

      {modalOpen && (
        <SubmitCorrectionModal
          onClose={() => setModalOpen(false)}
          onSubmit={async (payload) => {
            await submit.mutateAsync(payload);
            setModalOpen(false);
          }}
          isPending={submit.isPending}
        />
      )}
    </div>
  );
};

export default Corrections;
