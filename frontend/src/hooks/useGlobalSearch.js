/**
 * Enterprise search data layer (Phase 204 / blueprint §07).
 *
 * Three behaviours the brief calls for, and what each actually buys:
 *
 *  DEBOUNCE (250ms) — one request per pause, not one per keystroke. Typing
 *  "budget" would otherwise fire six fan-outs of four requests each.
 *
 *  CANCELLATION — every fetch gets an AbortSignal tied to the query it belongs
 *  to. Without it a slow response for "bud" can land after "budget" resolved
 *  and repaint the list with stale rows; debouncing alone does not prevent
 *  that, because the earlier request is already in flight.
 *
 *  CACHING — react-query keyed on the query string, so re-opening the palette
 *  or backspacing to a term already typed is instant and silent. `placeholderData`
 *  serves the previous query's rows while the next loads, which stops the list
 *  flashing empty between keystrokes.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from './useAuth';
import { useWorkQueue } from './useWorkQueue';
import {
  MIN_QUERY, sourcesFor, searchLocal, normaliseResults,
  withQueueActions, groupResults,
} from '../services/globalSearch';

export const SEARCH_DEBOUNCE_MS = 250;

/** Stable empty payload, so an idle palette does not remount its result list. */
const EMPTY_REMOTE = { results: [], failed: [] };

/** Debounce a value. Exported for the tests, which drive it with fake timers. */
export const useDebounced = (value, delay = SEARCH_DEBOUNCE_MS) => {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return settled;
};

/**
 * Fan out to every remote source, tolerating failure per source.
 *
 * Same posture as the Work Queue: `allSettled` with a synchronous-throw guard,
 * so one broken source costs its own group and nothing else. A palette that
 * shows nothing because one module is down would be worse than no palette.
 */
export const fetchRemote = async (query, signal, role) => {
  // Only the sources this role may query: asking the asset register on
  // behalf of an employee would be a 403 shown as "Assets couldn't load".
  const sources = sourcesFor(role);
  const started = sources.map((source) => {
    try {
      return Promise.resolve(source.fetch(query, signal));
    } catch (error) {
      return Promise.reject(error);
    }
  });
  const settled = await Promise.allSettled(started);
  const results = [];
  const failed = [];
  settled.forEach((outcome, i) => {
    const source = sources[i];
    if (outcome.status === 'fulfilled') {
      results.push(...normaliseResults(source, outcome.value, role));
    } else if (outcome.reason?.name !== 'CanceledError'
               && outcome.reason?.code !== 'ERR_CANCELED') {
      // An aborted request is not a failure - it is the previous query being
      // superseded, which is the system working.
      failed.push(source.group);
    }
  });
  return { results, failed };
};

export const useGlobalSearch = (rawQuery, { enabled = true } = {}) => {
  const { role } = useAuth();
  const queryClient = useQueryClient();
  const query = useDebounced(rawQuery.trim());
  const long = query.length >= MIN_QUERY;

  // The queue is already cached by Home and /queue, so borrowing its actions
  // costs nothing. `enabled: false` means search never triggers its own fetch -
  // it decorates only when the data happens to be there.
  const { allItems } = useWorkQueue({ enabled: false });

  const abortRef = useRef(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const remote = useQuery({
    queryKey: ['globalSearch', role, query],
    enabled: enabled && long,
    staleTime: 30_000,
    retry: false,
    // Idiomatic keep-previous-data: while a new query loads, react-query serves
    // the last resolved payload as placeholder. Replaces holding it in a ref,
    // which meant reading a ref during render - unsafe under concurrent React.
    placeholderData: (previous) => previous,
    queryFn: () => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      return fetchRemote(query, controller.signal, role);
    },
  });

  // Local results need no request, so they update on the RAW query - the
  // navigation and quick-action groups respond to every keystroke while the
  // remote groups wait for the pause.
  const local = useMemo(
    () => (rawQuery.trim().length >= 1 ? searchLocal(rawQuery, role) : []),
    [rawQuery, role],
  );

  const remoteData = (long && remote.data) || EMPTY_REMOTE;

  const groups = useMemo(() => {
    const merged = [...local, ...withQueueActions(remoteData.results, allItems)];
    return groupResults(merged);
  }, [local, remoteData, allItems]);

  /** Flat list in render order — what the arrow keys traverse. */
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    queryClient.removeQueries({ queryKey: ['globalSearch'] });
  }, [queryClient]);

  return {
    groups,
    flat,
    query,
    isLoading: long && remote.isFetching && !remote.data,
    // `isPlaceholderData` is true while showing the previous query's rows.
    isSearching: long && remote.isFetching,
    tooShort: rawQuery.trim().length > 0 && !long,
    failedGroups: remoteData.failed,
    reset,
  };
};

export default useGlobalSearch;
