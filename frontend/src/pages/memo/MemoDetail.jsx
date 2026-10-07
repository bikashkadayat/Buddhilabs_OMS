import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowLeft, Ban, CheckCircle2, FileDown, Loader, Lock, Paperclip, Send, Trash2, X, XCircle } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { memoService } from '../../services/memoService';
import { documentService, saveBlob } from '../../services/documentService';
import {
  MemoStatusBadge, MemoTypeBadge,
} from '../../components/memo/badges';
import { roleTypeLabel, roleAction, roleGuidance } from '../../components/memo/memoLabels';
import RichTextEditor from '../../components/memo/RichTextEditor';
import MemoSpecialActions from '../../components/memo/MemoSpecialActions';
import ApprovalMatrixTable from '../../components/memo/ApprovalMatrixTable';
import ApprovalMatrixEditor from '../../components/memo/ApprovalMatrixEditor';
import WorkflowTimeline from '../../components/memo/WorkflowTimeline';
import WorkflowTracker from '../../components/memo/WorkflowTracker';
import WorkflowStatusCard from '../../components/memo/WorkflowStatusCard';
import SignatureCards from '../../components/memo/SignatureCards';
import ApprovalSeal from '../../components/memo/ApprovalSeal';
import AuditTrailPanel from '../../components/memo/AuditTrailPanel';
import ConfirmModal from '../../components/admin/ConfirmModal';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import MemoAttachmentPanel from '../../components/memo/AttachmentPanel';

const MIN_REMARK = 10;

