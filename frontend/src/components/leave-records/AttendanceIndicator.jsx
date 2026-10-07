import React from 'react';
import { num } from '../../utils/leaveFormat';

/**
 * Attendance percentage pill. Colour-coded AND text-labelled so it is legible
 * without relying on colour (colour-blind support).
 *
 * @param {{percentage: number|string}} props
 */
const AttendanceIndicator = ({ percentage }) => {
  const value = num(percentage);
  let tone = 'good';
  let label = 'Good';
  if (value < 85) {
    tone = 'low';
    label = 'Low';
  } else if (value < 95) {
    tone = 'mid';
    label = 'Fair';
  }

  return (
    <span
      className={`lr-attendance lr-att-${tone}`}
      title={`Attendance ${value.toFixed(1)}% (${label})`}
      aria-label={`Attendance ${value.toFixed(1)} percent, ${label}`}
    >
      <span className="lr-att-dot" aria-hidden="true" />
      {value.toFixed(0)}% · {label}
    </span>
  );
};

export default AttendanceIndicator;
