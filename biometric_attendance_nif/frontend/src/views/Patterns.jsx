import { ClockChart, clockTable } from '../components/charts/ClockChart'
import { DayLengthChart, dayLengthTable } from '../components/charts/DayLengthChart'
import { HeatmapChart, heatmapTable } from '../components/charts/HeatmapChart'
import { OccupancyChart, occupancyTable } from '../components/charts/OccupancyChart'
import { PunchMixChart, punchMixTable } from '../components/charts/PunchMixChart'
import { TimingTrendChart, timingTrendTable } from '../components/charts/TimingTrendChart'
import { WeekdayChart, weekdayTable } from '../components/charts/WeekdayChart'
import { ChartCard } from '../components/ui'

/** When the office is busy — the shape of a day and of a week. */
export function DailyPattern({ view }) {
  return (
    <>
      <ChartCard
        title="People on site through the day"
        subtitle="Counted between each person's first and last punch, averaged over active days."
        table={() => occupancyTable(view)}
      >
        <OccupancyChart view={view} />
      </ChartCard>

      <div className="grid-2">
        <ChartCard
          title="Attendance by weekday"
          subtitle="Average people present, over every calendar day in range."
          table={() => weekdayTable(view)}
        >
          <WeekdayChart view={view} />
        </ChartCard>

        <ChartCard
          title="First and last punch of the day"
          subtitle="When people arrive and when they leave, by hour."
          table={() => clockTable(view)}
        >
          <ClockChart view={view} />
        </ChartCard>
      </div>

      <ChartCard
        title="Activity by weekday and hour"
        subtitle="Punch volume. Darker means busier."
        table={() => heatmapTable(view)}
      >
        <HeatmapChart view={view} />
      </ChartCard>
    </>
  )
}

/** How the numbers are moving, and how spread out they are. */
export function Trends({ view, lateMinutes }) {
  return (
    <>
      <ChartCard
        title="Arrival and departure over time"
        subtitle="Median first and last punch per day — higher means later. Both are times of day, so they share one axis."
        table={() => timingTrendTable(view)}
      >
        <TimingTrendChart view={view} lateMinutes={lateMinutes} />
      </ChartCard>

      <div className="grid-2">
        <ChartCard
          title="How long days are"
          subtitle="First punch to last punch, bucketed by hour."
          table={() => dayLengthTable(view)}
        >
          <DayLengthChart view={view} />
        </ChartCard>

        <ChartCard
          title="Punch types"
          subtitle="What the terminal recorded each scan as."
          table={() => punchMixTable(view)}
        >
          <PunchMixChart view={view} />
        </ChartCard>
      </div>
    </>
  )
}
