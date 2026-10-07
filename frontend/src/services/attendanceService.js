import api from './api';

// A GPS fix this good (metres) is treated as exact — stop watching immediately.
const DESIRED_ACCURACY_M = 25;
// Hard ceiling on how long a check-in waits for the fix to converge.
const MAX_WAIT_MS = 8000;
// Second chance without high accuracy, for devices that have no GPS at all.
const FALLBACK_WAIT_MS = 6000;

const PERMISSION_DENIED = 1;

const toFix = (pos) => ({
  latitude: pos.coords.latitude,
  longitude: pos.coords.longitude,
  accuracy: pos.coords.accuracy,
});

/**
 * Turn a GeolocationPositionError into something a user can act on.
 *
 * POSITION_UNAVAILABLE is the confusing one: the OS location setting can be ON
 * and this still fires, because the *browser* needs its own location provider —
 * a desktop with no GPS asks a network service (Chrome asks Google's), and if
 * that is unreachable there is simply no position to report.
 */
export const geolocationErrorMessage = (err) => {
  if (err?.code === PERMISSION_DENIED) {
    return 'Location permission denied. Allow location access for this site, then retry.';
  }
  if (err?.code === 2 /* POSITION_UNAVAILABLE */) {
    return 'Your browser could not determine a position. This is common on a desktop PC with no GPS — check in from your phone for an exact location.';
  }
  if (err?.code === 3 /* TIMEOUT */) {
    return 'Timed out waiting for a location fix. Move near a window or use your phone, then retry.';
  }
  return 'Could not get your location. Check that location services are on.';
};

/**
 * Why geolocation can't run, or null when it can.
 *
 * The API is a *secure context* feature: over plain HTTP (anything but
 * localhost) browsers reject it outright, so a site served without TLS records
 * no location at all. Surfacing that is the difference between "the GPS is
 * inaccurate" and "the page was never allowed to ask".
 */
export const geolocationBlockedReason = () => {
  if (!('geolocation' in navigator)) return 'This browser does not support location.';
  if (typeof window !== 'undefined' && window.isSecureContext === false) {
    return 'Location needs a secure (HTTPS) connection. Open this site over HTTPS to record your check-in location.';
  }
  return null;
};

/**
 * One capture attempt. Resolves `{ fix, error }` — `fix` is the most accurate
 * reading seen, `error` the last GeolocationPositionError when nothing arrived.
 *
 * Uses watchPosition rather than a single getCurrentPosition because a device
 * reports its coarse Wi-Fi/network estimate first and only refines to a true GPS
 * fix over the next few seconds. `maximumAge: 0` forbids a cached fix, so we
 * never record where the employee was a minute ago.
 */
const attemptFix = ({ desiredAccuracy, maxWait, highAccuracy }) =>
  new Promise((resolve) => {
    let best = null;
    let lastError = null;
    let watchId = null;
    let timer = null;
    let settled = false;

    const finish = () => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      resolve({ fix: best, error: best ? null : lastError });
    };

    timer = setTimeout(finish, maxWait);
    watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const fix = toFix(pos);
        if (!best || fix.accuracy < best.accuracy) best = fix;
        if (best.accuracy <= desiredAccuracy) finish();
      },
      // An error after a good reading still resolves with that reading.
      (err) => { lastError = err; finish(); },
      { enableHighAccuracy: highAccuracy, timeout: maxWait, maximumAge: 0 },
    );
  });

/**
 * Best-effort browser geolocation, resolving to `{ latitude, longitude, accuracy }`
 * or `null` when no position can be obtained. Never rejects — the caller checks
 * in either way, since location is optional.
 *
 * Two attempts: a high-accuracy one (GPS, exact), then a coarse network one.
 * A device with no GPS can fail the first and satisfy the second, and a rough
 * location is still far better evidence than none.
 */
/**
 * What produced a fix, as far as the browser lets us tell.
 *
 * The Geolocation API reports `coords.accuracy` and never says how it was
 * obtained, so this is the honest best guess and the server records it as
 * one: a satellite lock is tens of metres, Wi-Fi triangulation is hundreds,
 * and an IP lookup is kilometres. `enableHighAccuracy` having succeeded is
 * the strongest signal available, which is why the caller passes it.
 *
 * The server validates this against a fixed vocabulary and re-derives it
 * from the accuracy when it is absent or unrecognised, so a tampered value
 * cannot put `gps` on a pin that was never one.
 */
