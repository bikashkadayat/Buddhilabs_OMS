import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';

/**
 * The Create bottom sheet (Phase F).
 *
 * An OVERLAY, never a page. The brief is explicit and it is the right call: a
 * Create tab that navigated would destroy whatever the person was looking at,
 * and "I was reading a memo and now I am not" is how people learn to distrust a
 * bottom bar.
 *
 * Gated with the same can() the rail uses - an Admin has no self-service, so
 * they are offered no creators they cannot use.
 */
const OPTIONS = [
  // First: the most common thing anybody creates. Home and search both
  // offered it; the phone's Create button did not.
  { key: 'task', label: 'Create task', to: '/tasks/create', gate: 'createTask', tag: 'TASK', tone: 'task' },
  { key: 'memo', label: 'Create memo', to: '/memos/create', gate: 'createMemo', tag: 'MEMO', tone: 'memo' },
  { key: 'minute', label: 'Create minute', to: '/minutes/create', tag: 'MINUTE', tone: 'minute' },
  { key: 'circular', label: 'Create circular', to: '/circulars/create', tag: 'CIRCULAR', tone: 'circular' },
  { key: 'leave', label: 'Apply for leave', to: '/leave/apply', gate: 'applyLeave', tag: 'LEAVE', tone: 'leave' },
  { key: 'asset', label: 'Request an asset', to: '/inventory/requests', tag: 'ASSET', tone: 'asset' },
];

const CreateSheet = ({ open, onClose }) => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const sheetRef = useRef(null);
  const restoreRef = useRef(null);

  // Focus in, focus back out. Without the restore, dismissing the sheet drops
  // the user at the top of the document.
  useEffect(() => {
    if (!open) return undefined;
    restoreRef.current = document.activeElement;
    const t = setTimeout(() => {
      sheetRef.current?.querySelector('button, a')?.focus();
    }, 0);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(t);
      document.body.style.overflow = prev;
      document.removeEventListener('keydown', onKey);
      const el = restoreRef.current;
      if (el?.focus && document.contains(el)) el.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  const options = OPTIONS.filter((o) => !o.gate || can(role, o.gate));

  const go = (to) => { onClose(); navigate(to); };

  return (
    <div
      className="cs-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="cs-sheet" role="dialog" aria-modal="true" aria-label="Create" ref={sheetRef}>
        <span className="cs-grip" aria-hidden="true" />
        <p className="cs-title">Create</p>
        <ul className="cs-list">
          {options.map((o) => (
            <li key={o.key}>
              <button type="button" className="cs-opt" onClick={() => go(o.to)}>
                <span className={`wq-tag wq-tag-${o.tone}`}>{o.tag}</span>
                <span className="cs-opt-label">{o.label}</span>
              </button>
            </li>
          ))}
        </ul>
        <p className="cs-note">Drafts autosave from the first keystroke.</p>
        <button type="button" className="cs-cancel" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
};

export default CreateSheet;
