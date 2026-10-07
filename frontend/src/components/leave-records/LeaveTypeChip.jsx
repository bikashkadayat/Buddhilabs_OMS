import React from 'react';
import { days } from '../../utils/leaveFormat';

/**
 * Coloured chip showing a leave type and its day count, e.g. "Sick · 3.5 days".
 * The count is always spelled out in text (not colour alone).
 *
 * @param {{leaveType:string, count:number|string, color?:string, unit?:string}} props
 */
const LeaveTypeChip = ({ leaveType, count, color = '#6B7280', unit = 'days' }) => (
  <span className="lr-type-chip" style={{ '--chip-color': color }}>
    <span className="lr-type-swatch" aria-hidden="true" />
    <span className="lr-type-label">{leaveType}</span>
    {count != null && <span className="lr-type-count">{days(count)} {unit}</span>}
  </span>
);

export default LeaveTypeChip;
