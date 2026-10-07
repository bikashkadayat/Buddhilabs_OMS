import React from 'react';
import StatTile from './StatTile';

/**
 * DEPRECATED (Phase D). Superseded by StatTile.
 *
 * Kept as a thin adapter rather than deleted: it has call sites across the leave
 * and admin pages, and rewriting them all in a design phase would mean touching
 * files this phase has no other reason to open. Existing callers keep working
 * and get the new visual language for free; new code should import StatTile.
 *
 * The one behavioural difference is deliberate: StatTile can be a link, and
 * StatCard never was, so a converted card renders as a plain figure until its
 * caller gives it a `to`.
 */
const StatCard = ({ title, value, subtitle, colorClass = '' }) => (
  <StatTile value={value} label={title} hint={subtitle} tone={colorClass.replace(/^s-/, '')} />
);

export default StatCard;
