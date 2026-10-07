import { useCallback, useContext, useState } from 'react';
import { QueryClientContext } from '@tanstack/react-query';

import { WORK_QUEUE_KEY } from '../../hooks/useWorkQueue';
import { remarkState } from './remarkRules';

/**
 * The decision behaviour of one queue item (Phase F).
 *
 * Extracted so the desktop row and the mobile card share it rather than each
 * carrying their own copy. Two renderings of the same decision that drift apart
 * is exactly how one of them ends up firing a rejection without a remark, which
 * is the failure the palette already had to be fixed for.
 *
 * THE RULES IT ENFORCES
 *
 *  - An action declaring `needsRemark` cannot run until its remark reaches
 *    `minRemark`, counted as the server counts (remarkRules.js).
 *
 *  - An action declaring `precheck` confirms, the moment its dialog opens, that
 *    it can still be done - that the memo is still waiting on this person and
 *    has not been withdrawn or moved on since the queue loaded (Phase
 *    MEMO-QUEUE-UX-HARDENING). If not, the dialog says so BEFORE anything is
 *    typed, and cannot be submitted. The server is otherwise the first to know
 *    and the person finds out after writing their remark.
 *
 *  - When the action still fails, the dialog stays open with the server's
 *    message and the typed remark intact.
 */
export const useRowAction = (item, onAction) => {
  const [remarkFor, setRemarkFor] = useState(null);
  const [remark, setRemark] = useState('');
  const [remarkError, setRemarkError] = useState('');
  const [touched, setTouched] = useState(false);
  // 'idle' | 'checking' | 'ready' | 'blocked'
  const [precheck, setPrecheck] = useState('idle');
  const [blockedReason, setBlockedReason] = useState('');
  const [busy, setBusy] = useState(false);
  // Read from context rather than useQueryClient(), which throws outside a
  // provider - these rows are also rendered in isolation (tests, the QA harness).
  const queryClient = useContext(QueryClientContext);

  const reset = useCallback(() => {
    // Closing a dialog that found the item already gone refreshes the queue, so
    // the dead row does not sit there offering the same refused action again.
    if (precheck === 'blocked') queryClient?.invalidateQueries({ queryKey: WORK_QUEUE_KEY });
    setRemarkFor(null);
    setRemark('');
    setRemarkError('');
    setTouched(false);
    setPrecheck('idle');
    setBlockedReason('');
  }, [precheck, queryClient]);

  const run = useCallback(async (action, text = '') => {
    setBusy(true);
    setRemarkError('');
    const result = await onAction?.(item, action, text);
    setBusy(false);
    if (result && result.ok === false && action?.needsRemark) {
      setRemarkError(result.error || 'Could not complete that action.');
      return;
    }
    reset();
  }, [item, onAction, reset]);

  /** Click handler: opens the remark dialog (confirming first), or acts immediately. */
  const start = useCallback(async (action) => {
    if (!action?.needsRemark) { run(action); return; }
    setRemarkError('');
    setTouched(false);
    setRemarkFor(action);
    if (!action.precheck) { setPrecheck('ready'); return; }
    setPrecheck('checking');
    setBlockedReason('');
    try {
      const verdict = await action.precheck();
      if (verdict?.ok === false) {
        setBlockedReason(verdict.reason || 'This item can no longer be actioned.');
        setPrecheck('blocked');
      } else {
        setPrecheck('ready');
      }
    } catch {
      // A failed check is not a refusal: the person may act, and the server
      // still decides. Blocking them because a read failed would be worse.
      setPrecheck('ready');
    }
  }, [run]);

  const rules = remarkState(remark, remarkFor?.minRemark);

  const submitRemark = useCallback((e) => {
    e?.preventDefault?.();
    setTouched(true);
    if (!rules.valid || busy || precheck !== 'ready') return;
    run(remarkFor, remark.trim());
  }, [rules.valid, busy, precheck, remark, remarkFor, run]);

  return {
    remarkFor, remark, setRemark, remarkError, busy,
    start, run, submitRemark, cancelRemark: reset,
    touched, markTouched: () => setTouched(true),
    precheck, blockedReason,
    rules,
    minRemark: rules.min,
    canSubmit: rules.valid && !busy && precheck === 'ready',
  };
};

export default useRowAction;
