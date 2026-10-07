import React, { useState } from 'react';
import { X } from 'lucide-react';

/**
 * Confirmation modal for destructive / sensitive admin actions.
 *
 * @param {{
 *   title: string, message?: string, impact?: string,
 *   confirmWord?: string,        // if set, user must type it exactly
 *   requireReason?: boolean,     // if true, a non-empty reason is required
 *   confirmLabel?: string, danger?: boolean,
 *   onConfirm: (reason: string) => void, onClose: () => void, busy?: boolean,
 * }} props
 */
const ConfirmModal = ({
  title, message, impact, confirmWord, requireReason = false,
  confirmLabel = 'Confirm', danger = false, onConfirm, onClose, busy = false,
}) => {
  const [typed, setTyped] = useState('');
  const [reason, setReason] = useState('');

  const wordOk = !confirmWord || typed === confirmWord;
  const reasonOk = !requireReason || reason.trim().length >= 5;
  const canConfirm = wordOk && reasonOk && !busy;

  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head">
          <h3>{title}</h3>
          <button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>

        {message && <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--fs-body)', margin: '0 0 12px' }}>{message}</p>}
        {impact && (
          <div className="lr-impact" role="note">{impact}</div>
        )}

        {requireReason && (
          <label className="lr-field">
            <span>Reason <span aria-hidden="true">*</span></span>
            <textarea
              rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="Required (min 5 characters)" aria-label="Reason"
            />
          </label>
        )}

        {confirmWord && (
          <label className="lr-field">
            <span>Type <b>{confirmWord}</b> to confirm</span>
            <input
              type="text" value={typed} onChange={(e) => setTyped(e.target.value)}
              aria-label={`Type ${confirmWord} to confirm`} autoComplete="off"
            />
          </label>
        )}

        <div className="lr-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className={`lr-btn ${danger ? 'lr-btn-danger' : 'lr-btn-primary'}`}
            disabled={!canConfirm}
            onClick={() => onConfirm(reason)}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ConfirmModal;
