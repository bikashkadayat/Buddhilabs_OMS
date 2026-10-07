import React from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, Check, Filter, Lock, Plus } from 'lucide-react';

/**
 * The four empty states (Phase D / blueprint §21).
 *
 * "No results" is not an empty state, it is an absence of one. Each variant here
 * answers a different question:
 *
 *   cleared  - there was work and you finished it. Say so, and when.
 *   first    - there has never been anything. Offer the way to make one.
 *   filtered - there IS work, just not matching this filter. Offer the way back.
 *   error    - it could not be loaded. Say what failed and what still works.
 *   denied   - you may not see this. State it plainly; do not offer a retry,
 *              because retrying will not change the answer.
 *
 * An error never apologises and never blames the user; it states the fact and
 * offers the retry.
 */
// Lucide, not typed glyphs (Phase UI-PRODUCTION-V1). A '+' and a '—' set in
// the body face never optically matched the icons beside them, and 'filtered'
// as an em dash said nothing at all — a funnel does.
const ICONS = {
  cleared: Check, first: Plus, filtered: Filter, error: AlertCircle,
  // `denied` is not an error (Phase UI-CONSISTENCY): nothing failed and there
  // is nothing to retry. It was previously an inline 48px lock SVG pasted into
  // three leave and inventory pages, which is how one refusal ends up drawn
  // three slightly different ways.
  denied: Lock,
};

const EmptyState = ({
  variant = 'first', title, body, action, onAction, actionLabel,
}) => (
  <div className={`ui-empty is-${variant}`}>
    <span className={`ui-empty-ic is-${variant}`} aria-hidden="true">
      {React.createElement(ICONS[variant] || ICONS.first, { size: 22 })}
    </span>
    <h2 className="ui-empty-title">{title}</h2>
    {body && <p className="ui-empty-body">{body}</p>}
    {action?.to && (
      <Link to={action.to} className="ui-empty-btn">{action.label}</Link>
    )}
    {onAction && (
      <button type="button" className="ui-empty-btn" onClick={onAction}>
        {actionLabel || 'Try again'}
      </button>
    )}
  </div>
);

export default EmptyState;
