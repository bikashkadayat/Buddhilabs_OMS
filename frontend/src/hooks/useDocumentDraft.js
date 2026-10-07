import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { draftService } from '../services/draftService';
import * as local from '../services/draftStore';

/**
 * The autosave engine (Phase 111.2) — one implementation, used by Memo, Minute
 * and Circular.
 *
 * It is a single hook rather than three integrations because the three forms
 * differ only in how they serialise themselves. Everything below that — when to
 * save, what to do offline, which copy wins on recovery — is the same problem
 * in all three, and solving it three times would mean fixing the next bug in it
 * three times.
 *
 * AUTOSAVE STATE IS NOT DOCUMENT STATE
 * The status this hook reports ('saving' | 'saved' | 'offline' | 'local-only')
 * describes how recently the text reached durable storage. It is deliberately
 * separate from the document's workflow status, which this hook never reads and
 * never writes. Memo keeps its eight states, Minute its four, Circular its nine.
 *
 * TWO TIERS, BECAUSE THEY FAIL DIFFERENTLY
 *   IndexedDB  survives a crash, a power cut and being offline; dies with the
 *              machine, and cannot be read from another device.
 *   Server     survives losing the machine, logging out and session expiry;
 *              needs a network and an unexpired token.
 * Neither covers the other, so every change goes to both and the recovery path
 * takes whichever is newer.
 */

export const STATUS = {
  IDLE: 'idle',
  SAVING: 'saving',
  SAVED: 'saved',
  OFFLINE: 'offline',
  LOCAL_ONLY: 'local-only',
};

/** Phase 111.3 cadence. */
export const IDLE_MS = 5000;      // save this long after the user stops typing
export const INTERVAL_MS = 30000; // ...and at least this often while they type

