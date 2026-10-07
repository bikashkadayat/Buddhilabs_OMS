/* Shared chart plumbing.
 *
 * Colours are referenced as CSS custom properties through `style`, never as
 * resolved hex strings. That is what lets the theme switch repaint every chart
 * without React re-rendering a single one.
 */

import { useMeasure } from '../../hooks/useMeasure'

export const COLOR = {
  series1: 'var(--series-1)',
  series2: 'var(--series-2)',
  surface: 'var(--surface-1)',
  ink: 'var(--text-primary)',
  seq: (step) => `var(--seq-${step})`,
}

export const SEQ_STEPS = 8

/** Sequential step for a value. sqrt so a few very busy cells don't flatten
 *  everything else onto one step. */
export function seqStep(value, max) {
  if (!value) return 0
  return Math.min(SEQ_STEPS - 1, Math.max(1, Math.ceil(Math.sqrt(value / max) * (SEQ_STEPS - 1))))
}

/** Bar grown from a left baseline: rounded at the data end, square at the base. */
export function roundedBar(x, y, width, height, r = 4) {
  const radius = Math.min(r, width, height / 2)
  return `M${x},${y} H${x + width - radius} A${radius},${radius} 0 0 1 ${x + width},${y + radius}`
    + ` V${y + height - radius} A${radius},${radius} 0 0 1 ${x + width - radius},${y + height} H${x} Z`
}

/** Column grown up from a bottom baseline. */
export function roundedColumn(x, y, width, height, r = 4) {
  const radius = Math.min(r, width / 2, height)
  return `M${x},${y + height} V${y + radius} A${radius},${radius} 0 0 1 ${x + radius},${y}`
    + ` H${x + width - radius} A${radius},${radius} 0 0 1 ${x + width},${y + radius} V${y + height} Z`
}

/**
 * Measures its container and hands the plot geometry to a render function.
 * Children get {iw, ih, width} in an already-translated <g>.
 */
export function ChartFrame({ height, margin, minWidth = 320, children }) {
  const [ref, width] = useMeasure()
  const m = { top: 12, right: 16, bottom: 26, left: 40, ...margin }
  const w = Math.max(minWidth, width)
  const iw = w - m.left - m.right
  const ih = height - m.top - m.bottom

  return (
    <div className="chart" ref={ref}>
      {width > 0 ? (
        <svg viewBox={`0 0 ${w} ${height}`} width={w} height={height} preserveAspectRatio="xMidYMid meet">
          <g transform={`translate(${m.left},${m.top})`}>{children({ iw, ih, width: w, m })}</g>
        </svg>
      ) : null}
    </div>
  )
}

/** Horizontal gridlines plus their y-axis labels. */
export function YAxis({ ticks, scale, width, format }) {
  return (
    <g>
      {ticks.map((tick) => (
        <g key={tick}>
          <line className="gridline" x1={0} x2={width} y1={scale(tick)} y2={scale(tick)} />
          <text className="axis-text" x={-8} y={scale(tick) + 4} textAnchor="end">{format(tick)}</text>
        </g>
      ))}
    </g>
  )
}

export const Baseline = (props) => <line className="baseline" {...props} />

/** Always present for two or more series -- identity is never colour alone. */
export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span className="legend-item" key={item.label}>
          <span className={item.box ? 'legend-key box' : 'legend-key'} style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  )
}

export function ScaleLegend({ label, max, format }) {
  return (
    <div className="scale-legend">
      <span>{label}</span>
      <span>0</span>
      {Array.from({ length: SEQ_STEPS }, (_, i) => (
        <span className="scale-swatch" key={i} style={{ background: COLOR.seq(i) }} />
      ))}
      <span>{format(max)}</span>
    </div>
  )
}

export const EmptyChart = ({ message }) => <p className="chart-empty">{message}</p>
