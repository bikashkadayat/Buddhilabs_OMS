/* Number and duration formatting.
 *
 * Two rules worth keeping straight:
 *  - `--` is for the screen, '' is for a CSV cell. A spreadsheet should see an
 *    empty cell where there is no measurement, not a string.
 *  - durations are offered both as `8h 07m` (to read) and `8.11` (to sum).
 */

export const fmtInt = (n) => Math.round(n).toLocaleString()

export const fmtCompact = (n) =>
  n >= 1e6
    ? `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`
    : n >= 10000
      ? `${(n / 1000).toFixed(1).replace(/\.0$/, '')}K`
      : fmtInt(n)

export const fmt1 = (n) =>
  (Math.round(n * 10) / 10).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export const fmtPct = (ratio) => `${Math.round(ratio * 100)}%`

export function fmtClock(minutes) {
  if (minutes == null || !Number.isFinite(minutes)) return '--'
  const m = Math.round(minutes)
  return `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export function fmtSpan(minutes) {
  if (minutes == null || !Number.isFinite(minutes)) return '--'
  const m = Math.round(minutes)
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

export const decimalHours = (minutes) =>
  minutes == null || !Number.isFinite(minutes) ? '' : (Math.round((minutes / 60) * 100) / 100).toFixed(2)

/* Nice round axis ticks. Every y-axis here counts people or punches, so the
   step never drops below 1 -- a "0.25 people" gridline is nonsense. The top is
   rounded UP past the max; stopping at the last tick below it would scale the
   chart to a top the data exceeds, and the tallest mark would be drawn above
   its own axis. */
export function ticks(max, count = 5) {
  if (max <= 0) return [0, 1]
  const rough = max / count
  const mag = 10 ** Math.floor(Math.log10(rough))
  const step = Math.max(1, [1, 2, 2.5, 5, 10].find((s) => s * mag >= rough) * mag)
  const top = Math.ceil(max / step) * step
  const out = []
  for (let v = 0; v <= top + step * 0.001; v += step) out.push(v)
  return out
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
