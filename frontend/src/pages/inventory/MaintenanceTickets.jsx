import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Wrench } from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState, EmptyState } from '../../components/leave-records/States';

const TONES = {
  reported: 'warn', assigned: 'info', in_maintenance: 'warn',
  completed: 'ok', returned: 'ok', cancelled: 'muted',
};
const PRIORITY_TONES = { low: 'muted', normal: 'muted', high: 'warn', critical: 'no' };

/**
 * Maintenance tickets (Phase 70.8).
 *
 * The stage buttons come off `can_manage`, so this file never asks who the user
 * is. What it DOES encode is which action each stage offers next, because that is
 * the shape of the workflow rather than a permission - and the server refuses any
 * out-of-order call regardless, so the two cannot drift into disagreement about
 * who may act.
 */
const NEXT = {
  reported: [['assign', 'Assign'], ['start', 'Send for repair']],
  assigned: [['start', 'Send for repair']],
  in_maintenance: [['complete', 'Mark complete']],
  completed: [['return-to-service', 'Return to service']],
};

const MaintenanceTickets = () => {
  const qc = useQueryClient();
  const [toast, setToast] = useState(null);
  const [fields, setFields] = useState({});

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'maintenance'],
    queryFn: () => assetLifecycle.tickets(),
  });

  const fail = (e) => {
    const payload = e?.response?.data;
    const message = payload?.detail || payload?.status || payload?.resolution
      || payload?.technician || payload?.reason || payload?.issue
      || (typeof payload === 'string' && payload.length < 300 && !payload.includes('<') ? payload : null) || 'That didn’t go through. Please try again.';
    setToast({ message: Array.isArray(message) ? message[0] : String(message),
      tone: 'error' });
  };

  const act = useMutation({
    mutationFn: ({ id, verb, payload }) => ({
      assign: assetLifecycle.assignTicket,
      start: assetLifecycle.startTicket,
      complete: assetLifecycle.completeTicket,
      'return-to-service': assetLifecycle.returnToService,
      cancel: assetLifecycle.cancelTicket,
    }[verb](id, payload)),
    onSuccess: () => {
      setToast({ message: 'Recorded.', tone: 'success' });
      setFields({});
      qc.invalidateQueries({ queryKey: ['inventory'] });
    },
    onError: fail,
  });

  const field = (id, key) => (fields[id] || {})[key] || '';
  const setField = (id, key, value) =>
    setFields({ ...fields, [id]: { ...(fields[id] || {}), [key]: value } });

  const payloadFor = (id, verb) => ({
    assign: { vendor: field(id, 'vendor'), remarks: field(id, 'remarks') },
    start: { remarks: field(id, 'remarks') },
    complete: {
      resolution: field(id, 'resolution'),
      condition: field(id, 'condition') || undefined,
      cost: field(id, 'cost') || undefined,
    },
    'return-to-service': { remarks: field(id, 'remarks') },
    cancel: { reason: field(id, 'remarks') },
  }[verb]);

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Maintenance</h1>
          <p className="lr-page-sub">
            Reporting a fault does not take an asset off somebody's desk — it goes
            away when it is sent for repair
          </p>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState message="No maintenance tickets." ctaTo={null} />
      ) : rows.map((row) => (
        <div key={row.id} className="memo-panel" data-testid="maintenance-ticket">
          <div className="memo-matrix-head">
            <h3 className="memo-panel-title" style={{ margin: 0 }}>
              <Wrench size={15} aria-hidden="true" /> {row.reference}
            </h3>
            <span>
              <span className={`min-status is-${PRIORITY_TONES[row.priority]}`}>
                {row.priority_label}
              </span>
              {' '}
              <span className={`min-status is-${TONES[row.status] || 'muted'}`}>
                {row.status_label}
              </span>
            </span>
          </div>

          <dl className="memo-doc-meta">
            <div><dt>Asset</dt><dd>{row.item_code} · {row.item_name}</dd></div>
            <div><dt>Reported By</dt><dd>{row.reported_by_name}</dd></div>
            <div><dt>Open</dt><dd>{row.days_open} day{row.days_open === 1 ? '' : 's'}</dd></div>
            {(row.assigned_to_name || row.vendor) && (
              <div><dt>With</dt><dd>{row.assigned_to_name || row.vendor}</dd></div>
            )}
            {row.cost && <div><dt>Cost</dt><dd>{row.cost}</dd></div>}
          </dl>
          <p><b>Issue.</b> {row.issue}</p>
          {row.resolution && <p><b>Resolution.</b> {row.resolution}</p>}

          {row.can_manage && (
            <>
              {row.status === 'in_maintenance' && (
                <label className="lr-field">
                  <span>Resolution *</span>
                  <textarea rows={2} value={field(row.id, 'resolution')}
                    aria-label={`Resolution for ${row.reference}`}
                    onChange={(e) => setField(row.id, 'resolution', e.target.value)} />
                </label>
              )}
              {row.status === 'reported' && (
                <label className="lr-field">
                  <span>Vendor or technician</span>
                  <input value={field(row.id, 'vendor')}
                    aria-label={`Vendor for ${row.reference}`}
                    onChange={(e) => setField(row.id, 'vendor', e.target.value)} />
                </label>
              )}
              <div className="memo-matrix-actions">
                {(NEXT[row.status] || []).map(([verb, label]) => (
                  <button key={verb} type="button" className="lr-btn lr-btn-primary"
                    disabled={act.isPending}
                    onClick={() => act.mutate(
                      { id: row.id, verb, payload: payloadFor(row.id, verb) })}>
                    {label}
                  </button>
                ))}
                <button type="button" className="lr-btn lr-btn-ghost"
                  disabled={act.isPending}
                  onClick={() => act.mutate(
                    { id: row.id, verb: 'cancel',
                      payload: payloadFor(row.id, 'cancel') })}>
                  Cancel ticket
                </button>
              </div>
            </>
          )}
        </div>
      ))}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default MaintenanceTickets;
