import { useEffect, useRef } from 'react';

/**
 * Keep manually-fetched data fresh across accounts without a page refresh.
 * Calls `refetch`:
 *   - on a fixed interval (default 20s) while the tab is VISIBLE,
 *   - immediately when the tab becomes visible again, and
 *   - when the window regains focus.
 * Polling is paused while the tab is hidden (no wasted requests) and resumes on
 * return. `refetch` is read through a ref so passing an inline function is fine.
 *
 * Pass `intervalMs <= 0` to disable the interval while keeping the focus /
 * visibility refresh — that is how a live WebSocket switches polling off
 * without the caller needing a second code path:
 *   useAutoRefresh(refetch, connected ? 0 : 20000)
 */
export function useAutoRefresh(refetch, intervalMs = 20000) {
  const cb = useRef(refetch);
  // Synced in an effect, not during render: React may render without
  // committing, and a render-time write would let a discarded render's callback
  // leak into the live timer. Declared before the effect below, so on every
  // commit it runs first; the ref is only read from timers and DOM events.
  useEffect(() => { cb.current = refetch; }, [refetch]);

  useEffect(() => {
    let timer = null;
    const polling = intervalMs > 0;
    const run = () => { if (document.visibilityState === 'visible') cb.current?.(); };
    const start = () => { if (polling && !timer) timer = setInterval(run, intervalMs); };
    const stop = () => { if (timer) { clearInterval(timer); timer = null; } };

    const onVisibility = () => {
      if (document.visibilityState === 'visible') { cb.current?.(); start(); }
      else stop();
    };
    const onFocus = () => cb.current?.();

    start();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('focus', onFocus);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('focus', onFocus);
    };
  }, [intervalMs]);
}

export default useAutoRefresh;
