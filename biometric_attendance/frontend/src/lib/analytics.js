/* Every number on the page comes out of computeView().
 *
 * One pass over the punch log produces the KPIs, every chart's series, and the
 * per-employee rows. If a component needs a figure that isn't in here, the fix
 * belongs in this file -- not in the component -- or two parts of the screen
 * will eventually disagree about the same thing.
 */

import { DAY, MONTHS, dayOf, dayToDate, fmtDay, minutesOf, weekdayOf } from './time'

export function median(sorted) {
  if (!sorted.length) return null
  const mid = sorted.length >> 1
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/** Resolve the filter's range to [fromDay, toDay] over the loaded data. */
export function resolveRange(payload, filters, today) {
  const t = payload?.records?.t
  if (!t || !t.length) return null

  const firstDay = dayOf(t[0])
  const lastDay = dayOf(t[t.length - 1])

  /* Relative ranges hang off the newest *plausible* day, not the newest row.
     A terminal that lost its clock writes punches years ahead (this data has
     some), and anchoring to those makes "last 90 days" a window into 2033.
     Anchoring to today is wrong the other way when the collector has been down
     for months -- so take whichever of the two is earlier. */
  const anchor = Math.min(lastDay, today)

  if (filters.range === 'all') return [firstDay, lastDay]
  if (filters.range === 'ytd') {
    const year = dayToDate(anchor).getUTCFullYear()
    return [Math.floor(Date.parse(`${year}-01-01T00:00:00Z`) / 1000 / DAY), anchor]
  }
  if (filters.range === 'custom') {
    const a = filters.from ? Math.floor(Date.parse(`${filters.from}T00:00:00Z`) / 1000 / DAY) : firstDay
    const b = filters.to ? Math.floor(Date.parse(`${filters.to}T00:00:00Z`) / 1000 / DAY) : lastDay
    return a <= b ? [a, b] : [b, a]
  }
  return [anchor - (parseInt(filters.range, 10) - 1), anchor]
}

export function computeView(payload, filters, today) {
  const range = resolveRange(payload, filters, today)
  if (!range) return null

  const [fromDay, toDay] = range
  const rec = payload.records
  const deviceIdx = filters.device === '' ? -1 : parseInt(filters.device, 10)
  const lateMinutes = filters.lateMinutes

  // (employee, day) -> that person's day. Keyed numerically; employee counts
  // are in the tens, so a shift by 2^13 leaves plenty of headroom.
  const sessions = new Map()
  const dayPunches = new Map()
  const hourWeekday = Array.from({ length: 7 }, () => new Array(24).fill(0))
  const punchMix = new Map()
  let total = 0
  let live = 0

  for (let i = 0; i < rec.t.length; i++) {
    const t = rec.t[i]
    const day = dayOf(t)
    if (day < fromDay || day > toDay) continue
    if (deviceIdx >= 0 && rec.d[i] !== deviceIdx) continue

    total++
    if (rec.s[i] === 1) live++

    const minute = minutesOf(t)
    hourWeekday[weekdayOf(day)][Math.floor(minute / 60)]++
    punchMix.set(rec.p[i], (punchMix.get(rec.p[i]) || 0) + 1)
    dayPunches.set(day, (dayPunches.get(day) || 0) + 1)

    const key = day * 8192 + rec.e[i]
    let session = sessions.get(key)
    if (!session) {
      session = { day, employee: rec.e[i], first: minute, last: minute, count: 0, punches: [] }
      sessions.set(key, session)
    }
    if (minute < session.first) session.first = minute
    if (minute > session.last) session.last = minute
    session.count++
    // device/source ride along only so the punch-log export can reproduce the
    // source rows faithfully; nothing on screen reads them.
    session.punches.push({ minute, punch: rec.p[i], device: rec.d[i], source: rec.s[i] })
  }

  // Days on which *anyone* punched. This is the denominator for attendance
  // rate -- calendar days would be wrong, because nobody punches on a holiday.
  const activeDays = new Map()
  const perEmployee = new Map()

  for (const session of sessions.values()) {
    activeDays.set(session.day, (activeDays.get(session.day) || 0) + 1)

    let emp = perEmployee.get(session.employee)
    if (!emp) {
      emp = {
        index: session.employee,
        days: 0, punches: 0, late: 0,
        arrivals: [], departures: [], spans: [],
        firstDay: session.day, lastDay: session.day, lastSeen: 0, sessions: [],
      }
      perEmployee.set(session.employee, emp)
    }
    emp.days++
    emp.punches += session.count
    emp.arrivals.push(session.first)
    emp.departures.push(session.last)
    emp.spans.push(session.last - session.first)
    if (session.first > lateMinutes) emp.late++
    emp.firstDay = Math.min(emp.firstDay, session.day)
    emp.lastDay = Math.max(emp.lastDay, session.day)
    emp.lastSeen = Math.max(emp.lastSeen, session.day * DAY + session.last * 60)
    session.punches.sort((a, b) => a.minute - b.minute)
    emp.sessions.push(session)
  }

  const activeDayCount = activeDays.size
  const employees = []
  for (const emp of perEmployee.values()) {
    const person = payload.employees[emp.index]
    emp.arrivals.sort((a, b) => a - b)
    emp.departures.sort((a, b) => a - b)
    emp.spans.sort((a, b) => a - b)
    emp.sessions.sort((a, b) => a.day - b.day)
    employees.push(Object.assign(emp, {
      id: person.id,
      name: person.name,
      enrolled: person.enrolled,
      medianArrival: median(emp.arrivals),
      medianDeparture: median(emp.departures),
      medianSpan: median(emp.spans),
      rate: activeDayCount ? emp.days / activeDayCount : 0,
    }))
  }

  const dayList = [...activeDays.keys()].sort((a, b) => a - b)
    .map((day) => ({ day, present: activeDays.get(day), punches: dayPunches.get(day) || 0 }))

  const allArrivals = []
  const allDepartures = []
  const allSpans = []
  const arrivalHist = new Array(24).fill(0)
  const departureHist = new Array(24).fill(0)
  for (const session of sessions.values()) {
    allArrivals.push(session.first)
    allDepartures.push(session.last)
    allSpans.push(session.last - session.first)
    arrivalHist[Math.floor(session.first / 60)]++
    departureHist[Math.floor(session.last / 60)]++
  }
  allArrivals.sort((a, b) => a - b)
  allDepartures.sort((a, b) => a - b)
  allSpans.sort((a, b) => a - b)

  /* How many people are on site during each hour: an employee-day counts for
     every hour between its first and last punch. This is the shape of the
     working day itself, which the arrival/departure histograms only imply. */
  const occupancy = new Array(24).fill(0)
  for (const session of sessions.values()) {
    for (let hour = Math.floor(session.first / 60); hour <= Math.floor(session.last / 60); hour++) {
      occupancy[hour]++
    }
  }

  /* Attendance per weekday, over CALENDAR days in range rather than active
     ones -- a Saturday nobody comes in has an average of zero, and hiding that
     would make every weekday look equally busy. */
  const weekdayCalendar = new Array(7).fill(0)
  for (let day = fromDay; day <= toDay; day++) weekdayCalendar[weekdayOf(day)]++
  const weekdayPresent = new Array(7).fill(0)
  const weekdayPunches = new Array(7).fill(0)
  for (const d of dayList) {
    weekdayPresent[weekdayOf(d.day)] += d.present
    weekdayPunches[weekdayOf(d.day)] += d.punches
  }
  const weekdayProfile = weekdayCalendar.map((days, weekday) => ({
    weekday,
    days,
    present: weekdayPresent[weekday],
    punches: weekdayPunches[weekday],
    average: days ? weekdayPresent[weekday] / days : 0,
  }))

  // Day-length histogram, one bucket per hour, everything past 12h in the last.
  const lengthHist = new Array(13).fill(0)
  let singlePunchDays = 0
  for (const session of sessions.values()) {
    if (session.count < 2) { singlePunchDays++; continue }   // no measurable length
    lengthHist[Math.min(12, Math.floor((session.last - session.first) / 60))]++
  }

  // Median arrival and departure per day, for the drift-over-time chart.
  const perDay = new Map()
  for (const session of sessions.values()) {
    let entry = perDay.get(session.day)
    if (!entry) perDay.set(session.day, (entry = { day: session.day, arrivals: [], departures: [] }))
    entry.arrivals.push(session.first)
    entry.departures.push(session.last)
  }
  const dailyTiming = [...perDay.values()].sort((a, b) => a.day - b.day).map((entry) => {
    entry.arrivals.sort((a, b) => a - b)
    entry.departures.sort((a, b) => a - b)
    return { day: entry.day, arrival: median(entry.arrivals), departure: median(entry.departures) }
  })

  const presentCounts = dayList.map((d) => d.present)
  const lateTotal = employees.reduce((sum, e) => sum + e.late, 0)

  return {
    occupancy,
    weekdayProfile,
    lengthHist,
    singlePunchDays,
    dailyTiming,
    fromDay, toDay, total, live,
    rangeDays: toDay - fromDay + 1,
    sessions: sessions.size,
    dayList,
    activeDayCount,
    employees,
    hourWeekday,
    punchMix: [...punchMix.entries()].sort((a, b) => b[1] - a[1]),
    arrivalHist,
    departureHist,
    medianArrival: median(allArrivals),
    medianDeparture: median(allDepartures),
    medianSpan: median(allSpans),
    avgPresent: presentCounts.length ? presentCounts.reduce((a, b) => a + b, 0) / presentCounts.length : 0,
    peakPresent: presentCounts.length ? Math.max(...presentCounts) : 0,
    lateShare: sessions.size ? lateTotal / sessions.size : 0,
    lateTotal,
  }
}

/* Daily resolution stops being readable past a few hundred points, so roll up
 * rather than draw a 4,000-point line nobody can read.
 *
 * `avg` fields are averaged over the days in the bucket, `sum` fields added.
 * The distinction matters: two people present on each of five days is an
 * average of 2, but a total of 40 punches.
 */
export function bucketDaily(rows, { avg = [], sum = [] } = {}) {
  const span = rows.length ? rows[rows.length - 1].day - rows[0].day : 0
  const unit = span <= 400 ? 'day' : span > 1200 ? 'month' : 'week'

  if (unit === 'day') {
    return {
      unit,
      points: rows.map((row) => {
        const point = { x: row.day, day: row.day, label: fmtDay(row.day), days: 1 }
        for (const field of [...avg, ...sum]) point[field] = row[field]
        return point
      }),
    }
  }

  const groups = new Map()
  for (const row of rows) {
    let key
    let label
    if (unit === 'month') {
      const date = dayToDate(row.day)
      key = date.getUTCFullYear() * 12 + date.getUTCMonth()
      label = `${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`
    } else {
      key = Math.floor(row.day / 7)
      label = `Week of ${fmtDay(key * 7)}`
    }

    let group = groups.get(key)
    if (!group) {
      groups.set(key, (group = { x: key, day: unit === 'week' ? key * 7 : row.day, label, days: 0, totals: {}, counts: {} }))
    }
    group.days++
    for (const field of [...avg, ...sum]) {
      // Skip nulls rather than counting them as zero -- a day with no median
      // arrival should not drag the bucket's average down.
      if (row[field] == null) continue
      group.totals[field] = (group.totals[field] || 0) + row[field]
      group.counts[field] = (group.counts[field] || 0) + 1
    }
  }

  const points = [...groups.values()].sort((a, b) => a.x - b.x).map((group) => {
    const point = { x: group.x, day: group.day, label: group.label, days: group.days }
    for (const field of avg) point[field] = group.counts[field] ? group.totals[field] / group.counts[field] : null
    for (const field of sum) point[field] = group.totals[field] || 0
    return point
  })
  return { unit, points }
}

export function bucketTrend(dayList) {
  const { unit, points } = bucketDaily(dayList, { avg: ['present'], sum: ['punches'] })
  return { unit, points: points.map((point) => ({ ...point, value: point.present })) }
}

export const SORTERS = {
  name: (e) => e.name.toLowerCase(),
  days: (e) => e.days,
  rate: (e) => e.rate,
  arrival: (e) => e.medianArrival ?? Infinity,
  departure: (e) => e.medianDeparture ?? -Infinity,
  hours: (e) => e.medianSpan ?? -Infinity,
  late: (e) => e.late,
  punches: (e) => e.punches,
  last: (e) => e.lastSeen,
}

/** The employee rows a table should show, in the order it should show them. */
export function visibleEmployees(view, query, sort) {
  const needle = query.trim().toLowerCase()
  const rows = view.employees.filter(
    (e) => !needle || e.name.toLowerCase().includes(needle) || e.id.toLowerCase().includes(needle),
  )
  const key = SORTERS[sort.key] || SORTERS.days
  return rows.sort((a, b) => {
    const va = key(a)
    const vb = key(b)
    if (va < vb) return -sort.dir
    if (va > vb) return sort.dir
    return a.name.localeCompare(b.name)
  })
}
