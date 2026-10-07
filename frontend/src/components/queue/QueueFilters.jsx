import React from 'react';
import { FILTERS } from '../../services/workQueue';

/**
 * Type filters for the Work Queue.
 *
 * State lives in the URL (`?filter=approvals`) rather than in component state,
 * so Home's tiles can deep-link into a filtered queue and a filtered view can be
 * bookmarked or shared. Rendered as a radio group so a screen reader announces
 * the selection rather than five unrelated buttons.
 */


const QueueFilters = ({ counts = {}, active = 'all', onChange }) => (
  <div className="wq-filters" role="radiogroup" aria-label="Filter work queue">
    {FILTERS.map((f) => {
      const count = counts[f.countKey] ?? 0;
      const selected = active === f.key;
      return (
        <button
          key={f.key}
          type="button"
          role="radio"
          aria-checked={selected}
          className={`wq-chip${selected ? ' is-on' : ''}${f.tone === 'danger' && count > 0 ? ' is-danger' : ''}`}
          onClick={() => onChange?.(f.key)}
        >
          {f.label}
          <span className="wq-chip-n">{count}</span>
        </button>
      );
    })}
  </div>
);

export default QueueFilters;
