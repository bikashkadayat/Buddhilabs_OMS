import React from 'react';
import { Link } from 'react-router-dom';

/**
 * A figure that is also a door (Phase D / blueprint §21).
 *
 * The rule this component exists to enforce: EVERY NUMBER IS A LINK to the
 * filtered list that produced it. A statistic you cannot act on belongs in a
 * report, not on a working screen.
 *
 * `to` is therefore required in practice. A tile without one still renders - some
 * figures genuinely have no destination - but it renders as a plain figure with
 * no affordance, rather than as something that looks clickable and is not.
 */
const StatTile = ({ value, label, hint, to, tone = '', cta = 'Open' }) => {
  const body = (
    <>
      <span className="ui-tile-v">{value}</span>
      <span className="ui-tile-l">{label}</span>
      {hint && <span className="ui-tile-h">{hint}</span>}
      {to && <span className="ui-tile-cta">{cta} →</span>}
    </>
  );

  const cls = `ui-tile${tone ? ` is-${tone}` : ''}${to ? '' : ' is-static'}`;
  return to
    ? <Link to={to} className={cls}>{body}</Link>
    : <div className={cls}>{body}</div>;
};

export default StatTile;
