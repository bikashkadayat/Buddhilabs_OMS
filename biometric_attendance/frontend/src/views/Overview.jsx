import { fmt1, fmtCompact, fmtClock, fmtInt, fmtPct, fmtSpan } from '../lib/format'
import { HeroTile, StatTile } from '../components/StatTile'
import { Sparkline } from '../components/charts/Sparkline'
import { TrendChart, trendTable } from '../components/charts/TrendChart'
import { CalendarChart, calendarTable } from '../components/charts/CalendarChart'
import { ChartCard } from '../components/ui'

export function Overview({ view, payload, filters }) {
  const active = view.dayList.length > 0

  return (
    <>
      <section className="kpi-row" aria-label="Headline figures">
        <HeroTile
          label="Average daily attendance"
          value={active ? fmt1(view.avgPresent) : '--'}
          sub={active ? `people per active day · peak ${fmtInt(view.peakPresent)}` : 'no activity in range'}
        >
          <Sparkline dayList={view.dayList} />
        </HeroTile>

        <div className="stat-grid">
          <StatTile
            label="Punches"
            value={fmtCompact(view.total)}
            sub={view.live ? `${fmtInt(view.live)} captured live` : 'all from device backlog'}
          />
          <StatTile
            label="People seen"
            value={fmtInt(view.employees.length)}
            sub={`of ${fmtInt(payload.employees.length)} known`}
          />
          <StatTile
            label="Active days"
            value={fmtInt(view.activeDayCount)}
            sub={`days with any punch, of ${fmtInt(view.rangeDays)}`}
          />
          <StatTile
            label="Median arrival"
            value={fmtClock(view.medianArrival)}
            sub={`${fmtPct(view.lateShare)} after ${fmtClock(filters.lateMinutes)}`}
          />
          <StatTile
            label="Median departure"
            value={fmtClock(view.medianDeparture)}
            sub={`over ${fmtInt(view.sessions)} employee-days`}
          />
          <StatTile
            label="Median day length"
            value={fmtSpan(view.medianSpan)}
            sub="first punch to last punch"
          />
        </div>
      </section>

      <ChartCard
        title="People present per day"
        subtitle="Distinct employees with at least one punch, and the 7-day average."
        table={() => trendTable(view)}
      >
        <TrendChart view={view} />
      </ChartCard>

      <ChartCard
        title="Attendance calendar"
        subtitle="Every day in range, shaded by how many people were present."
        table={() => calendarTable(view)}
      >
        <CalendarChart view={view} />
      </ChartCard>
    </>
  )
}
