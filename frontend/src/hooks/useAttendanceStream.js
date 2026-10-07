import { useEffect, useRef, useState } from 'react';

/**
 * Live attendance over WebSocket, with automatic fallback to polling.
 *
 * An event here is a CACHE-INVALIDATION SIGNAL, not a data payload. The REST
 * endpoint stays the single source of truth, so a dropped or out-of-order
 * message can never leave the UI showing something the server disagrees with —
 * and the polling fallback needs no separate code path, because it triggers the
 * exact same refetch.
 *
 * The JWT travels in the Sec-WebSocket-Protocol header rather than the query
 * string: nginx logs full request lines by default, and `?token=<jwt>` would
 * write a live 30-minute credential into the access logs of both proxy layers.
 *
 * Returns { connected, lastEvent }. Callers pair it with
 *   useAutoRefresh(refetch, connected ? 0 : 20000)
 * so polling stops while the socket is healthy and resumes the moment it drops.
 */

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const HEARTBEAT_MS = 25000;
// Server closes with this when the token is missing/invalid//inactive. Retrying
// with the same credential would just spin, so we stop and let the axios layer's
// refresh cycle produce a new one on the next API call.
const CLOSE_UNAUTHORIZED = 4401;

export function useAttendanceStream({ onEvent, enabled = true } = {}) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);

  const onEventRef = useRef(onEvent);
  // Synced after commit rather than during render (see useAutoRefresh). Only
  // socket message handlers read it, and those always run after a commit.
  useEffect(() => { onEventRef.current = onEvent; }, [onEvent]);

  const socketRef = useRef(null);
  const retryRef = useRef(0);
  const timersRef = useRef({ reconnect: null, heartbeat: null });
  const closedByUsRef = useRef(false);

  useEffect(() => {
    if (!enabled) return undefined;

    const clearTimers = () => {
      const t = timersRef.current;
      if (t.reconnect) { clearTimeout(t.reconnect); t.reconnect = null; }
      if (t.heartbeat) { clearInterval(t.heartbeat); t.heartbeat = null; }
    };

    const scheduleReconnect = () => {
      // Exponential backoff with jitter: without it, every dashboard in the
      // office reconnects in lockstep the instant the backend comes back.
      const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** retryRef.current);
      retryRef.current += 1;
      timersRef.current.reconnect = setTimeout(connect, delay / 2 + Math.random() * (delay / 2));
    };

    function connect() {
      const token = localStorage.getItem('accessToken');
      if (!token) { scheduleReconnect(); return; }

      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${scheme}://${window.location.host}/ws/attendance/`;

      let socket;
      try {
        socket = new WebSocket(url, ['jwt', token]);
      } catch {
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        retryRef.current = 0;
        setConnected(true);
        // Keeps intermediaries from reaping an idle connection, and surfaces a
        // half-open socket that onclose would otherwise never fire for.
        timersRef.current.heartbeat = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }));
          }
        }, HEARTBEAT_MS);
      };

      socket.onmessage = (raw) => {
        let message;
        try { message = JSON.parse(raw.data); } catch { return; }
        if (!message?.type || message.type === 'pong') return;
        setLastEvent(message);
        onEventRef.current?.(message);
      };

      socket.onerror = () => { /* onclose always follows; handle it there. */ };

      socket.onclose = (event) => {
        setConnected(false);
        clearTimers();
        socketRef.current = null;
        if (closedByUsRef.current) return;
        if (event.code === CLOSE_UNAUTHORIZED) return;  // bad token — do not spin
        scheduleReconnect();
      };
    }

    closedByUsRef.current = false;
    connect();

    // A backgrounded tab gets its timers throttled, so come back eagerly.
    const onVisible = () => {
      if (document.visibilityState === 'visible'
          && socketRef.current?.readyState !== WebSocket.OPEN) {
        retryRef.current = 0;
        clearTimers();
        connect();
      }
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      closedByUsRef.current = true;
      document.removeEventListener('visibilitychange', onVisible);
      clearTimers();
      socketRef.current?.close();
      socketRef.current = null;
      setConnected(false);
    };
  }, [enabled]);

  return { connected, lastEvent };
}

export default useAttendanceStream;
