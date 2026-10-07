import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ClipboardList, HandHelping, Plus, XCircle } from 'lucide-react';

import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState, EmptyState } from '../../components/leave-records/States';

const STATUS_TONES = {
  pending: 'warn',
  supervisor_approved: 'info',
  inventory_approved: 'info',
  handed_over: 'warn',
  accepted: 'ok',
  rejected: 'no',
  cancelled: 'muted',
};

/**
 * The asset request queue (Phase 70.5).
 *
 * EVERY BUTTON IS RENDERED FROM A SERVER CAPABILITY FLAG. The five stages have
 * five different people entitled to act, and re-deriving that in JavaScript would
 * be a second copy of the rule that the first person to change it forgets about.
 * `can_supervisor_approve`, `can_inventory_approve`, `can_hand_over`, `can_accept`
 * and `can_cancel` come off the payload; this file never asks who the user is.
 *
 * The one thing it does decide is the ORDER of the stage chips, because that is
 * presentation rather than authorization.
 */
const STAGES = [
  ['pending', 'Requested'],
  ['supervisor_approved', 'Supervisor'],
  ['inventory_approved', 'Inventory'],
  ['handed_over', 'Handed over'],
  ['accepted', 'Accepted'],
];

const Progress = ({ status }) => {
  const reached = STAGES.findIndex(([key]) => key === status);
  const closed = status === 'rejected' || status === 'cancelled';
  return (
    <div className="inv-progress" aria-label={`Stage: ${status}`}>
      {STAGES.map(([key, label], index) => (
        <span key={key}
          className={`inv-progress-step${
            closed ? ' is-dead' : index <= reached ? ' is-done' : ''}`}>
          {label}
        </span>
      ))}
    </div>
  );
};

