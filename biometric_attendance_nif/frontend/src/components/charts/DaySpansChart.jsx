import { useState } from 'react'
import { fmtClock, fmtInt, fmtSpan } from '../../lib/format'
import { WEEKDAYS, fmtDay, weekdayOf } from '../../lib/time'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart } from './common'

const MAX_DAYS = 90

/* Each column runs from the day's first punch to its last, against a
 * time-of-day axis that reads downward like a day planner. The workday-start
 * line is the same threshold the "late" counts use, so the reader can see what
 * is being measured rather than take the number on trust.
 */
export function DaySpansChart({ employee, lateMinutes }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  const sessions = employee.sessions.slice(-MAX_DAYS)
  if (!sessions.length) return <EmptyChart message="No days in this range." />

  return (
    <ChartFrame height={260} margin={{ left: 44, right: 12, top: 12, bottom: 30 }}>
      {({ iw, ih }) => {
        const band = iw / sessions.length
        const barWidth = Math.min(14, Math.max(3, band - 3))
        const y = (minute) => (minute / 1440) * ih
        const labelEvery = Math.max(1, Math.ceil(sessions.length / 8))

        return (
          <>
            {[0, 4, 8, 12, 16, 20, 24].map((hour) => (
              <g key={hour}>
                <line className="gridline" x1={0} x2={iw} y1={y(hour * 60)} y2={y(hour * 60)} />
                <text className="axis-text" x={-8} y={y(hour * 60) + 4} textAnchor="end">
                  {`${String(hour).padStart(2, '0')}:00`}
                </text>
              </g>
            ))}

            <line
              x1={0} x2={iw} y1={y(lateMinutes)} y2={y(lateMinutes)}
              strokeWidth={1.5} strokeOpacity={0.7} style={{ stroke: COLOR.series2 }}
            />
            {/* paint-order gives the label a surface-coloured halo, so it stays
                legible wherever it lands on the rule or a bar. */}
            <text
              className="axis-text strong" x={iw} y={y(lateMinutes) - 7} textAnchor="end"
              style={{ paintOrder: 'stroke', stroke: COLOR.surface, strokeWidth: 3, strokeLinejoin: 'round' }}
            >
              {`day starts ${fmtClock(lateMinutes)}`}
            </text>

            {/* Drawn before the bars so the columns sit on top of the rule,
                not the other way round. */}

            {sessions.map((session, i) => {
              const x = i * band + (band - barWidth) / 2
              const topY = y(session.first)
              const height = Math.max(2, y(session.last) - topY)
              return (
                <g key={session.day}>
                  <rect
                    x={x} y={topY} width={barWidth} height={height}
                    rx={Math.min(3, barWidth / 2)}
                    className={hover === session.day ? 'mark-hover' : undefined}
                    style={{ fill: COLOR.series1 }}
                  />
                  {/* Every individual scan, so a day with four punches is
                      visibly different from a day with two. */}
                  {session.punches.map((punch, j) => (
                    <circle
                      key={j} cx={x + barWidth / 2} cy={y(punch.minute)} r={2.5} strokeWidth={1.5}
                      style={{ fill: COLOR.series1, stroke: COLOR.surface }}
                    />
                  ))}
                  {i % labelEvery === 0 || i === sessions.length - 1 ? (
                    <text className="axis-text" x={i * band + band / 2} y={ih + 18} textAnchor="middle">
                      {fmtDay(session.day, false)}
                    </text>
                  ) : null}
                  <rect
                    className="hit" x={i * band} y={0} width={band} height={ih}
                    onPointerMove={(event) => {
                      setHover(session.day)
                      tip.show(event, `${fmtDay(session.day)} · ${WEEKDAYS[weekdayOf(session.day)]}`, [
                        { color: COLOR.series1, name: 'First', value: fmtClock(session.first) },
                        { name: 'Last', value: fmtClock(session.last) },
                        { name: 'Span', value: session.count > 1 ? fmtSpan(session.last - session.first) : '--' },
                        { name: 'Punches', value: fmtInt(session.count) },
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
