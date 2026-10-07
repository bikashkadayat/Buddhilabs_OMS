import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileWarning, Paperclip, UserPlus, UserX, Users, X } from 'lucide-react';

import { memoService } from '../../services/memoService';
import EmployeeSelector from './EmployeeSelector';
import { UNAVAILABILITY_REASONS } from './memoLabels';

/**
 * The manual's special cases, on the memo they belong to.
 *
 *   * "Mark as Unavailable and Forward memo" (pp. 8-9) — the five reasons, verbatim
 *   * "ADD NEW APPROVER" → "Submit to the new Approver" (p.14)
 *   * "Add noted member" (pp. 10-11) — open to everyone in the chain
 *   * "Add file" with its File Name box (p.6)
 *   * "Add view access" on an archived memo (p.17)
 *
 * Every control is gated on a server capability flag, so the panel can never offer
 * an action the API would refuse. The backend implemented all of this; none of it
 * had a way in from the screen until now.
 */
const Modal = ({ title, children, busy, confirmLabel, onClose, onConfirm, ready }) => (
  <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label={title}
    onClick={onClose}>
    <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
      <div className="lr-modal-head">
        <h3>{title}</h3>
        <button type="button" className="lr-modal-close" aria-label="Close"
          onClick={onClose}><X size={18} aria-hidden="true" /></button>
      </div>
      {children}
      <div className="memo-modal-actions">
        <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>
          Close
        </button>
        <button type="button" className="lr-btn lr-btn-primary"
          disabled={busy || !ready} onClick={onConfirm}>
          {busy ? 'Working…' : confirmLabel}
        </button>
      </div>
    </div>
  </div>
);

