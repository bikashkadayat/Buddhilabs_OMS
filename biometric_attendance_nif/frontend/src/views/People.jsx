import { fmtInt } from '../lib/format'
import { PeopleTable } from '../components/PeopleTable'
import { PunctualityChart, punctualityTable } from '../components/charts/PunctualityChart'
import { Card, CardHead, ChartCard } from '../components/ui'

export function People({ rows, query, setQuery, sort, onSort, selected, onSelect, lateMinutes, total }) {
  return (
    <>
      <ChartCard
        title="Punctuality"
        subtitle="Each line spans a person's earliest to latest arrival; the dot is their median. Red means the median is after the workday start."
        table={() => punctualityTable(rows)}
      >
        <PunctualityChart rows={rows} lateMinutes={lateMinutes} />
      </ChartCard>

      <Card className="people-card">
        <CardHead
          title="Employees"
          subtitle="Click a row for that person's detail. Click a heading to sort."
        >
          <input
            type="search"
            className="search"
            value={query}
            placeholder="Filter by name or id"
            aria-label="Filter employees"
            onChange={(event) => setQuery(event.target.value)}
          />
        </CardHead>

        {query ? <p className="muted note-line">{`${fmtInt(rows.length)} of ${fmtInt(total)} shown`}</p> : null}

        <PeopleTable
          rows={rows}
          sort={sort}
          onSort={onSort}
          selected={selected}
          onSelect={onSelect}
          lateMinutes={lateMinutes}
        />
      </Card>
    </>
  )
}
