import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// The service module imports the axios instance at load time; the location
// helpers under test never touch it.
vi.mock('./api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

import { getCurrentLocation, geolocationBlockedReason, watchLocation } from './attendanceService';

const fix = (accuracy, latitude = 27.7045, longitude = 85.307) => ({
  coords: { latitude, longitude, accuracy },
});

let watchCallbacks;
let clearWatch;

const installGeolocation = () => {
  watchCallbacks = {};
  clearWatch = vi.fn();
  const geolocation = {
    watchPosition: vi.fn((onSuccess, onError) => {
      watchCallbacks.onSuccess = onSuccess;
      watchCallbacks.onError = onError;
      return 42; // watch id
    }),
    clearWatch,
  };
  Object.defineProperty(globalThis.navigator, 'geolocation', {
    value: geolocation, configurable: true, writable: true,
  });
};

describe('getCurrentLocation — best-of-N GPS fix', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    installGeolocation();
    window.isSecureContext = true;
  });
  afterEach(() => {
    vi.useRealTimers();
    delete globalThis.navigator.geolocation;
  });

  it('keeps the most accurate reading and returns it at the deadline', async () => {
    const promise = getCurrentLocation({ desiredAccuracy: 25, maxWait: 8000 });
    // Devices report the coarse network estimate first, then refine.
    watchCallbacks.onSuccess(fix(1200));
    watchCallbacks.onSuccess(fix(90));
    watchCallbacks.onSuccess(fix(140)); // a worse later reading must not win
    await vi.advanceTimersByTimeAsync(8000);
    const result = await promise;
    expect(result.accuracy).toBe(90);
    expect(clearWatch).toHaveBeenCalledWith(42);
  });

  it('resolves as soon as a fix is accurate enough, without waiting', async () => {
    const promise = getCurrentLocation({ desiredAccuracy: 25, maxWait: 8000 });
    watchCallbacks.onSuccess(fix(300));
    watchCallbacks.onSuccess(fix(12));
    const result = await promise;               // no timer advance needed
    expect(result).toEqual({ latitude: 27.7045, longitude: 85.307, accuracy: 12 });
    expect(clearWatch).toHaveBeenCalledWith(42);
  });

  it('still returns the best reading when the watch errors afterwards', async () => {
    const promise = getCurrentLocation({ desiredAccuracy: 5, maxWait: 8000 });
    watchCallbacks.onSuccess(fix(60));
    watchCallbacks.onError({ code: 3 });        // timeout mid-refinement
    const result = await promise;
    expect(result.accuracy).toBe(60);
  });

  it('resolves null when neither attempt produces a fix', async () => {
    const promise = getCurrentLocation({ maxWait: 8000 });
    await vi.advanceTimersByTimeAsync(8000);   // high-accuracy attempt expires
    await vi.advanceTimersByTimeAsync(6000);   // coarse fallback expires too
    await expect(promise).resolves.toBeNull();
  });

  it('never caches a stale position', () => {
    getCurrentLocation();
    const opts = navigator.geolocation.watchPosition.mock.calls[0][2];
    expect(opts).toMatchObject({ enableHighAccuracy: true, maximumAge: 0 });
  });

  it('falls back to a coarse fix when the device has no GPS', async () => {
    const promise = getCurrentLocation({ desiredAccuracy: 25, maxWait: 8000 });
    // POSITION_UNAVAILABLE from the high-accuracy provider — the desktop case.
    watchCallbacks.onError({ code: 2 });
    await vi.advanceTimersByTimeAsync(0);

    const second = navigator.geolocation.watchPosition.mock.calls[1][2];
    expect(second.enableHighAccuracy).toBe(false);

    // The coarse provider answers with a rough city-level position; imprecise
    // evidence still beats recording nothing at all.
    watchCallbacks.onSuccess(fix(3200));
    await expect(promise).resolves.toMatchObject({ accuracy: 3200 });
  });

  it('does not re-ask after the user refuses permission', async () => {
    const promise = getCurrentLocation({ maxWait: 8000 });
    watchCallbacks.onError({ code: 1 });
    await expect(promise).resolves.toBeNull();
    expect(navigator.geolocation.watchPosition).toHaveBeenCalledTimes(1);
  });
});

describe('geolocationBlockedReason', () => {
  afterEach(() => { delete globalThis.navigator.geolocation; });

  it('flags an insecure origin — the browser blocks location over plain HTTP', () => {
    installGeolocation();
    window.isSecureContext = false;
    expect(geolocationBlockedReason()).toMatch(/HTTPS/);
  });

  it('flags a browser with no geolocation support', () => {
    delete globalThis.navigator.geolocation;
    window.isSecureContext = true;
    expect(geolocationBlockedReason()).toMatch(/does not support/);
  });

  it('returns null when location can be requested', () => {
    installGeolocation();
    window.isSecureContext = true;
    expect(geolocationBlockedReason()).toBeNull();
  });
});

describe('watchLocation', () => {
  beforeEach(() => { installGeolocation(); window.isSecureContext = true; });
  afterEach(() => { delete globalThis.navigator.geolocation; });

  it('streams fixes and stops cleanly', () => {
    const onFix = vi.fn();
    const stop = watchLocation(onFix, vi.fn());
    watchCallbacks.onSuccess(fix(15));
    expect(onFix).toHaveBeenCalledWith({ latitude: 27.7045, longitude: 85.307, accuracy: 15 });
    stop();
    expect(clearWatch).toHaveBeenCalledWith(42);
  });

  it('reports a denied permission immediately, without a coarse retry', () => {
    const onError = vi.fn();
    watchLocation(vi.fn(), onError);
    watchCallbacks.onError({ code: 1 });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/permission denied/i));
    expect(navigator.geolocation.watchPosition).toHaveBeenCalledTimes(1);
  });

  it('retries without high accuracy before reporting POSITION_UNAVAILABLE', () => {
    const onFix = vi.fn();
    const onError = vi.fn();
    watchLocation(onFix, onError);

    watchCallbacks.onError({ code: 2 });          // no GPS on this machine
    expect(onError).not.toHaveBeenCalled();       // silently retried instead
    expect(navigator.geolocation.watchPosition.mock.calls[1][2].enableHighAccuracy).toBe(false);

    watchCallbacks.onSuccess(fix(2500));
    expect(onFix).toHaveBeenCalledWith(expect.objectContaining({ accuracy: 2500 }));
  });

  it('explains POSITION_UNAVAILABLE in terms the user can act on', () => {
    const onError = vi.fn();
    watchLocation(vi.fn(), onError);
    watchCallbacks.onError({ code: 2 });          // high accuracy fails
    watchCallbacks.onError({ code: 2 });          // coarse fails too
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/phone/i));
  });
});
