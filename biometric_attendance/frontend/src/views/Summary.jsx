/* A plain-language view of attendance for non-technical readers.
 *
 * Everything here comes from the same computeView() output as the analytical
 * tabs — but framed as sentences and simple cards instead of charts, medians,
 * and "employee-days". No jargon: "Most arrive around 09:42", "Present 45 of 50
 * days", "Always on time".
 */
import { fmt1, fmtClock, fmtInt, fmtPct, fmtSpan } from '../lib/format'
import { DAY, fmtDay } from '../lib/time'
import { HeroTile, StatTile } from '../components/StatTile'
import { Sparkline } from '../components/charts/Sparkline'
import { SummaryCharts } from '../components/SummaryCharts'
import { Card, CardHead } from '../components/ui'

// Stable, soft colour per person so the same face keeps the same avatar tint.
function avatarHue(name) {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) & 0xffff
  return hash % 360
}

function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

// A person's attendance turned into a plain word + colour band.
function attendanceBand(rate) {
  if (rate >= 0.9) return { label: 'Excellent', tone: 'good' }
  if (rate >= 0.75) return { label: 'Good', tone: 'ok' }
  if (rate >= 0.5) return { label: 'Fair', tone: 'warn' }
  return { label: 'Low', tone: 'bad' }
}

function lastSeenText(lastSeen) {
  if (!lastSeen) return 'Not seen yet'
  const day = Math.floor(lastSeen / DAY)
  const minute = Math.floor((lastSeen % DAY) / 60)
  return `Last seen ${fmtDay(day)} at ${fmtClock(minute)}`
}

function PersonCard({ emp, activeDayCount, onSelect }) {
  const rate = activeDayCount ? emp.days / activeDayCount : 0
  const band = attendanceBand(rate)
  const hue = avatarHue(emp.name)

  return (
    <button type="button" className="person-card" onClick={() => onSelect(emp.index)}>
      <div className="person-top">
        <span className="avatar" style={{ background: `hsl(${hue} 60% 90%)`, color: `hsl(${hue} 45% 32%)` }}>
          {initials(emp.name)}
        </span>
        <div className="person-id">
          <span className="person-name">{emp.name}</span>
          <span className="person-sub muted">ID {emp.id}</span>
        </div>
        {emp.late === 0
          ? <span className="pill pill-good">On time</span>
          : <span className="pill pill-warn">{emp.late} late {emp.late === 1 ? 'day' : 'days'}</span>}
      </div>

      <div className="attn">
        <div className="attn-head">
          <span className={`attn-word attn-${band.tone}`}>{band.label} attendance</span>
          <span className="muted">{fmtPct(rate)}</span>
        </div>
        <div className="meter"><span className={`meter-fill meter-${band.tone}`} style={{ width: `${Math.round(rate * 100)}%` }} /></div>
        <span className="person-sub muted">Present {fmtInt(emp.days)} of {fmtInt(activeDayCount)} working days</span>
      </div>

      <div className="person-facts">
        <div className="fact"><span className="fact-label muted">Usually arrives</span><span className="fact-value">{fmtClock(emp.medianArrival)}</span></div>
        <div className="fact"><span className="fact-label muted">Usually leaves</span><span className="fact-value">{fmtClock(emp.medianDeparture)}</span></div>
        <div className="fact"><span className="fact-label muted">Hours a day</span><span className="fact-value">{fmtSpan(emp.medianSpan)}</span></div>
      </div>

      <p className="person-lastseen muted">{lastSeenText(emp.lastSeen)}</p>
    </button>
  )
}

export function Summary({ view, payload, filters, rows, query, setQuery, onSelect }) {
  const active = view.dayList.length > 0
  const onTime = fmtPct(1 - view.lateShare)

  return (
    <>
      <section className="kpi-row" aria-label="Attendance at a glance">
        <HeroTile
          label="People here on a typical day"
          value={active ? fmt1(view.avgPresent) : '--'}
          sub={active ? `out of ${fmtInt(view.employees.length)} people tracked` : 'no attendance in this period'}
        >
          <Sparkline dayList={view.dayList} />
        </HeroTile>

        <div className="stat-grid sum-stats">
          <StatTile label="Most arrive around" value={fmtClock(view.medianArrival)} sub={`${onTime} on time (before ${fmtClock(filters.lateMinutes)})`} />
          <StatTile label="Most leave around" value={fmtClock(view.medianDeparture)} />
          <StatTile label="Typical day at the office" value={fmtSpan(view.medianSpan)} sub="from first to last scan" />
          <StatTile label="People tracked" value={fmtInt(view.employees.length)} sub={`of ${fmtInt(payload.employees.length)} on the device`} />
        </div>
      </section>

      <SummaryCharts view={view} />

      <Card className="people-card">
        <CardHead title="Everyone's attendance" subtitle="Tap a person to see their day-by-day detail.">
          <input
            type="search"
            className="search"
            value={query}
            placeholder="Search by name or ID"
            aria-label="Search employees"
            onChange={(event) => setQuery(event.target.value)}
          />
        </CardHead>

        {query ? <p className="muted note-line">{`${fmtInt(rows.length)} of ${fmtInt(view.employees.length)} people`}</p> : null}

        {rows.length ? (
          <div className="people-grid">
            {rows.map((emp) => (
              <PersonCard key={emp.index} emp={emp} activeDayCount={view.activeDayCount} onSelect={onSelect} />
            ))}
          </div>
        ) : (
          <p className="placeholder">No people match “{query}”.</p>
        )}
      </Card>
    </>
  )
}
