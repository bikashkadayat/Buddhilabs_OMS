import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MapPin, Crosshair, AlertTriangle, Building2 } from 'lucide-react';
import { watchLocation } from '../../services/attendanceService';
import { haversineM, fmtDistance, osmEmbedSrc, googleMapsHref } from '../../utils/geo';

// Width : height of .att-map-frame — keep the two in step.
const FRAME_ASPECT = 2;

/**
 * Small live map of the signed-in user's current position.
 *
 * Uses the OpenStreetMap embed rather than a mapping library so it costs no new
 * dependency, no API key and no bundle weight — it is a read-only preview, not
 * an interactive GIS surface.
 */
const LiveLocationMap = ({ office = null, maxAccuracy = 100 }) => {
  const [fix, setFix] = useState(null);
  const [error, setError] = useState('');
  const stopRef = useRef(null);

  // Subscribing itself sets no state — the watch reports asynchronously — so the
  // effect below stays free of synchronous cascading renders.
  const start = useCallback(() => {
    stopRef.current?.();
    stopRef.current = watchLocation(
      (next) => { setError(''); setFix(next); },
      (message) => setError(message),
    );
  }, []);

  // Retry clears the message right away, then re-subscribes.
  const retry = useCallback(() => { setError(''); start(); }, [start]);

  // Watch while the tab is visible only: a background GPS watch drains the
  // phone battery for a map nobody is looking at.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) {
        stopRef.current?.();
        stopRef.current = null;
      } else if (!stopRef.current) {
        start();
      }
    };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      stopRef.current?.();
      stopRef.current = null;
    };
  }, [start]);

  // precision 4 (~11 m) matters here: at full detail every metre of GPS jitter
  // would rebuild the src and the iframe would visibly reload.
  const src = useMemo(
    () => (fix ? osmEmbedSrc({
      lat: fix.latitude, lng: fix.longitude, accuracy: fix.accuracy,
      aspect: FRAME_ASPECT, precision: 4,
    }) : null),
    [fix],
  );

  const distance = useMemo(() => {
    if (!fix || !office) return null;
    return haversineM(fix.latitude, fix.longitude, office.lat, office.lng);
  }, [fix, office]);

  const inside = distance != null && office ? distance <= office.radius_m : null;
  const poor = fix && fix.accuracy != null && fix.accuracy > maxAccuracy;

  return (
    <div className="att-map">
      <div className="att-map-head">
        <span className="att-map-title"><Crosshair size={14} aria-hidden="true" /> My live location</span>
        {fix && (
          <span className={`att-loc-acc${poor ? ' att-loc-acc-poor' : ''}`}>
            ±{Math.round(fix.accuracy)}m
          </span>
        )}
      </div>

      {error && (
        <div className="att-map-msg att-map-msg-warn">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>{error}</span>
          <button type="button" className="att-map-retry" onClick={retry}>Retry</button>
        </div>
      )}

      {!error && !fix && (
        <div className="att-map-msg">
          <MapPin size={14} aria-hidden="true" /> Locating you…
        </div>
      )}

      {src && (
        <>
          <iframe
            className="att-map-frame"
            title="Your current location on a map"
            src={src}
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
          />
          <div className="att-map-foot">
            <span className="att-loc-text" title={`${fix.latitude}, ${fix.longitude}`}>
              {fix.latitude.toFixed(5)}, {fix.longitude.toFixed(5)}
            </span>
            {office && distance != null && (
              <span className={`att-geo${inside ? ' att-geo-in' : ' att-geo-out'}`}>
                <Building2 size={12} aria-hidden="true" />
                {inside ? `At ${office.name}` : `${fmtDistance(distance)} from ${office.name}`}
              </span>
            )}
            <a
              className="att-loc-link"
              href={googleMapsHref(fix.latitude, fix.longitude)}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open in Maps
            </a>
          </div>
          {poor && (
            <div className="att-loc-warn">
              <AlertTriangle size={13} aria-hidden="true" />
              Approximate — this device has no GPS fix yet. Use a phone with location enabled for an exact position.
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default LiveLocationMap;
