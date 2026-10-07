import { useCallback, useEffect, useMemo, useState } from 'react'

import { computeView, visibleEmployees } from './lib/analytics'
import { absentEmployees, dailyReport, download, punchReport, reportFilename, summaryReport, weeklyReport } from './lib/csv'
import { todayDay } from './lib/time'
import { useDataset } from './hooks/useDataset'

import { TopBar } from './components/TopBar'
import { Toolbar } from './components/Toolbar'
import { NotesBanner } from './components/NotesBanner'
import { EmployeeDrawer } from './components/EmployeeDrawer'
import { TooltipProvider } from './components/Tooltip'
import { Summary } from './views/Summary'
import { Register } from './views/Register'
import { People } from './views/People'

// Kept simple on purpose: a plain-language Summary leads, with the detailed
// People list and the Register grid behind it. The heavier analytical views
// (Daily pattern, Trends, the old Overview) still exist as components but are
// no longer surfaced as tabs — most viewers only need the Summary.
const TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'people', label: 'People' },
  { id: 'register', label: 'Register' },
]

const DEFAULT_LATE = '10:15'

const minutesFromTime = (value) => {
  const [h, m] = (value || DEFAULT_LATE).split(':').map(Number)
  return (h || 0) * 60 + (m || 0)
}

/* The selected tab, range and employee live in the query string, so a view can
 * be pasted into a chat and land on exactly what the sender was looking at. */
function readUrl() {
  const params = new URLSearchParams(window.location.search)
  const tab = TABS.some((t) => t.id === params.get('tab')) ? params.get('tab') : 'summary'
  return {
    tab,
    employee: params.get('employee'),
    filters: {
      range: params.get('range') || '90',
      from: params.get('from') || '',
      to: params.get('to') || '',
      device: params.get('device') || '',
      lateTime: params.get('late') || DEFAULT_LATE,
      includeAbsent: false,
    },
  }
}

export default function App() {
  const initial = useMemo(readUrl, [])
  const { payload, meta, error, loading } = useDataset()

  const [tab, setTab] = useState(initial.tab)
  const [rawFilters, setFilters] = useState(initial.filters)
  const [selectedId, setSelectedId] = useState(initial.employee)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState({ key: 'days', dir: -1 })

  const filters = useMemo(
    () => ({ ...rawFilters, lateMinutes: minutesFromTime(rawFilters.lateTime) }),
    [rawFilters],
  )

  // Recomputed only when the data or a filter actually moves -- not on tab
  // switches, hovers, or sorting.
  const view = useMemo(
    () => (payload ? computeView(payload, filters, todayDay()) : null),
    [payload, filters],
  )

  const rows = useMemo(() => (view ? visibleEmployees(view, query, sort) : []), [view, query, sort])

  const selectedIndex = useMemo(() => {
    if (!payload || selectedId == null) return null
    const index = payload.employees.findIndex((p) => p.id === selectedId)
    return index >= 0 ? index : null
  }, [payload, selectedId])

  const selectedEmployee = useMemo(
    () => (view && selectedIndex != null ? view.employees.find((e) => e.index === selectedIndex) : null),
    [view, selectedIndex],
  )

  useEffect(() => {
    const params = new URLSearchParams()
    if (tab !== 'summary') params.set('tab', tab)
    params.set('range', filters.range)
    if (filters.range === 'custom') {
      if (filters.from) params.set('from', filters.from)
      if (filters.to) params.set('to', filters.to)
    }
    if (filters.device) params.set('device', filters.device)
    if (filters.lateTime !== DEFAULT_LATE) params.set('late', filters.lateTime)
    if (selectedId) params.set('employee', selectedId)
    const search = params.toString()
    window.history.replaceState(null, '', search ? `?${search}` : window.location.pathname)
  }, [tab, filters, selectedId])

  const onSort = useCallback((key) => {
    // Same column flips direction; a new column starts descending, except the
    // name, where A–Z is what people expect.
    setSort((prev) => (prev.key === key ? { key, dir: -prev.dir } : { key, dir: key === 'name' ? 1 : -1 }))
  }, [])

  const onSelect = useCallback((index) => {
    setSelectedId((prev) => (prev === payload.employees[index].id ? null : payload.employees[index].id))
  }, [payload])

  const exportReport = useCallback((kind, onlySelected) => {
    if (!view) return
    let employees
    let person = null

    if (onlySelected) {
      if (!selectedEmployee) return
      employees = [selectedEmployee]
      person = payload.employees[selectedIndex]
    } else {
      employees = rows
      if (kind === 'summary' && filters.includeAbsent) {
        employees = employees.concat(absentEmployees(payload, employees, query))
      }
    }

    const csv = kind === 'summary'
      ? summaryReport(view, employees)
      : kind === 'weekly'
        ? weeklyReport(employees, filters.lateMinutes)
        : kind === 'daily'
          ? dailyReport(employees, filters.lateMinutes)
          : punchReport(employees, payload.devices)

    download(reportFilename(kind, view, person), csv)
  }, [view, rows, payload, query, filters, selectedEmployee, selectedIndex])

  return (
    <TooltipProvider>
      <div className="app">
        <TopBar meta={meta} error={error} loading={loading && !payload} />
        <NotesBanner notes={meta?.notes} />

        <Toolbar
          payload={payload}
          filters={filters}
          setFilters={setFilters}
          view={view}
          onExport={(kind) => exportReport(kind, false)}
        />

        <nav className="tabs" role="tablist" aria-label="Sections">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              className="tab"
              aria-selected={tab === entry.id}
              onClick={() => setTab(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </nav>

        <main className={loading && payload ? 'content refreshing' : 'content'}>
          {!payload && loading ? <p className="placeholder">Loading attendance…</p> : null}
          {!payload && error ? (
            <div className="placeholder error">
              <p>Could not reach the dashboard API.</p>
              <p className="muted">
                Start it with <code>python -m morx web</code>, then reload.
              </p>
              <p className="muted">{error}</p>
            </div>
          ) : null}

          {view ? (
            <>
              {tab === 'summary' ? (
                <Summary
                  view={view}
                  payload={payload}
                  filters={filters}
                  rows={rows}
                  query={query}
                  setQuery={setQuery}
                  onSelect={onSelect}
                />
              ) : null}
              {tab === 'register' ? (
                <Register view={view} rows={rows} query={query} setQuery={setQuery} />
              ) : null}
              {tab === 'people' ? (
                <People
                  rows={rows}
                  total={view.employees.length}
                  query={query}
                  setQuery={setQuery}
                  sort={sort}
                  onSort={onSort}
                  selected={selectedIndex}
                  onSelect={onSelect}
                  lateMinutes={filters.lateMinutes}
                />
              ) : null}
            </>
          ) : null}

          {view && payload && !view.total ? (
            <p className="placeholder">No punches in this range. Try a wider date range.</p>
          ) : null}
        </main>

        <footer className="footnote muted">
          {'A day’s length is first punch to last punch on the same day — the terminal records scans, not shifts, '}
          {'so a missed check-out shortens the day rather than running past midnight. '}
          {payload ? `Reading ${payload.source.backend} at ${payload.source.location}.` : ''}
        </footer>

        {selectedEmployee ? (
          <EmployeeDrawer
            employee={selectedEmployee}
            person={payload.employees[selectedIndex]}
            view={view}
            lateMinutes={filters.lateMinutes}
            onClose={() => setSelectedId(null)}
            onExport={(kind) => exportReport(kind, true)}
          />
        ) : null}
      </div>
    </TooltipProvider>
  )
}
