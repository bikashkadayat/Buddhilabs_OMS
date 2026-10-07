import { useState } from 'react'
import { fmt1, fmtInt, ticks } from '../../lib/format'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart, YAxis, roundedColumn } from './common'

const bucketLabel = (hour) => (hour === 12 ? '12h+' : `${hour}–${hour + 1}h`)

/* Distribution of day lengths. The median tile says the typical day is ~8h;
 * this says whether that is a tight cluster or an average of half-days and
 * double shifts, which is a different management problem.
 */
export function DayLengthChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  const total = view.lengthHist.reduce((a, b) => a + b, 0)
  if (!total) return <EmptyChart message="No days with more than one punch in this range." />

  const yTicks = ticks(Math.max(...view.lengthHist))
  const top = yTicks[yTicks.length - 1]

  return (
    <>
      <ChartFrame height={230} margin={{ left: 40, right: 12, top: 16, bottom: 30 }}>
        {({ iw, ih }) => {
          const band = iw / view.lengthHist.length
          const barWidth = Math.min(24, band - 6)
          const y = (value) => ih - (value / top) * ih

          return (
            <>
              <YAxis ticks={yTicks} scale={y} width={iw} format={fmtInt} />

              {view.lengthHist.map((count, hour) => {
                const x = hour * band + (band - barWidth) / 2
                const height = count ? Math.max(2, ih - y(count)) : 0
                return (
                  <g key={hour}>
                    {height ? (
                      <path
                        d={roundedColumn(x, ih - height, barWidth, height)}
                        className={hover === hour ? 'mark-hover' : undefined}
                        style={{ fill: COLOR.series1 }}
                      />
                    ) : null}
                    {hour % 2 === 0 ? (
                      <text className="axis-text" x={hour * band + band / 2} y={ih + 18} textAnchor="middle">
                        {hour === 12 ? '12+' : hour}
                      </text>
                    ) : null}

                    <rect
                      className="hit" x={hour * band} y={0} width={band} height={ih}
                      onPointerMove={(event) => {
                        setHover(hour)
                        tip.show(event, bucketLabel(hour), [
                          { color: COLOR.series1, name: 'Employee-days', value: fmtInt(count) },
                          { name: 'Share', value: `${fmt1((count / total) * 100)}%` },
                        ])
                      }}
                      onPointerLeave={() => { setHover(null); tip.hide() }}
                    />
                  </g>
                )
              })}

              <text className="axis-text" x={iw / 2} y={ih + 30} textAnchor="middle">hours between first and last punch</text>
              <Baseline x1={0} x2={iw} y1={ih} y2={ih} />
            </>
          )
        }}
      </ChartFrame>

      {view.singlePunchDays ? (
        <p className="muted note-line">
          {`${fmtInt(view.singlePunchDays)} employee-days had a single punch and no measurable length — they are excluded here.`}
        </p>
      ) : null}
    </>
  )
}

export const dayLengthTable = (view) => {
  const total = view.lengthHist.reduce((a, b) => a + b, 0)
  return {
    headers: ['Day length', 'Employee-days', 'Share'],
    rows: view.lengthHist.map((count, hour) => [
      bucketLabel(hour), fmtInt(count), `${fmt1(total ? (count / total) * 100 : 0)}%`,
    ]),
  }
}
