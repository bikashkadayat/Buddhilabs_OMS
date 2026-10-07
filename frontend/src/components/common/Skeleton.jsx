import React from 'react';

/**
 * Loading placeholders (Phase D / blueprint §21).
 *
 * Replaces the bare "Loading…" strings each page rolled by hand. A skeleton
 * matching the real layout means nothing reflows on arrival - a blank that
 * suddenly fills reads as slowness even when the request was fast.
 *
 * `aria-hidden` and a single polite status line: announcing twelve skeleton rows
 * individually is noise, "Loading" once is information.
 */
const Skeleton = ({ rows = 3, height = 11, gap = 8, label = 'Loading' }) => (
  <>
    <div className="ui-sk-wrap" aria-hidden="true" style={{ gap }}>
      {Array.from({ length: rows }, (_, i) => (
        <span
          key={i}
          className="ui-sk"
          style={{ height, width: `${100 - (i % 3) * 12}%` }}
        />
      ))}
    </div>
    <span className="sr-only" role="status">{label}</span>
  </>
);

export default Skeleton;
