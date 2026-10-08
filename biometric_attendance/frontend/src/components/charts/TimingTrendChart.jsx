import { useState } from 'react'
import { bucketDaily } from '../../lib/analytics'
import { clamp, fmtClock } from '../../lib/format'
import { fmtDay, todayDay } from '../../lib/time'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart, Legend } from './common'

/* Median arrival and departure over time -- is the office drifting later?
 *
 * Two series on ONE axis, which is legitimate here only because both measure
 * the same thing in the same unit: a time of day. Plotting a count against a
 * clock on two scales would be the dual-axis mistake.
 */
export function TimingTrendChart({ view, lateMinutes }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  if (view.dailyTiming.length < 2) return <EmptyChart message="Not enough days in this range." />

  const { unit, points } = bucketDaily(view.dailyTiming, { avg: ['arrival', 'departure'] })
  const values = points.flatMap((p) => [p.arrival, p.departure]).filter((v) => v != null)
  if (!values.length) return <EmptyChart message="No arrival times in this range." />

  // Whole hours either side, so the gridlines land on readable clock times.
  const low = Math.max(0, Math.floor(Math.min(...values) / 60) - 1) * 60
  const high = Math.min(24, Math.ceil(Math.max(...values) / 60) + 1) * 60
  const hourTicks = []
  for (let minute = low; minute <= high; minute += 60) hourTicks.push(minute)

  return (
    <>
      <Legend items={[
        { label: 'Median arrival', color: COLOR.series1 },
        { label: 'Median departure', color: COLOR.series2 },
      ]}
      />

      <ChartFrame height={250} margin={{ left: 52, right: 16, top: 14, bottom: 28 }}>
        {({ iw, ih }) => {
          const x = (i) => (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw)
          /* Later is HIGHER here, unlike the drawer's day-planner chart. On a
             trend the question is "is this going up?", and a reader should not
             have to notice an inverted axis to answer it. */
          const y = (minute) => ih - ((minute - low) / (high - low)) * ih
          const path = (field) => points
            .map((point, i) => (point[field] == null ? null : `${x(i)},${y(point[field])}`))
            .filter(Boolean)
            .join(' ')

          const step = Math.max(1, Math.ceil(points.length / 6))
          const labelled = points.map((_, i) => i).filter((i) => (points.length - 1 - i) % step === 0)

          const onMove = (event) => {
            const box = event.currentTarget.getBoundingClientRect()
            const ratio = box.width ? (event.clientX - box.left) / box.width : 0
            const i = clamp(Math.round(ratio * (points.length - 1)), 0, points.length - 1)
            setHover(i)
            tip.show(event, points[i].label, [
              { color: COLOR.series1, name: 'Median arrival', value: fmtClock(points[i].arrival) },
              { color: COLOR.series2, name: 'Median departure', value: fmtClock(points[i].departure) },
            ])
          }

          return (
            <>
              {hourTicks.map((minute) => (
                <g key={minute}>
                  <line className="gridline" x1={0} x2={iw} y1={y(minute)} y2={y(minute)} />
                  <text className="axis-text" x={-8} y={y(minute) + 4} textAnchor="end">{fmtClock(minute)}</text>
                </g>
              ))}

              {/* The workday-start line the late counts are measured against. */}
              {lateMinutes >= low && lateMinutes <= high ? (
                <line
                  x1={0} x2={iw} y1={y(lateMinutes)} y2={y(lateMinutes)}
                  strokeWidth={1.5} strokeDasharray="0" strokeOpacity={0.45} style={{ stroke: COLOR.series2 }}
                />
              ) : null}

              <polyline points={path('departure')} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" style={{ stroke: COLOR.series2 }} />
              <polyline points={path('arrival')} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" style={{ stroke: COLOR.series1 }} />

              {labelled.map((i) => (
                <text className="axis-text" key={i} x={x(i)} y={ih + 18} textAnchor="middle">
                  {unit === 'month' ? points[i].label : fmtDay(points[i].day, false)}
                </text>
              ))}
              <Baseline x1={0} x2={iw} y1={ih} y2={ih} />

              {hover !== null ? (
                <g pointerEvents="none">
                  <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={0} y2={ih} />
                  {points[hover].arrival != null ? (
                    <circle cx={x(hover)} cy={y(points[hover].arrival)} r={4} strokeWidth={2} style={{ fill: COLOR.series1, stroke: COLOR.surface }} />
                  ) : null}
                  {points[hover].departure != null ? (
                    <circle cx={x(hover)} cy={y(points[hover].departure)} r={4} strokeWidth={2} style={{ fill: COLOR.series2, stroke: COLOR.surface }} />
                  ) : null}
                </g>
              ) : null}

              <rect
                className="hit" x={0} y={0} width={iw} height={ih}
                onPointerMove={onMove}
                onPointerLeave={() => { setHover(null); tip.hide() }}
              />
            </>
          )
        }}
      </ChartFrame>

      {/* Today's departures haven't happened yet, so the last point drops
          hard. Saying so beats letting it read as a sudden trend. */}
      {view.dailyTiming.length && view.dailyTiming[view.dailyTiming.length - 1].day >= todayDay() ? (
        <p className="muted note-line">
          The last point is today, still in progress — its median departure only counts punches so far.
        </p>
      ) : null}
    </>
  )
}

export const timingTrendTable = (view) => ({
  headers: ['Date', 'Median arrival', 'Median departure'],
  rows: view.dailyTiming.slice().reverse()
    .map((entry) => [fmtDay(entry.day), fmtClock(entry.arrival), fmtClock(entry.departure)]),
})
