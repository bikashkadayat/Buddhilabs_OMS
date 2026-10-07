import React, { useEffect } from 'react';

/**
 * Lightweight success/error toast with an optional Undo action.
 * @param {{message:string, tone?:'success'|'error', onUndo?:Function, onClose:Function}} props
 */
const Toast = ({ message, tone = 'success', onUndo, onClose }) => {
  useEffect(() => {
    const t = setTimeout(onClose, onUndo ? 6000 : 3500);
    return () => clearTimeout(t);
  }, [onClose, onUndo]);

  return (
    <div className={`lr-toast lr-toast-${tone}`} role="status" aria-live="polite">
      <span>{message}</span>
      {onUndo && (
        <button type="button" className="lr-toast-undo" onClick={() => { onUndo(); onClose(); }}>
          Undo
        </button>
      )}
    </div>
  );
};

export default Toast;
