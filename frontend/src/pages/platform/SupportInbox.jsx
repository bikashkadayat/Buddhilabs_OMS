import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Star } from 'lucide-react';

import PageHeader from '../../components/common/PageHeader';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';
import { describeApiError } from '../../services/apiErrors';

/**
 * What customers wrote to the platform team: questions, problems, feature
 * requests and ratings. A reply is emailed to the person who wrote.
 */
const TABS = [
  ['open', 'Open'],
  ['', 'Everything'],
  ['feature', 'Feature requests'],
  ['feedback', 'Ratings'],
];
const when = (iso) => new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });

const Request = ({ r, onSaved }) => {
  const [reply, setReply] = useState(r.response || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async (status) => {
    setBusy(true); setError('');
    try {
      await platformService.updateSupport(r.id, { status, response: reply });
      onSaved();
    } catch (e) {
      setError(describeApiError(e, 'That didn’t save. Please try again.'));
      setBusy(false);
    }
  };
  return (
    <article className="pc-card" aria-label={r.subject || r.kind_display}>
      <header className="pc-head">
        <div>
          <p className="pc-org">{r.kind === 'feedback' ? `Rated ${r.feature || 'the product'}` : (r.subject || r.message.slice(0, 80))}</p>
          <p className="pc-muted">
            {r.kind_display} · {r.organization_slug
              ? <Link to={`/platform/organizations/${r.organization_slug}`}>{r.organization_name}</Link>
              : 'platform'} · {r.submitted_by_name} &lt;{r.submitted_by_email}&gt; · {when(r.created_at)}
          </p>
        </div>
        {r.kind === 'feedback' ? (
          <span className="si-stars" aria-label={`${r.rating} out of 5`}>
            {[1, 2, 3, 4, 5].map((n) => <Star key={n} size={15} className={n <= r.rating ? 'is-on' : ''} aria-hidden="true" />)}
          </span>
        ) : (
          <span className={`pf-chip ${r.status === 'resolved' ? 'pf-chip-ok' : 'pf-chip-wait'}`}>{r.status_display}</span>
        )}
      </header>
      {r.message && <p className="pc-note">{r.message}</p>}
      {r.page && <p className="pc-muted">From the page <code>{r.page}</code></p>}
      {r.kind !== 'feedback' && (
        <>
          <label className="pc-text">
            <span>Reply (emailed to {r.submitted_by_email})</span>
            <textarea rows={2} value={reply} onChange={(e) => setReply(e.target.value)} />
          </label>
          {error && <p className="pc-err" role="alert">{error}</p>}
          <div className="pc-actions">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save('resolved')}>
              {reply.trim() && reply !== r.response ? 'Reply and resolve' : 'Mark resolved'}
            </button>
            {r.status === 'open' && (
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => save('in_progress')}>
                Working on it
              </button>
            )}
          </div>
        </>
      )}
    </article>
  );
};

const SupportInbox = () => {
  const [tab, setTab] = useState('open');
  const [data, setData] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const params = tab === 'open' ? { status: 'open' }
      : tab === 'feature' || tab === 'feedback' ? { kind: tab } : {};
    platformService.supportInbox(params).then(({ data: d }) => setData(d)).catch(() => setData({ requests: [] }));
  }, [tab, reload]);

  const fb = data?.feedback_30d;
  return (
    <div className="page">
      <PageHeader breadcrumb="Customers" title="Support"
                  description={fb?.count
                    ? `What customers asked, reported and suggested. Ratings in the last 30 days: ${fb.average} / 5 from ${fb.count}.`
                    : 'What customers asked, reported and suggested.'} />
      <div className="pf-tabs" role="tablist" aria-label="Show">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={`pf-tab${tab === key ? ' is-active' : ''}`} onClick={() => { setTab(key); setData(null); }}>
            {label}
          </button>
        ))}
      </div>
      {data === null ? <p className="pc-muted">Loading…</p> : data.requests.length === 0 ? (
        <EmptyState variant="cleared" title={tab === 'open' ? 'Nothing waiting' : 'Nothing here yet'}
                    body={tab === 'open' ? 'Every request has an answer.' : 'Requests and ratings from customers appear here.'} />
      ) : (
        <div className="pc-list">
          {data.requests.map((r) => <Request key={r.id} r={r} onSaved={() => setReload((n) => n + 1)} />)}
        </div>
      )}
    </div>
  );
};

export default SupportInbox;