const deviceLabel = () => {
  if (typeof navigator === 'undefined') return '';
  const ua = navigator.userAgent || '';
  const browser = /Edg\//.test(ua) ? 'Edge'
    : /Chrome\//.test(ua) ? 'Chrome'
      : /Safari\//.test(ua) ? 'Safari'
        : /Firefox\//.test(ua) ? 'Firefox' : 'Browser';
  const os = /Mac/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows'
      : /Android/.test(ua) ? 'Android'
        : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} on ${os}` : browser;
};

const stableStringify = (value) => {
  // Key order must not decide whether something counts as a change, or an
  // unrelated re-render would trigger a save and the 5s timer would never idle.
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  return `{${Object.keys(value).sort()
    .map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(',')}}`;
};

/**
 * @param {Object}   opts
 * @param {string}   opts.kind          'memo' | 'minute' | 'circular' | 'task'
 * @param {string}   opts.documentKey   document id, or 'new' before first save
 * @param {Object}   opts.payload       the serialised form, rebuilt each render
 * @param {string|number} opts.userId   scopes the local copy to one person
 * @param {boolean}  opts.enabled       false while the form is still loading
 * @param {boolean}  opts.confidential  skip the local copy entirely (blocker B4)
 * @param {number}   opts.idleMs        how long after the last keystroke to save
 *                                      (default IDLE_MS; the task form uses a
 *                                      shorter pause because its fields are short)
 */
export const useDocumentDraft = ({
  kind, documentKey = 'new', payload, userId,
  enabled = true, confidential = false, idleMs = IDLE_MS,
}) => {
  const [status, setStatus] = useState(STATUS.IDLE);
  // Tracked as state, not derived from the lastPersisted ref: `unsaved` drives
  // the exit guard, so it has to be something the component re-renders on.
  const [unsaved, setUnsaved] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState(null);
  const [recoverable, setRecoverable] = useState(null);
  // Retained milestones, read at mount and refreshed after a milestone write so
  // the history panel does not go stale while the user works.
  const [versions, setVersions] = useState([]);
  const [checkedForRecovery, setCheckedForRecovery] = useState(false);
  const [online, setOnline] = useState(
    typeof navigator === 'undefined' ? true : navigator.onLine !== false,
  );

  const idleTimer = useRef(null);
  const intervalTimer = useRef(null);
  // The last payload actually persisted to the SERVER. Compared against on
  // every save so an unchanged form does not generate traffic every 30s, and
  // so a reconnect knows whether anything is genuinely pending.
  const lastPersisted = useRef(null);
  const pendingSync = useRef(false);
  const mounted = useRef(true);
  // The form as it looked the moment it became ready. Until the user changes
  // something, there is nothing worth saving — without this an untouched
  // Create form writes an empty snapshot on mount, and the next visit offers to
  // "recover" a blank document.
  const baseline = useRef(null);
  const everChanged = useRef(false);

  const serialised = useMemo(
    () => (payload === undefined ? null : stableStringify(payload)),
    [payload],
  );
  // Read through a ref inside timers, so a firing timer always sends the
  // CURRENT text rather than whatever was current when it was scheduled.
  // Assigned in an effect rather than during render: effects run immediately
  // after paint, which is many seconds ahead of the 5s and 30s timers.
  const latest = useRef({ payload, serialised });
  useEffect(() => { latest.current = { payload, serialised }; }, [payload, serialised]);

  // Set on EVERY mount, not just cleared on unmount. React StrictMode invokes
  // effects mount -> cleanup -> mount in development, so a cleanup-only version
  // leaves `mounted` false for the rest of the component's life and every
  // setStatus below is silently skipped — which showed up as an indicator that
  // never appeared even though the snapshot was reaching the server.
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // Reset the baseline whenever the engine points at a different document.
  useEffect(() => {
    if (!enabled) return;
    baseline.current = latest.current.serialised;
    everChanged.current = false;
  }, [enabled, kind, documentKey]);

  /* ------------------------------------------------------------------ *
   * The save itself
   * ------------------------------------------------------------------ */
  const persist = useCallback(async ({ milestone, force = false } = {}) => {
    if (!enabled) return;
    const { payload: body, serialised: now } = latest.current;
    if (!body || now === null) return;
    if (!force && now === lastPersisted.current) return;
    if (!force && !everChanged.current && now === baseline.current) return;

    // Local first, and unconditionally: it is the copy that survives the events
    // most likely to be happening right now (a crash, a shutdown, a lost link).
    // A confidential memo skips it — see the note in draftStore.
    const localOk = confidential
      ? false
      : await local.putSnapshot({ userId, kind, documentKey, payload: body });

    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      pendingSync.current = true;
      if (mounted.current) setStatus(localOk ? STATUS.OFFLINE : STATUS.LOCAL_ONLY);
      return;
    }

    if (mounted.current) setStatus(STATUS.SAVING);
    try {
      const result = await draftService.saveDraft(kind, documentKey, body, {
        milestone, deviceLabel: deviceLabel(),
      });
      lastPersisted.current = now;
      pendingSync.current = false;
      if (mounted.current) {
        setUnsaved(false);
        setStatus(STATUS.SAVED);
        setLastSavedAt(result?.saved_at ? new Date(result.saved_at) : new Date());
      }
    } catch (error) {
      // 413 is terminal: the snapshot is too large and retrying cannot help, so
      // the local copy is the honest story. Everything else is treated as a
      // connectivity problem and retried by the next trigger.
      pendingSync.current = error?.response?.status !== 413;
      if (mounted.current) {
        setStatus(localOk ? STATUS.OFFLINE : STATUS.LOCAL_ONLY);
      }
    }
  }, [enabled, kind, documentKey, userId, confidential]);

  /* ------------------------------------------------------------------ *
   * Phase 111.3 triggers
   * ------------------------------------------------------------------ */

  // 1. Five seconds after the user stops typing.
  useEffect(() => {
    if (!enabled || serialised === null) return undefined;
    if (serialised === lastPersisted.current) return undefined;
    // Nothing typed yet: an untouched form is not unsaved work.
    if (!everChanged.current && serialised === baseline.current) return undefined;
    everChanged.current = true;
    // Marking the document dirty IS a response to content changing, which is
    // what this effect exists to observe; there is no render-time expression
    // for it, because the comparison is against a ref the timers also write.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUnsaved(true);
    clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => persist(), idleMs);
    return () => clearTimeout(idleTimer.current);
  }, [serialised, enabled, persist, idleMs]);

  // 2. At least every thirty seconds, so a user who never pauses still saves.
  //    Retained as a milestone: it marks a coherent stopping point, which is
  //    what makes the version list worth reading (Decision 3).
  useEffect(() => {
    if (!enabled) return undefined;
    intervalTimer.current = setInterval(
      () => persist({ milestone: 'interval' }), INTERVAL_MS,
    );
    return () => clearInterval(intervalTimer.current);
  }, [enabled, persist]);

  // 3. When the tab is hidden — switching away, minimising, closing. This is
  //    the last reliable moment before a tab goes; `visibilitychange` fires on
  //    mobile teardown where `beforeunload` does not.
  useEffect(() => {
    if (!enabled || typeof document === 'undefined') return undefined;
    const onHide = () => {
      if (document.visibilityState === 'hidden') persist({ milestone: 'tab_hidden' });
    };
    document.addEventListener('visibilitychange', onHide);
    return () => document.removeEventListener('visibilitychange', onHide);
  }, [enabled, persist]);

  // 4. Offline/online. On reconnect anything typed while disconnected is
  //    flushed immediately rather than waiting for the next timer (Phase 111.9).
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const goOnline = () => {
      setOnline(true);
      if (pendingSync.current) persist({ force: true });
    };
    const goOffline = () => {
      setOnline(false);
      setStatus((s) => (s === STATUS.LOCAL_ONLY ? s : STATUS.OFFLINE));
    };
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
    };
  }, [persist]);

  /* ------------------------------------------------------------------ *
   * Recovery (Phase 111.5)
   * ------------------------------------------------------------------ */
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;

    (async () => {
      const [localRow, serverRow] = await Promise.all([
        confidential ? null : local.getSnapshot({ userId, kind, documentKey }),
        draftService.getDraft(kind, documentKey).catch(() => null),
      ]);
      if (cancelled) return;

      // Newer wins. The local copy is usually ahead — it is written on every
      // change, whereas the server copy waits for a trigger — and it is exactly
      // that gap that a crash falls into.
      const localAt = localRow?.savedAt ? new Date(localRow.savedAt) : null;
      const serverAt = serverRow?.saved_at ? new Date(serverRow.saved_at) : null;
      const useLocal = localAt && (!serverAt || localAt > serverAt);
      const chosen = useLocal ? localRow?.payload : serverRow?.payload;

      if (chosen) {
        setRecoverable({
          payload: chosen,
          savedAt: useLocal ? localAt : serverAt,
          source: useLocal ? 'local' : 'server',
          device: serverRow?.device_label || '',
          versions: serverRow?.versions || [],
        });
        if (serverRow) lastPersisted.current = stableStringify(serverRow.payload);
      }
      if (serverRow?.versions) setVersions(serverRow.versions);
      setCheckedForRecovery(true);
    })();

    return () => { cancelled = true; };
    // Runs once per document. `payload` deliberately absent: re-checking on
    // every keystroke would re-offer recovery over what the user is typing.
  }, [enabled, kind, documentKey, userId, confidential]);

  /* ------------------------------------------------------------------ *
   * Actions the form calls
   * ------------------------------------------------------------------ */

  /** Re-read the retained milestones after one is written. */
  const refreshVersions = useCallback(async () => {
    const row = await draftService.getDraft(kind, documentKey).catch(() => null);
    if (mounted.current && row?.versions) setVersions(row.versions);
  }, [kind, documentKey]);

  /** Force a save now, retained in history. Used by an explicit Save button. */
  const saveNow = useCallback(
    () => persist({ milestone: 'manual_save', force: true }).then(refreshVersions),
    [persist, refreshVersions],
  );

  /** The user took the recovered copy. */
  const acceptRecovery = useCallback(() => {
    setRecoverable(null);
    draftService.markRecovered(kind, documentKey).catch(() => {});
  }, [kind, documentKey]);

  /** The user kept what is on screen and ignored the offer. */
  const dismissRecovery = useCallback(() => setRecoverable(null), []);

  /** The user threw the snapshot away. Removes both copies. */
  const discard = useCallback(async () => {
    setRecoverable(null);
    lastPersisted.current = null;
    setUnsaved(false);
    clearTimeout(idleTimer.current);
    await Promise.all([
      local.deleteSnapshot({ userId, kind, documentKey }),
      draftService.discardDraft(kind, documentKey).catch(() => {}),
    ]);
    if (mounted.current) setStatus(STATUS.IDLE);
  }, [kind, documentKey, userId]);

  /**
   * The document was really saved or submitted. The snapshot must go, or the
   * next visit offers to "restore" content that is already committed.
   */
  const complete = useCallback(async () => {
    clearTimeout(idleTimer.current);
    clearInterval(intervalTimer.current);
    lastPersisted.current = null;
    setUnsaved(false);
    await Promise.all([
      local.deleteSnapshot({ userId, kind, documentKey }),
      draftService.discardDraft(kind, documentKey, { submitted: true }).catch(() => {}),
    ]);
    if (mounted.current) setStatus(STATUS.IDLE);
  }, [kind, documentKey, userId]);

  /** Section change — an explicit structural edit is a milestone (Phase 111.3). */
  const saveSectionChange = useCallback(
    () => persist({ milestone: 'manual_save' }), [persist],
  );

  return {
    status,
    online,
    lastSavedAt,
    recoverable,
    versions,
    refreshVersions,
    checkedForRecovery,
    unsaved,
    saveNow,
    saveSectionChange,
    acceptRecovery,
    dismissRecovery,
    discard,
    complete,
  };
};

export default useDocumentDraft;
