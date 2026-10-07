import NavIcon from '../layout/NavIcon';
import React, { useCallback, useEffect, useState } from 'react';
import CommandPalette from './CommandPalette';
import { useLocation } from 'react-router-dom';

/**
 * The search entry point (Phase 204).
 *
 * Owns the shortcut and the open/closed state, and renders the palette. Lives
 * in the header so every screen has it, which is what makes a trimmed sidebar
 * safe: anything a menu no longer lists is two keystrokes away.
 *
 * The binding is capture-phase and refuses to fire while the user is typing in
 * a field or a rich-text editor, so Ctrl/Cmd-K inside the memo editor still
 * belongs to the editor. It also honours the platform: Cmd on macOS, Ctrl
 * elsewhere, and never both.
 */
const isTypingTarget = (el) => {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (el.isContentEditable) return true;
  // `isContentEditable` is a live DOM property that jsdom does not implement,
  // so the attribute is checked too. `closest` rather than a direct read
  // because a keystroke inside a TipTap editor lands on a nested node, not on
  // the editable root.
  return typeof el.closest === 'function' && !!el.closest('[contenteditable="true"]');
};

const GlobalSearch = () => {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // The header palette owns its own open state, so it needs the same rule as
  // Layout's: a navigation dismisses it. Without this the magnifier — still on
  // the bar below 900px, just icon-only — opened a palette that survived every
  // route change, and fixing Layout alone would have left that path broken
  // while looking fixed. Reset during render, as Layout does, so the new route
  // never paints with the palette still open over it.
  const [seenKey, setSeenKey] = useState(location.key);
  if (seenKey !== location.key) {
    setSeenKey(location.key);
    setOpen(false);
  }
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const onKey = (e) => {
      const combo = (e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K');
      if (!combo) return;
      if (!open && isTypingTarget(e.target)) return;
      e.preventDefault();
      setOpen((v) => !v);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open]);

  return (
    <>
      <button
        type="button"
        data-tour="search"
        className="hd-search"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <NavIcon name="search" size={15} />
        <span className="hd-search-label">Search…</span>
        <kbd className="hd-search-kbd">Ctrl K</kbd>
      </button>
      <CommandPalette open={open} onClose={close} />
    </>
  );
};

export default GlobalSearch;
