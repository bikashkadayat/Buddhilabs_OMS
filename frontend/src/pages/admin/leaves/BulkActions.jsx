import React, { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminLeaveService } from '../../../services/adminLeaveService';
import ConfirmModal from '../../../components/admin/ConfirmModal';
import Toast from '../../../components/admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';

const BulkActions = () => {
  const qc = useQueryClient();
  const [selected, setSelected] = useState(() => new Set());
  const [pending, setPending] = useState(null); // { action }
  const [toast, setToast] = useState(null);

  const { data: leaves = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['admin-pending-leaves'],
    queryFn: () => adminLeaveService.getPendingLeaves(),
  });

  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const toggleAll = () => setSelected((prev) => (prev.size === leaves.length ? new Set() : new Set(leaves.map((l) => l.id))));

  // Impact summary: how many employees / departments the selection touches.
  const impact = useMemo(() => {
    const chosen = leaves.filter((l) => selected.has(l.id));
    const employees = new Set(chosen.map((l) => l.user_name || l.user));
    const depts = new Set(chosen.map((l) => l.department || l.user_department || '—'));
    return { count: chosen.length, employees: employees.size, departments: depts.size };
  }, [leaves, selected]);

  const run = useMutation({
    mutationFn: ({ action, comment }) => adminLeaveService.bulkLeaveAction({ leave_ids: [...selected], action, comment }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['admin-pending-leaves'] });
      // "rejectd" was built by appending a "d" to the verb. Past tenses
      // spelled out, and the sentence says what to do about failures.
      const done = pending.action === 'approve' ? 'approved' : 'rejected';
      setToast({
        message: res.failed
          ? `${res.succeeded} of ${res.total} requests ${done}. ${res.failed} couldn’t be processed — they may already have been dealt with.`
          : `${res.succeeded} ${res.succeeded === 1 ? 'request' : 'requests'} ${done}.`,
        tone: res.failed ? 'error' : 'success',
      });
      setSelected(new Set());
      setPending(null);
    },
    onError: () => setToast({ message: 'Nothing was changed — the requests couldn’t be processed. Please try again.', tone: 'error' }),
  });

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Bulk Actions</h2><div className="lr-page-sub">Approve or reject multiple pending applications at once</div></div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button type="button" className="lr-btn lr-btn-primary" disabled={selected.size === 0} onClick={() => setPending({ action: 'approve' })}>Approve ({selected.size})</button>
          <button type="button" className="lr-btn lr-btn-danger" disabled={selected.size === 0} onClick={() => setPending({ action: 'reject' })}>Reject ({selected.size})</button>
        </div>
      </div>

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && leaves.length === 0 && <EmptyState message="No pending applications." ctaTo={undefined} />}

      {!isLoading && !isError && leaves.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead>
              <tr>
                <th scope="col"><input type="checkbox" checked={selected.size === leaves.length} onChange={toggleAll} aria-label="Select all" /></th>
                <th scope="col">Employee</th><th scope="col">Type</th><th scope="col">From</th><th scope="col">To</th><th scope="col">Days</th>
              </tr>
            </thead>
            <tbody>
              {leaves.map((l) => (
                <tr key={l.id}>
                  <td><input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.user_name}`} /></td>
                  <td>{l.user_name}</td><td>{l.leave_type}</td><td>{l.start_date}</td><td>{l.end_date}</td>
                  <td>{l.days ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pending && (
        <ConfirmModal
          title={`${pending.action === 'approve' ? 'Approve' : 'Reject'} ${selected.size} application(s)`}
          impact={`This will affect ${impact.employees} employee(s) across ${impact.departments} department(s).`}
          requireReason={pending.action === 'reject'}
          danger={pending.action === 'reject'}
          confirmLabel={pending.action === 'approve' ? 'Approve all' : 'Reject all'}
          busy={run.isPending}
          onClose={() => setPending(null)}
          onConfirm={(reason) => run.mutate({ action: pending.action, comment: reason })}
        />
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default BulkActions;