const MemoSpecialActions = ({ memo, onDone, onError }) => {
  const qc = useQueryClient();
  const [modal, setModal] = useState(null);
  const [reason, setReason] = useState(UNAVAILABILITY_REASONS[0].code);
  const [remarks, setRemarks] = useState('');
  const [person, setPerson] = useState(null);
  const [fileName, setFileName] = useState('');
  const [files, setFiles] = useState([]);
  const [departmentId, setDepartmentId] = useState('');

  const { data: departments = [] } = useQuery({
    queryKey: ['memo-departments'], queryFn: memoService.listDepartments,
    staleTime: 10 * 60 * 1000, enabled: modal === 'access',
  });

  const close = () => {
    setModal(null); setRemarks(''); setPerson(null);
    setFileName(''); setFiles([]); setDepartmentId('');
  };

  const done = (message) => {
    close();
    qc.invalidateQueries({ queryKey: ['memo', String(memo.id)] });
    onDone?.(message);
  };

  const fail = (e) => {
    const payload = e?.response?.data || {};
    const first = payload.detail
      || (typeof payload === 'object' ? Object.values(payload).flat()[0] : null);
    onError?.(String(first || 'That didn’t go through. Please try again.'));
  };

  const unavailable = useMutation({
    mutationFn: () => memoService.markUnavailable(memo.id,
      { reason, reason_note: remarks }),
    onSuccess: () => done('Marked unavailable and forwarded.'),
    onError: fail,
  });

  const replacement = useMutation({
    mutationFn: () => memoService.addReplacement(memo.id,
      { user_id: String(person.id), reason: remarks }),
    onSuccess: () => done('The memo has been assigned to the new approver.'),
    onError: fail,
  });

  const note = useMutation({
    mutationFn: () => memoService.askToNote(memo.id,
      { user_ids: [String(person.id)], remarks }),
    onSuccess: () => done('Added for the Noted activity.'),
    onError: fail,
  });

  const upload = useMutation({
    mutationFn: () => memoService.uploadAttachments(memo.id, files, { name: fileName }),
    onSuccess: () => done('File attached.'),
    onError: fail,
  });

  const grant = useMutation({
    mutationFn: () => memoService.grantArchiveAccess(memo.id, {
      target: 'department', department_id: departmentId, reason: remarks,
      include_children: true,
    }),
    onSuccess: () => done('View access granted.'),
    onError: fail,
  });

  const busy = unavailable.isPending || replacement.isPending || note.isPending
    || upload.isPending || grant.isPending;

  // Nothing on offer means no card. An "Actions" heading over an empty box tells
  // the reader there is something here and then does not deliver it.
  const offers = memo.can_edit || memo.can_request_notes
    || memo.can_manage_assignments || memo.can_share_archive;
  if (!offers) return null;

  return (
    <>
      <div className="memo-panel memo-actions-panel">
        <h3 className="memo-panel-title">Memo actions</h3>

        {memo.can_edit && (
          <button type="button" className="lr-btn" onClick={() => setModal('file')}>
            <Paperclip size={14} /> Add file
          </button>
        )}

        {/* Anyone in the approving chain may add a Noted member (p.8). */}
        {memo.can_request_notes && (
          <button type="button" className="lr-btn" onClick={() => setModal('note')}>
            <Users size={14} /> Add noted member
          </button>
        )}

        {memo.can_manage_assignments && (
          <button type="button" className="lr-btn" onClick={() => setModal('unavailable')}>
            <UserX size={14} /> Mark as Unavailable and Forward memo
          </button>
        )}

        {/* The same permission covers naming the stand-in; the server decides
            whether there is an open absence to fill and refuses if not. */}
        {memo.can_manage_assignments && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => setModal('replacement')}>
            <UserPlus size={14} /> Add new approver
          </button>
        )}

        {memo.can_share_archive && (
          <button type="button" className="lr-btn" onClick={() => setModal('access')}>
            <FileWarning size={14} /> Add view access
          </button>
        )}
      </div>

      {modal === 'unavailable' && (
        <Modal title="Mark as Unavailable" busy={busy} ready confirmLabel="Submit"
          onClose={close} onConfirm={() => unavailable.mutate()}>
          <label className="lr-field">
            <span>Select Unavailable type</span>
            <select value={reason} aria-label="Select Unavailable type"
              onChange={(e) => setReason(e.target.value)}>
              {UNAVAILABILITY_REASONS.map((row) => (
                <option key={row.code} value={row.code}>{row.label}</option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span>Remarks</span>
            <textarea rows={3} value={remarks} aria-label="Remarks"
              placeholder="Write remarks …"
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
        </Modal>
      )}

      {modal === 'replacement' && (
        <Modal title="Add new approver" busy={busy} ready={Boolean(person) && remarks.trim()}
          confirmLabel="Submit to the new Approver"
          onClose={close} onConfirm={() => replacement.mutate()}>
          <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>
            The memo is with you because the approver was marked unavailable. Name
            their replacement and the memo goes straight to them.
          </p>
          {person ? (
            <div className="min-person">
              <span className="min-person-main">
                <span className="min-person-name">{person.full_name}</span>
                {person.designation && (
                  <span className="min-person-sub">{person.designation}</span>
                )}
              </span>
              <button type="button" className="memo-icon-btn is-danger"
                aria-label={`Remove ${person.full_name}`}
                onClick={() => setPerson(null)}><X size={18} aria-hidden="true" /></button>
            </div>
          ) : (
            <EmployeeSelector onSelect={setPerson}
              placeholder="Select the new approver…" />
          )}
          <label className="lr-field">
            <span>Reason</span>
            <textarea rows={3} value={remarks} aria-label="Reason"
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
        </Modal>
      )}

      {modal === 'note' && (
        <Modal title="Employee for Noted activity" busy={busy} ready={Boolean(person)}
          confirmLabel="Add" onClose={close} onConfirm={() => note.mutate()}>
          {person ? (
            <div className="min-person">
              <span className="min-person-main">
                <span className="min-person-name">{person.full_name}</span>
              </span>
              <button type="button" className="memo-icon-btn is-danger"
                aria-label={`Remove ${person.full_name}`}
                onClick={() => setPerson(null)}><X size={18} aria-hidden="true" /></button>
            </div>
          ) : (
            <EmployeeSelector onSelect={setPerson} placeholder="Select User…" />
          )}
        </Modal>
      )}

      {modal === 'file' && (
        <Modal title="Add Supportive files" busy={busy} ready={files.length > 0}
          confirmLabel="Add" onClose={close} onConfirm={() => upload.mutate()}>
          <label className="lr-field">
            <span>File Name:</span>
            <input value={fileName} aria-label="File Name"
              onChange={(e) => setFileName(e.target.value)} />
          </label>
          <label className="lr-field">
            <span>Choose file: Accept only PDF, doc, docx, xls, xlsx, csv.</span>
            <input type="file" multiple aria-label="Choose file"
              accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.ppt,.pptx,.png,.jpg,.jpeg,.webp,.zip"
              onChange={(e) => setFiles(Array.from(e.target.files || []))} />
          </label>
          <p className="lr-page-sub">
            A single file larger than 10 MB will not be attached.
          </p>
        </Modal>
      )}

      {modal === 'access' && (
        <Modal title="Add view access" busy={busy}
          ready={Boolean(departmentId) && remarks.trim()} confirmLabel="submit"
          onClose={close} onConfirm={() => grant.mutate()}>
          <label className="lr-field">
            <span>Department</span>
            <select value={departmentId} aria-label="Department"
              onChange={(e) => setDepartmentId(e.target.value)}>
              <option value="">— select —</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span>Reason</span>
            <textarea rows={3} value={remarks} aria-label="Reason"
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
          <p className="lr-page-sub">
            The memo will also appear in the Archived Memo section for employees of
            the selected department.
          </p>
        </Modal>
      )}
    </>
  );
};

export default MemoSpecialActions;
