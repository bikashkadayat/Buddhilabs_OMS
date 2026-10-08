/* CSV reports.
 *
 * Built here rather than server-side so that what downloads is exactly what is
 * on screen: same range, device, workday start, search box and sort order,
 * computed by the same analytics that drew the charts. A second implementation
 * in Python would be a second set of numbers to keep in agreement.
 */

import { WEEKDAYS, dayToISO, punchLabel, weekdayOf } from './time'
import { decimalHours, fmtClock, fmtSpan } from './format'

function csvCell(value) {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

const toCsv = (rows) => rows.map((row) => row.map(csvCell).join(',')).join('\r\n')

export function download(filename, csv) {
  // The BOM is what makes Excel read UTF-8 names correctly instead of mangling
  // them; every other tool ignores it.
  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const slug = (text) => text.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'employee'

export function reportFilename(kind, view, person) {
  const who = person ? `${slug(person.name)}-${slug(person.id)}_` : ''
  return `attendance-${kind}_${who}${dayToISO(view.fromDay)}_to_${dayToISO(view.toDay)}.csv`
}

/* People with no punches in the range never produced a session, so they are
   absent from view.employees entirely. "Nobody came" is exactly what an
   attendance report is often asked for, so this adds them back as explicit
   zero rows rather than leaving a silent gap. */
export function absentEmployees(payload, shown, query) {
  const needle = query.trim().toLowerCase()
  const seen = new Set(shown.map((e) => e.index))
  const out = []
  payload.employees.forEach((person, index) => {
    if (seen.has(index)) return
    if (needle && !person.name.toLowerCase().includes(needle) && !person.id.toLowerCase().includes(needle)) return
    out.push({
      index, id: person.id, name: person.name,
      days: 0, punches: 0, late: 0, rate: 0,
      medianArrival: null, medianDeparture: null, medianSpan: null,
      firstDay: null, lastDay: null, sessions: [],
    })
  })
  return out
}

export function summaryReport(view, employees) {
  const rows = [[
    'Employee ID', 'Name', 'Days present', 'Active days in range', 'Attendance %',
    'Median arrival', 'Median departure', 'Median day length', 'Median day length (hours)',
    'Late days', 'Late %', 'Punches', 'First seen', 'Last seen',
  ]]
  for (const emp of employees) {
    rows.push([
      emp.id, emp.name, emp.days, view.activeDayCount, Math.round(emp.rate * 100),
      // Blank, not the on-screen "--": a spreadsheet should see an empty cell
      // where there is no measurement.
      emp.medianArrival == null ? '' : fmtClock(emp.medianArrival),
      emp.medianDeparture == null ? '' : fmtClock(emp.medianDeparture),
      emp.medianSpan == null ? '' : fmtSpan(emp.medianSpan),
      decimalHours(emp.medianSpan),
      emp.late, emp.days ? Math.round((emp.late / emp.days) * 100) : 0,
      emp.punches,
      emp.firstDay == null ? '' : dayToISO(emp.firstDay),
      emp.lastDay == null ? '' : dayToISO(emp.lastDay),
    ])
  }
  return toCsv(rows)
}

export function dailyReport(employees, lateMinutes) {
  const rows = [[
    'Date', 'Weekday', 'Employee ID', 'Name', 'First punch', 'Last punch',
    'Day length', 'Day length (hours)', 'Punches', 'Late',
  ]]
  const flat = []
  for (const emp of employees) for (const session of emp.sessions) flat.push({ emp, session })
  flat.sort((a, b) => a.session.day - b.session.day || a.emp.name.localeCompare(b.emp.name))

  for (const { emp, session } of flat) {
    // A single punch gives no measurable span -- leave it blank rather than
    // report a day that lasted zero minutes.
    const span = session.count > 1 ? session.last - session.first : null
    rows.push([
      dayToISO(session.day), WEEKDAYS[weekdayOf(session.day)], emp.id, emp.name,
      fmtClock(session.first), fmtClock(session.last),
      span == null ? '' : fmtSpan(span), decimalHours(span),
      session.count, session.first > lateMinutes ? 'yes' : 'no',
    ])
  }
  return toCsv(rows)
}

/* One row per employee per calendar week (Sunday start) — the "how did this
   person attend, week by week" export behind the drawer's weekly view. */
export function weeklyReport(employees, lateMinutes) {
  const rows = [[
    'Week start', 'Week end', 'Employee ID', 'Name',
    'Days present', 'Days late', 'Avg arrival', 'Total hours', 'Total hours (decimal)',
  ]]
  const flat = []
  for (const emp of employees) {
    const byWeek = new Map()
    for (const session of emp.sessions) {
      const weekStart = session.day - weekdayOf(session.day) // back up to Sunday
      let wk = byWeek.get(weekStart)
      if (!wk) byWeek.set(weekStart, (wk = { weekStart, sessions: [] }))
      wk.sessions.push(session)
    }
    for (const wk of byWeek.values()) flat.push({ emp, wk })
  }
  flat.sort((a, b) => a.wk.weekStart - b.wk.weekStart || a.emp.name.localeCompare(b.emp.name))

  for (const { emp, wk } of flat) {
    const present = wk.sessions
    const arrivals = present.map((s) => s.first)
    const avgIn = arrivals.length ? Math.round(arrivals.reduce((a, b) => a + b, 0) / arrivals.length) : null
    // Single-punch days have no measurable span, so they add nothing to hours.
    const totalMin = present.reduce((sum, s) => sum + (s.count > 1 ? s.last - s.first : 0), 0)
    const late = present.filter((s) => s.first > lateMinutes).length
    rows.push([
      dayToISO(wk.weekStart), dayToISO(wk.weekStart + 6), emp.id, emp.name,
      present.length, late,
      avgIn == null ? '' : fmtClock(avgIn),
      totalMin ? fmtSpan(totalMin) : '', decimalHours(totalMin || null),
    ])
  }
  return toCsv(rows)
}

export function punchReport(employees, devices) {
  const rows = [['Date', 'Time', 'Employee ID', 'Name', 'Punch code', 'Punch type', 'Device', 'Source']]
  const flat = []
  for (const emp of employees) {
    for (const session of emp.sessions) {
      for (const punch of session.punches) flat.push({ emp, day: session.day, punch })
    }
  }
  flat.sort(
    (a, b) => (a.day * 1440 + a.punch.minute) - (b.day * 1440 + b.punch.minute)
      || a.emp.name.localeCompare(b.emp.name),
  )

  for (const { emp, day, punch } of flat) {
    rows.push([
      dayToISO(day), fmtClock(punch.minute), emp.id, emp.name,
      punch.punch, punchLabel(punch.punch),
      devices[punch.device] || '', punch.source === 1 ? 'LIVE' : 'HISTORY',
    ])
  }
  return toCsv(rows)
}
