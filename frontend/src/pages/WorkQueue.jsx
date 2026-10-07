import React, { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CheckCircle2 } from 'lucide-react';
import { useWorkQueue } from '../hooks/useWorkQueue';
import { FILTERS } from '../services/workQueue';
import QueueRow from '../components/queue/QueueRow';
import MobileQueueCards from '../components/queue/MobileQueueCards';
import { useIsMobile } from '../hooks/useIsMobile';
import QueueFilters from '../components/queue/QueueFilters';
import QueueSourceNotice from '../components/queue/QueueSourceNotice';
import { EMPTY } from '../services/emptyStates';

/**
 * My Work Queue (Phase 203 / blueprint §04).
 *
 * Every "waiting on me" list in the system, unioned, sorted by urgency and
 * actionable in place. Replaces having to visit eight per-module pending pages
 * and remember that all eight exist.
 */

const Skeletons = () => (
  <div className="wq-list" aria-hidden="true">
    {Array.from({ length: 5 }, (_, i) => (
      <div className="wq-row is-skeleton" key={i}>
        <span className="wq-sk" style={{ width: 58 }} />
        <div className="wq-main">
          <span className="wq-sk" style={{ width: '46%' }} />
          <span className="wq-sk wq-sk-sm" style={{ width: '28%' }} />
        </div>
        <span className="wq-sk" style={{ width: 96 }} />
        <span className="wq-sk" style={{ width: 62 }} />
        <span className="wq-sk" style={{ width: 120 }} />
      </div>
    ))}
  </div>
);

const WorkQueue = () => {
  const isMobile = useIsMobile();
  const [params, setParams] = useSearchParams();
  const raw = params.get('filter') || 'all';
  const filter = FILTERS.some((f) => f.key === raw) ? raw : 'all';

  const {
    items, counts, sources, failedSources, isLoading, isError, act, retrySources,
  } = useWorkQueue({ filter });

  const setFilter = (key) => {
    const next = new URLSearchParams(params);
    if (key === 'all') next.delete('filter');
    else next.set('filter', key);
    setParams(next, { replace: true });
  };

  const allFailed = sources.length > 0 && failedSources.length === sources.length;
  const outstanding = counts.total;

  const subtitle = useMemo(() => {
    if (isLoading) return 'Gathering everything that needs you…';
    if (allFailed) return 'Could not reach the system just now.';
    const bits = [`${outstanding} item${outstanding === 1 ? '' : 's'} waiting`];
    if (counts.overdue) bits.push(`${counts.overdue} overdue`);
    bits.push('sorted by urgency');
    return bits.join(' · ');
  }, [isLoading, allFailed, outstanding, counts.overdue]);

  return (
    <div className="page wq-page">
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">Workspace</div>
          <h1 className="pg-title">My Work Queue</h1>
          <div className="pg-desc">{subtitle}</div>
        </div>
      </div>

      <QueueFilters counts={counts} active={filter} onChange={setFilter} />

      <QueueSourceNotice sources={sources} onRetry={retrySources} />

      {isLoading ? (
        <Skeletons />
      ) : allFailed || isError ? (
        <div className="wq-empty is-error">
          <div className="wq-empty-ic" aria-hidden="true">!</div>
          <h2>Nothing could be loaded</h2>
          <p>
            The system did not respond. This is usually a connection or session
            problem rather than anything you did.
          </p>
          <button type="button" className="wq-btn is-primary" onClick={retrySources}>
            Try again
          </button>
        </div>
      ) : items.length === 0 ? (
        filter === 'all' ? (
          <div className="wq-empty is-clear">
            <CheckCircle2 size={26} className="wq-empty-ic" aria-hidden="true" />
            <h2>{EMPTY.nothingWaiting}</h2>
            <p>Every approval, review and acknowledgement assigned to you is done.</p>
          </div>
        ) : (
          <div className="wq-empty">
            <div className="wq-empty-ic" aria-hidden="true">—</div>
            <h2>No {FILTERS.find((f) => f.key === filter)?.label.toLowerCase()} right now</h2>
            <p>There are {counts.total} other items in your queue.</p>
            <button type="button" className="wq-btn is-primary" onClick={() => setFilter('all')}>
              Show all {counts.total}
            </button>
          </div>
        )
      ) : (
        isMobile ? (
          <MobileQueueCards items={items} onAction={act} />
        ) : (
          <>
            <div className="wq-head" aria-hidden="true">
              <span>Type</span><span>Item</span><span>Requester</span><span>Waiting</span><span>Action</span>
            </div>
            <div className="wq-list">
              {items.map((item) => (
                <QueueRow key={item.id} item={item} onAction={act} />
              ))}
            </div>
          </>
        )
      )}
    </div>
  );
};

export default WorkQueue;
