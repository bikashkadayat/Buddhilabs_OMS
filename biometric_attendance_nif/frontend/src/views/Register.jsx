import { fmtInt } from '../lib/format'
import { RegisterGrid, registerTable } from '../components/charts/RegisterGrid'
import { ChartCard } from '../components/ui'

export function Register({ view, rows, query, setQuery }) {
  return (
    <ChartCard
      title="Attendance register"
      subtitle="One row per person, one column per day, shaded by time on site. Empty means no punches at all."
      table={() => registerTable(rows, view.fromDay, view.toDay)}
      actions={(
        <input
          type="search"
          className="search"
          value={query}
          placeholder="Filter by name or id"
          aria-label="Filter employees"
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
    >
      {query ? <p className="muted note-line">{`${fmtInt(rows.length)} of ${fmtInt(view.employees.length)} shown`}</p> : null}
      <RegisterGrid rows={rows} fromDay={view.fromDay} toDay={view.toDay} />
    </ChartCard>
  )
}
