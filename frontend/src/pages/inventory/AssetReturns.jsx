import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, PackageCheck, Undo2, XCircle } from 'lucide-react';

import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState, EmptyState } from '../../components/leave-records/States';

const MIN_REASON = 10;

const STATUS_TONES = {
  requested: 'warn', verifying: 'info', inspected: 'info', accepted: 'ok', rejected: 'no',
};
const CONDITIONS = [['new', 'New'], ['good', 'Good'], ['fair', 'Fair'], ['damaged', 'Damaged']];
const STAGES = [
  ['requested', 'Requested'], ['verifying', 'Verified'],
  ['inspected', 'Inspected'], ['accepted', 'Accepted'],
];

const firstError = (e) => {
  const payload = e?.response?.data;
  if (!payload) return 'The request failed. Check your connection and try again.';
  if (typeof payload === 'string') return payload;
  const value = payload.detail || Object.values(payload)[0];
  return Array.isArray(value) ? String(value[0]) : String(value);
};

/**
 * Asset returns (Phase ASSET-CUSTODY-TRANSFER).
 *
 * The return workflow existed on the server since Phase 70.6 with no screen at
 * all - an employee could not start one, and the store could not see one. This is
 * that screen. The holder raises the return; an inventory officer verifies it,
 * inspects the condition, and accepts or rejects it. Custody ends only on accept.
 *
 * Buttons come from `can_verify` / `can_inspect` / `can_accept` on the payload.
 */
