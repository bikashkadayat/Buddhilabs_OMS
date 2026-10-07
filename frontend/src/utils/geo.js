/** Shared geo helpers for attendance locations (live map + pinned records). */

// Metres per degree of latitude (constant); longitude shrinks by cos(lat).
const M_PER_DEG_LAT = 111320;
const EARTH_RADIUS_M = 6371008.8;

/** Great-circle metres between two points — mirrors the backend's haversine. */
export const haversineM = (lat1, lng1, lat2, lng2) => {
  const rad = (d) => (d * Math.PI) / 180;
  const p1 = rad(lat1);
  const p2 = rad(lat2);
  const dPhi = p2 - p1;
  const dLambda = rad(lng2 - lng1);
  const a =
    Math.sin(dPhi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLambda / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
};

/** "340 m" / "1.24 km"; null when there is nothing to format. */
export const fmtDistance = (m) =>
  m == null ? null : m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(2)} km`;

/** Google Maps deep link for a coordinate — opens in the native app on phones. */
export const googleMapsHref = (lat, lng) =>
  `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;

/**
 * OpenStreetMap embed URL with a pin at (lat, lng).
 *
 * `aspect` is the width:height of the frame it will sit in. The embed fits the
 * bounds without distorting, so a mismatch only costs zoom — a square box in a
 * wide frame zooms further out than needed.
 *
 * `precision` rounds the coordinates used in the URL. The live map passes 4
 * (~11 m) because otherwise every metre of GPS jitter rebuilds the src and the
 * iframe visibly reloads; a stored pin never moves, so it can use full detail.
 */
export const osmEmbedSrc = ({ lat, lng, accuracy, aspect = 2, precision = 5 }) => {
  if (lat == null || lng == null) return null;
  const la = Number(Number(lat).toFixed(precision));
  const ln = Number(Number(lng).toFixed(precision));
  if (Number.isNaN(la) || Number.isNaN(ln)) return null;
  // Frame roughly four times the accuracy radius so the true position is always
  // on screen: tight for a GPS fix, wide for a coarse network one. Clamped to a
  // sane 150 m – 6 km.
  const spanM = Math.min(6000, Math.max(150, (accuracy || 50) * 4));
  const dLat = spanM / 2 / M_PER_DEG_LAT;
  const dLng = (dLat * aspect) / Math.max(0.2, Math.cos((la * Math.PI) / 180));
  const bbox = [ln - dLng, la - dLat, ln + dLng, la + dLat].map((n) => n.toFixed(5)).join(',');
  return `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${la}%2C${ln}`;
};
