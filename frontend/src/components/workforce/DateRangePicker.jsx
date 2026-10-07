import React from 'react';

/** `?from=` / `?to=` for every windowed endpoint. Uncontrolled dates would
 *  cause a refetch per keystroke, so the caller debounces before querying. */
const DateRangePicker = ({ from, to, onChange, label = 'Period' }) => (
  <div className="wf-daterange">
    <span className="wf-field-label">{label}</span>
    <input
      type="date" className="wf-input" value={from || ''} aria-label="From date"
      max={to || undefined}
      onChange={(e) => onChange({ from: e.target.value, to })}
    />
    <span className="wf-daterange-sep">→</span>
    <input
      type="date" className="wf-input" value={to || ''} aria-label="To date"
      min={from || undefined}
      onChange={(e) => onChange({ from, to: e.target.value })}
    />
  </div>
);

export default DateRangePicker;