const AssetReturns = () => {
  const qc = useQueryClient();
  const [toast, setToast] = useState(null);
  const [raising, setRaising] = useState(false);
  const [form, setForm] = useState({ item: '', reason: '', declared_condition: 'good' });
  const [inspect, setInspect] = useState({});
  const [notes, setNotes] = useState({});

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'returns'],
    queryFn: () => assetLifecycle.returns(),
  });
  const { data: mine = [] } = useQuery({
    queryKey: ['inventory', 'my-assets'],
    queryFn: inventoryService.myAssets,
  });
  const returnable = mine.filter((a) => a.is_active !== false);
  const pendingFor = new Set(rows.filter((r) => ['requested', 'verifying', 'inspected']
    .includes(r.status)).map((r) => String(r.item)));

  const done = (message) => {
    setToast({ message, tone: 'success' });
    setNotes({});
    qc.invalidateQueries({ queryKey: ['inventory'] });
  };
  const fail = (e) => setToast({ message: firstError(e), tone: 'error' });

  const raise = useMutation({
    mutationFn: () => assetLifecycle.createReturn(form),
    onSuccess: (record) => {
      setRaising(false);
      setForm({ item: '', reason: '', declared_condition: 'good' });
      done(`${record.reference} raised. The store will verify and inspect it.`);
    },
    onError: fail,
  });

  const act = useMutation({
    mutationFn: ({ id, verb }) => ({
      verify: () => assetLifecycle.verifyReturn(id, { remarks: notes[id] || '' }),
      inspect: () => assetLifecycle.inspectReturn(id, {
        condition: inspect[id] || 'good', remarks: notes[id] || '' }),
      accept: () => assetLifecycle.acceptReturn(id, { remarks: notes[id] || '' }),
      reject: () => assetLifecycle.rejectReturn(id, { reason: notes[id] || '' }),
    }[verb]()),
    onSuccess: (record, { verb }) => done({
      verify: `${record.reference} verified.`,
      inspect: `${record.reference} inspected.`,
      accept: `${record.reference} accepted. The asset is back in stock.`,
      reject: `${record.reference} rejected. The asset stays with its holder.`,
    }[verb]),
    onError: fail,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Asset Return</h1>
          <p className="lr-page-sub">
            Giving an asset back to the store. The holder raises it; the store
            verifies it, inspects its condition, then accepts or rejects it.
          </p>
        </div>
        {returnable.length > 0 && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => setRaising((v) => !v)}>
            <Undo2 size={14} /> Return an asset
          </button>
        )}
      </div>

      {raising && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">Return an Asset</h3>
          <label className="lr-field">
            <span>Asset *</span>
            <select value={form.item} onChange={(e) => setForm({ ...form, item: e.target.value })}>
              <option value="">— choose one of your assets —</option>
              {returnable.map((a) => {
                const itemId = String(a.item?.id ?? a.item ?? '');
                return (
                  <option key={a.id} value={itemId} disabled={pendingFor.has(itemId)}>
                    {a.item_code} · {a.item_name}{pendingFor.has(itemId) ? ' (return already raised)' : ''}
                  </option>
                );
              })}
            </select>
          </label>
          <label className="lr-field">
            <span>Condition you are returning it in</span>
            <select value={form.declared_condition}
              onChange={(e) => setForm({ ...form, declared_condition: e.target.value })}>
              {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="lr-field">
            <span>Reason *</span>
            <textarea rows={3} value={form.reason}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              placeholder="Why it is coming back — leaving, replaced, no longer needed." />
          </label>
          <button type="button" className="lr-btn lr-btn-primary"
            disabled={!form.item || form.reason.trim().length < MIN_REASON || raise.isPending}
            onClick={() => raise.mutate()}>
            {raise.isPending ? 'Raising…' : 'Raise return'}
          </button>
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState ctaTo={null} message="There are no asset returns." />
      ) : rows.map((row) => {
        const reached = STAGES.findIndex(([key]) => key === row.status);
        const note = notes[row.id] || '';
        const canDecide = row.can_verify || row.can_inspect || row.can_accept;
        return (
          <div key={row.id} className="memo-panel" data-testid="asset-return">
            <div className="memo-matrix-head">
              <h3 className="memo-panel-title" style={{ margin: 0 }}>
                <PackageCheck size={15} aria-hidden="true" /> {row.reference}
              </h3>
              <span className={`min-status is-${STATUS_TONES[row.status] || 'muted'}`}>
                {row.status_label}
              </span>
            </div>
            <dl className="memo-doc-meta">
              <div><dt>Asset</dt><dd>{row.item_code} · {row.item_name}</dd></div>
              <div><dt>Returned by</dt><dd>{row.returned_by_name}</dd></div>
              <div><dt>Declared condition</dt><dd>{row.declared_condition || '—'}</dd></div>
              {row.inspected_condition && (
                <div><dt>Inspected condition</dt>
                  <dd>{row.inspected_condition}{row.condition_disputed && ' (disputed)'}</dd></div>
              )}
              <div><dt>Reason</dt><dd>{row.reason}</dd></div>
            </dl>
            <div className="inv-progress" aria-label={`Stage: ${row.status_label}`}>
              {STAGES.map(([key, label], index) => (
                <span key={key} className={`inv-progress-step${
                  row.status === 'rejected' ? ' is-dead' : index <= reached ? ' is-done' : ''}`}>
                  {label}
                </span>
              ))}
            </div>
            {row.rejection_reason && (
              <div className="memo-notice is-warn" role="status">
                <XCircle size={14} aria-hidden="true" /> Rejected: {row.rejection_reason}
              </div>
            )}

            {canDecide && (
              <>
                {row.can_inspect && (
                  <label className="lr-field">
                    <span>Condition found on inspection</span>
                    <select value={inspect[row.id] || row.declared_condition || 'good'}
                      onChange={(e) => setInspect({ ...inspect, [row.id]: e.target.value })}>
                      {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </label>
                )}
                <label className="lr-field">
                  <span>Remarks</span>
                  <textarea rows={2} value={note} aria-label={`Remarks for ${row.reference}`}
                    onChange={(e) => setNotes({ ...notes, [row.id]: e.target.value })} />
                </label>
                <div className="memo-matrix-actions">
                  {row.can_verify && (
                    <button type="button" className="lr-btn lr-btn-primary" disabled={act.isPending}
                      onClick={() => act.mutate({ id: row.id, verb: 'verify' })}>
                      Verify
                    </button>
                  )}
                  {row.can_inspect && (
                    <button type="button" className="lr-btn lr-btn-primary" disabled={act.isPending}
                      onClick={() => act.mutate({ id: row.id, verb: 'inspect' })}>
                      Record inspection
                    </button>
                  )}
                  {row.can_accept && (
                    <button type="button" className="lr-btn lr-btn-primary" disabled={act.isPending}
                      onClick={() => act.mutate({ id: row.id, verb: 'accept' })}>
                      <CheckCircle2 size={14} /> Accept return
                    </button>
                  )}
                  <button type="button" className="lr-btn lr-btn-danger"
                    disabled={act.isPending || note.trim().length < MIN_REASON}
                    title={note.trim().length < MIN_REASON
                      ? `Give a reason of at least ${MIN_REASON} characters to reject` : undefined}
                    onClick={() => act.mutate({ id: row.id, verb: 'reject' })}>
                    <XCircle size={14} /> Reject
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default AssetReturns;
