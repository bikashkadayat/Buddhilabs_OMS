import { useState } from 'react'
import { fmtClock, fmtInt, fmtSpan } from '../../lib/format'
import { MONTHS, WEEKDAYS, dayToDate, fmtDay, weekdayOf } from '../../lib/time'
import { useMeasure } from '../../hooks/useMeasure'
import { useTooltip } from '../Tooltip'
import { COLOR, SEQ_STEPS, ScaleLegend, EmptyChart } from './common'

const CELL = 15
const ROW = 20
const LEFT = 136
const TOP = 34
export const REGISTER_MAX_DAYS = 92

/* The attendance register: one row per person, one column per day, shaded by
 * how long they were in.
 *
 * This is the view the aggregate charts can't give -- who was missing on the
 * 14th, who works a short Friday, which absences line up across the team. The
 * calendar shows the total was low; only this shows whose row is empty.
 */
export function RegisterGrid({ rows, fromDay, toDay }) {
  const tip = useTooltip()
  const [ref, width] = useMeasure()
  const [hover, setHover] = useState(null)

  if (!rows.length) return <EmptyChart message="No employees in this range." />

  const lastDay = toDay
  const firstDay = Math.max(fromDay, lastDay - (REGISTER_MAX_DAYS - 1))
  const truncated = firstDay > fromDay
  const days = []
  for (let day = firstDay; day <= lastDay; day++) days.push(day)

  // Longest day in view sets the top of the ramp, so the shading uses its full
  // range whatever the range's own scale happens to be.
  let maxSpan = 0
  const byEmployee = rows.map((employee) => {
    const sessions = new Map()
    for (const session of employee.sessions) {
      if (session.day < firstDay || session.day > lastDay) continue
      sessions.set(session.day, session)
      const span = session.count > 1 ? session.last - session.first : 0
      if (span > maxSpan) maxSpan = span
    }
    return { employee, sessions }
  })

  const w = Math.max(width || 360, LEFT + days.length * CELL + 12)
  const height = TOP + rows.length * ROW + 10

  const monthLabels = []
  let lastMonth = -1
  days.forEach((day, i) => {
    const month = dayToDate(day).getUTCMonth()
    if (month !== lastMonth) {
      lastMonth = month
      monthLabels.push({ i, label: `${MONTHS[month]} ${dayToDate(day).getUTCFullYear()}` })
    }
  })

  /* Shading is by hours present, with a floor of step 1 -- a present day must
     never be indistinguishable from an absent one just because it was short. */
  const shadeOf = (session) => {
    if (!session) return null
    if (session.count < 2 || !maxSpan) return 2
    const span = session.last - session.first
    return Math.min(SEQ_STEPS - 1, Math.max(1, Math.round((span / maxSpan) * (SEQ_STEPS - 1))))
  }

  return (
    <>
      <div className="chart register-scroll" ref={ref}>
        {width > 0 ? (
          <svg viewBox={`0 0 ${w} ${height}`} width={w} height={height}>
            <g transform={`translate(${LEFT},${TOP})`}>
              {monthLabels.map(({ i, label }) => (
                <text className="axis-text strong" key={label} x={i * CELL} y={-22}>{label}</text>
              ))}

              {days.map((day, i) => (
                // Day-of-month every third column; the weekday initial under it
                // is what makes weekend gaps legible at a glance.
                i % 3 === 0 ? (
                  <text className="axis-text" key={day} x={i * CELL + CELL / 2} y={-6} textAnchor="middle">
                    {dayToDate(day).getUTCDate()}
                  </text>
                ) : null
              ))}

              {byEmployee.map(({ employee, sessions }, row) => (
                <g key={employee.index}>
                  <text className="label-text" x={-LEFT + 4} y={row * ROW + ROW / 2 + 4}>
                    {employee.name.length > 16 ? `${employee.name.slice(0, 15)}…` : employee.name}
                  </text>

                  {days.map((day, i) => {
                    const session = sessions.get(day)
                    const shade = shadeOf(session)
                    const key = `${employee.index}-${day}`
                    return (
                      <rect
                        key={day}
                        x={i * CELL + 1} y={row * ROW + 2}
                        width={CELL - 2} height={ROW - 4} rx={2}
                        style={{
                          fill: shade == null ? 'var(--absent)' : COLOR.seq(shade),
                          stroke: hover === key ? COLOR.ink : 'none',
                          strokeWidth: 1,
                        }}
                        onPointerMove={(event) => {
                          setHover(key)
                          tip.show(event, `${employee.name} · ${fmtDay(day)} ${WEEKDAYS[weekdayOf(day)]}`, session
                            ? [
                              { name: 'First', value: fmtClock(session.first) },
                              { name: 'Last', value: fmtClock(session.last) },
                              { name: 'Length', value: session.count > 1 ? fmtSpan(session.last - session.first) : '--' },
                              { name: 'Punches', value: fmtInt(session.count) },
                            ]
                            : [{ name: 'No punches', value: 'absent' }])
                        }}
                        onPointerLeave={() => { setHover(null); tip.hide() }}
                      />
                    )
                  })}
                </g>
              ))}
            </g>
          </svg>
        ) : null}
      </div>

      <div className="register-legend">
        <span className="scale-legend">
          <span className="scale-swatch absent" />
          <span>absent</span>
        </span>
        <ScaleLegend label="Time on site" max={maxSpan} format={fmtSpan} />
      </div>

      {truncated ? (
        <p className="muted note-line">
          {`Showing the most recent ${REGISTER_MAX_DAYS} days of the selected range — a wider grid stops being readable. The other tabs cover all of it.`}
        </p>
      ) : null}
    </>
  )
}

export const registerTable = (rows, fromDay, toDay) => {
  const firstDay = Math.max(fromDay, toDay - (REGISTER_MAX_DAYS - 1))
  const days = []
  for (let day = firstDay; day <= toDay; day++) days.push(day)

  return {
    headers: ['Employee', ...days.map((day) => fmtDay(day, false))],
    rows: rows.map((employee) => {
      const sessions = new Map(employee.sessions.map((session) => [session.day, session]))
      return [
        `${employee.name} #${employee.id}`,
        ...days.map((day) => {
          const session = sessions.get(day)
          if (!session) return '—'
          return session.count > 1 ? fmtSpan(session.last - session.first) : fmtClock(session.first)
        }),
      ]
    }),
  }
}
