import NavIcon from '../layout/NavIcon';
import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useGlobalSearch } from '../../hooks/useGlobalSearch';
import { useWorkQueue } from '../../hooks/useWorkQueue';
import SearchResults from './SearchResults';

/**
 * The command palette (Phase 204 / blueprint §07).
 *
 * ACCESSIBILITY
 * A combobox, not a box with a list under it. The input keeps focus at all
 * times and `aria-activedescendant` moves the screen reader's attention through
 * the options, which is what lets Up/Down work without the caret leaving the
 * field. The listbox is labelled, each option has a stable id, and the result
 * count is announced politely so a non-sighted user learns that typing changed
 * something.
 *
 * Focus is captured on open and restored to whatever opened it on close -
 * without that, dismissing the palette drops the user at the top of the
 * document and undoes the point of a keyboard shortcut.
 */
const CommandPalette = ({ open, onClose, initialQuery = '' }) => {
  const navigate = useNavigate();
  // `initialQuery` exists for the visual-QA harness, which needs the palette to
  // render populated. Empty in the app, where the palette always opens blank.
  // Mounting closed starts blank, exactly as the old clear-on-close effect did
  // when it ran on mount.
  const [raw, setRaw] = useState(open ? initialQuery : '');
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(null);

  const inputRef = useRef(null);
  const listRef = useRef(null);
  const restoreRef = useRef(null);
  const baseId = useId();

  const { groups, flat, isSearching, tooShort, failedGroups, reset } = useGlobalSearch(raw, { enabled: open });
  const { act } = useWorkQueue({ enabled: false });

  const optionId = useCallback((i) => `${baseId}-opt-${i}`, [baseId]);

  // Remember the opener, focus the field, restore on close.
  useEffect(() => {
    if (!open) return undefined;
    restoreRef.current = document.activeElement;
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      clearTimeout(t);
      const el = restoreRef.current;
      if (el && typeof el.focus === 'function' && document.contains(el)) el.focus();
    };
  }, [open]);

  // Clear on close so re-opening starts fresh rather than showing the last
  // person's search on a shared machine. The palette's own state is reset
  // during render, on the transition to closed; reset() stays in an effect
  // because it is a genuine side effect - it aborts the in-flight request and
  // evicts the cached results.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (!open) {
      setRaw('');
      setActive(0);
      setBusy(null);
    }
  }
  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  // A changing result set must not leave the highlight past the end.
  const [seenCount, setSeenCount] = useState(flat.length);
  if (seenCount !== flat.length) {
    setSeenCount(flat.length);
    setActive(0);
  }

  // Body scroll lock, matching what Layout.jsx already does for the drawer.
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  const run = useCallback(async (result, action) => {
    if (!action) return;
    if (action.queueAction) {
      setBusy(result.id);
      // Remarks are not collectable in a one-line palette, so a rejection is
      // never offered here - it opens the record instead. See the escalation
      // rule in the Work Queue.
      await act(action.item, action.queueAction, '');
      setBusy(null);
      onClose();
      return;
    }
    if (action.to) {
      onClose();
      navigate(action.to);
    }
  }, [act, navigate, onClose]);

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
    if (!flat.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => (i + 1) % flat.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (i - 1 + flat.length) % flat.length);
    } else if (e.key === 'Home') {
      e.preventDefault(); setActive(0);
    } else if (e.key === 'End') {
      e.preventDefault(); setActive(flat.length - 1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const result = flat[active];
      if (result) run(result, result.actions?.[0]);
    }
  };

  // Keep the highlighted option in view without moving focus off the input.
  // Guarded: scrollIntoView is absent in jsdom and in some embedded webviews,
  // and an unguarded call inside an effect takes the whole palette down.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector(`#${CSS.escape(optionId(active))}`);
    if (typeof el?.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  }, [active, open, optionId]);

  const status = useMemo(() => {
    if (tooShort) return 'Type at least two characters';
    if (isSearching) return 'Searching…';
    if (!raw.trim()) return 'Type to search, or pick a quick action';
    return `${flat.length} result${flat.length === 1 ? '' : 's'}`;
  }, [tooShort, isSearching, raw, flat.length]);

  if (!open) return null;

  return (
    <div className="cp-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cp" role="dialog" aria-modal="true" aria-label="Search the workspace">
        <div className="cp-field">
          <span className="cp-icon" aria-hidden="true">
            {/* The same Search glyph the header button and the mobile tab bar
                use — all three drew their own magnifier before. */}
            <NavIcon name="search" size={17} />
          </span>
          <input
            ref={inputRef}
            className="cp-input"
            type="text"
            role="combobox"
            aria-expanded={flat.length > 0}
            aria-controls={`${baseId}-listbox`}
            aria-activedescendant={flat.length ? optionId(active) : undefined}
            aria-autocomplete="list"
            aria-label="Search people, tasks, leave, documents, pages and help"
            placeholder="Search people, tasks, leave, documents, pages, help…"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            onKeyDown={onKeyDown}
            autoComplete="off"
            spellCheck="false"
          />
          <kbd className="cp-esc">Esc</kbd>
        </div>

        <div className="cp-body" ref={listRef}>
          <SearchResults
            groups={groups}
            flat={flat}
            active={active}
            busyId={busy}
            listboxId={`${baseId}-listbox`}
            optionId={optionId}
            onHover={setActive}
            onRun={run}
            emptyLabel={raw.trim() && !tooShort && !isSearching ? `No matches for “${raw.trim()}”` : null}
          />
        </div>

        {failedGroups.length > 0 && (
          <p className="cp-degraded" role="status">
            {failedGroups.join(', ')} could not be searched. Everything else is shown.
          </p>
        )}

        <div className="cp-foot">
          <span className="cp-hints">
            <kbd>↑</kbd><kbd>↓</kbd> move · <kbd>↵</kbd> open · <kbd>Esc</kbd> close
          </span>
          <span className="cp-status" role="status" aria-live="polite">{status}</span>
        </div>
      </div>
    </div>
  );
};

export default CommandPalette;