const AssetRequests = () => {
  const qc = useQueryClient();
  const [toast, setToast] = useState(null);
  const [remarks, setRemarks] = useState({});
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ item: '', requested_category: '', purpose: '' });

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'requests'],
    queryFn: () => assetLifecycle.requests(),
  });
  const { data: categories = [] } = useQuery({
    queryKey: ['inventory', 'categories'],
    queryFn: inventoryService.categories,
    staleTime: 10 * 60 * 1000,
  });

  const done = (message) => {
    setToast({ message, tone: 'success' });
    setRemarks({});
    qc.invalidateQueries({ queryKey: ['inventory'] });
  };
  const fail = (e) => {
    const payload = e?.response?.data;
    const message = payload?.detail || payload?.item || payload?.status
      || payload?.remarks || payload?.purpose || payload?.reason
      || (typeof payload === 'string' && payload.length < 300 && !payload.includes('<') ? payload : null) || 'That didn’t go through. Please try again.';
    setToast({ message: Array.isArray(message) ? message[0] : String(message),
      tone: 'error' });
  };

  const create = useMutation({
    mutationFn: () => assetLifecycle.createRequest({
      ...(form.item ? { item: form.item } : {}),
      ...(form.requested_category
        ? { requested_category: form.requested_category } : {}),
      purpose: form.purpose,
    }),
    onSuccess: () => {
      setCreating(false);
      setForm({ item: '', requested_category: '', purpose: '' });
      done('Request submitted.');
    },
    onError: fail,
  });

  const act = useMutation({
    mutationFn: ({ id, verb, payload }) => {
      const call = {
        supervisor: assetLifecycle.supervisorDecision,
        inventory: assetLifecycle.inventoryDecision,
        handover: assetLifecycle.handOver,
        accept: assetLifecycle.acceptAsset,
        cancel: assetLifecycle.cancelRequest,
      }[verb];
      return call(id, payload);
    },
    onSuccess: () => done('Recorded.'),
    onError: fail,
  });

  const run = (id, verb, payload = {}) => act.mutate({ id, verb, payload });
  const note = (id) => remarks[id] || '';

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Asset Requests</h1>
          <p className="lr-page-sub">
            Requested, approved by a supervisor, then by an inventory officer,
            handed over, and accepted by the person receiving it
          </p>
        </div>
        <button type="button" className="lr-btn lr-btn-primary"
          onClick={() => setCreating((v) => !v)}>
          <Plus size={14} /> Request an asset
        </button>
      </div>

      {creating && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">New Request</h3>
          <p className="lr-page-sub">
            Name a category rather than a specific asset unless you need a
            particular one — the inventory officer can see what is actually free.
          </p>
          <label className="lr-field">
            <span>Category</span>
            <select value={form.requested_category}
              onChange={(e) => setForm({ ...form, requested_category: e.target.value })}>
              <option value="">— choose a category —</option>
              {categories.map((row) => (
                <option key={row.id} value={row.id}>{row.name}</option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span>Purpose *</span>
            <textarea rows={3} value={form.purpose}
              onChange={(e) => setForm({ ...form, purpose: e.target.value })}
              placeholder="Why you need it — this is what the approvers read." />
          </label>
          <button type="button" className="lr-btn lr-btn-primary"
            disabled={create.isPending || form.purpose.trim().length < 10}
            onClick={() => create.mutate()}>
            {create.isPending ? 'Submitting…' : 'Submit request'}
          </button>
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState message="There are no asset requests." ctaTo={null} />
      ) : rows.map((row) => (
        <div key={row.id} className="memo-panel" data-testid="asset-request">
          <div className="memo-matrix-head">
            <h3 className="memo-panel-title" style={{ margin: 0 }}>
              <ClipboardList size={15} aria-hidden="true" /> {row.reference}
            </h3>
            <span className={`min-status is-${STATUS_TONES[row.status] || 'muted'}`}>
              {row.status_label}
            </span>
          </div>

          <dl className="memo-doc-meta">
            <div><dt>Asset</dt>
              <dd>{row.item_code || row.category_name || '—'}
                {row.item_name && <> · {row.item_name}</>}</dd></div>
            <div><dt>Requested By</dt><dd>{row.requested_by_name}</dd></div>
            <div><dt>Department</dt><dd>{row.department_name || '—'}</dd></div>
            <div><dt>Purpose</dt><dd>{row.purpose}</dd></div>
          </dl>

          <Progress status={row.status} />

          {row.rejection_reason && (
            <div className="memo-notice is-warn" role="status">
              <XCircle size={14} aria-hidden="true" />
              Refused by {row.rejected_by_name}: {row.rejection_reason}
            </div>
          )}

          {(row.can_supervisor_approve || row.can_inventory_approve
            || row.can_hand_over || row.can_accept || row.can_cancel) && (
            <>
              <label className="lr-field">
                <span>Remarks</span>
                <textarea rows={2} value={note(row.id)}
                  aria-label={`Remarks for ${row.reference}`}
                  onChange={(e) => setRemarks({ ...remarks, [row.id]: e.target.value })} />
              </label>
              <div className="memo-matrix-actions">
                {row.can_supervisor_approve && (
                  <>
                    <button type="button" className="lr-btn lr-btn-primary"
                      disabled={act.isPending}
                      onClick={() => run(row.id, 'supervisor',
                        { approve: true, remarks: note(row.id) })}>
                      <CheckCircle2 size={14} /> Approve as supervisor
                    </button>
                    <button type="button" className="lr-btn lr-btn-danger"
                      disabled={act.isPending || note(row.id).trim().length < 10}
                      onClick={() => run(row.id, 'supervisor',
                        { approve: false, remarks: note(row.id) })}>
                      <XCircle size={14} /> Refuse
                    </button>
                  </>
                )}
                {row.can_inventory_approve && (
                  <>
                    <button type="button" className="lr-btn lr-btn-primary"
                      disabled={act.isPending}
                      onClick={() => run(row.id, 'inventory',
                        { approve: true, remarks: note(row.id) })}>
                      <CheckCircle2 size={14} /> Approve as inventory officer
                    </button>
                    <button type="button" className="lr-btn lr-btn-danger"
                      disabled={act.isPending || note(row.id).trim().length < 10}
                      onClick={() => run(row.id, 'inventory',
                        { approve: false, remarks: note(row.id) })}>
                      <XCircle size={14} /> Refuse
                    </button>
                  </>
                )}
                {row.can_hand_over && (
                  <button type="button" className="lr-btn lr-btn-primary"
                    disabled={act.isPending}
                    onClick={() => run(row.id, 'handover',
                      { remarks: note(row.id) })}>
                    <HandHelping size={14} /> Hand over
                  </button>
                )}
                {row.can_accept && (
                  <button type="button" className="lr-btn lr-btn-primary"
                    disabled={act.isPending}
                    onClick={() => run(row.id, 'accept',
                      { remarks: note(row.id) })}>
                    <CheckCircle2 size={14} /> I have received this
                  </button>
                )}
                {row.can_cancel && (
                  <button type="button" className="lr-btn lr-btn-ghost"
                    disabled={act.isPending}
                    onClick={() => run(row.id, 'cancel',
                      { remarks: note(row.id) })}>
                    Withdraw
                  </button>
                )}
              </div>
              {row.can_accept && (
                <p className="lr-page-sub">
                  Only you can confirm receipt. Until you do, the record says an
                  officer handed it over — not that you have it.
                </p>
              )}
            </>
          )}
        </div>
      ))}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default AssetRequests;
