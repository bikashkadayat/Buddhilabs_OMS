import { useState } from 'react'
import { fmt1, fmtInt, ticks } from '../../lib/format'
import { WEEKDAYS } from '../../lib/time'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart, YAxis, roundedColumn } from './common'

/* Average people present on each weekday. Measured over calendar days in
 * range, not active ones -- a weekend nobody works averages zero, and that is
 * the answer the chart is being asked for.
 */
export function WeekdayChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  const profile = view.weekdayProfile
  if (!profile.some((entry) => entry.present)) return <EmptyChart message="No punches in this range." />

  const yTicks = ticks(Math.max(1, ...profile.map((entry) => entry.average)))
  const top = yTicks[yTicks.length - 1]

  return (
    <ChartFrame height={230} margin={{ left: 40, right: 12, top: 18, bottom: 28 }}>
      {({ iw, ih }) => {
        const band = iw / 7
        const barWidth = Math.min(24, band - 14)   // capped: never fill the slot
        const y = (value) => ih - (value / top) * ih

        return (
          <>
            <YAxis ticks={yTicks} scale={y} width={iw} format={fmtInt} />

            {profile.map((entry) => {
              const x = entry.weekday * band + (band - barWidth) / 2
              const height = entry.average > 0 ? Math.max(2, ih - y(entry.average)) : 0

              return (
                <g key={entry.weekday}>
                  {height ? (
                    <path
                      d={roundedColumn(x, ih - height, barWidth, height)}
                      className={hover === entry.weekday ? 'mark-hover' : undefined}
                      style={{ fill: COLOR.series1 }}
                    />
                  ) : null}
                  <text className="value-text" x={x + barWidth / 2} y={ih - height - 6} textAnchor="middle">
                    {fmt1(entry.average)}
                  </text>
                  <text className="axis-text strong" x={entry.weekday * band + band / 2} y={ih + 18} textAnchor="middle">
                    {WEEKDAYS[entry.weekday]}
                  </text>

                  <rect
                    className="hit" x={entry.weekday * band} y={0} width={band} height={ih}
                    onPointerMove={(event) => {
                      setHover(entry.weekday)
                      tip.show(event, WEEKDAYS[entry.weekday], [
                        { color: COLOR.series1, name: 'Average present', value: fmt1(entry.average) },
                        { name: 'Punches', value: fmtInt(entry.punches) },
                        { name: 'Days in range', value: fmtInt(entry.days) },
                      ])
                    }}
                    onPointerLeave={() => { setHover(null); tip.hide() }}
                  />
                </g>
              )
            })}

            <Baseline x1={0} x2={iw} y1={ih} y2={ih} />
          </>
        )
      }}
    </ChartFrame>
  )
}

export const weekdayTable = (view) => ({
  headers: ['Weekday', 'Average present', 'Total punches', 'Days in range'],
  rows: view.weekdayProfile.map((entry) => [
    WEEKDAYS[entry.weekday], fmt1(entry.average), fmtInt(entry.punches), fmtInt(entry.days),
  ]),
})
