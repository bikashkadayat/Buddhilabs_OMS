import React, { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowLeft, Ban, CheckCircle2, FileDown, Loader, Lock, Send, Trash2, X, XCircle } from 'lucide-react';

import { circularService } from '../../services/circularService';
import { saveBlob } from '../../services/documentService';
import RichTextEditor from '../../components/memo/RichTextEditor';
import WorkflowTracker from '../../components/memo/WorkflowTracker';
import WorkflowTimeline from '../../components/memo/WorkflowTimeline';
import ApprovalMatrixTable from '../../components/memo/ApprovalMatrixTable';
import AuditTrailPanel from '../../components/memo/AuditTrailPanel';
import BroadcastPanel from '../../components/circular/BroadcastPanel';
import CircularAckPanel from '../../components/circular/CircularAckPanel';
import { CIRCULAR_STATUS_TONES, PRIORITY_TONES }
  from '../../components/circular/circularLabels';
import ConfirmModal from '../../components/admin/ConfirmModal';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import { useAuth } from '../../hooks/useAuth';

const MIN_REMARK = 10;

const DecisionModal = ({ title, intro, requireRemarks, confirmLabel, danger, busy,
  onClose, onSubmit }) => {
  const [remarks, setRemarks] = useState('');
  const ok = !requireRemarks || remarks.trim().length >= MIN_REMARK;
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true"
      aria-label={title} onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head">
          <h3>{title}</h3>
          <button type="button" className="lr-modal-close" aria-label="Close"
            onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>
        {intro && <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>{intro}</p>}
        <label className="lr-field">
          <span>
            Remarks{requireRemarks && (
              <span aria-hidden> * (minimum {MIN_REMARK} characters)</span>
            )}
          </span>
          <textarea rows={4} value={remarks} aria-label="Remarks"
            onChange={(e) => setRemarks(e.target.value)} />
        </label>
        <div className="memo-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button"
            className={`lr-btn ${danger ? 'lr-btn-danger' : 'lr-btn-primary'}`}
            disabled={!ok || busy} onClick={() => onSubmit(remarks)}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

/**
 * The circular detail page (Phase 50).
 *
 * Reuses the memo module's presentation wholesale - the workflow tracker, the
 * timeline, the chain table and the audit panel - because the serializers
 * deliberately produce the same shapes. What is new is what a circular has and a
 * memo does not: an audience, read tracking, and an optional acknowledgement round.
 *
 * Every button is rendered from a server capability flag (`can_*`), so authorization
 * has exactly one home and the UI cannot offer an action the API would refuse.
 */
const CircularDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null);
  const [exporting, setExporting] = useState(false);

  const { data: circular, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['circular', id],
    queryFn: () => circularService.getCircular(id),
  });

  // The registers are a separate request because they are a separate PERMISSION:
  // a recipient may read the circular and not the list of who else has. Fetched
  // only when the server says the reader is entitled to them.
  const { data: registers } = useQuery({
    queryKey: ['circular', id, 'registers'],
    queryFn: async () => {
      const [recipients, acks] = await Promise.all([
        circularService.getRecipients(id),
        circular?.acknowledgement?.required
          ? circularService.getAcknowledgements(id)
          : Promise.resolve({ recipients: [] }),
      ]);
      // Merged by recipient so the panel renders ONE row per person rather than
      // two tables the reader has to align by eye.
      const byName = new Map(
        (acks.recipients || []).map((row) => [row.recipient_id, row]));
      return (recipients.recipients || []).map((row) => {
        const ack = byName.get(row.id);
        return {
          ...row,
          ack_state: ack?.state,
          ack_state_label: ack?.state_label,
          ack_responded_at: ack?.responded_at,
          ack_remarks: ack?.remarks,
          ack_is_late: ack?.is_late,
        };
      });
    },
    enabled: Boolean(circular?.can_view_registers),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['circular', id] });
    qc.invalidateQueries({ queryKey: ['circulars'] });
  };

  const succeed = (message) => {
    setToast({ message, tone: 'success' });
    setModal(null);
    invalidate();
  };

  const fail = (e) => {
    const payload = e?.response?.data;
    const message = payload?.detail || payload?.workflow || payload?.broadcast
      || payload?.audience || payload?.acknowledgement || payload?.remarks
      || payload?.archive || payload?.cancel
      || (typeof payload === 'string' && payload.length < 300 && !payload.includes('<') ? payload : null) || 'That didn’t go through. Please try again.';
    setToast({ message: Array.isArray(message) ? message[0] : String(message),
      tone: 'error' });
  };

  const act = useMutation({
    mutationFn: ({ decision, remarks }) =>
      circularService.act(id, { decision, remarks }),
    onSuccess: (_r, variables) => succeed(
      variables.decision === 'reject'
        ? 'Circular returned to its author.'
        : 'Your action was recorded.'),
    onError: fail,
  });
  const broadcast = useMutation({
    mutationFn: (payload) => circularService.broadcast(id, payload),
    onSuccess: (row) => succeed(
      `Broadcast to ${row.broadcasts?.[0]?.recipient_count ?? 0} recipient(s).`),
    onError: fail,
  });
  const acknowledge = useMutation({
    mutationFn: ({ accept, remarks }) =>
      circularService.acknowledge(id, { accept, remarks }),
    onSuccess: (_r, variables) => succeed(
      variables.accept ? 'Acknowledgement recorded.'
        : 'Your decline has been recorded against the circular.'),
    onError: fail,
  });
  const remind = useMutation({
    mutationFn: () => circularService.remind(id),
    onSuccess: (data) => succeed(`Reminded ${data.reminded} recipient(s).`),
    onError: fail,
  });
  const archive = useMutation({
    mutationFn: () => circularService.archive(id),
    onSuccess: () => succeed('Circular filed to the permanent record.'),
    onError: fail,
  });
  const cancel = useMutation({
    mutationFn: (remarks) => circularService.cancel(id, remarks),
    onSuccess: () => succeed('Circular cancelled.'),
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: () => circularService.deleteCircular(id),
    onSuccess: () => { invalidate(); navigate('/circulars/drafts'); },
    onError: fail,
  });

  if (isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }
  // react-query can hand back `data: undefined` with neither flag set while a failed
  // query is being retried; everything below dereferences `circular` heavily.
  if (!circular) return <div className="page"><Skeleton rows={5} /></div>;

  const download = async () => {
    setExporting(true);
    try {
      saveBlob(await circularService.pdf(id), `${circular.circular_number}.pdf`);
    } catch {
      setToast({ message: 'PDF download failed.', tone: 'error' });
    } finally {
      setExporting(false);
    }
  };

  const stamp = (iso) => (iso ? new Date(iso).toLocaleString() : null);
  const myStep = circular.my_step;
  const myTurn = Boolean(myStep?.is_my_turn) && circular.can_act;

  return (
    <div className="page memo-page memo-detail">
      <button type="button" className="lr-btn lr-btn-ghost memo-back"
        onClick={() => navigate(-1)}>
        <ArrowLeft size={14} /> Back
      </button>

      <header className="memo-doc-head">
        <div className="memo-doc-head-top">
          <div>
            <div className="memo-doc-kicker">Circular</div>
            <div className="memo-head-number">{circular.circular_number}</div>
            <h1 className="memo-head-title">{circular.subject}</h1>
          </div>
          <div className="memo-doc-head-actions">
            {circular.can_export && (
              <button type="button" className="lr-btn" disabled={exporting}
                onClick={download}>
                {exporting ? <Loader size={14} className="lr-spin" />
                  : <FileDown size={14} />} PDF
              </button>
            )}
          </div>
        </div>

        <div className="memo-head-badges">
          <span className={`min-status is-${
            CIRCULAR_STATUS_TONES[circular.status] || 'muted'}`}>
            {circular.status_label}
          </span>
          <span className={`min-status is-${
            PRIORITY_TONES[circular.priority] || 'muted'}`}>
            {circular.priority_label}
          </span>
          <span className="min-status is-muted">{circular.category_label}</span>
          <span className={`min-status is-${
            circular.classification === 'internal' ? 'muted' : 'no'}`}>
            {circular.classification_label}
          </span>
          {circular.is_read_only && (
            <span className="memo-lock-tag">
              <Lock size={11} aria-hidden="true" /> Read-only
            </span>
          )}
        </div>

        <dl className="memo-doc-meta">
          <div><dt>Issue Date</dt><dd>{circular.issue_date || '—'}</dd></div>
          <div><dt>Department</dt><dd>{circular.department_label || '—'}</dd></div>
          <div><dt>Prepared By</dt>
            <dd>{circular.created_by?.full_name || '—'}</dd></div>
          <div><dt>Issued By</dt><dd>{circular.issued_by_name || '—'}</dd></div>
          {circular.external_reference && (
            <div><dt>Reference</dt><dd>{circular.external_reference}</dd></div>
          )}
          {circular.memo_reference_label && (
            <div><dt>Arising From</dt>
              <dd>Memo {circular.memo_reference_label}</dd></div>
          )}
          {circular.minute_reference_label && (
            <div><dt>Arising From</dt>
              <dd>Minute {circular.minute_reference_label}</dd></div>
          )}
          <div><dt>Pending With</dt><dd>
            {circular.pending_with
              ? `${circular.pending_with.name} · ${circular.pending_with.role_label}`
              : '—'}
          </dd></div>
          {circular.broadcast_at && (
            <div><dt>Broadcast</dt><dd>{stamp(circular.broadcast_at)}</dd></div>
          )}
        </dl>
      </header>

      {circular.is_read_only && (
        <div className="memo-notice" role="status">
          <Lock size={14} aria-hidden="true" />
          This circular is {circular.status_label.toLowerCase()} and is part of the
          permanent record. It can be viewed and exported but no longer changed.
        </div>
      )}
      {circular.status === 'rejected' && circular.can_edit && (
        <div className="memo-notice is-warn" role="status">
          <XCircle size={14} aria-hidden="true" />
          This circular was returned to you. Revise it and submit again — the chain
          restarts from the top.
        </div>
      )}

      <div className="memo-detail-grid">
        <div className="memo-detail-main">
          <WorkflowTracker stages={circular.tracker} />

          <div className="memo-panel">
            <h3 className="memo-panel-title">Circular</h3>
            <RichTextEditor value={circular.content} readOnly />
          </div>

          {circular.attachments?.length > 0 && (
            <div className="memo-panel">
              <h3 className="memo-panel-title">Attachments</h3>
              <ul className="min-file-list">
                {circular.attachments.map((file) => (
                  <li key={file.id} className="min-file">
                    <span className="min-file-main">
                      <a href={file.url} className="min-file-name">
                        {file.original_name}
                      </a>
                      <span className="lr-page-sub">
                        {file.size_label} · {file.uploaded_by?.full_name}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <ApprovalMatrixTable
            steps={(circular.workflow_steps || []).map((step) => ({
              ...step, role_type: step.role_type, role_label: step.role_label,
            }))}
            currentUserId={user?.id} />

          {circular.can_broadcast && (
            <BroadcastPanel circular={circular} busy={broadcast.isPending}
              onBroadcast={(payload) => broadcast.mutate(payload)} />
          )}

          {circular.broadcasts?.length > 0 && (
            <div className="memo-panel">
              <h3 className="memo-panel-title">Broadcast History</h3>
              <div className="lr-table-wrap">
                <table className="lr-table cir-register">
                  <thead>
                    <tr><th>S.N.</th><th>Audience</th><th>Recipients</th>
                      <th>Broadcast By</th><th>Date</th></tr>
                  </thead>
                  <tbody>
                    {circular.broadcasts.map((row) => (
                      <tr key={row.id}>
                        <td>{row.sequence}</td>
                        <td className="is-primary">{row.audience_label}</td>
                        <td>
                          {row.recipient_count}
                          {row.already_present_count > 0 && (
                            <span className="lr-page-sub">
                              {' '}(+{row.already_present_count} already held it)
                            </span>
                          )}
                        </td>
                        <td>{row.broadcast_by}</td>
                        <td>{stamp(row.broadcast_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(circular.status === 'broadcasted' || circular.status === 'archived') && (
            <CircularAckPanel
              readSummary={circular.read_summary}
              acknowledgement={circular.acknowledgement}
              myAcknowledgement={circular.my_acknowledgement}
              register={registers}
              canAcknowledge={circular.can_acknowledge}
              canRemind={circular.can_view_registers}
              busy={acknowledge.isPending || remind.isPending}
              onAcknowledge={(accept, remarks) =>
                acknowledge.mutate({ accept, remarks })}
              onRemind={() => remind.mutate()} />
          )}

          <div className="memo-panel">
            <h3 className="memo-panel-title">Activity Timeline</h3>
            <WorkflowTimeline entries={circular.timeline} />
          </div>

          {/* Renders nothing for a reader the endpoint 403s, rather than
              advertising data it cannot show. */}
          <AuditTrailPanel
            queryKey={['circular', id, 'audit-trail']}
            queryFn={() => circularService.getAuditTrail(id)} />
        </div>

        <aside className="memo-detail-side">
          <div className="memo-panel memo-actions-panel">
            <h3 className="memo-panel-title">Actions</h3>

            {myStep && !myTurn && (
              <p className="lr-page-sub">
                You are step {myStep.sequence} ({myStep.role_label}). Earlier steps
                must complete before you can act.
              </p>
            )}
            {myTurn && (
              <>
                <p className="lr-page-sub">
                  This circular is with you for <b>{myStep.role_label}</b>.
                </p>
                <button type="button" className="lr-btn lr-btn-primary"
                  onClick={() => setModal({ type: 'proceed' })}>
                  <CheckCircle2 size={14} /> {myStep.role_label}
                </button>
                <button type="button" className="lr-btn lr-btn-danger"
                  onClick={() => setModal({ type: 'reject' })}>
                  <XCircle size={14} /> Return for revision
                </button>
              </>
            )}

            {circular.can_edit && (
              <button type="button" className="lr-btn lr-btn-ghost"
                onClick={() => navigate(`/circulars/${id}/edit`)}>
                Edit circular
              </button>
            )}
            {circular.can_submit && (
              <button type="button" className="lr-btn lr-btn-primary"
                onClick={() => navigate(`/circulars/${id}/edit`)}>
                <Send size={14} /> Submit
              </button>
            )}
            {circular.can_archive && (
              <button type="button" className="lr-btn"
                disabled={archive.isPending} onClick={() => archive.mutate()}>
                <Archive size={14} /> Archive
              </button>
            )}
            {circular.can_cancel && (
              <button type="button" className="lr-btn lr-btn-ghost"
                onClick={() => setModal({ type: 'cancel' })}>
                <Ban size={14} /> Cancel circular
              </button>
            )}
            {circular.can_delete && (
              <button type="button" className="lr-btn lr-btn-danger"
                onClick={() => setModal({ type: 'delete' })}>
                <Trash2 size={14} /> Delete draft
              </button>
            )}
          </div>
        </aside>
      </div>

      {modal?.type === 'proceed' && (
        <DecisionModal
          title={`${myStep.role_label} — confirm`}
          intro={myStep.role_type === 'issuer'
            ? 'Issuing makes this circular official. Its content can no longer be changed.'
            : 'Recording your review moves this circular to the next step.'}
          requireRemarks={Boolean(myStep.requires_remarks)}
          confirmLabel="Confirm" busy={act.isPending}
          onClose={() => setModal(null)}
          onSubmit={(remarks) => act.mutate({ decision: 'proceed', remarks })} />
      )}
      {modal?.type === 'reject' && (
        <DecisionModal
          title="Return for revision"
          intro="The circular returns to its author and the remaining steps stand down."
          requireRemarks confirmLabel="Return" danger busy={act.isPending}
          onClose={() => setModal(null)}
          onSubmit={(remarks) => act.mutate({ decision: 'reject', remarks })} />
      )}
      {modal?.type === 'cancel' && (
        <ConfirmModal
          title="Cancel this circular?"
          message="It is marked Cancelled and outstanding steps stand down. A broadcast circular cannot be cancelled."
          requireReason danger confirmLabel="Cancel circular" busy={cancel.isPending}
          onClose={() => setModal(null)}
          onConfirm={(reason) => cancel.mutate(reason)} />
      )}
      {modal?.type === 'delete' && (
        <ConfirmModal
          title="Delete this draft?"
          message="The draft is removed permanently. The deletion is recorded in the audit log."
          danger confirmLabel="Delete" busy={remove.isPending}
          onClose={() => setModal(null)} onConfirm={() => remove.mutate()} />
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default CircularDetail;
