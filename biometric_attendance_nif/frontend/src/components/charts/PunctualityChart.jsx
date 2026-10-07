import { useState } from 'react'
import { fmtClock, fmtInt } from '../../lib/format'
import { useMeasure } from '../../hooks/useMeasure'
import { useTooltip } from '../Tooltip'
import { COLOR, EmptyChart } from './common'

const ROW = 22
const LEFT = 132
const RIGHT = 16
const TOP = 26

/* One row per person: the line spans their earliest to latest arrival, the dot
 * sits on their median, and the vertical rule is the workday start.
 *
 * A median alone hides the thing that matters most here -- somebody who is
 * always 10:20 and somebody who swings between 08:00 and 13:00 have the same
 * median and are completely different situations. The line length is that
 * difference, made visible.
 */
export function PunctualityChart({ rows, lateMinutes }) {
  const tip = useTooltip()
  const [ref, width] = useMeasure()
  const [hover, setHover] = useState(null)

  const people = rows.filter((employee) => employee.arrivals.length)
  if (!people.length) return <EmptyChart message="No arrivals in this range." />

  const earliest = Math.min(...people.map((e) => e.arrivals[0]))
  const latest = Math.max(...people.map((e) => e.arrivals[e.arrivals.length - 1]))
  const low = Math.max(0, Math.floor(earliest / 60) * 60 - 30)
  const high = Math.min(1440, Math.ceil(latest / 60) * 60 + 30)

  const height = TOP + people.length * ROW + 12
  const w = Math.max(360, width)
  const iw = w - LEFT - RIGHT
  const x = (minute) => ((minute - low) / (high - low)) * iw

  // Drop any hour tick that would collide with the workday-start label. The
  // rule's own time is the more useful of the two, so the hour yields.
  const hourTicks = []
  for (let minute = Math.ceil(low / 60) * 60; minute <= high; minute += 60) {
    if (Math.abs(minute - lateMinutes) < 40) continue
    hourTicks.push(minute)
  }

  return (
    <div className="chart" ref={ref}>
      {width > 0 ? (
        <svg viewBox={`0 0 ${w} ${height}`} width={w} height={height}>
          <g transform={`translate(${LEFT},${TOP})`}>
            {hourTicks.map((minute) => (
              <g key={minute}>
                <line className="gridline" x1={x(minute)} x2={x(minute)} y1={-8} y2={people.length * ROW} />
                <text className="axis-text" x={x(minute)} y={-14} textAnchor="middle">{fmtClock(minute)}</text>
              </g>
            ))}

            {lateMinutes >= low && lateMinutes <= high ? (
              <>
                <line
                  x1={x(lateMinutes)} x2={x(lateMinutes)} y1={-8} y2={people.length * ROW}
                  strokeWidth={1.5} style={{ stroke: COLOR.series2 }} strokeOpacity={0.75}
                />
                <text
                  className="axis-text strong" x={x(lateMinutes)} y={-14} textAnchor="middle"
                  style={{ paintOrder: 'stroke', stroke: COLOR.surface, strokeWidth: 3, strokeLinejoin: 'round' }}
                >
                  {fmtClock(lateMinutes)}
                </text>
              </>
            ) : null}

            {people.map((employee, i) => {
              const y = i * ROW + ROW / 2
              const first = employee.arrivals[0]
              const last = employee.arrivals[employee.arrivals.length - 1]
              const isLate = employee.medianArrival > lateMinutes

              return (
                <g key={employee.index} className={hover === employee.index ? 'mark-hover' : undefined}>
                  <text className="label-text" x={-LEFT + 4} y={y + 4}>
                    {employee.name.length > 16 ? `${employee.name.slice(0, 15)}…` : employee.name}
                  </text>

                  <line
                    x1={x(first)} x2={x(last)} y1={y} y2={y}
                    strokeWidth={3} strokeLinecap="round"
                    style={{ stroke: COLOR.series1 }} strokeOpacity={0.35}
                  />
                  {/* The median rides on top; the 2px surface ring keeps it
                      readable where it sits on its own range line. */}
                  <circle
                    cx={x(employee.medianArrival)} cy={y} r={4.5} strokeWidth={2}
                    style={{ fill: isLate ? 'var(--status-critical)' : COLOR.series1, stroke: COLOR.surface }}
                  />

                  <rect
                    className="hit" x={-LEFT} y={i * ROW} width={iw + LEFT} height={ROW}
                    onPointerMove={(event) => {
                      setHover(employee.index)
                      tip.show(event, `${employee.name} · #${employee.id}`, [
                        { color: COLOR.series1, name: 'Median arrival', value: fmtClock(employee.medianArrival) },
                        { name: 'Earliest', value: fmtClock(first) },
                        { name: 'Latest', value: fmtClock(last) },
                        { name: 'Late days', value: `${fmtInt(employee.late)} of ${fmtInt(employee.days)}` },
                      ])
                    }}
                    onPointerLeave={() => { setHover(null); tip.hide() }}
                  />
                </g>
              )
            })}
          </g>
        </svg>
      ) : null}
    </div>
  )
}

export const punctualityTable = (rows) => ({
  headers: ['Employee', 'Earliest arrival', 'Median arrival', 'Latest arrival', 'Late days', 'Days present'],
  rows: rows.filter((e) => e.arrivals.length).map((employee) => [
    `${employee.name} #${employee.id}`,
    fmtClock(employee.arrivals[0]),
    fmtClock(employee.medianArrival),
    fmtClock(employee.arrivals[employee.arrivals.length - 1]),
    fmtInt(employee.late),
    fmtInt(employee.days),
  ]),
})
