/* Time handling.
 *
 * The API sends each punch as seconds since the epoch with the device's WALL
 * CLOCK encoded as if it were UTC (see morx/web/dataset.py). So every reader
 * here uses getUTC* or plain arithmetic -- getHours() would shift a 09:05 punch
 * by the viewer's timezone, which is exactly wrong for attendance.
 */

export const DAY = 86400
export const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const PUNCH_LABELS = {
  0: 'Check in',
  1: 'Check out',
  2: 'Break out',
  3: 'Break in',
  4: 'Overtime in',
  5: 'Overtime out',
}

export const punchLabel = (code) => PUNCH_LABELS[code] || `Code ${code}`

export const dayOf = (t) => Math.floor(t / DAY)
export const minutesOf = (t) => Math.floor((t - dayOf(t) * DAY) / 60)

/** 0 = Sunday. Epoch day 0 (1 Jan 1970) was a Thursday. */
export const weekdayOf = (day) => (((day % 7) + 7) + 4) % 7

export const dayToDate = (day) => new Date(day * DAY * 1000)
export const dayToISO = (day) => dayToDate(day).toISOString().slice(0, 10)
export const isoToDay = (iso) => Math.floor(Date.parse(`${iso}T00:00:00Z`) / 1000 / DAY)
export const todayDay = () => Math.floor(Date.now() / 1000 / DAY)

export function fmtDay(day, withYear = true) {
  const d = dayToDate(day)
  const base = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`
  return withYear ? `${base} ${d.getUTCFullYear()}` : base
}

export function fmtDayRange(fromDay, toDay) {
  const a = dayToDate(fromDay)
  const b = dayToDate(toDay)
  const sameYear = a.getUTCFullYear() === b.getUTCFullYear()
  return `${fmtDay(fromDay, !sameYear)} – ${fmtDay(toDay)}`
}
