import { useState } from 'react'
import { fmtInt } from '../../lib/format'
import { MONTHS, WEEKDAYS, dayToDate, fmtDay, weekdayOf } from '../../lib/time'
import { useMeasure } from '../../hooks/useMeasure'
import { useTooltip } from '../Tooltip'
import { COLOR, EmptyChart, ScaleLegend, seqStep } from './common'

const CELL = 13
const LEFT = 30
const MAX_DAYS = 371   // past ~53 weeks a day-grid calendar stops being readable

export function CalendarChart({ view }) {
  const tip = useTooltip()
  const [ref, width] = useMeasure()
  const [hover, setHover] = useState(null)

  if (!view.dayList.length) return <EmptyChart message="No punches in this range." />

  const present = new Map(view.dayList.map((d) => [d.day, d]))
  const lastDay = view.toDay
  const firstDay = Math.max(view.fromDay, lastDay - (MAX_DAYS - 1))
  const truncated = firstDay > view.fromDay

  // Start on the Sunday at or before the first day, so rows are weekdays.
  const gridStart = firstDay - weekdayOf(firstDay)
  const weeks = Math.ceil((lastDay - gridStart + 1) / 7)
  const max = Math.max(...view.dayList.map((d) => d.present))
  const svgWidth = Math.max(width || 320, LEFT + weeks * CELL + 8)
  const height = 7 * CELL + 26

  const monthLabels = []
  let lastMonth = -1
  for (let w = 0; w < weeks; w++) {
    const month = dayToDate(gridStart + w * 7 + 3).getUTCMonth()
    if (month !== lastMonth) {
      lastMonth = month
      monthLabels.push({ w, label: MONTHS[month] })
    }
  }

  return (
    <>
      <div className="chart calendar-scroll" ref={ref}>
        {width > 0 ? (
          <svg viewBox={`0 0 ${svgWidth} ${height}`} width={svgWidth} height={height}>
            <g transform={`translate(${LEFT},18)`}>
              {[1, 3, 5].map((d) => (
                <text className="axis-text" key={d} x={-6} y={d * CELL + 9} textAnchor="end">{WEEKDAYS[d]}</text>
              ))}
              {monthLabels.map(({ w, label }) => (
                <text className="axis-text" key={`${w}-${label}`} x={w * CELL} y={-6}>{label}</text>
              ))}

              {Array.from({ length: weeks }, (_, w) => Array.from({ length: 7 }, (_, d) => {
                const day = gridStart + w * 7 + d
                if (day < firstDay || day > lastDay) return null
                const entry = present.get(day)
                const value = entry ? entry.present : 0
                return (
                  <rect
                    key={day}
                    x={w * CELL + 1} y={d * CELL + 1} width={CELL - 2} height={CELL - 2} rx={2}
                    style={{
                      fill: COLOR.seq(seqStep(value, max)),
                      stroke: hover === day ? COLOR.ink : 'none',
                      strokeWidth: 1,
                    }}
                    onPointerMove={(event) => {
                      setHover(day)
                      tip.show(event, `${WEEKDAYS[d]} ${fmtDay(day)}`, entry
                        ? [
                          { name: 'People present', value: fmtInt(entry.present) },
                          { name: 'Punches', value: fmtInt(entry.punches) },
                        ]
                        : [{ name: 'People present', value: 'none' }])
                    }}
                    onPointerLeave={() => { setHover(null); tip.hide() }}
                  />
                )
              }))}
            </g>
          </svg>
        ) : null}
      </div>

      <ScaleLegend label="People present" max={max} format={fmtInt} />
      {truncated ? (
        <p className="muted note-line">
          {`Showing the most recent ${MAX_DAYS} days of the selected range; the other charts cover all of it.`}
        </p>
      ) : null}
    </>
  )
}

export const calendarTable = (view) => ({
  headers: ['Date', 'Weekday', 'People present', 'Punches'],
  rows: view.dayList.slice().reverse()
    .map((d) => [fmtDay(d.day), WEEKDAYS[weekdayOf(d.day)], fmtInt(d.present), fmtInt(d.punches)]),
})
