import { useState } from 'react'
import { clamp, fmt1, fmtInt, ticks } from '../../lib/format'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart, YAxis } from './common'

const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`

/* How many people are on site at each hour -- the shape of the working day.
 * The arrival and departure histograms show when the edges happen; this shows
 * what is true in between, which is what "how busy is the office at 3pm" means.
 */
export function OccupancyChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  if (!view.activeDayCount) return <EmptyChart message="No punches in this range." />

  // Per active day, so the number reads as "people", not "employee-days".
  const perDay = view.occupancy.map((count) => count / view.activeDayCount)
  const yTicks = ticks(Math.max(1, ...perDay))
  const top = yTicks[yTicks.length - 1]
  const peak = perDay.indexOf(Math.max(...perDay))

  return (
    <ChartFrame height={230} margin={{ left: 40, right: 16, top: 16, bottom: 28 }}>
      {({ iw, ih }) => {
        const x = (hour) => (hour / 23) * iw
        const y = (value) => ih - (value / top) * ih
        const line = perDay.map((value, hour) => `${x(hour)},${y(value)}`).join(' ')

        const onMove = (event) => {
          const box = event.currentTarget.getBoundingClientRect()
          const ratio = box.width ? (event.clientX - box.left) / box.width : 0
          const hour = clamp(Math.round(ratio * 23), 0, 23)
          setHover(hour)
          tip.show(event, `${hourLabel(hour)} – ${String(hour).padStart(2, '0')}:59`, [
            { color: COLOR.series1, name: 'People on site', value: fmt1(perDay[hour]) },
            { name: 'Employee-days', value: fmtInt(view.occupancy[hour]) },
          ])
        }

        return (
          <>
            <YAxis ticks={yTicks} scale={y} width={iw} format={fmtInt} />

            <polygon points={`0,${ih} ${line} ${iw},${ih}`} style={{ fill: COLOR.series1 }} fillOpacity={0.1} />
            <polyline
              points={line} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
              style={{ stroke: COLOR.series1 }}
            />

            {/* One direct label, on the point the chart exists to show. */}
            <circle cx={x(peak)} cy={y(perDay[peak])} r={4.5} strokeWidth={2} style={{ fill: COLOR.series1, stroke: COLOR.surface }} />
            <text
              className="value-text" x={x(peak)} y={y(perDay[peak]) - 10} textAnchor="middle"
              style={{ paintOrder: 'stroke', stroke: COLOR.surface, strokeWidth: 3, strokeLinejoin: 'round' }}
            >
              {`${fmt1(perDay[peak])} at ${hourLabel(peak)}`}
            </text>

            {[0, 3, 6, 9, 12, 15, 18, 21].map((hour) => (
              <text className="axis-text" key={hour} x={x(hour)} y={ih + 18} textAnchor="middle">
                {String(hour).padStart(2, '0')}
              </text>
            ))}
            <Baseline x1={0} x2={iw} y1={ih} y2={ih} />

            {hover !== null ? (
              <g pointerEvents="none">
                <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={0} y2={ih} />
                <circle cx={x(hover)} cy={y(perDay[hover])} r={4} strokeWidth={2} style={{ fill: COLOR.series1, stroke: COLOR.surface }} />
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
  )
}

export const occupancyTable = (view) => ({
  headers: ['Hour', 'Average people on site', 'Employee-days'],
  rows: view.occupancy.map((count, hour) => [
    hourLabel(hour),
    view.activeDayCount ? fmt1(count / view.activeDayCount) : '0',
    fmtInt(count),
  ]),
})
