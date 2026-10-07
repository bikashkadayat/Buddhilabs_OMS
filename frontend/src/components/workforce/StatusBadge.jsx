import React from 'react';
import { requestLabel, requestTone, statusLabel, statusTone } from './statusMeta';

/**
 * Attendance status chip. Colours come from `statusMeta`, so this component and
 * every table, tile and report legend agree by construction.
 */
const StatusBadge = ({ status, size = 'md', title }) => (
  <span
    className={`wf-badge wf-badge-${statusTone(status)} ${size === 'sm' ? 'wf-badge-sm' : ''}`}
    title={title || statusLabel(status)}
  >
    {statusLabel(status)}
  </span>
);

/** Workflow status chip for correction and WFH requests. */
export const RequestBadge = ({ status }) => (
  <span className={`wf-badge wf-badge-${requestTone(status)}`}>
    {requestLabel(status)}
  </span>
);

export default StatusBadge;
