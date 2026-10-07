import React from 'react';
import StatusBadge from './StatusBadge';

/**
 * DEPRECATED (Phase D). Superseded by StatusBadge.
 *
 * The original was a switch over a handful of statuses that fell through to the
 * raw string for anything it did not know - so a new workflow state rendered as
 * `pending_acknowledgement` on screen. StatusBadge maps the full set and this
 * adapter inherits that fix.
 *
 * Kept for its existing call sites; new code should import StatusBadge.
 */
const Badge = ({ status }) => <StatusBadge status={status} />;

export default Badge;
