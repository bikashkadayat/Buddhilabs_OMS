/* One employee's attendance grouped into calendar weeks — the quickest way to
 * answer "how has this person been coming in lately?". Each week is a row of
 * seven day-cells (Sun–Sat): green = on time, amber = late, grey = no scan,
 * with a one-line plain summary underneath. Newest week first.
 *
 * Reads the same per-day sessions computeView() already built, so it inherits
 * the toolbar's date range (e.g. the last 90 days ≈ 13 weeks).
 */
import { fmtClock, fmtInt, fmtSpan } from '../lib/format'
import { WEEKDAYS, dayToDate, fmtDay, weekdayOf } from '../lib/time'

// Bucket day-sessions into weeks keyed by their Sunday, newest week first.
function toWeeks(sessions) {
  const byWeek = new Map()
  for (const s of sessions) {
    const weekStart = s.day - weekdayOf(s.day) // back up to Sunday
    let wk = byWeek.get(weekStart)
    if (!wk) byWeek.set(weekStart, (wk = { weekStart, days: new Map() }))
    wk.days.set(s.day, s)
  }
  return [...byWeek.values()].sort((a, b) => b.weekStart - a.weekStart)
}

const dayNum = (day) => dayToDate(day).getUTCDate()
const mean = (nums) => (nums.length ? Math.round(nums.reduce((a, b) => a + b, 0) / nums.length) : null)

export function WeeklyAttendance({ employee, lateMinutes }) {
  const weeks = toWeeks(employee.sessions)
  if (!weeks.length) return <p className="muted">No attendance in the selected period.</p>

  return (
    <div className="weekly">
      {weeks.map((wk) => {
        const present = [...wk.days.values()]
        const avgIn = mean(present.map((s) => s.first))
        const totalMin = present.reduce((sum, s) => sum + (s.count > 1 ? s.last - s.first : 0), 0)
        const late = present.filter((s) => s.first > lateMinutes).length

        return (
          <div key={wk.weekStart} className="week-row">
            <div className="week-head">
              <span className="week-range">{fmtDay(wk.weekStart, false)} – {fmtDay(wk.weekStart + 6)}</span>
              <span className={`week-count ${present.length >= 5 ? 'ok' : present.length ? 'mid' : 'none'}`}>
                {present.length} of 7 days
              </span>
            </div>

            <div className="week-days">
              {Array.from({ length: 7 }, (_, i) => {
                const day = wk.weekStart + i
                const s = wk.days.get(day)
                const state = !s ? 'absent' : s.first > lateMinutes ? 'late' : 'present'
                const title = s
                  ? `${fmtDay(day)} · in ${fmtClock(s.first)}${s.count > 1 ? ` · out ${fmtClock(s.last)}` : ''}${s.first > lateMinutes ? ' · late' : ''}`
                  : `${fmtDay(day)} · absent`
                return (
                  <div key={i} className={`day-cell ${state}`} title={title}>
                    <span className="dc-wd">{WEEKDAYS[weekdayOf(day)]}</span>
                    <span className="dc-num">{dayNum(day)}</span>
                    <span className="dc-time">{s ? fmtClock(s.first) : '—'}</span>
                  </div>
                )
              })}
            </div>

            <p className="week-facts muted">
              {present.length ? (
                <>
                  Usually in <b>{fmtClock(avgIn)}</b>
                  {totalMin ? <> · <b>{fmtSpan(totalMin)}</b> at the office</> : null}
                  {late ? <> · <span className="flag">{fmtInt(late)} late</span></> : <> · <span className="on-time">all on time</span></>}
                </>
              ) : 'No attendance this week'}
            </p>
          </div>
        )
      })}
    </div>
  )
}
