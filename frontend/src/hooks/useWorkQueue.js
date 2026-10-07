/**
 * The Work Queue's data layer (Phase 203 / blueprint §04).
 *
 * One react-query entry feeding both Home's "My Day" and the full queue page,
 * so the two can never disagree and the six requests are issued once rather
 * than twice. react-query is already the project's fetching pattern - the
 * sidebar badges use it - so this adds no new state library.
 *
 * Acting on a row optimistically marks it done, then invalidates BOTH this
 * query and the source module's dashboard key, because that badge is what the
 * sidebar renders and a stale count beside a cleared row looks like a bug.
 */
import { useCallback, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { describeApiError } from '../services/apiErrors';
import {
  fetchWorkQueue, summarise, applyFilter, TYPES,
} from '../services/workQueue';

export const WORK_QUEUE_KEY = ['workqueue'];

/** Which dashboard badge a completed action invalidates. */
const DASHBOARD_KEY_FOR = {
  [TYPES.TASK]: ['tasks', 'dashboard'],
  [TYPES.MEMO]: ['memos', 'dashboard'],
  [TYPES.MINUTE]: ['minutes', 'dashboard'],
  [TYPES.CIRCULAR]: ['circulars', 'dashboard'],
};

/**
 * @param {Object}  options
 * @param {string}  options.filter  chip name; 'all' when omitted
 * @param {boolean} options.enabled skip fetching (used while auth resolves)
 */
export const useWorkQueue = ({ filter = 'all', enabled = true } = {}) => {
  const queryClient = useQueryClient();
  // Rows the user has just acted on. Held locally rather than mutating the
  // cache so a failed action can restore the row exactly as it was.
  const [resolved, setResolved] = useState({});
  const [failures, setFailures] = useState({});

  const query = useQuery({
    queryKey: WORK_QUEUE_KEY,
    queryFn: fetchWorkQueue,
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: true,
    // One retry, matching the app-wide default. Six sources retried hard would
    // turn a flaky connection into a request storm on the busiest screen.
    retry: 1,
  });

  // Read inside the memo, not above it: `?? []` allocates a fresh array on every
  // render, which would make the memo below recompute every time and defeat it.
  const sources = useMemo(() => query.data?.sources ?? [], [query.data]);

  /** Decorate with any local outcome, so a resolved row stays visible but done. */
  const items = useMemo(
    () => (query.data?.items ?? []).map((item) => (
      resolved[item.id]
        ? { ...item, resolved: resolved[item.id] }
        : (failures[item.id] ? { ...item, error: failures[item.id] } : item)
    )),
    [query.data, resolved, failures],
  );

  // Counts are computed over items that are still OUTSTANDING: a chip that
  // keeps counting rows the user just cleared reads as broken.
  const outstanding = useMemo(
    () => items.filter((i) => !i.resolved), [items],
  );
  const counts = useMemo(() => summarise(outstanding), [outstanding]);
  const visible = useMemo(() => applyFilter(items, filter), [items, filter]);

  const act = useCallback(async (item, action, remarks = '') => {
    setFailures((f) => {
      const next = { ...f };
      delete next[item.id];
      return next;
    });
    // Optimistic: mark done immediately so the row settles under the pointer.
    setResolved((r) => ({ ...r, [item.id]: { verb: action.verb, pending: true } }));
    try {
      await action.run(remarks);
      setResolved((r) => ({ ...r, [item.id]: { verb: action.verb, pending: false } }));
      queryClient.invalidateQueries({ queryKey: WORK_QUEUE_KEY });
      const dashKey = DASHBOARD_KEY_FOR[item.type];
      if (dashKey) queryClient.invalidateQueries({ queryKey: dashKey });
      return { ok: true };
    } catch (error) {
      // Restore the row and surface the reason ON it - never a toast that
      // scrolls away from the thing it is about.
      setResolved((r) => {
        const next = { ...r };
        delete next[item.id];
        return next;
      });
      // The server's own sentence, never axios's "Request failed with status
      // code 400". `data.detail || error.message` fell through to the latter for
      // every DRF field error, which has no `detail` key - see apiErrors.js.
      const message = describeApiError(error);
      setFailures((f) => ({ ...f, [item.id]: message }));
      return { ok: false, error: message };
    }
  }, [queryClient]);

  const retrySources = useCallback(
    () => queryClient.invalidateQueries({ queryKey: WORK_QUEUE_KEY }),
    [queryClient],
  );

  return {
    items: visible,
    allItems: items,
    counts,
    summary: { total: counts.total, overdue: counts.overdue },
    sources,
    failedSources: sources.filter((s) => !s.ok),
    isLoading: query.isLoading,
    isError: query.isError,
    act,
    retrySources,
  };
};

export default useWorkQueue;
