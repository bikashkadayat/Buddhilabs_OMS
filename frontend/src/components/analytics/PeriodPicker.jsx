import React from 'react';
import { CalendarRange } from 'lucide-react';

/**
 * The filter row every analytics page shares: preset, custom range, granularity,
 * comparison. One row, above the charts, never beside them.
 *
 * Presets carry the load because the honest answer to "what window?" is almost
 * always a named period. The custom range is there for the audit case, and only
 * expands when chosen — two date inputs on every dashboard is clutter charging
 * rent.
 */
const PRESETS = [
  { value: 'mtd', label: 'Month to date' },
  { value: 'qtd', label: 'Quarter to date' },
  { value: 'ytd', label: 'Year to date' },
  { value: 'last_30d', label: 'Last 30 days' },
  { value: 'last_6m', label: 'Last 6 months' },
  { value: 'last_12m', label: 'Last 12 months' },
  { value: 'last_24m', label: 'Last 24 months' },
];

const GRANULARITIES = [
  { value: '', label: 'Auto' },
  { value: 'day', label: 'Daily' },
  { value: 'month', label: 'Monthly' },
  { value: 'quarter', label: 'Quarterly' },
  { value: 'year', label: 'Yearly' },
];

const PeriodPicker = ({
  value = {}, onChange, showGranularity = true, showCompare = true,
  departments = [], generatedAt, cached, truncated,
}) => {
  const custom = Boolean(value.from || value.to);
  const set = (patch) => onChange({ ...value, ...patch });

  return (
    <div className="an-filters">
      <div className="an-filter">
        <label className="wf-field-label" htmlFor="an-period">Period</label>
        <select
          id="an-period" className="wf-input" value={custom ? 'custom' : (value.period || 'last_12m')}
          onChange={(event) => {
            const next = event.target.value;
            if (next === 'custom') set({ period: undefined, from: '', to: '' });
            else set({ period: next, from: undefined, to: undefined });
          }}
        >
          {PRESETS.map((preset) => (
            <option key={preset.value} value={preset.value}>{preset.label}</option>
          ))}
          <option value="custom">Custom range…</option>
        </select>
      </div>

      {custom && (
        <div className="an-filter an-filter-range">
          <label className="wf-field-label" htmlFor="an-from">
            <CalendarRange size={13} aria-hidden="true" /> Range
          </label>
          <span className="an-range-inputs">
            <input
              id="an-from" type="date" className="wf-input" aria-label="From date"
              value={value.from || ''} max={value.to || undefined}
              onChange={(event) => set({ from: event.target.value })}
            />
            <span aria-hidden="true">→</span>
            <input
              type="date" className="wf-input" aria-label="To date"
              value={value.to || ''} min={value.from || undefined}
              onChange={(event) => set({ to: event.target.value })}
            />
          </span>
        </div>
      )}

      {showGranularity && (
        <div className="an-filter">
          <label className="wf-field-label" htmlFor="an-granularity">Group by</label>
          <select
            id="an-granularity" className="wf-input" value={value.granularity || ''}
            onChange={(event) => set({ granularity: event.target.value || undefined })}
          >
            {GRANULARITIES.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      )}

      {departments.length > 0 && (
        <div className="an-filter">
          <label className="wf-field-label" htmlFor="an-department">Department</label>
          <select
            id="an-department" className="wf-input" value={value.department || ''}
            onChange={(event) => set({ department: event.target.value || undefined })}
          >
            <option value="">All departments</option>
            {departments.map((department) => (
              <option key={department.id ?? 'unassigned'} value={department.id ?? 'unassigned'}>
                {department.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {showCompare && (
        <div className="an-filter">
          <label className="wf-field-label" htmlFor="an-compare">Compare with</label>
          <select
            id="an-compare" className="wf-input" value={value.compare || ''}
            onChange={(event) => set({ compare: event.target.value || undefined })}
          >
            <option value="">No comparison</option>
            <option value="previous">Previous period</option>
            <option value="year_ago">Same period last year</option>
          </select>
        </div>
      )}

      <div className="an-filter-meta">
        {generatedAt && (
          <span className="an-asof">
            as of {new Date(generatedAt).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit' })}
            {cached && <span className="an-cached" title="Served from cache"> · cached</span>}
          </span>
        )}
        {truncated && (
          <span className="wf-badge wf-badge-info wf-badge-sm">
            Range or detail reduced to fit
          </span>
        )}
      </div>
    </div>
  );
};

export default PeriodPicker;
