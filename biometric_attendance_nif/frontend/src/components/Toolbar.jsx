import { fmtClock, fmtInt } from '../lib/format'
import { dayToISO, fmtDayRange } from '../lib/time'
import { Field, Popover, SegmentedControl } from './ui'

const RANGES = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '12 months' },
  { value: 'all', label: 'All time' },
  { value: 'custom', label: 'Custom' },
]

/* One row, above everything it scopes. Date range first -- it is the control
 * every reader reaches for -- with the rarely-touched settings (workday start,
 * device) folded behind a popover so they stop competing for attention.
 */
export function Toolbar({ payload, filters, setFilters, view, onExport }) {
  const set = (patch) => setFilters((prev) => ({ ...prev, ...patch }))
  const devices = payload?.devices || []

  return (
    <div className="toolbar">
      <SegmentedControl
        label="Date range"
        options={RANGES}
        value={filters.range}
        onChange={(range) => set({ range })}
      />

      {filters.range === 'custom' ? (
        <div className="custom-range">
          <Field label="From">
            <input
              type="date" value={filters.from}
              max={filters.to || undefined}
              onChange={(e) => set({ from: e.target.value })}
            />
          </Field>
          <Field label="To">
            <input
              type="date" value={filters.to}
              min={filters.from || undefined}
              onChange={(e) => set({ to: e.target.value })}
            />
          </Field>
        </div>
      ) : null}

      <p className="range-summary muted">
        {view ? (
          <>
            <strong>{fmtDayRange(view.fromDay, view.toDay)}</strong>
            {` · ${fmtInt(view.total)} punches · ${fmtInt(view.employees.length)} people`}
          </>
        ) : null}
      </p>

      <Popover label="Settings">
        {() => (
          <div className="popover-form">
            <Field label="Day starts" hint="Anything later counts as a late arrival.">
              <input
                type="time" step={300} value={filters.lateTime}
                onChange={(e) => set({ lateTime: e.target.value })}
              />
            </Field>
            {devices.length > 1 ? (
              <Field label="Device">
                <select value={filters.device} onChange={(e) => set({ device: e.target.value })}>
                  <option value="">All devices</option>
                  {devices.map((name, i) => <option key={name} value={String(i)}>{name}</option>)}
                </select>
              </Field>
            ) : null}
            <p className="popover-note">
              {`Late is measured against ${fmtClock(filters.lateMinutes)}. `}
              {devices.length === 1 ? `One device: ${devices[0]}.` : ''}
            </p>
          </div>
        )}
      </Popover>

      <Popover label="Download">
        {(close) => (
          <div className="popover-menu">
            <button type="button" onClick={() => { onExport('summary'); close() }}>Employee summary…</button>
            <button type="button" onClick={() => { onExport('daily'); close() }}>Daily detail…</button>
            <button type="button" onClick={() => { onExport('punches'); close() }}>Punch log…</button>
            <label className="popover-check">
              <input
                type="checkbox" checked={filters.includeAbsent}
                onChange={(e) => set({ includeAbsent: e.target.checked })}
              />
              Include people with no punches in range
            </label>
            <p className="popover-note">
              {view ? `CSV, ${dayToISO(view.fromDay)} to ${dayToISO(view.toDay)}, as filtered.` : ''}
            </p>
          </div>
        )}
      </Popover>
    </div>
  )
}
