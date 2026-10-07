/**
 * One palette and one set of axis/tooltip props for every analytics chart.
 *
 * Nine dashboards drawn by nine components become nine colour schemes within a
 * release. Everything visual lives here; the chart components import roles, not
 * hex codes.
 *
 * Two palettes, because they answer two different questions:
 *
 *   STATUS  — present / late / WFH / absent / on-leave. These are reserved,
 *             semantic, and identical to the colours the Phase 9 workforce
 *             dashboards already use (`statusMeta.js`, `AttendanceTrendChart`).
 *             Green never means "series 3" here.
 *   SERIES  — department identity. A fixed hue order, assigned by position and
 *             never cycled: a filter that removes a department must not repaint
 *             the survivors.
 *
 * Both were validated with the data-viz palette checker rather than eyeballed —
 * lightness band, chroma floor, adjacent-pair CVD separation (protan/deutan),
 * normal-vision separation and surface contrast, in light and dark. Results:
 *
 *   STATUS light: worst adjacent CVD ΔE 8.9, normal-vision 23.8 — pass.
 *   SERIES light: worst adjacent CVD ΔE 9.1, normal-vision 19.6 — pass.
 *   SERIES dark:  worst adjacent CVD ΔE 8.4, normal-vision 19.3 — pass.
 *
 * Both carry a contrast WARN on their light-mode yellows and greens (below 3:1
 * against a white card). The rule for that is relief, not a colour change: every
 * chart in this folder ships a "View data" table and a legend, so identity is
 * never carried by colour alone. `ChartFrame` provides both — do not render a
 * chart in this app without it.
 */

/** Reserved semantic colours. Never reassign one of these to a data series. */
export const STATUS_COLOURS = {
  present: '#10b981',
  late: '#f59e0b',
  half_day: '#f97316',
  wfh: '#7c3aed',
  absent: '#ef4444',
  on_leave: '#3b82f6',
  holiday: '#94a3b8',
  compliance: '#274095',
  overtime: '#0ea5e9',
};

/**
 * Categorical hues for department identity, in FIXED order.
 *
 * A ninth department is never a generated ninth hue — the API folds everything
 * past the eighth into an "Others" aggregate and reports the fold, so this list
 * is never indexed past its end.
 */
export const SERIES_LIGHT = [
  '#2a78d6', '#eb6834', '#1baf7a', '#eda100',
  '#e87ba4', '#008300', '#4a3aa7', '#e34948',
];
export const SERIES_DARK = [
  '#3987e5', '#d95926', '#199e70', '#c98500',
  '#d55181', '#008300', '#9085e9', '#e66767',
];
/** The aggregate bucket is deliberately grey: "Others" is not an identity. */
export const OTHERS_COLOUR = '#94a3b8';

/**
 * Colour for series `index`, by POSITION not by rank.
 *
 * Charts sort rows for readability all the time; if colour followed the sorted
 * position, re-sorting would repaint every line and destroy the reader's
 * mapping between colour and department. Callers pass a stable index.
 */
export const seriesColour = (index, isAggregate = false) => {
  if (isAggregate) return OTHERS_COLOUR;
  return SERIES_LIGHT[index % SERIES_LIGHT.length];
};

/** Health-score bands, used by both the bar fill and the badge. */
export const healthBand = (score) => {
  if (score === null || score === undefined) return { tone: 'muted', label: 'No data' };
  if (score >= 90) return { tone: 'ok', label: 'Healthy' };
  if (score >= 75) return { tone: 'warn', label: 'Watch' };
  return { tone: 'bad', label: 'At risk' };
};

export const HEALTH_COLOURS = {
  ok: '#10b981',
  warn: '#f59e0b',
  bad: '#ef4444',
  muted: '#cbd5e1',
};

// ---------------------------------------------------------------------------
// shared recharts props
// ---------------------------------------------------------------------------
export const GRID = {
  strokeDasharray: '3 3',
  stroke: 'var(--border-light, #e5e7eb)',
  vertical: false,
};

export const AXIS = {
  // Tokens, like the `fill` beside them: Recharts spreads these onto the
  // SVG <text>, where presentation attributes are CSS declarations and
  // var() resolves. Verified against a rendered chart, not assumed.
  tick: { fontSize: 'var(--fs-label)', fill: 'var(--text-secondary, #475569)' },
  tickLine: false,
  axisLine: { stroke: 'var(--border-light, #e5e7eb)' },
};

export const TOOLTIP = {
  contentStyle: {
    background: 'var(--bg-card, #fff)',
    border: '1px solid var(--border-light, #e5e7eb)',
    borderRadius: 8,
    fontSize: 'var(--fs-meta)',
    boxShadow: 'var(--shadow-md)',
  },
  // Text wears text tokens, never the series colour: the coloured swatch beside
  // a row already carries identity, and colouring the number too makes the
  // value harder to read for exactly the people the palette is protecting.
  itemStyle: { color: 'var(--text-primary, #0f172a)' },
  labelStyle: { color: 'var(--text-secondary, #475569)', marginBottom: 4 },
};

export const LEGEND = { wrapperStyle: { fontSize: 'var(--fs-meta)', paddingTop: 4 } };

/** 2px lines and ≥8px markers, per the mark spec. Thin, not hairline. */
export const LINE = { strokeWidth: 2, dot: false, activeDot: { r: 4 } };
export const AREA = { strokeWidth: 2, fillOpacity: 0.18 };
/** 4px rounded data-end, anchored to the baseline. */
export const BAR_RADIUS = [4, 4, 0, 0];
export const BAR_RADIUS_H = [0, 4, 4, 0];

export const CHART_MARGIN = { top: 8, right: 12, left: -12, bottom: 0 };

/** Percentages get one decimal; hours and days get at most two. */
export const fmtPct = (value) =>
  value === null || value === undefined ? '—' : `${Number(value).toFixed(1)}%`;
export const fmtNum = (value, digits = 1) =>
  value === null || value === undefined ? '—' : Number(value).toFixed(digits).replace(/\.0+$/, '');
export const fmtHours = (value) =>
  value === null || value === undefined ? '—' : `${fmtNum(value)}h`;
export const fmtDays = (value) =>
  value === null || value === undefined ? '—' : `${fmtNum(value)}d`;
