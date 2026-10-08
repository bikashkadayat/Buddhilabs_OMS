import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import EmptyState from '../../components/common/EmptyState';
import { supportService } from '../../services/supportService';

/** What's New: how the product has changed, newest first. Opening the page marks them seen. */
const LABEL_TONE = {
  new: 'pf-chip-ok', improved: 'pf-chip-wait', fixed: 'pf-chip-mute', release: 'pf-chip-ok',
  announcement: 'pf-chip-warn', maintenance: 'pf-chip-warn',
};
const FILTERS = [['', 'Everything'], ['new', 'New'], ['improved', 'Improved'], ['fixed', 'Fixed'],
  ['release', 'Release notes'], ['announcement', 'Announcements'], ['maintenance', 'Maintenance']];

const ProductUpdates = () => {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState('');
  useEffect(() => {
    supportService.updates().then((d) => {
      setData(d);
      if (d.unseen) supportService.markUpdatesSeen().catch(() => {});
    }).catch(() => setData({ updates: [] }));
  }, []);

  return (
    <div className="page hc sc">
      <h1 className="hc-h1">What’s new</h1>
      <p className="hc-muted">New features, improvements and fixes, as we ship them.</p>
      {data && data.updates.length > 0 && (
        <div className="hc-cats" role="group" aria-label="Category">
          {FILTERS.filter(([k]) => !k || data.updates.some((u) => u.category === k)).map(([k, label]) => (
            <button key={k || 'all'} type="button" className={`hc-chip${filter === k ? ' is-on' : ''}`}
                    aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>
          ))}
        </div>
      )}
      {data === null ? <p className="hc-muted">Loading…</p> : data.updates.length === 0 ? (
        <EmptyState title="Nothing announced yet." body="When we ship something new, it appears here first." />
      ) : (
        <ol className="sc-updates">
          {data.updates.filter((u) => !filter || u.category === filter).map((u) => (
            <li key={u.id} className={u.is_new ? 'is-new' : ''}>
              <p className="sc-update-meta">
                <span className={`pf-chip ${LABEL_TONE[u.category] || ''}`}>{u.category_display}</span>
                <time dateTime={u.published_at}>{new Date(u.published_at).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}</time>
                {u.is_new && <span className="sc-new">New for you</span>}
              </p>
              <h2>{u.title}</h2>
              {u.summary && <p className="sc-update-summary">{u.summary}</p>}
              {u.body && <p className="sc-update-body">{u.body}</p>}
              {u.link && <Link className="btn btn-ghost btn-sm" to={u.link}>Try it</Link>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};

export default ProductUpdates;
