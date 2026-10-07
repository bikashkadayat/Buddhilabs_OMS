import { describe, it, expect } from 'vitest';

import { haversineM, fmtDistance, osmEmbedSrc, googleMapsHref } from './geo';

// Kathmandu Durbar Square -> Thamel, ~1.32 km apart on the ground.
const DURBAR = [27.7045, 85.3070];
const THAMEL = [27.7154, 85.3123];

const bboxOf = (src) => {
  const raw = decodeURIComponent(new URL(src).searchParams.get('bbox'));
  const [minLng, minLat, maxLng, maxLat] = raw.split(',').map(Number);
  return { minLng, minLat, maxLng, maxLat };
};

describe('haversineM', () => {
  it('matches a known real-world distance', () => {
    const d = haversineM(...DURBAR, ...THAMEL);
    expect(d).toBeGreaterThan(1300);
    expect(d).toBeLessThan(1340);
  });

  it('is zero for the same point and symmetric between two', () => {
    expect(haversineM(...DURBAR, ...DURBAR)).toBe(0);
    expect(haversineM(...DURBAR, ...THAMEL)).toBeCloseTo(haversineM(...THAMEL, ...DURBAR), 6);
  });

  it('agrees with the backend to within a metre', () => {
    // backend services.haversine_m(27.7045, 85.3070, 27.7154, 85.3123) == 1319.6
    expect(haversineM(...DURBAR, ...THAMEL)).toBeCloseTo(1319.6, 0);
  });
});

describe('fmtDistance', () => {
  it('uses metres below a kilometre and kilometres above', () => {
    expect(fmtDistance(340.4)).toBe('340 m');
    expect(fmtDistance(999)).toBe('999 m');
    expect(fmtDistance(1000)).toBe('1.00 km');
    expect(fmtDistance(1319.6)).toBe('1.32 km');
  });

  it('returns null when there is nothing to show', () => {
    expect(fmtDistance(null)).toBeNull();
    expect(fmtDistance(undefined)).toBeNull();
  });

  it('still formats a zero distance rather than dropping it', () => {
    expect(fmtDistance(0)).toBe('0 m');
  });
});

describe('osmEmbedSrc', () => {
  it('pins the marker at the coordinate', () => {
    const src = osmEmbedSrc({ lat: 27.7045, lng: 85.307, accuracy: 15 });
    expect(new URL(src).searchParams.get('marker')).toBe('27.7045,85.307');
  });

  it('frames a box matching the requested aspect', () => {
    const { minLng, minLat, maxLng, maxLat } = bboxOf(
      osmEmbedSrc({ lat: 27.7045, lng: 85.307, accuracy: 50, aspect: 2 }),
    );
    const widthDeg = maxLng - minLng;
    const heightDeg = maxLat - minLat;
    // Longitude degrees are shorter than latitude ones at 27.7°N, so a 2:1 box
    // in metres spans more than 2x in degrees.
    expect(widthDeg / heightDeg).toBeGreaterThan(2);
    expect(widthDeg / heightDeg).toBeLessThan(2.5);
  });

  it('zooms out for an imprecise fix and stays tight for a GPS one', () => {
    const tight = bboxOf(osmEmbedSrc({ lat: 27.7045, lng: 85.307, accuracy: 10 }));
    const loose = bboxOf(osmEmbedSrc({ lat: 27.7045, lng: 85.307, accuracy: 3000 }));
    expect(loose.maxLat - loose.minLat).toBeGreaterThan(tight.maxLat - tight.minLat);
  });

  it('rounds coordinates to the requested precision', () => {
    // The live map rounds to ~11 m so GPS jitter cannot reload the iframe.
    const src = osmEmbedSrc({ lat: 27.70451234, lng: 85.30709876, precision: 4 });
    expect(new URL(src).searchParams.get('marker')).toBe('27.7045,85.3071');
  });

  it('returns null without a coordinate rather than a broken URL', () => {
    expect(osmEmbedSrc({ lat: null, lng: 85.307 })).toBeNull();
    expect(osmEmbedSrc({ lat: 27.7045, lng: undefined })).toBeNull();
  });
});

describe('googleMapsHref', () => {
  it('builds a deep link that opens the native app on a phone', () => {
    expect(googleMapsHref(27.7045, 85.307)).toBe(
      'https://www.google.com/maps/search/?api=1&query=27.7045,85.307',
    );
  });
});
