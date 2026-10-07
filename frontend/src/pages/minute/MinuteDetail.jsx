import React, { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, CalendarClock, ClipboardSignature, FileDown, FileSpreadsheet, Loader, Lock, Trash2, UserSquare, X } from 'lucide-react';

import { minuteService } from '../../services/minuteService';
import { saveBlob } from '../../services/documentService';
import RichTextEditor from '../../components/memo/RichTextEditor';
import WorkflowTimeline from '../../components/memo/WorkflowTimeline';
import AuditTrailPanel from '../../components/memo/AuditTrailPanel';
import AcknowledgementPanel from '../../components/minute/AcknowledgementPanel';
import AttachmentPanel from '../../components/minute/AttachmentPanel';
import MinuteStatusTrail from '../../components/minute/MinuteStatusTrail';
import NextStepBanner from '../../components/minute/NextStepBanner';
import MoreDetails from '../../components/minute/MoreDetails';
import { MINUTE_STATUS_TONES } from '../../components/minute/minuteLabels';
import ConfirmModal from '../../components/admin/ConfirmModal';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The minute page (E-minute-manual pp. 6-10).
 *
 * Read top to bottom it answers: what minute is this, what state is it in and is
 * anything wanted from me (the banner), what the meeting recorded, and who has
 * acknowledged it. The audit-grade material - the history and the trail - is kept but
 * folded into "Full record" at the bottom rather than opening with the page.
 *
 * Every button is rendered from a server capability flag (`can_*`), so authorization
 * has exactly one home and the UI cannot offer an action the API would refuse. There
 * is no approve button, because the manual has no approver.
 */
const ReturnModal = ({ busy, onClose, onSubmit }) => {
  const [remarks, setRemarks] = useState('');
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true"
      aria-label="Return to author" onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head">
          <h3>Return to author</h3>
          <button type="button" className="lr-modal-close" aria-label="Close"
            onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>
        <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>
          The draft goes back to whoever wrote it, for the changes you ask for.
        </p>
        <label className="lr-field">
          <span>Remarks</span>
          <textarea rows={4} value={remarks} aria-label="Remarks"
            onChange={(e) => setRemarks(e.target.value)} />
        </label>
        <div className="memo-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={busy}
            onClick={() => onSubmit(remarks)}>
            {busy ? 'Working…' : 'Return minute'}
          </button>
        </div>
      </div>
    </div>
  );
};

const MinuteDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null);
  const [exporting, setExporting] = useState(false);

  const { data: minute, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['minute', id],
    queryFn: () => minuteService.getMinute(id),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['minute', id] });
    qc.invalidateQueries({ queryKey: ['minutes'] });
  };

  const succeed = (message) => {
    setToast({ message, tone: 'success' });
    setModal(null);
    invalidate();
  };

  const fail = (e) => {
    const payload = e?.response?.data;
    const message = payload?.detail || payload?.workflow || payload?.acknowledgement
      || payload?.participants || (typeof payload === 'string' && payload.length < 300 && !payload.includes('<') ? payload : null)
      || 'That didn’t go through. Please try again.';
    setToast({
      message: Array.isArray(message) ? message[0] : String(message), tone: 'error',
    });
  };

  const sendForReview = useMutation({
    mutationFn: () => minuteService.sendForReview(id),
    onSuccess: () => succeed('Sent to the FRO for draft review.'),
    onError: fail,
  });

  const returnReview = useMutation({
    mutationFn: (remarks) => minuteService.returnReview(id, remarks),
    onSuccess: () => succeed('Returned to the author.'),
    onError: fail,
  });

  const sendForAcknowledgement = useMutation({
    mutationFn: () => minuteService.sendForAcknowledgement(id),
    onSuccess: () => succeed('Submitted — the members present have been asked to '
      + 'acknowledge.'),
    onError: fail,
  });

  const acknowledge = useMutation({
    mutationFn: (remarks) => minuteService.acknowledge(id, { remarks }),
    onSuccess: () => succeed('Thank you — your acknowledgement is recorded.'),
    onError: fail,
  });

  const remind = useMutation({
    mutationFn: () => minuteService.remindAcknowledgements(id),
    onSuccess: (data) => succeed(`Reminded ${data.reminded} member(s).`),
    onError: fail,
  });

  const remove = useMutation({
    mutationFn: () => minuteService.deleteMinute(id),
    onSuccess: () => {
      setToast({ message: 'Minute deleted.', tone: 'success' });
      invalidate();
      navigate('/minutes/mine');
    },
    onError: fail,
  });

  const uploadFiles = useMutation({
    mutationFn: ({ files, replaces }) =>
      minuteService.uploadAttachments(id, files, { replaces }),
    onSuccess: (rows) => succeed(
      `${rows.length} file${rows.length === 1 ? '' : 's'} attached.`),
    onError: fail,
  });
  const removeFile = useMutation({
    mutationFn: (attachmentId) => minuteService.deleteAttachment(id, attachmentId),
    onSuccess: () => succeed('Attachment removed.'),
    onError: fail,
  });

  if (isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }
  // react-query can hand back `data: undefined` with neither flag set while a failed
  // query is being retried. Everything below dereferences `minute` heavily, so without
  // this guard that state takes the whole page down instead of degrading.
  if (!minute) return <div className="page"><Skeleton rows={5} /></div>;

  const EXPORTS = {
    pdf: { fetch: () => minuteService.pdf(id), ext: 'pdf', label: 'PDF' },
    excel: { fetch: () => minuteService.excel(id), ext: 'xlsx', label: 'Excel' },
    sheet: {
      fetch: () => minuteService.acknowledgementSheet(id),
      ext: 'pdf', suffix: '-acknowledgement', label: 'Signature sheet',
    },
  };

  const download = async (kind) => {
    const spec = EXPORTS[kind];
    setExporting(true);
    try {
      saveBlob(await spec.fetch(),
        `${minute.minute_number}${spec.suffix || ''}.${spec.ext}`);
    } catch {
      setToast({ message: `${spec.label} download failed.`, tone: 'error' });
    } finally {
      setExporting(false);
    }
  };

  const stamp = (iso) => (iso ? new Date(iso).toLocaleString() : null);
  const busy = sendForReview.isPending || sendForAcknowledgement.isPending
    || acknowledge.isPending || returnReview.isPending;

  return (
    <div className="page memo-page memo-detail">
      <button type="button" className="lr-btn lr-btn-ghost memo-back"
        onClick={() => navigate(-1)}>
        <ArrowLeft size={14} /> Back
      </button>

      <header className="memo-doc-head">
        <div className="memo-doc-head-top">
          <div>
            <div className="memo-doc-kicker">Minute · {minute.minute_number}</div>
            <h1 className="memo-head-title">{minute.title}</h1>
            <div className="memo-head-badges">
              <span className={`min-status is-${
                MINUTE_STATUS_TONES[minute.status] || 'muted'}`}>
                {minute.status_label}
              </span>
              <span className="min-status is-muted">{minute.type_label}</span>
              {minute.is_read_only && (
                <span className="memo-lock-tag">
                  <Lock size={11} aria-hidden="true" /> Read-only
                </span>
              )}
            </div>
          </div>
          <div className="memo-doc-head-actions">
            {minute.can_export && (
              <button type="button" className="lr-btn" disabled={exporting}
                onClick={() => download('pdf')}>
                {exporting
                  ? <Loader size={14} className="lr-spin" />
                  : <FileDown size={14} />}
                {' '}Download PDF
              </button>
            )}
            {minute.can_edit && (
              <button type="button" className="lr-btn"
                onClick={() => navigate(`/minutes/${id}/edit`)}>Edit</button>
            )}
            {minute.can_delete && (
              <button type="button" className="lr-btn lr-btn-ghost"
                onClick={() => setModal({ type: 'delete' })}>
                <Trash2 size={14} /> Delete
              </button>
            )}
          </div>
        </div>

        {/* The manual's own header line: date, time, type, then who wrote it. */}
        <dl className="memo-doc-meta">
          <div><dt>Date</dt><dd>{minute.meeting_date || '—'}</dd></div>
          <div><dt>Time</dt>
            <dd>{(minute.meeting_time || '').slice(0, 5) || '—'}</dd></div>
          <div><dt>Minute Type</dt><dd>{minute.type_label}</dd></div>
          <div><dt>Initiated By</dt>
            <dd>{minute.created_by?.full_name || '—'}</dd></div>
          {minute.reference_number && (
            <div><dt>Reference No.</dt><dd>{minute.reference_number}</dd></div>
          )}
          {minute.fro_name && (
            <div><dt><UserSquare size={11} aria-hidden="true" /> FRO</dt>
              <dd>{minute.fro_name}</dd></div>
          )}
        </dl>
      </header>

      <NextStepBanner minute={minute} busy={busy}
        onSendForReview={() => sendForReview.mutate()}
        onSendForAcknowledgement={() => sendForAcknowledgement.mutate()}
        onReturnReview={() => setModal({ type: 'return' })}
        onAcknowledge={() => acknowledge.mutate('')}
        onEdit={() => navigate(`/minutes/${id}/edit`)} />

      <MinuteStatusTrail stages={minute.tracker}
        acknowledgement={minute.acknowledgement} />

      {/* Members Present / Absent / Invitee, as the manual's minute page prints them. */}
      <div className="memo-panel">
        <h3 className="memo-panel-title">
          <CalendarClock size={15} aria-hidden="true" /> Members
        </h3>
        <dl className="min-meeting-grid">
          {['present', 'absent', 'invitee'].map((group) => {
            const names = (minute.participants || [])
              .filter((row) => row.attendance === group)
              .map((row) => row.name);
            if (!names.length) return null;
            const label = group === 'present' ? 'Members Present'
              : group === 'absent' ? 'Members Absent' : 'Invitee Members';
            return (
              <div key={group}>
                <dt>{label}</dt>
                <dd>{names.join(', ')}</dd>
              </div>
            );
          })}
        </dl>
      </div>

      <div className="memo-panel">
        <h3 className="memo-panel-title">Meeting Agenda/Discussion/Decisions</h3>
        {minute.agenda_body
          ? <RichTextEditor value={minute.agenda_body} readOnly />
          : <p className="lr-page-sub">Nothing was recorded.</p>}
      </div>

      <AttachmentPanel
        attachments={minute.attachments}
        canEdit={minute.can_edit}
        busy={uploadFiles.isPending || removeFile.isPending}
        onUpload={(files, opts) => uploadFiles.mutate({ files, ...opts })}
        onDelete={(attachmentId) => removeFile.mutate(attachmentId)} />

      <div id="acknowledge">
        <AcknowledgementPanel
          summary={minute.acknowledgement}
          deadline={minute.acknowledgement_deadline}
          participants={minute.participants}
          canAcknowledge={minute.can_acknowledge}
          canRemind={minute.can_remind_acknowledgements}
          myRow={minute.my_participation}
          busy={acknowledge.isPending || remind.isPending}
          onAcknowledge={(remarks) => acknowledge.mutate(remarks)}
          onRemind={() => remind.mutate()} />
      </div>

      {/* Kept, not deleted: the record an auditor asks for, one click away. */}
      <MoreDetails title="Full record"
        hint="Signature blocks, history, audit trail and the other exports">
        <div className="memo-panel">
          <h3 className="memo-panel-title">Signature blocks</h3>
          <div className="min-sig-grid">
            {(minute.signatures || []).map((block) => (
              <div className="min-sig" key={block.name}>
                <div className="min-sig-stamp">
                  {block.stamp && (
                    <span className={`min-status is-${
                      block.stamp === 'ABSENT' ? 'no' : 'ok'}`}>{block.stamp}</span>
                  )}
                </div>
                <div className="min-sig-line" />
                <div className="min-sig-name">{block.name}</div>
                <div className="min-sig-meta">
                  {block.department || block.designation || ''}
                  {block.date && <><br />{new Date(block.date)
                    .toLocaleDateString()}</>}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="memo-doc-head-actions" style={{ margin: '12px 0 18px' }}>
          {minute.can_export && (
            <>
              <button type="button" className="lr-btn" disabled={exporting}
                onClick={() => download('excel')}>
                <FileSpreadsheet size={14} /> Excel
              </button>
              <button type="button" className="lr-btn" disabled={exporting}
                onClick={() => download('sheet')}>
                <ClipboardSignature size={14} /> Signature sheet
              </button>
            </>
          )}
        </div>

        <dl className="memo-doc-meta">
          <div><dt>Department</dt><dd>{minute.department_name || '—'}</dd></div>
          <div><dt>Created</dt><dd>{stamp(minute.created_at) || '—'}</dd></div>
          {minute.sent_for_review_at && (
            <div><dt>Sent for review</dt>
              <dd>{stamp(minute.sent_for_review_at)}</dd></div>
          )}
          {minute.acknowledgement_opened_at && (
            <div><dt>Submitted for acknowledgement</dt>
              <dd>{stamp(minute.acknowledgement_opened_at)}</dd></div>
          )}
          {minute.archived_at && (
            <div><dt>Archived</dt><dd>{stamp(minute.archived_at)}</dd></div>
          )}
        </dl>

        <div className="memo-panel">
          <h3 className="memo-panel-title">History</h3>
          <WorkflowTimeline entries={minute.timeline || []} />
        </div>

        {/* Renders nothing for a user the endpoint 403s, rather than advertising
            data it cannot show. */}
        <AuditTrailPanel
          queryKey={['minute', id, 'audit-trail']}
          queryFn={() => minuteService.getAuditTrail(id)} />
      </MoreDetails>

      {modal?.type === 'return' && (
        <ReturnModal busy={returnReview.isPending}
          onClose={() => setModal(null)}
          onSubmit={(remarks) => returnReview.mutate(remarks)} />
      )}
      {modal?.type === 'delete' && (
        <ConfirmModal
          title="Delete this minute?"
          message="The draft is removed permanently. The deletion is recorded in the audit log."
          danger confirmLabel="Delete" busy={remove.isPending}
          onClose={() => setModal(null)} onConfirm={() => remove.mutate()} />
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default MinuteDetail;
