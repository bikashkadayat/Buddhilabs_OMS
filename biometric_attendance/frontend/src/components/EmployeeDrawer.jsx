import { useEffect } from 'react'
import { fmtClock, fmtInt, fmtPct, fmtSpan } from '../lib/format'
import { WEEKDAYS, fmtDay, punchLabel, weekdayOf } from '../lib/time'
import { DaySpansChart } from './charts/DaySpansChart'
import { WeeklyAttendance } from './WeeklyAttendance'
import { Popover } from './ui'

/* A drawer rather than another section on the page: the detail is about one
 * person, and appending it below thirty other rows meant the reader had to
 * scroll away from the row they clicked to see the answer.
 */
export function EmployeeDrawer({ employee, person, view, lateMinutes, onClose, onExport }) {
  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!employee) return null

  // Values stay short so every tile is one line and the row keeps its rhythm;
  // the qualifier goes underneath rather than wrapping the number.
  const tiles = [
    { label: 'Days present', value: fmtInt(employee.days), sub: `of ${fmtInt(view.activeDayCount)} active days` },
    { label: 'Attendance', value: fmtPct(employee.rate) },
    { label: 'Late days', value: fmtInt(employee.late), sub: fmtPct(employee.days ? employee.late / employee.days : 0) },
    { label: 'Usually arrives', value: fmtClock(employee.medianArrival) },
    { label: 'Usually leaves', value: fmtClock(employee.medianDeparture) },
    { label: 'Hours a day', value: fmtSpan(employee.medianSpan) },
  ]

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`${person.name} detail`}>
        <header className="drawer-head">
          <div>
            <h2>{person.name}</h2>
            <p className="muted">
              {`id ${person.id} · ${fmtInt(employee.punches)} punches · ${fmtDay(employee.firstDay)} – ${fmtDay(employee.lastDay)}`}
              {person.enrolled ? '' : ' · not in the device roster'}
            </p>
          </div>
          <div className="head-actions">
            <Popover label="Download">
              {(close) => (
                <div className="popover-menu">
                  <button type="button" onClick={() => { onExport('weekly'); close() }}>Weekly summary…</button>
                  <button type="button" onClick={() => { onExport('daily'); close() }}>Daily detail…</button>
                  <button type="button" onClick={() => { onExport('punches'); close() }}>Punch log…</button>
                  <p className="popover-note">{`CSV for ${person.name} only, over the selected range.`}</p>
                </div>
              )}
            </Popover>
            <button type="button" className="ghost-button" onClick={onClose}>Close</button>
          </div>
        </header>

        <div className="drawer-body">
          <div className="detail-kpis">
            {tiles.map((tile) => (
              <div key={tile.label}>
                <p className="k">{tile.label}</p>
                <p className="v">{tile.value}</p>
                {tile.sub ? <p className="s">{tile.sub}</p> : null}
              </div>
            ))}
          </div>

          <h3>Week by week</h3>
          <p className="muted">
            Green = on time, amber = late, grey = no scan. Hover a day for its times. Newest week first.
          </p>
          <WeeklyAttendance employee={employee} lateMinutes={lateMinutes} />

          <h3>Daily span</h3>
          <p className="muted">
            {`Each bar runs from the first punch to the last punch of that day. Most recent ${Math.min(90, employee.sessions.length)} days in range.`}
          </p>
          <DaySpansChart employee={employee} lateMinutes={lateMinutes} />

          <h3>Every day</h3>
          <div className="table-scroll capped">
            <table>
              <thead>
                <tr>
                  <th className="left">Date</th>
                  <th>First</th>
                  <th>Last</th>
                  <th>Span</th>
                  <th>Punches</th>
                  <th className="left">Sequence</th>
                </tr>
              </thead>
              <tbody>
                {employee.sessions.slice().reverse().map((session) => (
                  <tr key={session.day}>
                    <td className="left">{`${fmtDay(session.day)} · ${WEEKDAYS[weekdayOf(session.day)]}`}</td>
                    <td className={session.first > lateMinutes ? 'flag' : undefined}>{fmtClock(session.first)}</td>
                    <td>{fmtClock(session.last)}</td>
                    <td>{session.count > 1 ? fmtSpan(session.last - session.first) : '--'}</td>
                    <td>{fmtInt(session.count)}</td>
                    <td className="left dim">
                      {session.punches.slice(0, 6)
                        .map((p) => `${fmtClock(p.minute)} ${punchLabel(p.punch).toLowerCase()}`)
                        .join(' · ')}
                      {session.punches.length > 6 ? ' …' : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </aside>
    </>
  )
}
