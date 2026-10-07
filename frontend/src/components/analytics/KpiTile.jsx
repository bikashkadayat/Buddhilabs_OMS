import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowDownRight, ArrowUpRight, Info, Minus } from 'lucide-react';

/**
 * One executive number, its movement, and its definition.
 *
 * The definition tooltip is the point. Every KPI here is a ratio with a chosen
 * denominator, and a percentage whose denominator nobody can state is a
 * percentage nobody should act on — so `/analytics/meta/` serves the formulas
 * and they surface here rather than living in a document.
 *
 * A null value renders as an em dash, never as 0. The API returns null when
 * there is nothing to divide by, and printing 0% would turn "we cannot say"
 * into "it is zero".
 */

const DELTA_ICON = { up: ArrowUpRight, down: ArrowDownRight, flat: Minus };

const Delta = ({ value, higherIsBetter = true, suffix = '' }) => {
  if (value === null || value === undefined) return null;
  const rounded = Number(value);
  const direction = rounded > 0.05 ? 'up' : rounded < -0.05 ? 'down' : 'flat';
  const Icon = DELTA_ICON[direction];
  // Tone follows meaning, not sign: a rising absence rate is bad news drawn in
  // the same red as a falling attendance rate.
  const good = direction === 'flat'
    ? null
    : (direction === 'up') === higherIsBetter;
  const tone = good === null ? 'an-delta-flat' : good ? 'an-delta-good' : 'an-delta-bad';

  return (
    <span className={`an-delta ${tone}`}>
      <Icon size={12} aria-hidden="true" />
      {rounded > 0 ? '+' : ''}{rounded.toFixed(1)}{suffix}
      <span className="sr-only"> versus the comparison period</span>
    </span>
  );
};

const KpiTile = ({
  label, value, suffix = '', delta, higherIsBetter = true, definition,
  tone = 'default', to, hint,
}) => {
  const display = value === null || value === undefined
    ? '—'
    : `${typeof value === 'number' ? Number(value.toFixed(2)).toLocaleString() : value}${suffix}`;

  const body = (
    <>
      <span className="an-kpi-label">
        {label}
        {definition && (
          <span className="an-kpi-info" tabIndex={0} role="note" aria-label={definition}>
            <Info size={12} aria-hidden="true" />
            <span className="an-kpi-tip">{definition}</span>
          </span>
        )}
      </span>
      <span className={`an-kpi-value an-tone-${tone}`}>{display}</span>
      <span className="an-kpi-foot">
        <Delta value={delta} higherIsBetter={higherIsBetter} suffix={suffix} />
        {hint && <span className="an-kpi-hint">{hint}</span>}
      </span>
    </>
  );

  return to
    ? <Link to={to} className="an-kpi an-kpi-link">{body}</Link>
    : <div className="an-kpi">{body}</div>;
};

export const KpiGrid = ({ children }) => <div className="an-kpi-grid">{children}</div>;

export default KpiTile;