/** Collects remarks for a workflow decision. Enforces the same floor as the API. */
const DecisionModal = ({ title, intro, requireRemarks, confirmLabel, danger, busy, onClose, onSubmit }) => {
  const [remarks, setRemarks] = useState('');
  const ok = !requireRemarks || remarks.trim().length >= MIN_REMARK;
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head">
          <h3>{title}</h3>
          <button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>
        {intro && <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>{intro}</p>}
        <label className="lr-field">
          <span>
            Remarks{requireRemarks && <span aria-hidden> * (minimum {MIN_REMARK} characters)</span>}
          </span>
          <textarea rows={4} value={remarks} onChange={(e) => setRemarks(e.target.value)}
            aria-label="Remarks" placeholder={requireRemarks ? 'Explain your decision so the author knows what to change…' : 'Optional'} />
        </label>
        {requireRemarks && !ok && remarks.length > 0 && (
          <p className="lr-page-sub">{MIN_REMARK - remarks.trim().length} more character(s) needed.</p>
        )}
        <div className="memo-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className={`lr-btn ${danger ? 'lr-btn-danger' : 'lr-btn-primary'}`}
            disabled={!ok || busy} onClick={() => onSubmit(remarks)}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

const MemoDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [editingMatrix, setEditingMatrix] = useState(false);
  const [matrixRows, setMatrixRows] = useState([]);
  const [matrixError, setMatrixError] = useState(null);

  const { data: memo, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['memo', id], queryFn: () => memoService.getMemo(id),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['memo', id] });
    qc.invalidateQueries({ queryKey: ['memos'] });
  };

  const succeed = (message) => {
    setToast({ message, tone: 'success' });
    setModal(null);
    setEditingMatrix(false);
    invalidate();
  };

  const fail = (e) => {
    const payload = e?.response?.data;
    const message = payload?.detail
      || payload?.workflow
      || payload?.remarks
      || (typeof payload === 'string' && payload.length < 300 && !payload.includes('<') ? payload : null)
      || 'That didn’t go through. Please try again.';
    setToast({ message: Array.isArray(message) ? message[0] : String(message), tone: 'error' });
  };

  // Seed the editor from the memo's existing matrix so "edit workflow" starts
  // from what is already there rather than from an empty table.
  const beginMatrixEdit = () => {
    setMatrixRows((memo.workflow_steps || []).map((step) => ({
      assignee_id: String(step.assignee?.id),
      full_name: step.assignee?.full_name || '',
      designation: step.designation || step.assignee?.designation || '',
      department: step.department_label || '',
      role_type: step.role_type,
    })));
    setMatrixError(null);
    setEditingMatrix(true);
  };

  const saveMatrix = useMutation({
    mutationFn: () => memoService.setMatrix(id, matrixRows.map(({ assignee_id, role_type }) => ({
      assignee_id, role_type,
    }))),
    onSuccess: () => succeed('Approval workflow saved.'),
    onError: (e) => {
      const detail = e?.response?.data?.workflow;
      setMatrixError(Array.isArray(detail) ? detail[0] : (detail || 'Could not save the workflow.'));
    },
  });

  const sendForReview = useMutation({
    mutationFn: () => memoService.sendForReview(id),
    onSuccess: () => succeed('Memo sent for review.'),
    onError: fail,
  });

  const act = useMutation({
    mutationFn: ({ decision, remarks }) => memoService.actOnMemo(id, { decision, remarks }),
    onSuccess: (_result, variables) => succeed(
      variables.decision === 'reject' ? 'Memo rejected and returned to the author.' : 'Your action was recorded.',
    ),
    onError: fail,
  });

  const archive = useMutation({
    mutationFn: () => memoService.archiveMemo(id),
    onSuccess: () => succeed('Memo archived.'),
    onError: fail,
  });

  const withdraw = useMutation({
    mutationFn: (remarks) => memoService.withdrawMemo(id, { remarks }),
    onSuccess: () => succeed('Memo withdrawn.'),
    onError: fail,
  });

  const remove = useMutation({
    mutationFn: () => memoService.deleteMemo(id),
    onSuccess: () => { setToast({ message: 'Memo deleted.', tone: 'success' }); invalidate(); navigate('/memos/drafts'); },
    onError: fail,
  });

  if (isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isError) return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  // Everything below dereferences `memo` heavily, so a single render with it
  // undefined takes the whole page down rather than degrading. That is reachable:
  // react-query can hand back `data: undefined` with neither flag set while a
  // failed query is being retried, which is exactly what happened during a
  // mid-session backend error and produced a blank screen with a console
  // TypeError instead of the error state above.
  if (!memo) return <div className="page"><Skeleton rows={5} /></div>;

  const downloadPdf = async () => {
    setPdfBusy(true);
    try { saveBlob(await documentService.memoPdf(id), `${memo.memo_number}.pdf`); }
    catch { setToast({ message: 'PDF download failed.', tone: 'error' }); }
    finally { setPdfBusy(false); }
  };

  const name = (u) => u?.full_name || '—';
  const stamp = (iso) => (iso ? new Date(iso).toLocaleString() : null);
  const myStep = memo.my_step;
  const myTurn = Boolean(myStep?.is_my_turn) && memo.can_act;
  // The reviewer role is the one the spec marks "(Comment Required)"; the server
  // enforces it, and mirroring it here means the button is never enabled into a
  // guaranteed 400.
  const remarksRequired = Boolean(myStep?.requires_comment);

  return (
    <div className="page memo-page memo-detail">
      <button type="button" className="lr-btn lr-btn-ghost memo-back" onClick={() => navigate(-1)}>
        <ArrowLeft size={14} /> Back
      </button>

      {/* Phase 17 header: one card carrying the document's identity - number,
          reference, department, dates, classification and status - so a reader can
          cite the memo without scrolling to find its metadata. */}
      <header className="memo-doc-head">
        <div className="memo-doc-head-top">
          <div>
            <div className="memo-doc-kicker">Memorandum</div>
            <div className="memo-head-number">{memo.memo_number}</div>
            <h1 className="memo-head-title">{memo.subject}</h1>
            {memo.to_line && (
              <p className="memo-doc-subject">To: {memo.to_line}</p>
            )}
          </div>
          <div className="memo-doc-head-actions">
            {memo.can_download_pdf && (
              <button type="button" className="lr-btn" onClick={downloadPdf} disabled={pdfBusy}>
                {pdfBusy ? <Loader size={14} className="lr-spin" /> : <FileDown size={14} />} Download PDF
              </button>
            )}
          </div>
        </div>

        <div className="memo-head-badges">
          <MemoStatusBadge status={memo.status} />
          <MemoTypeBadge memo_type={memo.memo_type} />
          {memo.is_read_only && (
            <span className="memo-lock-tag"><Lock size={11} aria-hidden="true" /> Read-only</span>
          )}
        </div>

        <dl className="memo-doc-meta">
          <div><dt>To</dt><dd>{memo.to_line || '—'}</dd></div>
          <div><dt>From</dt><dd>
            {memo.department_label || '—'}
            {memo.department_unit_name && ` · ${memo.department_unit_name}`}
            {memo.department_sub_unit_name && ` · ${memo.department_sub_unit_name}`}
          </dd></div>
          <div><dt>CC</dt>
            <dd>{memo.cc_department_names?.length
              ? memo.cc_department_names.join(', ') : '—'}</dd></div>
          <div><dt>Memo Type</dt><dd>{memo.memo_type_label || '—'}</dd></div>
          {memo.reference_number && (
            <div><dt>Reference Memo</dt><dd>{memo.reference_number}</dd></div>
          )}
          <div><dt>Created by</dt><dd>{name(memo.created_by)}</dd></div>
          <div><dt>Created</dt><dd>{stamp(memo.created_at)}</dd></div>
          <div><dt>Pending with</dt><dd>
            {memo.pending_with
              ? `${memo.pending_with.name} · ${memo.pending_with.role_label}`
              : '—'}
          </dd></div>
          {memo.approved_at && <div><dt>Approved</dt><dd>{stamp(memo.approved_at)}</dd></div>}
          {memo.archived_at && <div><dt>Archived</dt><dd>{stamp(memo.archived_at)}</dd></div>}
        </dl>
      </header>

      {/* Phase 26: an approved or archived memo answers "approved, by whom, when"
          here, above everything else, so the archive does not need the activity
          log read to establish it. */}
      <ApprovalSeal certificate={memo.approval_certificate} />

      {memo.is_read_only && (
        <div className="memo-notice" role="status">
          <Lock size={14} aria-hidden="true" />
          This memo is {memo.status === 'archived' ? 'archived' : memo.status} and is part of the
          permanent record. It can be viewed and downloaded but no longer changed.
        </div>
      )}

      {memo.keeps_log === false && (
        <div className="memo-notice is-warn" role="status">
          This is a <b>DRAFT type</b> memo, circulated for feedback before the memo
          is raised formally. It is temporary and no log record is kept for it.
        </div>
      )}

      {memo.status === 'rejected' && memo.can_edit && (
        <div className="memo-notice is-warn" role="status">
          <XCircle size={14} aria-hidden="true" />
          This memo was rejected and returned to you. Revise it, then send it for review again —
          the same approval workflow restarts from the top.
        </div>
      )}

      <div className="memo-detail-grid">
        <div className="memo-detail-main">
          <div className="memo-panel">
            {/* One heading per authored block: Background, Recommendation, and
                anything "+ Add more" produced (p.3). */}
            {(memo.sections || []).map((section) => (
              <div className="memo-section-read" key={section.id}>
                <h3 className="memo-panel-title">{section.title}</h3>
                <RichTextEditor value={section.body} readOnly />
              </div>
            ))}
            {!memo.sections?.length && (
              <p className="lr-page-sub">Nothing has been written yet.</p>
            )}
            <MemoAttachmentPanel
              files={memo.attachments || []}
              legacyUrl={memo.attachment_url || ''}
            />
          </div>

          {/* Where it is NOW, above the stage pills: the tracker answers
              "which stage", this answers "with whom, and since when" — the
              question people actually open a memo to ask. */}
          <WorkflowStatusCard steps={memo.workflow_steps} status={memo.status} />
          <WorkflowTracker stages={memo.tracker} />

          {editingMatrix ? (
            <div className="memo-panel">
              <ApprovalMatrixEditor rows={matrixRows} onChange={setMatrixRows} error={matrixError} />
              <div className="memo-modal-actions">
                <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setEditingMatrix(false)}>Cancel</button>
                <button type="button" className="lr-btn lr-btn-primary" disabled={saveMatrix.isPending}
                  onClick={() => saveMatrix.mutate()}>
                  {saveMatrix.isPending ? 'Saving…' : 'Save workflow'}
                </button>
              </div>
            </div>
          ) : (
            <ApprovalMatrixTable steps={memo.workflow_steps} currentUserId={user?.id} />
          )}

          <SignatureCards blocks={memo.signatures} />

          <div className="memo-panel">
            <h3 className="memo-panel-title">Activity Timeline</h3>
            <WorkflowTimeline entries={memo.timeline} />
          </div>

          {/* Renders only for HR/Admin — the endpoint 403s for everyone else and
              the panel removes itself rather than advertising data it cannot show. */}
          <AuditTrailPanel memoId={id} />
        </div>

        <aside className="memo-detail-side">
          <MemoSpecialActions memo={memo}
            onDone={(message) => setToast({ message, tone: 'success' })}
            onError={(message) => setToast({ message, tone: 'error' })} />
          {/* Rendered only when it has something to offer. On an archived memo
              every control below is gated off, which used to leave an empty
              "Actions" card on the page. */}
          {(myTurn || myStep || memo.can_edit || memo.can_edit_matrix
            || memo.can_send_for_review || memo.can_archive || memo.can_withdraw
            || memo.can_delete) && (
          <div className="memo-panel memo-actions-panel">
            <h3 className="memo-panel-title">Workflow</h3>

            {myStep && !myTurn && myStep.status === 'pending' && (
              <p className="lr-page-sub">
                You are step {myStep.sequence} ({myStep.role_label}). Earlier steps must be
                completed before you can act.
              </p>
            )}

            {myTurn && (
              <>
                {/* WHAT STEP THIS IS, WHAT TO DO, THEN THE BUTTON.
                    The panel used to say "This memo is with you for Reviewer
                    action" above a button labelled "Reviewer" — the role's
                    name twice, and no verb anywhere. Somebody who did not
                    already know the workflow had nothing to tell them that
                    the noun was how you pass the memo on. */}
                <div className="memo-step-guide">
                  <span className="memo-step-eyebrow">
                    Step {myStep.sequence} of {memo.workflow_steps?.length || myStep.sequence}
                    {' · '}{roleTypeLabel(myStep.role_type)}
                  </span>
                  <strong className="memo-step-title">
                    {roleGuidance(myStep.role_type).title}
                  </strong>
                  <span className="memo-step-help">
                    {roleGuidance(myStep.role_type).instruction}
                  </span>
                </div>
                <button type="button" className="lr-btn lr-btn-primary"
                  onClick={() => setModal({ type: 'proceed' })}>
                  <CheckCircle2 size={14} /> {roleAction(myStep.role_type)}
                </button>
                <button type="button" className="lr-btn lr-btn-danger"
                  onClick={() => setModal({ type: 'reject' })}>
                  <XCircle size={14} /> Reject
                </button>
              </>
            )}

            {memo.can_edit && (
              <button type="button" className="lr-btn lr-btn-ghost"
                onClick={() => navigate(`/memos/${id}/edit`)}>Edit memo</button>
            )}
            {memo.can_edit_matrix && !editingMatrix && (
              <button type="button" className="lr-btn lr-btn-ghost" onClick={beginMatrixEdit}>
                {memo.workflow_steps?.length ? 'Edit approval workflow' : 'Build approval workflow'}
              </button>
            )}
            {memo.can_send_for_review && (
              <button type="button" className="lr-btn lr-btn-primary" disabled={sendForReview.isPending}
                onClick={() => sendForReview.mutate()}>
                <Send size={14} /> {sendForReview.isPending ? 'Sending…' : 'Send for Review'}
              </button>
            )}
            {memo.can_archive && (
              <button type="button" className="lr-btn" disabled={archive.isPending}
                onClick={() => archive.mutate()}>
                <Archive size={14} /> Archive memo
              </button>
            )}
            {memo.can_withdraw && (
              <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setModal({ type: 'withdraw' })}>
                <Ban size={14} /> Withdraw memo
              </button>
            )}
            {memo.can_delete && (
              <button type="button" className="lr-btn lr-btn-danger" onClick={() => setModal({ type: 'delete' })}>
                <Trash2 size={14} /> Delete memo
              </button>
            )}

          </div>
          )}
        </aside>
      </div>

      {/* The modal's title is the VERB too, not the role noun: "Reviewer —
          confirm" told the reader who they are, which they knew, rather than
          what pressing the button is about to do. */}
      {modal?.type === 'proceed' && (
        <DecisionModal
          title={roleGuidance(myStep.role_type).action}
          intro={myStep.sequence === (memo.workflow_steps?.length || 0)
            ? 'You are the final step. This approves the memo and archives it.'
            : `This records the memo as ${roleGuidance(myStep.role_type).done} and passes it to the next step.`}
          requireRemarks={remarksRequired}
          confirmLabel={roleGuidance(myStep.role_type).action}
          busy={act.isPending}
          onClose={() => setModal(null)}
          onSubmit={(remarks) => act.mutate({ decision: 'proceed', remarks })}
        />
      )}
      {modal?.type === 'reject' && (
        <DecisionModal
          title="Reject memo"
          intro="The memo returns to its author with your comments, and the remaining steps are stood down."
          requireRemarks
          confirmLabel="Reject memo"
          danger
          busy={act.isPending}
          onClose={() => setModal(null)}
          onSubmit={(remarks) => act.mutate({ decision: 'reject', remarks })}
        />
      )}
      {modal?.type === 'withdraw' && (
        <ConfirmModal
          title="Withdraw this memo?"
          message="The memo is marked Cancelled and any outstanding approval steps are stood down. It cannot be re-sent."
          requireReason danger confirmLabel="Withdraw memo" busy={withdraw.isPending}
          onClose={() => setModal(null)} onConfirm={(reason) => withdraw.mutate(reason)} />
      )}
      {modal?.type === 'delete' && (
        <ConfirmModal title="Delete this memo?"
          message="The draft is removed permanently. The deletion is recorded in the audit log."
          danger confirmLabel="Delete" busy={remove.isPending}
          onClose={() => setModal(null)} onConfirm={() => remove.mutate()} />
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default MemoDetail;