export const classifyFix = (fix) => {
  if (!fix || fix.accuracy == null) return 'unknown';
  if (fix.accuracy <= 100) return 'gps';
  if (fix.accuracy <= 2000) return 'network';
  return 'ip';
};

export const getCurrentLocation = async ({
  desiredAccuracy = DESIRED_ACCURACY_M,
  maxWait = MAX_WAIT_MS,
} = {}) => {
  if (geolocationBlockedReason()) return null;

  const precise = await attemptFix({ desiredAccuracy, maxWait, highAccuracy: true });
  if (precise.fix) return precise.fix;
  // Permission was refused — a second ask would fail identically.
  if (precise.error?.code === PERMISSION_DENIED) return null;

  // Accept whatever the coarse provider returns, however imprecise.
  const coarse = await attemptFix({
    desiredAccuracy: Number.POSITIVE_INFINITY,
    maxWait: FALLBACK_WAIT_MS,
    highAccuracy: false,
  });
  if (!coarse.fix) {
    // Surfaces the browser's own wording ("Network location provider … no
    // response received"), which is what actually identifies the fault.
    console.warn('[attendance] no location fix:', coarse.error || precise.error);
  }
  return coarse.fix;
};

/**
 * Continuous position updates for the live map. Calls `onFix` with each new
 * `{ latitude, longitude, accuracy }` and `onError` with a human-readable
 * message. Returns a stop() function — always call it on unmount, or the GPS
 * keeps running and drains the battery.
 */
export const watchLocation = (onFix, onError) => {
  const blocked = geolocationBlockedReason();
  if (blocked) {
    onError?.(blocked);
    return () => {};
  }
  let id = null;
  let triedCoarse = false;

  const subscribe = (highAccuracy) => {
    id = navigator.geolocation.watchPosition(
      (pos) => onFix(toFix(pos)),
      (err) => {
        // A machine with no GPS can fail high accuracy yet answer a coarse
        // network lookup — drop the precision requirement once before giving up.
        if (highAccuracy && !triedCoarse && err?.code !== PERMISSION_DENIED) {
          triedCoarse = true;
          navigator.geolocation.clearWatch(id);
          subscribe(false);
          return;
        }
        console.warn('[attendance] live location error:', err);
        onError?.(geolocationErrorMessage(err));
      },
      { enableHighAccuracy: highAccuracy, timeout: highAccuracy ? 15000 : 25000, maximumAge: 5000 },
    );
  };

  subscribe(true);
  return () => { if (id !== null) navigator.geolocation.clearWatch(id); };
};

/** Attendance: check-in/out, today's status, and monthly calendar. */
export const attendanceService = {
  today: async () => (await api.get('/attendance/today/')).data,
  checkIn: async (coords = {}) => (await api.post('/attendance/check-in/', coords || {})).data,
  checkOut: async (coords = {}) => (await api.post('/attendance/check-out/', coords || {})).data,
  myCalendar: async (year, month) =>
    (await api.get('/attendance/me/', { params: { year, month } })).data,
  // Own biometric attendance (device punches); params: {days|from|to}. Returns
  // `today` for the live dashboard widget plus the windowed rows/summary.
  biometricMe: async (params = {}) =>
    (await api.get('/attendance/biometric/me/', { params })).data,
  list: async (params = {}) => (await api.get('/attendance/', { params })).data,
  manual: async (payload) => (await api.post('/attendance/manual/', payload)).data,

  // Live dashboard feed: today's counts, device health and recent punches.
  // Role-scoped server-side, and it is what a WebSocket event tells us to re-read.
  dashboard: async () => (await api.get('/attendance/dashboard/')).data,

  // HR/Admin-safe options (employees + departments) for the reports dropdowns.
  reportOptions: async () => (await api.get('/attendance/report/options/')).data,

  // Report exports (Admin/HR). Return the full axios response (blob) for saveBlob.
  reportWeekly: (id, week) => api.get(`/attendance/report/employee/${id}/weekly`, { params: { week }, responseType: 'blob' }),
  reportMonthly: (id, year, month) => api.get(`/attendance/report/employee/${id}/monthly`, { params: { year, month }, responseType: 'blob' }),
  reportAll: (params) => api.get('/attendance/report/all', { params, responseType: 'blob' }),
};

export default attendanceService;
