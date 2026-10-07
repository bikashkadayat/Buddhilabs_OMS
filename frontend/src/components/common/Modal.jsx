import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

/**
 * The single modal wrapper. Use this instead of hand-rolling `.lr-modal-overlay`
 * markup, so every modal gets the same behaviour.
 *
 * It renders through a PORTAL to document.body. That is not cosmetic: an ancestor
 * that creates a stacking context (a transform, a filter, or an opacity animation
 * like `.page`'s fade-in) traps a fixed-position child no matter how high its
 * z-index is. Portalling to body makes a modal immune to that class of bug for
 * good — it is what stops the "title clipped under the header" regression from
 * coming back the next time someone adds a transform to a wrapper.
 *
 * Styling reuses the existing .lr-modal-* classes, so it looks identical to the
 * modals already shipped.
 *
 * Props:
 *   title    — heading text (rendered in the sticky header)
 *   onClose  — called on backdrop click, the close button, and Escape
 *   footer   — optional node pinned to the bottom (Save/Cancel)
 *   width    — optional max-width override (default: the .lr-modal 420px)
 *   labelledBy / ariaLabel — accessible name; defaults to `title`
 */
const Modal = ({ title, onClose, footer, width, children, ariaLabel }) => {
  const panelRef = useRef(null);

  // Escape to close.
  useEffect(() => {
    if (!onClose) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Lock body scroll while open; restore exactly what was there before (never
  // hard-code 'auto' — that would clobber another lock, e.g. the mobile drawer).
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, []);

  // Move focus into the dialog so keyboard users land inside it.
  useEffect(() => {
    const first = panelRef.current?.querySelector(
      'input:not([disabled]), select, textarea, button');
    first?.focus?.();
  }, []);

  return createPortal(
    <div
      className="lr-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel || title}
      onClick={onClose}
    >
      <div
        className="lr-modal"
        ref={panelRef}
        style={width ? { maxWidth: width } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lr-modal-head">
          <h3>{title}</h3>
          {onClose && (
            <button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button>
          )}
        </div>
        {children}
        {footer && <div className="lr-modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
};

export default Modal;
