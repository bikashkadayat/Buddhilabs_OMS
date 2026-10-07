import React from 'react';
import { recentYears } from '../../utils/leaveFormat';

/**
 * Dropdown of selectable years (defaults to the last 4 years incl. current).
 *
 * @param {{currentYear:number, onChange:(y:number)=>void, minYear?:number, count?:number}} props
 */
const YearSelector = ({ currentYear, onChange, minYear, count = 4 }) => {
  let years = recentYears(count);
  if (minYear) years = years.filter((y) => y >= minYear);
  if (!years.includes(currentYear)) years = [currentYear, ...years];

  return (
    <label className="lr-year-select">
      <span className="sr-only">Select year</span>
      <select
        aria-label="Select year"
        value={currentYear}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {years.map((y) => (
          <option key={y} value={y}>{y}</option>
        ))}
      </select>
    </label>
  );
};

export default YearSelector;
