/* Four plain-language pictures of the team's attendance, sitting at the top of
 * the Summary tab. No axes, no medians, no "employee-days" — just labelled bars
 * anyone can read at a glance:
 *
 *   1. People here each day   — one bar per recent day, taller = busier.
 *   2. Busiest days of week   — average people in on Sun … Sat.
 *   3. When people arrive     — what time most check-ins happen.
 *
 * Every number comes from the same computeView() the rest of the page uses, so
 * nothing here can disagree with the cards below it.
 */
import { fmt1, fmtInt } from '../lib/format'
import { WEEKDAYS, fmtDay, weekdayOf } from '../lib/time'
import { Card, CardHead } from './ui'

// 12-hour clock label kept tiny for an axis tick: 9a, 12p, 5p.
function hourLabel(hour) {
  const period = hour < 12 ? 'a' : 'p'
  const base = hour % 12 === 0 ? 12 : hour % 12
  return `${base}${period}`
}

// A row of vertical columns — the simplest "distribution" picture. `bars` is a
// list of { key, value, x, title, highlight }; the tallest sets the scale.
function Columns({ bars, height = 96 }) {
  const max = Math.max(1, ...bars.map((b) => b.value))
  return (
    <div className="mcols" style={{ height }} role="img" aria-label="bar chart">
      {bars.map((b) => (
        <div className="mcol" key={b.key} title={b.title}>
          <span
            className="mcol-bar"
            style={{
              height: b.value > 0 ? `${Math.max(6, Math.round((b.value / max) * 100))}%` : '0%',
              background: b.highlight ? 'var(--series-2)' : 'var(--series-1)',
            }}
          />
          <span className="mcol-x">{b.x}</span>
        </div>
      ))}
    </div>
  )
}

// A labelled horizontal bar: name on the left, proportion in the middle, value
// on the right. Used for the weekday breakdown.
function BarRow({ label, value, frac, highlight }) {
  return (
    <div className="pbar-row">
      <span className="pbar-label">{label}</span>
      <span className="pbar-track">
        <span
          className="pbar-fill"
          style={{
            width: frac > 0 ? `${Math.max(3, Math.round(frac * 100))}%` : '0%',
            background: highlight ? 'var(--series-2)' : 'var(--series-1)',
          }}
        />
      </span>
      <span className="pbar-value">{value}</span>
    </div>
  )
}

export function SummaryCharts({ view }) {
  if (!view.dayList.length) return null

  // 1. People here each day — the most recent two working weeks of activity.
  const recent = view.dayList.slice(-14)
  const peakDay = Math.max(...recent.map((d) => d.present))
  const dayBars = recent.map((d) => ({
    key: d.day,
    value: d.present,
    x: WEEKDAYS[weekdayOf(d.day)][0],
    title: `${fmtDay(d.day)} · ${fmtInt(d.present)} people in`,
    highlight: d.present === peakDay,
  }))

  // 2. Busiest days of the week — average present, weekend included so an empty
  //    Saturday reads as empty rather than hidden.
  const profile = view.weekdayProfile
  const maxAvg = Math.max(1, ...profile.map((p) => p.average))

  // 3. When people arrive — arrivals per hour, trimmed to the hours that see any
  //    check-ins so the chart isn't mostly empty midnight columns.
  const hist = view.arrivalHist
  const firstH = hist.findIndex((v) => v > 0)
  const lastH = hist.length - 1 - [...hist].reverse().findIndex((v) => v > 0)
  const peakH = hist.indexOf(Math.max(...hist))
  const hourBars = []
  for (let h = firstH; h >= 0 && h <= lastH; h++) {
    hourBars.push({
      key: h,
      value: hist[h],
      x: h % 3 === 0 ? hourLabel(h) : '',
      title: `${hourLabel(h)} · ${fmtInt(hist[h])} check-ins`,
      highlight: h === peakH,
    })
  }

  return (
    <Card className="viz-card">
      <CardHead
        title="Attendance at a glance"
        subtitle="A quick visual read on how the team has been coming in."
      />

      <div className="viz-grid">
        <div className="viz-panel">
          <p className="viz-head">People here each day</p>
          <p className="viz-desc">Each bar is one day — taller means more people came in. Last 14 days.</p>
          <Columns bars={dayBars} />
        </div>

        <div className="viz-panel">
          <p className="viz-head">Busiest days of the week</p>
          <p className="viz-desc">Average number of people in on each day.</p>
          <div className="pbars">
            {profile.map((p) => (
              <BarRow
                key={p.weekday}
                label={WEEKDAYS[p.weekday]}
                value={fmt1(p.average)}
                frac={p.average / maxAvg}
                highlight={p.average === maxAvg}
              />
            ))}
          </div>
        </div>

        <div className="viz-panel viz-wide">
          <p className="viz-head">When people arrive</p>
          <p className="viz-desc">
            {peakH >= 0 ? `Busiest check-in time is around ${hourLabel(peakH)}.` : 'What time people check in.'}
          </p>
          {hourBars.length ? <Columns bars={hourBars} /> : <p className="muted">No check-ins in this range.</p>}
        </div>
      </div>
    </Card>
  )
}
