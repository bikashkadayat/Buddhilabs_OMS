import { describeApiError } from '../../services/apiErrors';
import React, { useEffect, useState, useCallback } from 'react';
import { X, Calendar, User, FileText, CheckCircle, XCircle, Paperclip } from 'lucide-react';
import { leaveService } from '../../services/leaveService';
import Badge from '../common/Badge';
import ApprovalTimeline from '../common/ApprovalTimeline';

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '');
const fmt = (ad, bs) => {
  if (!ad) return '—';
  const adStr = new Date(ad).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  return bs ? `${bs} BS · ${adStr}` : adStr;
};

// Detailed review slide-over for a single leave request. The approver MUST see
// this before deciding; Approve/Reject live inside, and Reject needs a remark.
const LeaveReviewModal = ({ leaveId, canApprove, onClose, onDecided }) => {
  const [leave, setLeave] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [remarks, setRemarks] = useState('');
  const [remarkError, setRemarkError] = useState('');
  const [submitting, setSubmitting] = useState(null); // 'approved' | 'rejected'

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setLeave(await leaveService.getById(leaveId));
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load request details.');
    } finally {
      setLoading(false);
    }
  }, [leaveId]);

  useEffect(() => { load(); }, [load]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !submitting) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, submitting]);

  const decide = async (status) => {
    if (status === 'rejected' && !remarks.trim()) {
      setRemarkError('A remark is required when rejecting.');
      return;
    }
    setRemarkError(''); setSubmitting(status);
    try {
      // Route to the correct workflow stage based on the leave's current status
      // (pending => Dept Head L1, pending_hr => HR L2). Enforces two-stage flow.
      await leaveService.reviewLeave(leaveId, leave.status, status, remarks.trim());
      onDecided?.(status);
    } catch (e) {
      setError(e?.response?.data?.error || describeApiError(e, 'That didn’t go through. Please try again.'));
      setSubmitting(null);
    }
  };

  const bal = leave?.remaining_balance;

  return (
    <div className="ar-overlay" onClick={() => !submitting && onClose()}>
      <aside className="ar-panel" role="dialog" aria-modal="true" aria-label="Leave request review" onClick={(e) => e.stopPropagation()}>
        <header className="ar-panel-head">
          <div>
            <div className="ar-eyebrow">Leave Request Review</div>
            <h2 className="ar-title">{leave ? `${cap(leave.leave_type)} Leave` : 'Loading…'}</h2>
          </div>
          <button type="button" className="ar-close" aria-label="Close" onClick={onClose} disabled={!!submitting}><X size={20} /></button>
        </header>

        {loading ? (
          <div className="ar-state">Loading request details…</div>
        ) : error && !leave ? (
          <div className="ar-state ar-state-error">{error}<button className="btn btn-ghost" onClick={load}>Retry</button></div>
        ) : (
          <div className="ar-panel-body">
            {error && <div className="ar-inline-error">{error}</div>}

            {/* Requester */}
            <section className="ar-section">
              <div className="ar-sec-title"><User size={15} /> Requester</div>
              <div className="ar-kv-grid">
                <div><span>Employee</span><strong>{leave.user_name}</strong></div>
                <div><span>Employee ID</span><strong>{leave.employee_id || '—'}</strong></div>
                <div><span>Department</span><strong>{leave.department_name || '—'}</strong></div>
                <div><span>Status</span><strong><Badge status={leave.status} /></strong></div>
              </div>
            </section>

            {/* Request */}
            <section className="ar-section">
              <div className="ar-sec-title"><Calendar size={15} /> Leave Details</div>
              <div className="ar-kv-grid">
                <div><span>Leave Type</span><strong>{cap(leave.leave_type)}</strong></div>
                <div><span>Total Days</span><strong>{leave.total_days ?? '—'}</strong></div>
                <div><span>Start Date</span><strong>{fmt(leave.start_date, leave.start_date_bs)}</strong></div>
                <div><span>End Date</span><strong>{fmt(leave.end_date, leave.end_date_bs)}</strong></div>
                <div className="ar-span2"><span>Remaining Balance</span><strong>{bal ? `${bal.remaining} of ${bal.total_allocated} day(s) (${bal.year})` : 'Not tracked for this type'}</strong></div>
              </div>
            </section>

            {/* Purpose — the "what it is for" */}
            <section className="ar-section">
              <div className="ar-sec-title"><FileText size={15} /> Purpose / Reason</div>
              <p className="ar-reason">{leave.reason || 'No reason provided.'}</p>
              {leave.handover_notes ? (
                <>
                  <div className="ar-sub-label">Work Handover Notes</div>
                  <p className="ar-reason ar-muted-box">{leave.handover_notes}</p>
                </>
              ) : null}
            </section>

            {/* Documents */}
            <section className="ar-section">
              <div className="ar-sec-title"><Paperclip size={15} /> Supporting Documents</div>
              {leave.documents && leave.documents.length ? (
                <ul className="ar-docs">
                  {leave.documents.map((d, i) => (
                    <li key={i}><a href={d.url} target="_blank" rel="noreferrer">{d.name || `Document ${i + 1}`}</a></li>
                  ))}
                </ul>
              ) : <p className="ar-muted">No documents attached.</p>}
            </section>

            {/* Timeline */}
            <section className="ar-section">
              <div className="ar-sec-title"><CheckCircle size={15} /> Approval Timeline</div>
              <ApprovalTimeline steps={leave.timeline || []} />
            </section>

            {/* Decision */}
            {canApprove && (leave.status === 'pending' || leave.status === 'pending_hr') && (
              <section className="ar-decision">
                <label htmlFor="ar-remarks" className="ar-sub-label">Remarks {`(required to reject)`}</label>
                <textarea
                  id="ar-remarks" className={`ar-textarea ${remarkError ? 'ar-textarea-error' : ''}`}
                  rows={3} value={remarks} placeholder="Add a remark for this decision…"
                  onChange={(e) => { setRemarks(e.target.value); if (remarkError) setRemarkError(''); }}
                  disabled={!!submitting}
                />
                {remarkError && <div className="ar-remark-error">{remarkError}</div>}
                <div className="ar-actions">
                  <button type="button" className="btn btn-ghost ar-reject" disabled={!!submitting} onClick={() => decide('rejected')}>
                    <XCircle size={16} /> {submitting === 'rejected' ? 'Rejecting…' : 'Reject'}
                  </button>
                  <button type="button" className="btn btn-success" disabled={!!submitting} onClick={() => decide('approved')}>
                    <CheckCircle size={16} /> {submitting === 'approved' ? 'Approving…' : 'Approve'}
                  </button>
                </div>
              </section>
            )}
          </div>
        )}
      </aside>
    </div>
  );
};

export default LeaveReviewModal;
