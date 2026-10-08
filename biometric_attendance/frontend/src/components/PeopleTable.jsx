import { fmtClock, fmtInt, fmtPct, fmtSpan } from '../lib/format'
import { fmtDay } from '../lib/time'

const COLUMNS = [
  { key: 'name', label: 'Employee', align: 'left' },
  { key: 'days', label: 'Days present' },
  { key: 'rate', label: 'Attendance' },
  { key: 'arrival', label: 'Median arrival' },
  { key: 'departure', label: 'Median departure' },
  { key: 'hours', label: 'Median hours' },
  { key: 'late', label: 'Late days' },
  { key: 'punches', label: 'Punches' },
  { key: 'last', label: 'Last seen' },
]

export function PeopleTable({ rows, sort, onSort, selected, onSelect, lateMinutes }) {
  return (
    <div className="table-scroll">
      <table className="people-table">
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.key}
                className={column.align === 'left' ? 'left' : undefined}
                aria-sort={sort.key === column.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
                onClick={() => onSort(column.key)}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((emp) => (
            <tr
              key={emp.index}
              tabIndex={0}
              className={emp.index === selected ? 'selected' : undefined}
              onClick={() => onSelect(emp.index)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelect(emp.index)
                }
              }}
            >
              <td className="left">
                <span className="emp-cell">
                  <span>{emp.name}</span>
                  <span className="emp-id">{`#${emp.id}`}</span>
                </span>
              </td>
              <td>{fmtInt(emp.days)}</td>
              {/* A bar behind the number turns a column of percentages into
                  something scannable without costing a separate chart. */}
              <td className="meter-cell">
                <span className="meter" style={{ '--fill': `${Math.round(emp.rate * 100)}%` }} />
                <span className="meter-value">{fmtPct(emp.rate)}</span>
              </td>
              <td className={emp.medianArrival > lateMinutes ? 'flag' : undefined}>{fmtClock(emp.medianArrival)}</td>
              <td>{fmtClock(emp.medianDeparture)}</td>
              <td>{fmtSpan(emp.medianSpan)}</td>
              <td className={emp.late ? undefined : 'dim'}>{fmtInt(emp.late)}</td>
              <td>{fmtInt(emp.punches)}</td>
              <td className="dim">{fmtDay(emp.lastDay)}</td>
            </tr>
          ))}
          {!rows.length ? (
            <tr><td className="left dim" colSpan={COLUMNS.length}>No employees match this filter.</td></tr>
          ) : null}
        </tbody>
      </table>
    </div>
  )
}
